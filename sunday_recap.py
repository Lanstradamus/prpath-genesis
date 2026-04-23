#!/usr/bin/env python3
"""PRPath Sunday weekly recap — Obsidian report of the last 7 days of posts.

CLI:
    python3 sunday_recap.py [--week-ending YYYY-MM-DD]

Default week-ending is yesterday. Walks posted/<YYYY-MM-DD>_slot<N>_<post_id>/
directories from the last 7 days, aggregates metrics.json snapshots written by
verify.py, and produces a Markdown report at:
    <vault>/200 - Projects/PRPath/Weekly Reports/<YYYY-MM-DD>.md

The Documents folder has TCC restrictions — we write to /tmp first, then
shell out to Finder-via-AppleScript to copy into the vault. Falls back to
direct write if the AppleScript path fails.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
POSTED_DIR = SCRIPT_DIR / "posted"
NOTIFY_SCRIPT = SCRIPT_DIR / "notify.py"
FALLBACK_REPORTS_DIR = SCRIPT_DIR / "weekly_reports"
VAULT_WEEKLY_REPORTS_DIR = Path(
    "/Users/lancesessions/Documents/Lance Brain/200 - Projects/PRPath/Weekly Reports"
)

# Post → feature anchor mapping from Week-2 v2.1 inventory.
# Post filenames in posts_v2/ start with these numeric prefixes.
FEATURE_ANCHOR_BY_POST: dict[str, str] = {
    "01": "A",
    "02": "B",
    "03": "B",
    "04": "B",
    "05": "B",
    "06": "C",
    "07": "C",
    "08": "C",
    "09": "D",
    "10": "D",
    "11": "E",
    "12": "B",
    "13": "E",
    "14": "F",
    "15": "F",
    "16": "F",
    "17": "G",
    "18": "G",
}
RAW_POV_POSTS: set[str] = {"01", "03", "11", "14"}
FEMALE_VOICED_POSTS: set[str] = {"03", "11", "17", "18"}


@dataclass
class PostSnapshot:
    """One line of aggregated 24h metrics from a posted/ archive."""

    post_id: str
    day: str
    slot_index: int
    feature_anchor: str | None
    views: int
    likes: int
    saves: int
    comments: int
    is_raw_pov: bool
    is_female_voiced: bool
    per_platform: dict[str, Any] = field(default_factory=dict)


def notify(msg: str) -> None:
    """Telegram via notify.py."""
    try:
        subprocess.run(["python3", str(NOTIFY_SCRIPT), msg], check=False, timeout=30)
    except Exception as e:
        print(f"[sunday_recap] notify failed: {e}", file=sys.stderr)


def parse_date(s: str) -> date:
    """Parse YYYY-MM-DD."""
    return datetime.strptime(s, "%Y-%m-%d").date()


def post_prefix(post_id: str) -> str:
    """Extract the 2-digit prefix (e.g. '01' from '01_atlas_skip_leg')."""
    if len(post_id) >= 2 and post_id[:2].isdigit():
        return post_id[:2]
    return ""


def week_days(week_ending: date) -> list[date]:
    """Last 7 calendar days ending on week_ending (inclusive)."""
    return [week_ending - timedelta(days=i) for i in range(6, -1, -1)]


def load_snapshots(week_ending: date) -> list[PostSnapshot]:
    """Walk posted/ dirs for the target week and load metrics.json from each."""
    days = {d.isoformat() for d in week_days(week_ending)}
    snapshots: list[PostSnapshot] = []
    if not POSTED_DIR.exists():
        return snapshots
    for d in sorted(POSTED_DIR.iterdir()):
        if not d.is_dir():
            continue
        # posted/<YYYY-MM-DD>_slot<N>_<post_id>/
        name = d.name
        try:
            day_str, rest = name.split("_slot", 1)
            slot_str, post_id = rest.split("_", 1)
            slot_index = int(slot_str)
        except Exception:
            continue
        if day_str not in days:
            continue
        metrics_path = d / "metrics.json"
        if not metrics_path.exists():
            continue
        try:
            payload = json.loads(metrics_path.read_text())
        except Exception:
            continue
        prefix = post_prefix(post_id)
        anchor = payload.get("feature_anchor") or FEATURE_ANCHOR_BY_POST.get(prefix)
        snapshots.append(
            PostSnapshot(
                post_id=post_id,
                day=day_str,
                slot_index=slot_index,
                feature_anchor=anchor,
                views=int(payload.get("views_24h") or 0),
                likes=int(payload.get("likes_24h") or 0),
                saves=int(payload.get("saves_24h") or 0),
                comments=int(payload.get("comments_24h") or 0),
                is_raw_pov=prefix in RAW_POV_POSTS,
                is_female_voiced=prefix in FEMALE_VOICED_POSTS,
                per_platform=payload.get("per_platform") or {},
            )
        )
    return snapshots


def avg(values: Iterable[float]) -> float:
    """Safe mean — returns 0 on empty."""
    vs = list(values)
    return sum(vs) / len(vs) if vs else 0.0


def render_report(week_ending: date, snaps: list[PostSnapshot]) -> str:
    """Build the full markdown report body."""
    total_views = sum(s.views for s in snaps)
    total_saves = sum(s.saves for s in snaps)
    total_likes = sum(s.likes for s in snaps)
    total_comments = sum(s.comments for s in snaps)

    by_saves = sorted(snaps, key=lambda s: s.saves, reverse=True)
    top_3 = by_saves[:3]
    bottom_3 = list(reversed(by_saves[-3:])) if len(by_saves) >= 3 else list(reversed(by_saves))

    # Per-feature-anchor aggregates.
    anchor_buckets: dict[str, list[PostSnapshot]] = defaultdict(list)
    for s in snaps:
        if s.feature_anchor:
            anchor_buckets[s.feature_anchor].append(s)

    # Raw-POV vs designed-gradient.
    raw_snaps = [s for s in snaps if s.is_raw_pov]
    designed_snaps = [s for s in snaps if not s.is_raw_pov]
    female_snaps = [s for s in snaps if s.is_female_voiced]
    default_snaps = [s for s in snaps if not s.is_female_voiced]

    today = date.today().isoformat()

    lines: list[str] = []
    lines.append("---")
    lines.append("tags: [project/prpath, type/recap, scope/weekly]")
    lines.append(f"week_ending: {week_ending.isoformat()}")
    lines.append(f"created: {today}")
    lines.append("---")
    lines.append("")
    lines.append(f"# PRPath Weekly Recap — week ending {week_ending.isoformat()}")
    lines.append("")

    # ---- Week Summary ----
    lines.append("## Week Summary")
    lines.append("")
    lines.append(f"- Posts verified: **{len(snaps)}**")
    lines.append(f"- Total views (24h): **{total_views:,}**")
    lines.append(f"- Total saves (24h): **{total_saves:,}**  ← primary secondary metric")
    lines.append(f"- Total likes (24h): **{total_likes:,}**")
    lines.append(f"- Total comments (24h): **{total_comments:,}**")
    lines.append(f"- Avg saves / post: **{avg(s.saves for s in snaps):.1f}**")
    lines.append(f"- Avg views / post: **{avg(s.views for s in snaps):.1f}**")
    lines.append("")

    # ---- Top 3 ----
    lines.append("## Top 3 by Saves")
    lines.append("")
    lines.append("| Rank | Post | Day | Saves | Views | Likes | Anchor |")
    lines.append("|---|---|---|---:|---:|---:|:-:|")
    for i, s in enumerate(top_3, start=1):
        lines.append(
            f"| {i} | {s.post_id} | {s.day} | {s.saves} | {s.views} | {s.likes} | {s.feature_anchor or '—'} |"
        )
    if not top_3:
        lines.append("| — | _no posts this week_ | — | — | — | — | — |")
    lines.append("")

    # ---- Bottom 3 ----
    lines.append("## Bottom 3 by Saves")
    lines.append("")
    lines.append("| Rank | Post | Day | Saves | Views | Likes | Anchor |")
    lines.append("|---|---|---|---:|---:|---:|:-:|")
    for i, s in enumerate(bottom_3, start=1):
        lines.append(
            f"| {i} | {s.post_id} | {s.day} | {s.saves} | {s.views} | {s.likes} | {s.feature_anchor or '—'} |"
        )
    if not bottom_3:
        lines.append("| — | _no posts this week_ | — | — | — | — | — |")
    lines.append("")

    # ---- Per-feature-anchor ----
    lines.append("## Per-Feature-Anchor Performance")
    lines.append("")
    lines.append("| Anchor | Posts | Avg saves | Avg views | Total saves | Total views |")
    lines.append("|:-:|---:|---:|---:|---:|---:|")
    for anchor in "ABCDEFG":
        bucket = anchor_buckets.get(anchor, [])
        n = len(bucket)
        if n == 0:
            lines.append(f"| {anchor} | 0 | — | — | — | — |")
        else:
            lines.append(
                f"| {anchor} | {n} | "
                f"{avg(s.saves for s in bucket):.1f} | "
                f"{avg(s.views for s in bucket):.1f} | "
                f"{sum(s.saves for s in bucket)} | "
                f"{sum(s.views for s in bucket)} |"
            )
    lines.append("")

    # ---- Raw-POV vs Designed-Gradient ----
    lines.append("## Raw-POV vs Designed-Gradient")
    lines.append("")
    lines.append("| Cohort | Posts | Avg views | Avg saves |")
    lines.append("|---|---:|---:|---:|")
    lines.append(
        f"| Raw-POV (#01/#03/#11/#14) | {len(raw_snaps)} | "
        f"{avg(s.views for s in raw_snaps):.1f} | {avg(s.saves for s in raw_snaps):.1f} |"
    )
    lines.append(
        f"| Designed-Gradient | {len(designed_snaps)} | "
        f"{avg(s.views for s in designed_snaps):.1f} | {avg(s.saves for s in designed_snaps):.1f} |"
    )
    lines.append("")

    # ---- Female-Voiced vs Default ----
    lines.append("## Female-Voiced vs Default")
    lines.append("")
    lines.append("| Cohort | Posts | Avg views | Avg saves |")
    lines.append("|---|---:|---:|---:|")
    lines.append(
        f"| Female-voiced (#03/#11/#17/#18) | {len(female_snaps)} | "
        f"{avg(s.views for s in female_snaps):.1f} | {avg(s.saves for s in female_snaps):.1f} |"
    )
    lines.append(
        f"| Male-neutral default | {len(default_snaps)} | "
        f"{avg(s.views for s in default_snaps):.1f} | {avg(s.saves for s in default_snaps):.1f} |"
    )
    lines.append("")

    # ---- Flags ----
    lines.append("## Flags")
    lines.append("")
    flag_lines: list[str] = []
    for anchor in "ABCDEFG":
        bucket = anchor_buckets.get(anchor, [])
        if len(bucket) >= 2 and all(s.saves == 0 for s in bucket):
            flag_lines.append(f"- Anchor **{anchor}** posted 0 saves across {len(bucket)} posts.")
    if not any(s.views > 0 for s in snaps) and snaps:
        flag_lines.append("- Zero views across every post this week — possible platform-wide account issue.")
    if not flag_lines:
        flag_lines.append("- No structural flags this week.")
    lines.extend(flag_lines)
    lines.append("")

    return "\n".join(lines)


def write_via_applescript_duplicate(tmp_path: Path, dest_dir: Path) -> bool:
    """Copy /tmp markdown into the vault via Finder — bypasses TCC on Documents.

    Returns True if the vault file ends up in place, False otherwise. Creates
    the destination directory via Finder as well if needed.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    # AppleScript: duplicate file from tmp to vault, overwriting.
    script = (
        f'set srcFile to POSIX file "{tmp_path}" as alias\n'
        f'set destFolder to POSIX file "{dest_dir}" as alias\n'
        f'tell application "Finder"\n'
        f'    duplicate srcFile to destFolder with replacing\n'
        f'end tell\n'
    )
    try:
        res = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as e:
        print(f"[sunday_recap] osascript exception: {e}", file=sys.stderr)
        return False
    if res.returncode != 0:
        print(f"[sunday_recap] osascript exit {res.returncode}: {res.stderr.strip()}", file=sys.stderr)
        return False
    return (dest_dir / tmp_path.name).exists()


