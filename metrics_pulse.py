#!/usr/bin/env python3
"""PRPath metrics pulse — pulls post analytics from Post for Me into SQLite.

One API call per connected social account (4 total — TT/IG/FB/YT) hits
`/v1/social-account-feeds/{id}?expand=metrics` and gets back the account's
recent posts with platform-specific engagement numbers attached.

Each matched post (its PFM social_post_id has to correspond to a slot we
fired) writes one row to the `post_metrics` table. Unknown posts (Cowork
era, manual posts outside this pipeline) are logged and skipped.

After the pull, a Telegram pulse summary is sent for today's slots — this
is the shadow-ban / slow-start signal the evening cron cares about.

CLI:
    python3 metrics_pulse.py                 # today's slots
    python3 metrics_pulse.py --date 2026-04-23
    python3 metrics_pulse.py --midweek       # Mon+Tue slots (fires Wed 9am)
    python3 metrics_pulse.py --no-notify     # skip the Telegram send
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from postforme_client import DEFAULT_BRAND, resolve_prpath_accounts  # type: ignore
from dashboard import db  # type: ignore

ENV_PATH = SCRIPT_DIR / ".env"
NOTIFY_SCRIPT = SCRIPT_DIR / "notify.py"
PFM_BASE_URL = "https://api.postforme.dev/v1"
HTTP_TIMEOUT_SEC = 30
FEED_LIMIT = 50  # covers ~2 weeks of PRPath fires at 9 slots/batch × 2 batches/week

# Evening-pulse thresholds (hours live → expected minimum views per platform).
SHADOW_HOURS = 6.0   # 0 views after this is DEAD
SLOW_HOURS = 3.0     # < SLOW_VIEWS after this is SLOW
SLOW_VIEWS = 50

PLATFORM_LABELS = {
    "tiktok": "TT",
    "instagram": "IG",
    "facebook": "FB",
    "youtube": "YT",
}


# ---------------------------------------------------------------------------
# PFM feed fetch
# ---------------------------------------------------------------------------
def _load_api_key() -> str:
    load_dotenv(ENV_PATH)
    import os
    key = os.environ.get("POSTFORME_API_KEY", "").strip()
    if not key:
        print(f"ERROR: POSTFORME_API_KEY missing in {ENV_PATH}", file=sys.stderr)
        sys.exit(1)
    return key


def fetch_account_feed(account_id: str, api_key: str, limit: int = FEED_LIMIT) -> list[dict]:
    """GET /v1/social-account-feeds/{account_id}?expand=metrics&limit=N.

    Returns the `data` array (list of PlatformPostDto). Empty list on any
    error — metrics_pulse must never crash a scheduled run.
    """
    url = f"{PFM_BASE_URL}/social-account-feeds/{account_id}?expand=metrics&limit={limit}"
    try:
        resp = requests.get(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=HTTP_TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        print(f"[metrics_pulse] PFM feed fetch failed for {account_id}: {exc}", file=sys.stderr)
        return []
    if not resp.ok:
        snippet = (resp.text or "")[:200]
        print(f"[metrics_pulse] PFM feed HTTP {resp.status_code} for {account_id}: {snippet}", file=sys.stderr)
        return []
    try:
        body = resp.json()
    except ValueError:
        return []
    return body.get("data") or []


# ---------------------------------------------------------------------------
# Slot matching — two strategies, because PFM's feed inconsistently populates
# social_post_id: IG + YT include it, TT + FB do not. Primary strategy is an
# exact map (platform, social_post_id) → slot_id. Fallback is a (posted_at
# within 30 min of scheduled_at, caption first-line match) lookup.
# ---------------------------------------------------------------------------
_TZ_NO_COLON_RE = re.compile(r"([+-]\d{2})(\d{2})$")


def _parse_iso_utc(s: str | None) -> datetime | None:
    """ISO 8601 → UTC datetime. Handles Z suffix, +0000/+00:00 offsets, and
    fractional seconds. Python 3.9's fromisoformat doesn't accept tz offsets
    without a colon (like `+0000`), so we normalize first."""
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    s = _TZ_NO_COLON_RE.sub(r"\1:\2", s)
    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def build_slot_reverse_index() -> dict[tuple[str, str], str]:
    """Map (platform, PFM social_post_id) → slot_id. Works for IG + YT."""
    index: dict[tuple[str, str], str] = {}
    with db.get_db() as d:
        rows = d.execute(
            "SELECT slot_id, pfm_post_ids FROM slots WHERE pfm_post_ids IS NOT NULL AND pfm_post_ids != '{}'"
        ).fetchall()
    for r in rows:
        try:
            blob = json.loads(r["pfm_post_ids"])
        except Exception:
            continue
        for platform, entry in blob.items():
            pfm_id = entry.get("id") if isinstance(entry, dict) else entry
            if pfm_id:
                index[(platform, pfm_id)] = r["slot_id"]
    return index


def build_slot_timing_index() -> dict[str, list[dict]]:
    """Map platform → [{slot_id, scheduled_at_utc, caption_first_line}, ...].

    Used for TT + FB fallback matching since those feeds don't include
    social_post_id. A feed post matches a slot when posted_at is within 30
    min of scheduled_at AND (if the caption has a hook) the hook's first
    line matches our stored caption's first line.
    """
    out: dict[str, list[dict]] = {}
    with db.get_db() as d:
        rows = d.execute(
            "SELECT slot_id, scheduled_at, caption, pfm_post_ids FROM slots "
            "WHERE pfm_post_ids IS NOT NULL AND pfm_post_ids != '{}'"
        ).fetchall()
    for r in rows:
        try:
            blob = json.loads(r["pfm_post_ids"] or "{}")
        except Exception:
            blob = {}
        scheduled_utc = _parse_iso_utc(r["scheduled_at"])
        first_line = (r["caption"] or "").split("\n", 1)[0].strip().lower()
        for platform in blob:
            out.setdefault(platform, []).append({
                "slot_id": r["slot_id"],
                "scheduled_at_utc": scheduled_utc,
                "caption_first_line": first_line,
            })
    return out


def match_by_timing(platform: str, post: dict, timing_idx: dict[str, list[dict]],
                    tolerance_minutes: int = 30) -> str | None:
    """Fallback match: within `tolerance_minutes` of scheduled_at + caption
    first-line tiebreaker. Returns slot_id or None."""
    posted = _parse_iso_utc(post.get("posted_at"))
    if not posted:
        return None
    tolerance = timedelta(minutes=tolerance_minutes)
    candidates = [
        s for s in timing_idx.get(platform, [])
        if s["scheduled_at_utc"] and abs(s["scheduled_at_utc"] - posted) <= tolerance
    ]
    if len(candidates) == 1:
        return candidates[0]["slot_id"]
    if len(candidates) > 1:
        # Tiebreaker: feed caption's first line must match our stored caption's first line.
        # (FB feed keeps the hook; TT feed reduces to hashtags only, so TT tiebreaker rarely fires.)
        feed_line = (post.get("caption") or "").split("\n", 1)[0].strip().lower()
        if feed_line:
            exact = [c for c in candidates if c["caption_first_line"] == feed_line]
            if len(exact) == 1:
                return exact[0]["slot_id"]
    return None


# ---------------------------------------------------------------------------
# Platform-specific metrics normalization → (views, likes, comments, saves)
# ---------------------------------------------------------------------------
def normalize_metrics(platform: str, m: dict) -> tuple[int, int, int, int]:
    """Reduce PFM's platform-specific metrics DTO to the 4 generic columns.

    Everything else is preserved in the `raw` JSON blob for later analysis.
    Saves is only meaningfully populated for IG (and TT Business if flagged).
    """
    if not m:
        return 0, 0, 0, 0
    p = (platform or "").lower()

    if p == "tiktok":
        # Basic TikTokPostMetricsDto: view_count/like_count/comment_count/share_count
        # Business DTO: video_views/likes/comments/favorites (saves)
        views = int(m.get("view_count") or m.get("video_views") or 0)
        likes = int(m.get("like_count") or m.get("likes") or 0)
        comments = int(m.get("comment_count") or m.get("comments") or 0)
        saves = int(m.get("favorites") or 0)  # Business only; basic has no saves
        return views, likes, comments, saves

    if p == "instagram":
        return (
            int(m.get("views") or 0),
            int(m.get("likes") or 0),
            int(m.get("comments") or 0),
            int(m.get("saved") or 0),
        )

    if p == "facebook":
        # Views column = reach (unique people who saw the post). FB's video_views
        # is only 3+-second plays and understates reality; `reach` is what MBS
        # surfaces to the user and is the most comparable to TT/IG/YT views.
        # FB has no saves field; reactions_total stands in for likes.
        return (
            int(m.get("reach") or m.get("video_views") or m.get("media_views") or 0),
            int(m.get("reactions_total") or 0),
            int(m.get("comments") or 0),
            0,
        )

    if p == "youtube":
        return (
            int(m.get("views") or 0),
            int(m.get("likes") or 0),
            int(m.get("comments") or 0),
            0,  # YT has no saves; subscribersGained lives in raw
        )

    # Unknown platform — best-effort
    return (
        int(m.get("views") or m.get("view_count") or 0),
        int(m.get("likes") or m.get("like_count") or 0),
        int(m.get("comments") or m.get("comment_count") or 0),
        0,
    )


# ---------------------------------------------------------------------------
# Pull + persist
# ---------------------------------------------------------------------------
def pull_and_persist(api_key: str) -> dict[str, Any]:
    """Hit all 4 account feeds, persist matched posts to post_metrics.

    Returns a summary dict for logging + the evening pulse message.
    """
    accounts = resolve_prpath_accounts(api_key, DEFAULT_BRAND)
    reverse_idx = build_slot_reverse_index()
    timing_idx = build_slot_timing_index()

    summary: dict[str, Any] = {
        "persisted": 0,
        "unmatched": 0,
        "per_platform": {},  # platform → {persisted, unmatched, total_views, matched_by_timing}
        "per_slot": {},      # slot_id → {platform → (views, likes, comments, saves)}
    }

    for platform, account_id in accounts.items():
        platform_stats = {"persisted": 0, "unmatched": 0, "total_views": 0, "matched_by_timing": 0}
        posts = fetch_account_feed(account_id, api_key)
        for post in posts:
            # Strategy 1: exact (platform, social_post_id) match (IG + YT)
            sp_id = post.get("social_post_id")
            slot_id = reverse_idx.get((platform, sp_id)) if sp_id else None
            # Strategy 2: timing + caption tiebreaker (TT + FB)
            if not slot_id:
                slot_id = match_by_timing(platform, post, timing_idx)
                if slot_id:
                    platform_stats["matched_by_timing"] += 1
            if not slot_id:
                platform_stats["unmatched"] += 1
                continue
            metrics = post.get("metrics") or {}
            views, likes, comments, saves = normalize_metrics(platform, metrics)
            db.record_post_metrics(
                slot_id=slot_id,
                platform=platform,
                views=views,
                likes=likes,
                comments=comments,
                saves=saves,
                raw=json.dumps(metrics)[:4000],
            )
            platform_stats["persisted"] += 1
            platform_stats["total_views"] += views
            summary["per_slot"].setdefault(slot_id, {})[platform] = (views, likes, comments, saves)

        summary["per_platform"][platform] = platform_stats
        summary["persisted"] += platform_stats["persisted"]
        summary["unmatched"] += platform_stats["unmatched"]

    return summary


# ---------------------------------------------------------------------------
# Telegram pulse summary
# ---------------------------------------------------------------------------
def _tz_today() -> date:
    """Today's date in America/Chicago — matches how we schedule slots."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Chicago")).date()
    except Exception:
        return date.today()


