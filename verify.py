#!/usr/bin/env python3
"""PRPath daily verify — confirm yesterday's posts published, pull 24h metrics, archive slides.

CLI:
    python3 verify.py [--date YYYY-MM-DD]

Default date is yesterday (America/Chicago). For each slot in yesterday's batch
manifest, hits Post for Me for status + 24h metrics (views/likes/saves/comments),
archives slide PNGs from `posts_v2/<post_id>/` to
`posted/<YYYY-MM-DD>_slot<N>_<post_id>/`, writes a one-shot `metrics.json`, and
appends the post_id to `data/used_post_ids.json`. Sends a Telegram summary
(green = ok, red = any flag). Saves is the PRIMARY SECONDARY METRIC per Week-2 v2.1.

Hard rules:
- NEVER delete slide files — always move them.
- NEVER modify metrics.json after first write (snapshot only).
- NEVER modify used_post_ids.json outside this script.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
PICKS_DIR = SCRIPT_DIR / "picks"
BATCHES_DIR = PICKS_DIR / "_batches"
POSTED_DIR = SCRIPT_DIR / "posted"
DATA_DIR = SCRIPT_DIR / "data"
USED_POSTS_PATH = DATA_DIR / "used_post_ids.json"
PRPATHSHOTS_POSTS_DIR = Path("/Users/lancesessions/Developer/PRPathShots/samples/posts_v2")
NOTIFY_SCRIPT = SCRIPT_DIR / "notify.py"
POSTFORME_CLIENT = SCRIPT_DIR / "postforme_client.py"
SLIDE_FILENAMES = ("slide_01_issue.png", "slide_02_solution.png")


@dataclass
class SlotResult:
    """Outcome of verifying a single slot in the batch manifest."""

    slot_index: int
    post_id: str
    day: str
    feature_anchor: str | None = None
    published: bool = False
    missing_from_pfm: bool = False
    platform_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    views: int = 0
    likes: int = 0
    saves: int = 0
    comments: int = 0
    flag: str | None = None  # "not_published" | "missing_from_pfm" | "archive_failed" | None


def notify(msg: str) -> None:
    """Send a Telegram message via notify.py. Non-fatal if it fails."""
    try:
        subprocess.run(
            ["python3", str(NOTIFY_SCRIPT), msg],
            check=False,
            timeout=30,
        )
    except Exception as e:
        print(f"[verify] notify failed: {e}", file=sys.stderr)


def run_pfm(*args: str) -> dict | list | None:
    """Call postforme_client.py with args, parse stdout JSON. Returns None on failure."""
    cmd = ["python3", str(POSTFORME_CLIENT), *args]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        print(f"[verify] PFM call failed ({args}): {e}", file=sys.stderr)
        return None
    if res.returncode != 0:
        print(f"[verify] PFM exit {res.returncode} ({args}): {res.stderr.strip()}", file=sys.stderr)
        return None
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        print(f"[verify] PFM non-JSON output ({args}): {res.stdout[:300]}", file=sys.stderr)
        return None


def parse_date(s: str) -> date:
    """Parse YYYY-MM-DD into a date object."""
    return datetime.strptime(s, "%Y-%m-%d").date()


def find_batch_manifest_for_date(target: date) -> tuple[Path, dict] | None:
    """Locate the batch manifest whose slots include the target date. Returns (path, manifest)."""
    if not BATCHES_DIR.exists():
        return None
    candidates: list[tuple[Path, dict]] = []
    for manifest_path in BATCHES_DIR.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception:
            continue
        slots = manifest.get("slots", [])
        for slot in slots:
            if slot.get("day") == target.isoformat():
                candidates.append((manifest_path, manifest))
                break
    if not candidates:
        return None
    # Prefer most-recently modified manifest
    candidates.sort(key=lambda pair: pair[0].stat().st_mtime, reverse=True)
    return candidates[0]


def slots_for_date(manifest: dict, target: date) -> list[dict]:
    """Return manifest slots scheduled for the target date, in slot_index order."""
    slots = [s for s in manifest.get("slots", []) if s.get("day") == target.isoformat()]
    slots.sort(key=lambda s: s.get("slot_index", s.get("slot", 0)))
    return slots


def read_scheduled(post_id: str) -> dict | None:
    """Load picks/<post_id>/scheduled.json (PFM post_id mapping). None if missing."""
    path = PICKS_DIR / post_id / "scheduled.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def extract_pfm_post_ids(scheduled: dict) -> dict[str, str]:
    """From scheduled.json, return {platform: pfm_post_id}. Handles dict + legacy string shapes."""
    # Newer shape: scheduled["post_id_pfm"] == {"instagram": "sp_...", "tiktok_business": "sp_..."}
    pfm = scheduled.get("post_id_pfm") or scheduled.get("pfm_post_ids") or {}
    if isinstance(pfm, dict):
        return {platform: pid for platform, pid in pfm.items() if pid}
    if isinstance(pfm, str):
        # Legacy single-post fallback — caller treats it as cross-platform.
        return {"_all": pfm}
    # Last-resort: top-level post_id field.
    pid = scheduled.get("post_id")
    if isinstance(pid, str):
        return {"_all": pid}
    return {}


def fetch_post_status(pfm_post_id: str) -> dict | None:
    """Pull status + metrics for a PFM post via postforme_client.py."""
    # TODO: confirm postforme_client.py supports `post-status --post-id`
    return run_pfm("post-status", "--post-id", pfm_post_id)  # type: ignore[return-value]


def aggregate_metrics(platform_results: dict[str, dict[str, Any]]) -> tuple[int, int, int, int]:
    """Sum views, likes, saves, comments across platforms."""
    views = likes = saves = comments = 0
    for p in platform_results.values():
        metrics = p.get("metrics") or {}
        views += int(metrics.get("views") or metrics.get("video_views") or metrics.get("media_views") or 0)
        likes += int(metrics.get("likes") or 0)
        saves += int(metrics.get("saves") or 0)
        comments += int(metrics.get("comments") or 0)
    return views, likes, saves, comments


def is_published(status: str | None) -> bool:
    """PFM status values that count as live."""
    if not status:
        return False
    return status.lower() in {"posted", "processed", "published", "completed", "success"}


def archive_slides(post_id: str, target_day: date, slot_index: int) -> Path | None:
    """Move slide PNGs from PRPathShots/posts_v2/<post_id>/ into posted/<day>_slot<N>_<post_id>/.

    Returns the destination directory on success, or None if staged slides could not be
    found. Uses shutil.move (atomic on same filesystem). Creates the posted/ subdir.
    """
    source_dir = PRPATHSHOTS_POSTS_DIR / post_id
    dest_dir = POSTED_DIR / f"{target_day.isoformat()}_slot{slot_index}_{post_id}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    moved_any = False
    for name in SLIDE_FILENAMES:
        src = source_dir / name
        if not src.exists():
            continue
        dest = dest_dir / name
        try:
            shutil.move(str(src), str(dest))
            moved_any = True
        except Exception as e:
            print(f"[verify] move failed for {src} → {dest}: {e}", file=sys.stderr)
    # Also copy scheduled.json (if present) so the archive is self-contained.
    scheduled_src = PICKS_DIR / post_id / "scheduled.json"
    if scheduled_src.exists():
        try:
            shutil.copy2(str(scheduled_src), str(dest_dir / "scheduled.json"))
        except Exception:
            pass
    return dest_dir if moved_any else None


def write_metrics_json(dest_dir: Path, slot_result: SlotResult) -> None:
    """Write a one-shot metrics.json snapshot into the archive dir. NEVER overwrite."""
    out = dest_dir / "metrics.json"
    if out.exists():
        # Snapshot already recorded — do not modify.
        return
    payload = {
        "post_id": slot_result.post_id,
        "day": slot_result.day,
        "slot_index": slot_result.slot_index,
        "feature_anchor": slot_result.feature_anchor,
        "published": slot_result.published,
        "missing_from_pfm": slot_result.missing_from_pfm,
        "views_24h": slot_result.views,
        "likes_24h": slot_result.likes,
        "saves_24h": slot_result.saves,
        "comments_24h": slot_result.comments,
        "per_platform": slot_result.platform_results,
        "recorded_at": datetime.utcnow().isoformat() + "Z",
    }
    out.write_text(json.dumps(payload, indent=2, default=str))


def append_used_post_id(post_id: str) -> None:
    """Append post_id to data/used_post_ids.json atomically, dedupe preserved."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if USED_POSTS_PATH.exists():
        try:
            data = json.loads(USED_POSTS_PATH.read_text())
        except Exception:
            data = {"used_post_ids": []}
    else:
        data = {"used_post_ids": []}
    if "used_post_ids" not in data or not isinstance(data["used_post_ids"], list):
        data["used_post_ids"] = []
    entries = data["used_post_ids"]
    # Entries may be strings or {post_id, date} dicts — support both, dedupe by post_id.
    existing_ids = set()
    for e in entries:
        if isinstance(e, str):
            existing_ids.add(e)
        elif isinstance(e, dict):
            if e.get("post_id"):
                existing_ids.add(e["post_id"])
    if post_id not in existing_ids:
        entries.append({"post_id": post_id, "used_on": datetime.utcnow().date().isoformat()})
    tmp = USED_POSTS_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(USED_POSTS_PATH)