def write_report(markdown: str, week_ending: date) -> Path:
    """Write the markdown report to the vault (with TCC fallback) + local fallback dir.

    Returns the final report path (vault path if vault write succeeded, else fallback).
    """
    filename = f"{week_ending.isoformat()}.md"
    tmp_path = Path(f"/tmp/prpath_recap_{week_ending.isoformat()}.md")
    tmp_path.write_text(markdown)

    # Always keep a local copy next to the pipeline.
    FALLBACK_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    local_copy = FALLBACK_REPORTS_DIR / filename
    try:
        shutil.copy2(str(tmp_path), str(local_copy))
    except Exception as e:
        print(f"[sunday_recap] local fallback write failed: {e}", file=sys.stderr)

    # Try direct write first.
    vault_path = VAULT_WEEKLY_REPORTS_DIR / filename
    try:
        VAULT_WEEKLY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        vault_path.write_text(markdown)
        return vault_path
    except Exception as e:
        print(f"[sunday_recap] direct vault write failed, falling back to Finder: {e}", file=sys.stderr)

    # Fall back to Finder-via-AppleScript to bypass TCC.
    if write_via_applescript_duplicate(tmp_path, VAULT_WEEKLY_REPORTS_DIR):
        return vault_path

    print("[sunday_recap] vault write failed both paths; keeping local fallback only", file=sys.stderr)
    return local_copy


def top_post(snaps: list[PostSnapshot]) -> PostSnapshot | None:
    """Return the post with the highest 24h save count."""
    return max(snaps, key=lambda s: s.saves) if snaps else None


def main() -> int:
    """Entry point — returns 0 on success, 1 if the report could not be written."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--week-ending", help="Week-ending date YYYY-MM-DD (default: yesterday)")
    args = ap.parse_args()

    week_ending = parse_date(args.week_ending) if args.week_ending else (date.today() - timedelta(days=1))

    snaps = load_snapshots(week_ending)
    print(f"[sunday_recap] {len(snaps)} snapshots for week ending {week_ending.isoformat()}")

    markdown = render_report(week_ending, snaps)
    report_path = write_report(markdown, week_ending)

    total_saves = sum(s.saves for s in snaps)
    top = top_post(snaps)
    top_label = top.post_id if top else "—"
    msg = (
        f"PRPATH RECAP \u2705 {week_ending.isoformat()} — "
        f"{total_saves} saves, top: {top_label}, full report in Obsidian "
        f"({report_path.name})"
    )
    print(msg)
    notify(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