def _render_number(n: int) -> str:
    if n >= 1000:
        v = n / 1000
        return (f"{v:.1f}K" if v < 10 else f"{v:.0f}K")
    return str(n)


def slots_for_dates(target_dates: list[str]) -> list[dict]:
    """Return slots scheduled for any of the given YYYY-MM-DD days."""
    if not target_dates:
        return []
    placeholders = ",".join("?" for _ in target_dates)
    with db.get_db() as d:
        rows = d.execute(
            f"SELECT slot_id, slot_index, post_id, feature_anchor, day, scheduled_at, pfm_post_ids "
            f"FROM slots WHERE day IN ({placeholders}) ORDER BY scheduled_at",
            target_dates,
        ).fetchall()
    return [dict(r) for r in rows]


def hours_live(scheduled_at_iso: str) -> float:
    try:
        dt = datetime.fromisoformat(scheduled_at_iso).astimezone(timezone.utc)
    except Exception:
        return 0.0
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def build_pulse_summary(target_dates: list[str], pull_summary: dict) -> str:
    """Compose the Telegram message for today (or Mon+Tue) slots."""
    slots = slots_for_dates(target_dates)
    if not slots:
        return f"PRPATH PULSE ✅ {target_dates[0]} — no slots for that date"

    # Per-platform totals across the target slots, using metrics fresh from
    # this pull (summary["per_slot"]) as primary, falling back to DB MAX.
    per_platform_views: dict[str, int] = {}
    slot_lines: list[str] = []
    flags: list[str] = []

    for slot in slots:
        slot_id = slot["slot_id"]
        metrics = pull_summary["per_slot"].get(slot_id, {})
        hrs = hours_live(slot["scheduled_at"])
        total_views = 0
        platform_flags: list[str] = []
        for platform in ("tiktok", "instagram", "facebook", "youtube"):
            v = metrics.get(platform, (0, 0, 0, 0))[0] if platform in metrics else 0
            per_platform_views[platform] = per_platform_views.get(platform, 0) + v
            total_views += v
            if hrs >= SHADOW_HOURS and v == 0 and platform in metrics:
                platform_flags.append(f"{PLATFORM_LABELS[platform]} DEAD")
            elif hrs >= SLOW_HOURS and 0 < v < SLOW_VIEWS:
                platform_flags.append(f"{PLATFORM_LABELS[platform]} SLOW")
        anchor = f"[{slot.get('feature_anchor')}]" if slot.get("feature_anchor") else ""
        tag_str = f" — {', '.join(platform_flags)}" if platform_flags else ""
        slot_lines.append(
            f"s{slot['slot_index']}{anchor} {slot['post_id']}: "
            f"{_render_number(total_views)} ({hrs:.1f}h){tag_str}"
        )

    # Account-wide flags: 2+ slots at 0 on the same platform after SHADOW_HOURS
    for platform in ("tiktok", "instagram", "facebook", "youtube"):
        zero_count = sum(
            1 for slot in slots
            if pull_summary["per_slot"].get(slot["slot_id"], {}).get(platform, (0,))[0] == 0
            and hours_live(slot["scheduled_at"]) >= SHADOW_HOURS
        )
        if zero_count >= 2:
            flags.append(f"{PLATFORM_LABELS[platform]} account-wide ({zero_count} slots at 0)")

    # Emoji severity
    if flags or any("DEAD" in line for line in slot_lines):
        emoji = "🔴"
    elif any("SLOW" in line for line in slot_lines):
        emoji = "🟡"
    else:
        emoji = "✅"

    header_range = (target_dates[0] if len(target_dates) == 1
                    else f"{target_dates[0]}..{target_dates[-1]}")
    platform_line = " | ".join(
        f"{PLATFORM_LABELS[p]}: {_render_number(per_platform_views.get(p, 0))}"
        for p in ("tiktok", "instagram", "facebook", "youtube")
    )

    lines = [
        f"PRPATH PULSE {emoji} {header_range}",
        platform_line,
    ]
    if flags:
        lines.append("Flag: " + "; ".join(flags))
    lines.extend(slot_lines)
    return "\n".join(lines)


