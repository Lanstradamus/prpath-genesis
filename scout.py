#!/usr/bin/env python3
"""PRPath daily 06:00 batch readiness scout.

CLI:
    python3 scout.py [--target sun|wed]

NOT a trending scout — PRPath posts are feature-driven from a fixed 18-post
inventory. This script confirms the next Sun/Wed batch can actually render:
  1. Slide PNGs present for each candidate post_id in PRPathShots/posts_v2/
  2. HTML templates present in templates/html/week2/
  3. Feature-anchor coverage: each of A-F has ≥1 available post, G has ≥1
  4. Posts that need re-render (#03/#08/#11/#12 per Week-2 v2.1) are flagged

Telegram severity mapping:
  - all green ✅
  - anchor coverage gap 🟡
  - missing slide PNGs 🔴
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
NOTIFY_SCRIPT = SCRIPT_DIR / "notify.py"
DATA_DIR = SCRIPT_DIR / "data"
USED_POSTS_PATH = DATA_DIR / "used_post_ids.json"
PRPATHSHOTS_POSTS_DIR = Path("/Users/lancesessions/Developer/PRPathShots/samples/posts_v2")
HTML_TEMPLATES_DIR = SCRIPT_DIR / "templates" / "html" / "week2"

REQUIRED_TEMPLATES = ("hook_photo_slide.html", "listicle_slide.html", "atlas_imessage.html")
REQUIRED_SLIDES = ("slide_01_issue.png", "slide_02_solution.png")

# Week-2 v2.1 inventory: post_id prefix → feature anchor. Post slugs in
# PRPathShots/posts_v2/ start with these 2-digit prefixes.
INVENTORY: dict[str, str] = {
    "01": "A",
    "02": "B",
    "03": "B",  # female-voiced
    "04": "B",
    "05": "B",
    "06": "C",
    "07": "C",
    "08": "C",
    "09": "D",
    "10": "D",
    "11": "E",  # female-voiced
    "12": "B",
    "13": "E",
    "14": "F",
    "15": "F",
    "16": "F",
    "17": "G",
    "18": "G",
}
POSTS_NEEDING_RERENDER: set[str] = {"03", "08", "11", "12"}
USED_LOOKBACK_DAYS = 30


@dataclass
class ScoutReport:
    """Aggregated findings from the readiness check."""

    target_batch: str  # "sun" or "wed"
    target_days: list[str]
    available_by_anchor: dict[str, list[str]] = field(default_factory=dict)
    missing_slides: list[str] = field(default_factory=list)  # post_ids missing slide PNGs
    missing_templates: list[str] = field(default_factory=list)  # template filenames missing
    stale_renders: list[str] = field(default_factory=list)  # posts needing re-render
    anchor_gaps: list[str] = field(default_factory=list)  # anchors below floor
    total_candidates: int = 0


def notify(msg: str) -> None:
    """Telegram via notify.py."""
    try:
        subprocess.run(["python3", str(NOTIFY_SCRIPT), msg], check=False, timeout=30)
    except Exception as e:
        print(f"[scout] notify failed: {e}", file=sys.stderr)


def load_used_post_ids() -> set[str]:
    """Return post_ids used within the last USED_LOOKBACK_DAYS."""
    if not USED_POSTS_PATH.exists():
        return set()
    try:
        data = json.loads(USED_POSTS_PATH.read_text())
    except Exception:
        return set()
    raw = data.get("used_post_ids", [])
    cutoff = date.today() - timedelta(days=USED_LOOKBACK_DAYS)
    used: set[str] = set()
    for entry in raw:
        if isinstance(entry, str):
            used.add(entry)
            continue
        if not isinstance(entry, dict):
            continue
        post_id = entry.get("post_id")
        if not post_id:
            continue
        used_on = entry.get("used_on")
        if not used_on:
            used.add(post_id)
            continue
        try:
            used_date = datetime.strptime(used_on, "%Y-%m-%d").date()
        except Exception:
            used.add(post_id)
            continue
        if used_date >= cutoff:
            used.add(post_id)
    return used


def list_staged_posts() -> list[str]:
    """Enumerate directories under PRPathShots/samples/posts_v2/."""
    if not PRPATHSHOTS_POSTS_DIR.exists():
        return []
    return sorted(
        d.name for d in PRPATHSHOTS_POSTS_DIR.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def post_prefix(post_id: str) -> str:
    """Return the 2-digit numeric prefix or ''."""
    if len(post_id) >= 2 and post_id[:2].isdigit():
        return post_id[:2]
    return ""


def determine_target(arg: str | None) -> tuple[str, list[date]]:
    """Pick which upcoming batch we're gating. Returns ('sun'|'wed', [days])."""
    today = date.today()
    if arg in ("sun", "wed"):
        target = arg
    else:
        # Default = whichever batch fires next.
        # Sun batch covers Mon/Tue/Wed (fires Sun 10am). Wed batch covers Thu/Fri/Sat (fires Wed 10am).
        # Monday=0 ... Sunday=6
        dow = today.weekday()
        if dow in (5, 6, 0):   # Sat/Sun/Mon → next batch is Sun's (Mon/Tue/Wed)
            target = "sun"
        elif dow in (1,):      # Tuesday → still Sun batch
            target = "sun"
        else:                  # Wed/Thu/Fri → next is Wed batch (Thu/Fri/Sat)
            target = "wed"

    if target == "sun":
        # Next Mon/Tue/Wed.
        days_ahead = (0 - today.weekday()) % 7
        days_ahead = days_ahead if days_ahead > 0 else 1
        monday = today + timedelta(days=days_ahead)
        target_days = [monday, monday + timedelta(days=1), monday + timedelta(days=2)]
    else:
        # Next Thu/Fri/Sat.
        days_ahead = (3 - today.weekday()) % 7
        days_ahead = days_ahead if days_ahead > 0 else 1
        thursday = today + timedelta(days=days_ahead)
        target_days = [thursday, thursday + timedelta(days=1), thursday + timedelta(days=2)]
    return target, target_days