def verify_slot(slot: dict, target: date) -> SlotResult:
    """Verify one manifest slot: fetch PFM status, aggregate metrics, archive slides."""
    post_id = slot.get("post_id") or slot.get("slug") or ""
    slot_index = int(slot.get("slot_index", slot.get("slot", 0)))
    result = SlotResult(
        slot_index=slot_index,
        post_id=post_id,
        day=target.isoformat(),
        feature_anchor=slot.get("feature_anchor") or slot.get("anchor"),
    )

    scheduled = read_scheduled(post_id)
    if not scheduled:
        result.missing_from_pfm = True
        result.flag = "missing_from_pfm"
        return result

    pfm_ids = extract_pfm_post_ids(scheduled)
    if not pfm_ids:
        result.missing_from_pfm = True
        result.flag = "missing_from_pfm"
        return result

    any_published = False
    for platform, pfm_pid in pfm_ids.items():
        status_payload = fetch_post_status(pfm_pid)
        if status_payload is None:
            result.platform_results[platform] = {"error": "pfm_call_failed", "pfm_post_id": pfm_pid}
            continue
        status = status_payload.get("status") or status_payload.get("state")
        metrics = status_payload.get("metrics") or {}
        result.platform_results[platform] = {
            "pfm_post_id": pfm_pid,
            "status": status,
            "metrics": metrics,
        }
        if is_published(status):
            any_published = True

    result.published = any_published
    result.views, result.likes, result.saves, result.comments = aggregate_metrics(result.platform_results)

    if not result.published:
        result.flag = "not_published"

    dest_dir = archive_slides(post_id, target, slot_index)
    if dest_dir is None:
        # Still write metrics.json to posted/ stub so we keep the snapshot.
        stub = POSTED_DIR / f"{target.isoformat()}_slot{slot_index}_{post_id}"
        stub.mkdir(parents=True, exist_ok=True)
        write_metrics_json(stub, result)
        if result.flag is None:
            result.flag = "archive_failed"
    else:
        write_metrics_json(dest_dir, result)

    append_used_post_id(post_id)
    return result