def notify(msg: str) -> None:
    """Telegram via notify.py. Non-fatal if it fails."""
    try:
        subprocess.run(["python3", str(NOTIFY_SCRIPT), msg], check=False, timeout=30)
    except Exception as exc:
        print(f"[metrics_pulse] notify failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--date", help="Target date YYYY-MM-DD (default: today CT)")
    ap.add_argument("--midweek", action="store_true",
                    help="Pulse Mon+Tue slots (Wed 9am cron)")
    ap.add_argument("--no-notify", action="store_true",
                    help="Skip Telegram send (useful for smoke tests)")
    args = ap.parse_args()

    api_key = _load_api_key()
    db.ensure_schema()

    # Pull + persist (same for both modes — we always ingest all fresh metrics)
    pull_summary = pull_and_persist(api_key)

    print(f"[metrics_pulse] persisted={pull_summary['persisted']} "
          f"unmatched={pull_summary['unmatched']}")
    for platform, stats in pull_summary["per_platform"].items():
        print(f"  {platform}: {stats}")

    # Determine which slots to summarize
    if args.midweek:
        today = _tz_today()
        # Mon+Tue of current week (fires Wed 9am)
        # Weekday: Mon=0, Tue=1, Wed=2
        wd = today.weekday()
        mon = today - timedelta(days=wd)
        tue = mon + timedelta(days=1)
        target_dates = [mon.isoformat(), tue.isoformat()]
    elif args.date:
        target_dates = [args.date]
    else:
        target_dates = [_tz_today().isoformat()]

    msg = build_pulse_summary(target_dates, pull_summary)
    print()
    print(msg)

    if not args.no_notify:
        notify(msg)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