def check_slides(post_id: str) -> list[str]:
    """Return list of missing slide filenames for a post_id."""
    src = PRPATHSHOTS_POSTS_DIR / post_id
    missing = []
    for name in REQUIRED_SLIDES:
        if not (src / name).exists():
            missing.append(name)
    return missing


def check_templates() -> list[str]:
    """Return list of missing required HTML templates."""
    missing = []
    for name in REQUIRED_TEMPLATES:
        if not (HTML_TEMPLATES_DIR / name).exists():
            missing.append(name)
    return missing


def check_rerender_staleness(staged: list[str]) -> list[str]:
    """Return the subset of POSTS_NEEDING_RERENDER that still have v1 renders.

    Heuristic: if the slide PNG mtime is older than 7 days, assume v1 render
    still in place. (Cowork prompt asks for explicit flagging; scripts that
    replace these will touch the mtime on re-render.)
    """
    flagged: list[str] = []
    cutoff_ts = (datetime.now() - timedelta(days=7)).timestamp()
    for post_id in staged:
        prefix = post_prefix(post_id)
        if prefix not in POSTS_NEEDING_RERENDER:
            continue
        src = PRPATHSHOTS_POSTS_DIR / post_id / REQUIRED_SLIDES[0]
        if not src.exists():
            continue
        if src.stat().st_mtime < cutoff_ts:
            flagged.append(post_id)
    return flagged


def build_report(target: str, target_days: list[date]) -> ScoutReport:
    """Core readiness logic — returns a ScoutReport."""
    used = load_used_post_ids()
    staged = list_staged_posts()

    report = ScoutReport(
        target_batch=target,
        target_days=[d.isoformat() for d in target_days],
    )

    available_by_anchor: dict[str, list[str]] = defaultdict(list)
    for post_id in staged:
        if post_id in used:
            continue
        prefix = post_prefix(post_id)
        anchor = INVENTORY.get(prefix)
        if not anchor:
            continue  # unknown post — skip anchor bucketing
        missing = check_slides(post_id)
        if missing:
            report.missing_slides.append(f"{post_id} (missing {','.join(missing)})")
            continue
        available_by_anchor[anchor].append(post_id)

    report.available_by_anchor = dict(available_by_anchor)
    report.total_candidates = sum(len(v) for v in available_by_anchor.values())
    report.missing_templates = check_templates()
    report.stale_renders = check_rerender_staleness(staged)

    # Anchor coverage floors: A-F need ≥1, G needs ≥1.
    for anchor in "ABCDEFG":
        if len(available_by_anchor.get(anchor, [])) < 1:
            report.anchor_gaps.append(anchor)

    return report


def format_telegram(report: ScoutReport) -> tuple[str, str]:
    """Return (emoji, message) for Telegram."""
    today = date.today().isoformat()
    anchor_summary = " ".join(
        f"{a}:{len(report.available_by_anchor.get(a, []))}"
        for a in "ABCDEFG"
    )
    target_label = "Mon-Wed" if report.target_batch == "sun" else "Thu-Sat"

    # Priority: missing slides = red, anchor gap = yellow, templates missing = red.
    if report.missing_slides:
        ids = ", ".join(s.split()[0] for s in report.missing_slides[:5])
        msg = (
            f"PRPATH SCOUT \U0001F534 {today} — "
            f"{len(report.missing_slides)} slide PNGs missing: {ids}"
        )
        if len(report.missing_slides) > 5:
            msg += f" (+{len(report.missing_slides) - 5} more)"
        return "red", msg

    if report.missing_templates:
        msg = (
            f"PRPATH SCOUT \U0001F534 {today} — templates missing: "
            + ", ".join(report.missing_templates)
        )
        return "red", msg

    if report.anchor_gaps:
        gap = report.anchor_gaps[0]
        available = len(report.available_by_anchor.get(gap, []))
        msg = (
            f"PRPATH SCOUT \U0001F7E1 {today} — anchor {gap} low "
            f"(only {available} posts available), reseed. "
            f"Target: {target_label}. Coverage: {anchor_summary}"
        )
        return "yellow", msg

    stale_note = ""
    if report.stale_renders:
        stale_note = f" (stale: {', '.join(report.stale_renders[:4])})"
    msg = (
        f"PRPATH SCOUT \u2705 {today} — batch ready for {target_label}, "
        f"anchor coverage A-F \u2713. {anchor_summary}"
        f"{stale_note}"
    )
    return "green", msg


def main() -> int:
    """Entry point — returns 0 always (Telegram conveys severity)."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--target", choices=["sun", "wed"], help="Which upcoming batch to check")
    args = ap.parse_args()

    target, target_days = determine_target(args.target)
    print(f"[scout] target batch: {target} → days {[d.isoformat() for d in target_days]}")

    report = build_report(target, target_days)
    print(f"[scout] candidates by anchor: "
          f"{ {a: len(v) for a, v in report.available_by_anchor.items()} }")
    if report.anchor_gaps:
        print(f"[scout] anchor gaps: {report.anchor_gaps}")
    if report.missing_slides:
        print(f"[scout] missing slides: {report.missing_slides[:10]}")
    if report.missing_templates:
        print(f"[scout] missing templates: {report.missing_templates}")
    if report.stale_renders:
        print(f"[scout] stale renders needing re-render: {report.stale_renders}")

    _, telegram_msg = format_telegram(report)
    print(telegram_msg)
    notify(telegram_msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
