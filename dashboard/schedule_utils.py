"""Schedule helpers — cron parsing, human-readable summaries, next-fire computation,
and launchd plist generation.

Supports a constrained cron subset sufficient for PRPath's fixed 9-script roster:
    minute  hour  day-of-month  month  day-of-week
where each field is either:
    *             (wildcard)
    integer       (e.g. 10)
    range A-B     (e.g. 1-6)
    list A,B,C    (e.g. 1,3,5)

Day-of-week: 0 = Sun, 1 = Mon, ..., 6 = Sat. Matches standard cron.
"""
from __future__ import annotations

import plistlib
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DAYS_FULL = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
WEEKDAYS_MON_SAT = {1, 2, 3, 4, 5, 6}


def _parse_field(spec: str, min_val: int, max_val: int) -> set[int]:
    """Parse a single cron field into a set of matching ints."""
    if spec == "*":
        return set(range(min_val, max_val + 1))
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def parse_cron(expr: str) -> dict[str, set[int]]:
    """Parse a 5-field cron expression into a dict of sets."""
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError(f"expected 5 fields, got {len(parts)}: {expr!r}")
    minute, hour, dom, month, dow = parts
    return {
        "minute":  _parse_field(minute, 0, 59),
        "hour":    _parse_field(hour, 0, 23),
        "dom":     _parse_field(dom, 1, 31),
        "month":   _parse_field(month, 1, 12),
        "weekday": _parse_field(dow, 0, 6),
    }


def _local_tz_abbrev() -> str:
    """Short abbreviation for the Mac's local timezone (e.g. 'CT', 'PT')."""
    name = datetime.now().astimezone().tzname() or ""
    # Normalize common Central variants to 'CT' so CDT/CST both look the same
    if name in ("CDT", "CST"):
        return "CT"
    if name in ("PDT", "PST"):
        return "PT"
    if name in ("EDT", "EST"):
        return "ET"
    if name in ("MDT", "MST"):
        return "MT"
    return name or ""


def cron_human(expr: str) -> str:
    """Short human-readable summary of a cron expression.

    Focused on the patterns we actually use. Falls back to the raw expression
    for anything unexpected. Appends local timezone abbreviation (e.g. 'CT').
    """
    try:
        p = parse_cron(expr)
    except Exception:
        return expr

    hour = next(iter(sorted(p["hour"]))) if p["hour"] else 0
    minute = next(iter(sorted(p["minute"]))) if p["minute"] else 0
    ampm = "am" if hour < 12 else "pm"
    hour12 = hour % 12 or 12
    tz = _local_tz_abbrev()
    tz_suffix = f" {tz}" if tz else ""
    time_str = f"{hour12}:{minute:02d}{ampm}{tz_suffix}" if minute else f"{hour12}{ampm}{tz_suffix}"

    wd = p["weekday"]
    dom = p["dom"]

    if wd == {0}:
        day_str = "Sun"
    elif wd == {3}:
        day_str = "Wed"
    elif wd == {1}:
        day_str = "Mon"
    elif wd == WEEKDAYS_MON_SAT:
        day_str = "Mon-Sat"
    elif wd == {0, 6}:
        day_str = "Sat/Sun"
    elif wd == set(range(0, 7)):
        day_str = "daily"
    else:
        day_str = "/".join(DAYS_FULL[d] for d in sorted(wd))

    # 1st Sunday of the month = weekday==0 AND dom in 1..7
    if wd == {0} and dom == set(range(1, 8)):
        return f"1st Sun {time_str}"

    return f"{day_str} {time_str}"


def next_fire(expr: str, now: datetime | None = None, max_lookahead_days: int = 14) -> datetime | None:
    """Compute the next datetime a cron expression would fire. Naive local tz."""
    try:
        p = parse_cron(expr)
    except Exception:
        return None
    now = now or datetime.now().replace(second=0, microsecond=0)

    # Start from now + 1 min to avoid matching "now" if still within the fire minute
    probe = now + timedelta(minutes=1)
    for _ in range(max_lookahead_days * 24 * 60):
        wd = probe.weekday()  # Mon=0..Sun=6
        cron_wd = (wd + 1) % 7  # cron: Sun=0..Sat=6
        if (
            probe.minute in p["minute"]
            and probe.hour in p["hour"]
            and probe.day in p["dom"]
            and probe.month in p["month"]
            and cron_wd in p["weekday"]
        ):
            return probe
        probe += timedelta(minutes=1)
    return None


