#!/usr/bin/env python3
"""PRPath evening metrics pulse — shadow-ban / slow-start detection 6-12h after posting.

CLI:
    python3 metrics_pulse.py [--date YYYY-MM-DD]

Default date is today (America/Chicago). Finds today's batch manifest, pulls
fresh per-platform metrics for each slot via Post for Me, and classifies each
platform × slot as HEALTHY / SLOW / DEAD. Flags account-wide issues when 2+
slots show zero on the same platform. Sends a single Telegram summary — this
is an end-of-day glance, not an Obsidian-archived report.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

try:
    from dateutil import parser as dateutil_parser  # type: ignore
except Exception:  # pragma: no cover
    dateutil_parser = None  # type: ignore

SCRIPT_DIR = Path(__file__).resolve().parent
PICKS_DIR = SCRIPT_DIR / "picks"
BATCHES_DIR = PICKS_DIR / "_batches"
NOTIFY_SCRIPT = SCRIPT_DIR / "notify.py"
POSTFORME_CLIENT = SCRIPT_DIR / "postforme_client.py"

# Per-platform thresholds (hours-live → expected minimum views).
SHADOW_HOURS = 6.0         # after this many hours, 0 views on a platform = flag
SLOW_HOURS = 3.0           # after this many hours, <50 views = slow start
SLOW_VIEWS = 50
HEALTHY_VIEWS = 100

PLATFORM_LABELS = {
    "tiktok_business": "TT",
    "tiktok": "TT",
    "instagram": "IG",
    "youtube": "YT",
    "facebook": "FB",
}


@dataclass
class SlotPulse:
    """Per-slot pulse snapshot: platform views + flags."""

    slot_index: int
    post_id: str
    feature_anchor: str | None
    scheduled_at: datetime | None
    hours_live: float
    per_platform_views: dict[str, int] = field(default_factory=dict)
    platform_flags: dict[str, str] = field(default_factory=dict)  # platform → OK|SLOW|DEAD

    @property
    def total_views(self) -> int:
        return sum(self.per_platform_views.values())


def notify(msg: str) -> None:
    """Telegram via notify.py. Non-fatal if it fails."""
    try:
        subprocess.run(["python3", str(NOTIFY_SCRIPT), msg], check=False, timeout=30)
    except Exception as e:
        print(f"[metrics_pulse] notify failed: {e}", file=sys.stderr)


def run_pfm(*args: str) -> dict | list | None:
    """Call postforme_client.py + parse JSON stdout."""
    try:
        res = subprocess.run(
            ["python3", str(POSTFORME_CLIENT), *args],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception as e:
        print(f"[metrics_pulse] PFM call failed ({args}): {e}", file=sys.stderr)
        return None
    if res.returncode != 0:
        print(f"[metrics_pulse] PFM exit {res.returncode} ({args}): {res.stderr.strip()}", file=sys.stderr)
        return None
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return None


def parse_date(s: str) -> date:
    """Parse YYYY-MM-DD."""
    return datetime.strptime(s, "%Y-%m-%d").date()


def parse_iso(s: str | None) -> datetime | None:
    """Parse an ISO timestamp into a UTC-aware datetime. None-safe."""
    if not s:
        return None
    try:
        if dateutil_parser is not None:
            dt = dateutil_parser.isoparse(s)
        else:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def find_batch_manifest_for_date(target: date) -> tuple[Path, dict] | None:
    """Locate batch manifest containing a slot for target date."""
    if not BATCHES_DIR.exists():
        return None
    candidates: list[tuple[Path, dict]] = []
    for p in BATCHES_DIR.glob("*/manifest.json"):
        try:
            manifest = json.loads(p.read_text())
        except Exception:
            continue
        for slot in manifest.get("slots", []):
            if slot.get("day") == target.isoformat():
                candidates.append((p, manifest))
                break
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0].stat().st_mtime, reverse=True)
    return candidates[0]


def slots_for_date(manifest: dict, target: date) -> list[dict]:
    """Return manifest slots scheduled for target date, ordered."""
    slots = [s for s in manifest.get("slots", []) if s.get("day") == target.isoformat()]
    slots.sort(key=lambda s: s.get("slot_index", s.get("slot", 0)))
    return slots


def read_scheduled(post_id: str) -> dict | None:
    """Load picks/<post_id>/scheduled.json."""
    p = PICKS_DIR / post_id / "scheduled.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def extract_pfm_post_ids(scheduled: dict) -> dict[str, str]:
    """Platform → PFM post_id mapping."""
    pfm = scheduled.get("post_id_pfm") or scheduled.get("pfm_post_ids") or {}
    if isinstance(pfm, dict):
        return {platform: pid for platform, pid in pfm.items() if pid}
    if isinstance(pfm, str):
        return {"_all": pfm}
    pid = scheduled.get("post_id")
    if isinstance(pid, str):
        return {"_all": pid}
    return {}


def fetch_metrics(pfm_post_id: str) -> dict | None:
    """Fetch fresh view/engagement counts for one PFM post."""
    # TODO: confirm postforme_client.py supports `post-metrics --post-id`
    return run_pfm("post-metrics", "--post-id", pfm_post_id)  # type: ignore[return-value]


def extract_views(platform: str, metrics: dict) -> int:
    """Pick the canonical view field per platform."""
    if not metrics:
        return 0
    plat = (platform or "").lower()
    if plat.startswith("facebook"):
        return int(metrics.get("media_views") or metrics.get("video_views") or metrics.get("views") or 0)
    if plat.startswith("tiktok"):
        return int(metrics.get("video_views") or metrics.get("views") or 0)
    # instagram + youtube + fallback
    return int(metrics.get("views") or metrics.get("video_views") or 0)


def classify_platform(views: int, hours_live: float) -> str:
    """Tag a single platform reading as OK | SLOW | DEAD."""
    if hours_live >= SHADOW_HOURS and views == 0:
        return "DEAD"
    if hours_live >= SLOW_HOURS and views < SLOW_VIEWS:
        return "SLOW"
    if views >= HEALTHY_VIEWS:
        return "OK"
    return "OK"


def pulse_slot(slot: dict) -> SlotPulse:
    """Pull fresh metrics for one slot and classify each platform."""
    post_id = slot.get("post_id") or slot.get("slug") or ""
    slot_index = int(slot.get("slot_index", slot.get("slot", 0)))
    feature_anchor = slot.get("feature_anchor") or slot.get("anchor")
    scheduled = read_scheduled(post_id) or {}
    scheduled_at = parse_iso(scheduled.get("scheduled_at") or slot.get("scheduled_at"))
    now = datetime.now(timezone.utc)
    hours_live = ((now - scheduled_at).total_seconds() / 3600.0) if scheduled_at else 0.0

    pulse = SlotPulse(
        slot_index=slot_index,
        post_id=post_id,
        feature_anchor=feature_anchor,
        scheduled_at=scheduled_at,
        hours_live=hours_live,
    )

    for platform, pfm_pid in extract_pfm_post_ids(scheduled).items():
        payload = fetch_metrics(pfm_pid) or {}
        metrics = payload.get("metrics") or payload or {}
        views = extract_views(platform, metrics)
        pulse.per_platform_views[platform] = views
        pulse.platform_flags[platform] = classify_platform(views, hours_live)

    return pulse


def account_wide_flags(pulses: list[SlotPulse]) -> list[str]:
    """Return list of 'FB slow start across all 3 slots'-style flags."""
    flags: list[str] = []
    platforms: set[str] = set()
    for pulse in pulses:
        platforms.update(pulse.per_platform_views.keys())
    for platform in sorted(platforms):
        zero_count = sum(1 for p in pulses if p.per_platform_views.get(platform, 0) == 0
                         and p.hours_live >= SHADOW_HOURS)
        slow_count = sum(
            1 for p in pulses
            if 0 < p.per_platform_views.get(platform, 0) < SLOW_VIEWS and p.hours_live >= SLOW_HOURS
        )
        label = PLATFORM_LABELS.get(platform, platform)
        if zero_count >= 2:
            flags.append(f"{label} possible account-wide issue ({zero_count} slots at 0)")
        elif slow_count >= len(pulses) and len(pulses) > 0:
            flags.append(f"{label} slow start across all {len(pulses)} slots")
    return flags


def format_pulse_summary(pulses: list[SlotPulse], target: date, wide_flags: list[str]) -> str:
    """Compose the Telegram summary string."""
    # Per-platform totals.
    totals: dict[str, int] = {}
    for pulse in pulses:
        for platform, views in pulse.per_platform_views.items():
            totals[platform] = totals.get(platform, 0) + views

    def render_n(n: int) -> str:
        if n >= 1000:
            return f"{n / 1000:.1f}K".rstrip("0").rstrip(".")
        return str(n)

    # Pick severity indicator.
    has_dead = any("DEAD" in p.platform_flags.values() for p in pulses)
    has_slow = any("SLOW" in p.platform_flags.values() for p in pulses)
    emoji = "\U0001F534" if has_dead or any("account-wide" in f for f in wide_flags) else (
        "\U0001F7E1" if has_slow or wide_flags else "\u2705"
    )

    # Build platform summary line ordered TT/IG/YT/FB.
    order = ["tiktok_business", "tiktok", "instagram", "youtube", "facebook"]
    seen: set[str] = set()
    parts = []
    for platform in order:
        if platform in totals and platform not in seen:
            label = PLATFORM_LABELS.get(platform, platform)
            parts.append(f"{label}: {render_n(totals[platform])}")
            seen.add(platform)
    for platform in totals:
        if platform not in seen:
            parts.append(f"{PLATFORM_LABELS.get(platform, platform)}: {render_n(totals[platform])}")

    platform_line = " | ".join(parts) if parts else "no data"

    # Top platform + top slot.
    top_platform: str | None = None
    top_views = 0
    for platform, total in totals.items():
        if total > top_views:
            top_platform = platform
            top_views = total
    top_slot = max(pulses, key=lambda p: p.total_views, default=None)

    lines = [
        f"PRPATH PULSE {emoji} {target.isoformat()}",
        platform_line,
    ]
    if top_slot and top_platform and top_slot.total_views > 0:
        lines.append(
            f"Top: {top_slot.post_id} {render_n(top_slot.total_views)} "
            f"({PLATFORM_LABELS.get(top_platform, top_platform)})"
        )
    if wide_flags:
        lines.append("Flag: " + "; ".join(wide_flags))
    # Per-slot detail tail.
    for pulse in pulses:
        tags = []
        for platform, flag in pulse.platform_flags.items():
            if flag != "OK":
                tags.append(f"{PLATFORM_LABELS.get(platform, platform)} {flag}")
        anchor = f"[{pulse.feature_anchor}]" if pulse.feature_anchor else ""
        tag_str = f" — {', '.join(tags)}" if tags else ""
        lines.append(
            f"s{pulse.slot_index}{anchor} {pulse.post_id}: "
            f"{render_n(pulse.total_views)} ({pulse.hours_live:.1f}h){tag_str}"
        )
    return "\n".join(lines)


def main() -> int:
    """Entry point — returns 0 on success."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="Target date YYYY-MM-DD (default: today)")
    args = ap.parse_args()

    target = parse_date(args.date) if args.date else date.today()

    found = find_batch_manifest_for_date(target)
    if found is None:
        msg = f"PRPATH PULSE \u2705 {target.isoformat()} — no batch active for {target.isoformat()}"
        print(msg)
        notify(msg)
        return 0

    manifest_path, manifest = found
    slots = slots_for_date(manifest, target)
    if not slots:
        msg = f"PRPATH PULSE \u2705 {target.isoformat()} — no slots for {target.isoformat()}"
        print(msg)
        notify(msg)
        return 0

    print(f"[metrics_pulse] batch: {manifest_path}")
    pulses = [pulse_slot(s) for s in slots]
    wide_flags = account_wide_flags(pulses)
    summary = format_pulse_summary(pulses, target, wide_flags)
    print(summary)
    notify(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