def summarise(results: list[SlotResult], target: date) -> str:
    """Build the Telegram summary string from slot results."""
    total = len(results)
    failed = [r for r in results if r.flag in ("not_published", "missing_from_pfm")]
    if failed:
        ids = ", ".join(r.post_id for r in failed)
        return (
            f"PRPATH VERIFY \U0001F6A8 {target.isoformat()} — "
            f"{len(failed)}/{total} failed publish: {ids}"
        )
    if total == 0:
        return f"PRPATH VERIFY \u2705 {target.isoformat()} — no slots for {target.isoformat()}"
    top = max(results, key=lambda r: r.saves)
    return (
        f"PRPATH VERIFY \u2705 {target.isoformat()} — "
        f"{total}/{total} published, top: {top.post_id} {top.saves} saves"
    )


def main() -> int:
    """Entry point. Returns 0 on clean verify (including no-slots), 1 on hard errors."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="Target date YYYY-MM-DD (default: yesterday)")
    args = ap.parse_args()

    target = parse_date(args.date) if args.date else (date.today() - timedelta(days=1))

    POSTED_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    found = find_batch_manifest_for_date(target)
    if found is None:
        msg = f"PRPATH VERIFY \u2705 {target.isoformat()} — no slots for {target.isoformat()}"
        print(msg)
        notify(msg)
        return 0

    manifest_path, manifest = found
    slots = slots_for_date(manifest, target)
    if not slots:
        msg = f"PRPATH VERIFY \u2705 {target.isoformat()} — no slots for {target.isoformat()}"
        print(msg)
        notify(msg)
        return 0

    print(f"[verify] batch: {manifest_path}")
    print(f"[verify] verifying {len(slots)} slots for {target.isoformat()}")
    results = [verify_slot(slot, target) for slot in slots]

    summary = summarise(results, target)
    print(summary)
    notify(summary)
    for r in results:
        print(
            f"  slot{r.slot_index} {r.post_id} anchor={r.feature_anchor} "
            f"published={r.published} views={r.views} saves={r.saves} flag={r.flag}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