def human_delta(when: datetime, from_: datetime | None = None) -> str:
    """Short relative time — 'in 2h 14m', 'in 3d 4h'."""
    from_ = from_ or datetime.now()
    delta = when - from_
    if delta.total_seconds() < 0:
        return "overdue"
    mins = int(delta.total_seconds() / 60)
    if mins < 60:
        return f"in {mins}m"
    hours = mins // 60
    remaining_mins = mins % 60
    if hours < 24:
        return f"in {hours}h {remaining_mins}m" if remaining_mins else f"in {hours}h"
    days = hours // 24
    remaining_hours = hours % 24
    return f"in {days}d {remaining_hours}h" if remaining_hours else f"in {days}d"


# ---------------------------------------------------------------------------
# launchd plist generation
# ---------------------------------------------------------------------------
GENESIS_ROOT = Path(__file__).resolve().parent.parent
LAUNCH_AGENTS_DIR = Path.home() / "Library" / "LaunchAgents"
LABEL_PREFIX = "app.prpath.pipeline"


def cron_to_calendar_intervals(expr: str) -> list[dict[str, int]]:
    """Convert a cron expression into a list of launchd StartCalendarInterval dicts.

    launchd doesn't support cron directly — each combination of (weekday, hour, minute)
    needs its own dict entry. We expand weekday ranges explicitly.
    """
    p = parse_cron(expr)
    intervals: list[dict[str, int]] = []
    for weekday in sorted(p["weekday"]):
        for hour in sorted(p["hour"]):
            for minute in sorted(p["minute"]):
                entry: dict[str, int] = {
                    "Hour":   hour,
                    "Minute": minute,
                    "Weekday": weekday,  # cron: 0=Sun..6=Sat; launchd: 0=Sun (same)
                }
                # Gate to 1st-of-month only (for monthly_retro) when dom range is 1-7
                if p["dom"] != set(range(1, 32)):
                    # launchd can't express "weekday AND day-of-month range"
                    # — we rely on the script itself to date-gate (monthly_retro does).
                    pass
                intervals.append(entry)
    return intervals


def generate_plist(script_key: str, command_argv: list[str], cron_expr: str) -> bytes:
    """Generate a launchd plist for a single pipeline script."""
    intervals = cron_to_calendar_intervals(cron_expr)
    label = f"{LABEL_PREFIX}.{script_key}"
    plist: dict[str, Any] = {
        "Label": label,
        "ProgramArguments": command_argv,
        "WorkingDirectory": str(GENESIS_ROOT),
        "EnvironmentVariables": {
            "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "PYTHONPATH": str(GENESIS_ROOT),
        },
        "RunAtLoad": False,
        "StandardOutPath":    str(GENESIS_ROOT / "dashboard" / f"{script_key}.log"),
        "StandardErrorPath":  str(GENESIS_ROOT / "dashboard" / f"{script_key}.error.log"),
        "StartCalendarInterval": intervals if len(intervals) > 1 else (intervals[0] if intervals else {}),
    }
    return plistlib.dumps(plist)


def install_plist(script_key: str, command_argv: list[str], cron_expr: str) -> tuple[bool, str]:
    """Write plist to ~/Library/LaunchAgents/ and load it. Returns (ok, message)."""
    try:
        LAUNCH_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        path = LAUNCH_AGENTS_DIR / f"{LABEL_PREFIX}.{script_key}.plist"
        path.write_bytes(generate_plist(script_key, command_argv, cron_expr))

        # Unload first (idempotent) then load
        label = f"{LABEL_PREFIX}.{script_key}"
        subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{label}"], capture_output=True)
        result = subprocess.run(
            ["launchctl", "bootstrap", f"gui/{_uid()}", str(path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return False, f"launchctl bootstrap: {result.stderr.strip() or result.stdout.strip()}"
        return True, f"installed at {path}"
    except Exception as exc:
        return False, str(exc)


def uninstall_plist(script_key: str) -> tuple[bool, str]:
    try:
        label = f"{LABEL_PREFIX}.{script_key}"
        path = LAUNCH_AGENTS_DIR / f"{label}.plist"
        subprocess.run(["launchctl", "bootout", f"gui/{_uid()}/{label}"], capture_output=True)
        if path.exists():
            path.unlink()
        return True, f"uninstalled {label}"
    except Exception as exc:
        return False, str(exc)


def _uid() -> int:
    import os
    return os.getuid()
