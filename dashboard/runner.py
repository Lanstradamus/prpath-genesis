"""Script runner for PRPath dashboard.

Uses asyncio subprocess primitives to safely spawn pipeline scripts as child
processes. Argv is always passed as a list — NO shell interpolation, NO
command injection surface. Script identifiers are restricted to a hardcoded
allowlist (SCRIPT_COMMANDS below).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import AsyncIterator

from dashboard import db

GENESIS_ROOT = Path(__file__).resolve().parent.parent  # /Users/.../prpath-genesis/

# Hardcoded allowlist: button key → argv list.
# User input never reaches argv — the button only selects a key.
SCRIPT_COMMANDS: dict[str, list[str]] = {
    "preflight":        ["python3", "preflight.py"],
    "scout":            ["python3", "scout.py"],
    "verify":           ["python3", "verify.py"],
    "metrics_pulse":    ["python3", "metrics_pulse.py"],
    "sunday_recap":     ["python3", "sunday_recap.py"],
    "run_batch_sun":    ["python3", "run_batch.py", "--target", "sun", "--dry-run"],
    "run_batch_wed":    ["python3", "run_batch.py", "--target", "wed", "--dry-run"],
    "run_batch_resume": ["python3", "run_batch.py"],  # --resume <id> appended per call
    "midweek_pulse":    ["python3", "metrics_pulse.py", "--midweek"],
    "monthly_retro":    ["python3", "sunday_recap.py", "--monthly"],
}


# Track active runs so the SSE endpoint can stream output to subscribers.
_active_runs: dict[int, asyncio.Queue[str | None]] = {}


async def run_script(script_key: str, extra_args: list[str] | None = None, triggered_by: str = "manual") -> int:
    """Start a script subprocess. Returns the run_id. Output streams in background."""
    if script_key not in SCRIPT_COMMANDS:
        raise ValueError(f"unknown script: {script_key}")

    cmd = list(SCRIPT_COMMANDS[script_key])
    if extra_args:
        cmd.extend(extra_args)
    args_str = " ".join(cmd[1:])

    run_id = db.start_script_run(script=script_key, args=args_str, triggered_by=triggered_by)

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    _active_runs[run_id] = queue

    db.log_event(
        kind="action",
        title=f"▶ Running {script_key}",
        detail=args_str,
        actor=triggered_by,
    )

    asyncio.create_task(_spawn_and_stream(run_id, cmd, queue))
    return run_id


async def _spawn_and_stream(run_id: int, cmd: list[str], queue: asyncio.Queue[str | None]) -> None:
    """Spawn subprocess (list-argv, no shell), capture output, push to queue + db."""
    accumulated: list[str] = []
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")

    try:
        # asyncio.create_subprocess_exec is the list-argv-only primitive:
        # it does NOT pass args to a shell. Each arg is a separate argv entry.
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(GENESIS_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
    except Exception as exc:
        msg = f"[dashboard runner error] could not spawn: {exc}"
        accumulated.append(msg)
        await queue.put(msg)
        await queue.put(None)
        db.finish_script_run(run_id, exit_code=127, output=msg)
        _active_runs.pop(run_id, None)
        return

    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        decoded = line.decode("utf-8", errors="replace").rstrip("\n")
        accumulated.append(decoded)
        await queue.put(decoded)
        if len(accumulated) % 10 == 0:
            db.update_running_output(run_id, "\n".join(accumulated))

    exit_code = await proc.wait()
    full_output = "\n".join(accumulated)
    db.finish_script_run(run_id, exit_code=exit_code, output=full_output)

    await queue.put(f"\n[exit {exit_code}]")
    await queue.put(None)
    _active_runs.pop(run_id, None)

    status_icon = "✓" if exit_code == 0 else "✗"
    kind = "success" if exit_code == 0 else "error"
    script_name = cmd[1] if len(cmd) > 1 else "script"
    db.log_event(
        kind=kind,
        title=f"{status_icon} Finished {script_name} (exit {exit_code})",
        detail="",
        actor="system",
    )

    # If a batch manifest was emitted, auto-import into SQLite so the approval
    # queue shows up on the dashboard without a manual step.
    if "run_batch.py" in cmd and exit_code == 0:
        for line in accumulated:
            if "Wrote manifest:" in line:
                try:
                    manifest_path = Path(line.split("Wrote manifest:", 1)[1].strip())
                    if manifest_path.is_file():
                        db.upsert_batch_from_manifest(manifest_path)
                        db.log_event(
                            "success",
                            f"Batch imported: {manifest_path.parent.name}",
                            f"9 slots ready for approval",
                            "system",
                        )
                except Exception as exc:
                    db.log_event("warn", "Batch import failed", str(exc), "system")
                break


async def stream_run_output(run_id: int) -> AsyncIterator[str]:
    """Async iterator yielding output lines for a specific run. SSE-friendly."""
    queue = _active_runs.get(run_id)
    if queue is None:
        # Already finished — replay from DB
        with db.get_db() as d:
            row = d.execute(
                "SELECT output, exit_code FROM script_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row:
            for line in (row["output"] or "").splitlines():
                yield line
            yield f"\n[exit {row['exit_code']}]"
        return

    while True:
        item = await queue.get()
        if item is None:
            return
        yield item
