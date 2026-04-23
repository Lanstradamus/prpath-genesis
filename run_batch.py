#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRPath batch generator orchestrator.

Runs the Sunday + Wednesday Cowork batches. Generates 9 carousel posts
(3 days x 3 slots) per fire, pauses at vision gates for the Cowork-Claude to
draft captions, then schedules to Post for Me via postforme_client.py.

Usage:
    python3 run_batch.py --target sun              # plan Mon/Tue/Wed batch, write manifest, open vision gate
    python3 run_batch.py --target wed              # plan Thu/Fri/Sat batch
    python3 run_batch.py --resume <batch_id>       # post vision gate: read captions.json and schedule
    python3 run_batch.py --resume <batch_id> --status  # print status table for an existing batch
    python3 run_batch.py --target sun --dry-run    # explicit dry-run (also the default)
    python3 run_batch.py --target sun --live       # live schedule (requires POSTFORME_DRY_RUN=false in .env)

Hard rules (preserved from LaunchLens):
  - python3 shebang, UTF-8 source
  - --live ONLY flips live if POSTFORME_DRY_RUN=false in .env AS WELL. Both required.
  - Brand filter lives in postforme_client.py (username == "prpathapp"). Not reimplemented here.
  - Never posts to any platform outside the 4 wired in PFM (TikTok, IG, YT, FB).
  - Never modifies data/used_post_ids.json outside this script.
  - Never deletes files in posted/.
  - If a step fails, stop and Telegram. Do not retry destructively.

Dependencies: requests, python-dotenv, pathlib (stdlib), argparse (stdlib), json (stdlib)
"""

from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover — dotenv optional but recommended
    def load_dotenv(*_args: Any, **_kwargs: Any) -> bool:
        return False


# ---------------------------------------------------------------------------
# Paths + constants
# ---------------------------------------------------------------------------

GENESIS_ROOT = Path("/Users/lancesessions/Developer/prpath-genesis")
SHOTS_POSTS_V2 = Path("/Users/lancesessions/Developer/PRPathShots/samples/posts_v2")
PICKS_ROOT = GENESIS_ROOT / "picks"
BATCHES_ROOT = PICKS_ROOT / "_batches"
DATA_ROOT = GENESIS_ROOT / "data"
USED_POST_IDS_PATH = DATA_ROOT / "used_post_ids.json"
ENV_PATH = GENESIS_ROOT / ".env"
NOTIFY_PY = GENESIS_ROOT / "notify.py"
POSTFORME_CLIENT_PY = GENESIS_ROOT / "postforme_client.py"

# Central Time, no DST handling here — Cowork pipelines live in CT only.
# Using naive -05:00 matches the LaunchLens manifest shape. If CDT/CST ever
# needs to flip, refactor to zoneinfo("America/Chicago").
CT_UTC_OFFSET = timedelta(hours=-5)
CT_TZ = timezone(CT_UTC_OFFSET, name="CT")

# Slot times in CT, per spec: 10am / 3pm / 8pm.
SLOT_TIMES = [time(10, 0), time(15, 0), time(20, 0)]

# Locked hashtag sets per Week-2 v2.1 spec.
HASHTAGS_GUY = ["#gymtok", "#strengthtraining", "#gymbro", "#prpath"]
HASHTAGS_GIRL = ["#gymtok", "#strengthtraining", "#gymgirl", "#prpath"]

# 30-day anti-repeat window.
REPEAT_LOCKOUT_DAYS = 30

# Max slot-caption minimum chars for validation on resume.
MIN_CAPTION_LEN = 20

# How many days sideways to look for "sun" and "wed" targets.
TARGET_DAYS_MAP = {
    "sun": {"anchor_weekday": 6, "offset_days": [1, 2, 3]},   # Sunday -> Mon/Tue/Wed
    "wed": {"anchor_weekday": 2, "offset_days": [1, 2, 3]},   # Wednesday -> Thu/Fri/Sat
}


# ---------------------------------------------------------------------------
# v2.1 post inventory — hardcoded per Week-2 strategy doc
# ---------------------------------------------------------------------------
# Each row: post_id, feature_anchor (A-G), women_angled (bool)
# post_ids match the folder names under PRPathShots/samples/posts_v2/.
# Feature-anchor mapping follows [[PRPath Cowork Scheduled Sessions]]:
#   A: 01  |  B: 02, 03 (f), 04, 05, 12 (emotional-universal)
#   C: 06, 07, 08 (emotional-universal)  |  D: 09, 10
#   E: 11 (f), 13  |  F: 14, 15, 16
#   G: 17, 18   (plus 03 + 11 double as women-angled)
INVENTORY: list[dict[str, Any]] = [
    {"post_id": "01_atlas_skip_leg",   "anchor": "A", "women_angled": False},
    {"post_id": "02_notes_mess",       "anchor": "B", "women_angled": False},
    {"post_id": "03_chest_fried",      "anchor": "B", "women_angled": True},
    {"post_id": "04_rest_or_push",     "anchor": "B", "women_angled": False},
    {"post_id": "05_program_ignores",  "anchor": "B", "women_angled": False},
    {"post_id": "06_four_weeks_bench", "anchor": "C", "women_angled": False},
    {"post_id": "07_pr_thursday",      "anchor": "C", "women_angled": False},
    {"post_id": "08_proof_not_hype",   "anchor": "C", "women_angled": False},
    {"post_id": "09_185_at_180",       "anchor": "D", "women_angled": False},
    {"post_id": "10_where_rank",       "anchor": "D", "women_angled": False},
    {"post_id": "11_mfp_tedious",      "anchor": "E", "women_angled": True},
    {"post_id": "12_give_up_thursday", "anchor": "B", "women_angled": False},
    {"post_id": "13_protein_guess",    "anchor": "E", "women_angled": False},
    {"post_id": "14_decide_gym",       "anchor": "F", "women_angled": False},
    {"post_id": "15_template_pdf",     "anchor": "F", "women_angled": False},
    {"post_id": "16_what_to_train",    "anchor": "F", "women_angled": False},
    {"post_id": "17_leaner_stronger",  "anchor": "G", "women_angled": True},
    {"post_id": "18_first_pr",         "anchor": "G", "women_angled": True},
]
INVENTORY_BY_ID = {p["post_id"]: p for p in INVENTORY}

REQUIRED_ANCHORS = ["A", "B", "C", "D", "E", "F"]
MAX_G_PER_BATCH = 2


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def log(msg: str) -> None:
    print(msg, flush=True)


def err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def notify(message: str) -> None:
    """Fire-and-forget Telegram via notify.py. Never crashes caller."""
    if not NOTIFY_PY.exists():
        err(f"WARN: notify.py not found at {NOTIFY_PY} — skipping Telegram send. "
            f"Message was: {message}")
        return
    try:
        subprocess.run(
            ["python3", str(NOTIFY_PY), message],
            check=False,
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001 — notify must never crash orchestrator
        err(f"WARN: notify.py invocation failed ({exc}). Message was: {message}")


def env_says_live() -> bool:
    """True iff POSTFORME_DRY_RUN is explicitly 'false' in .env. Unset or 'true' -> False."""
    load_dotenv(ENV_PATH, override=False)
    val = os.environ.get("POSTFORME_DRY_RUN", "true").strip().lower()
    return val == "false"


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Date / ISO helpers — with gotcha #9 same-day patches
# ---------------------------------------------------------------------------

def resolve_days(target: str, today: Optional[date] = None) -> list[date]:
    """Return 3 target post-dates for the requested batch target.

    Sun batch -> the NEXT Mon/Tue/Wed after today (or if today is Sun, upcoming Mon/Tue/Wed).
    Wed batch -> the NEXT Thu/Fri/Sat after today (or if today is Wed, upcoming Thu/Fri/Sat).

    Gotcha #9 patch: if today is the anchor weekday, `days_ahead=0` is allowed
    so the batch still fires when Cowork misses its normal window and runs a
    few hours late or the very next morning.
    """
    if target not in TARGET_DAYS_MAP:
        raise ValueError(f"Unknown target {target!r}; expected sun or wed.")

    today = today or date.today()
    cfg = TARGET_DAYS_MAP[target]
    anchor_weekday = cfg["anchor_weekday"]   # Mon=0 ... Sun=6

    # How many days until the next anchor weekday (Sun for sun, Wed for wed).
    # Allow 0 — same-day anchor firing is legal per gotcha #9.
    delta = (anchor_weekday - today.weekday()) % 7
    anchor_date = today + timedelta(days=delta)

    return [anchor_date + timedelta(days=off) for off in cfg["offset_days"]]


def make_iso(day: date, slot_time: time, *, now: Optional[datetime] = None) -> str:
    """Build a CT-aware ISO8601 string for the slot. Clamps to now+5min if in the past.

    Gotcha #9 patch: if the computed scheduled_at is already in the past
    (e.g., batch fires at 11:30am CT and slot is 10am same day), clamp to
    now + 5 minutes so PFM doesn't reject the schedule as past-dated.
    """
    now = now or datetime.now(CT_TZ)
    naive_scheduled = datetime.combine(day, slot_time)
    scheduled = naive_scheduled.replace(tzinfo=CT_TZ)
    if scheduled <= now:
        scheduled = now + timedelta(minutes=5)
    # Truncate seconds/microseconds for stable manifest diffs.
    scheduled = scheduled.replace(second=0, microsecond=0)
    return scheduled.isoformat()


# ---------------------------------------------------------------------------
# Used-post-ids tracking
# ---------------------------------------------------------------------------

def load_used_post_ids() -> dict[str, str]:
    """Return {post_id: ISO_date_last_used}. Returns {} if file missing."""
    if not USED_POST_IDS_PATH.exists():
        return {}
    try:
        raw = read_json(USED_POST_IDS_PATH)
    except (json.JSONDecodeError, OSError) as exc:
        err(f"WARN: could not read {USED_POST_IDS_PATH} ({exc}). Treating as empty.")
        return {}
    if not isinstance(raw, dict):
        err(f"WARN: {USED_POST_IDS_PATH} is not an object. Treating as empty.")
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def recently_used_ids(today: Optional[date] = None) -> set[str]:
    """post_ids used within REPEAT_LOCKOUT_DAYS of today."""
    today = today or date.today()
    used = load_used_post_ids()
    cutoff = today - timedelta(days=REPEAT_LOCKOUT_DAYS)
    blocked: set[str] = set()
    for post_id, used_date_str in used.items():
        try:
            used_date = date.fromisoformat(used_date_str[:10])
        except ValueError:
            # Unparseable date — block it defensively (safer than a surprise repeat).
            blocked.add(post_id)
            continue
        if used_date >= cutoff:
            blocked.add(post_id)
    return blocked


def record_used_post_ids(post_ids: list[str], on_day: date) -> None:
    """Append/overwrite each post_id with on_day's ISO date in used_post_ids.json."""
    existing = load_used_post_ids()
    stamp = on_day.isoformat()
    for pid in post_ids:
        existing[pid] = stamp
    write_json(USED_POST_IDS_PATH, existing)


# ---------------------------------------------------------------------------
# Slot planning — feature-anchor rotation
# ---------------------------------------------------------------------------

@dataclass
class PlannedSlot:
    slot_id: str
    day: str                  # YYYY-MM-DD
    scheduled_at: str         # ISO with CT offset
    post_id: str
    feature_anchor: str
    slide_01_path: str
    slide_02_path: str
    caption: Optional[str] = None
    hashtags: list[str] = field(default_factory=list)
    scheduled: bool = False
    post_id_pfm: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    failed: bool = False
    error: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def pick_post_ids(target_days: list[date], blocked_ids: set[str], *, seed: Optional[int] = None) -> list[dict[str, Any]]:
    """Return 9 inventory rows satisfying the rotation constraints.

    Rules:
      - Must cover anchors A-F at least once each.
      - G capped at MAX_G_PER_BATCH (2).
      - Avoid post_ids in `blocked_ids` when possible. If constraints cannot
        be met without dipping into blocked, fall back to blocked (noting a
        warning later in the caller) — keeps the batch from crashing on a
        small inventory.
      - Spread anchors across slot-times so the same anchor doesn't always
        hit the same time of day (interleave by shuffling within each day).

    Determinism: if `seed` is supplied, shuffling is reproducible. Default
    seed = today's ISO date so the same day always plans identically.
    """
    rng = random.Random(seed if seed is not None else date.today().toordinal())

    # Group inventory by anchor.
    by_anchor: dict[str, list[dict[str, Any]]] = {}
    for row in INVENTORY:
        by_anchor.setdefault(row["anchor"], []).append(row)

    def available(anchor: str, already_picked: set[str], allow_blocked: bool = False) -> list[dict[str, Any]]:
        pool = [r for r in by_anchor.get(anchor, []) if r["post_id"] not in already_picked]
        if not allow_blocked:
            pool = [r for r in pool if r["post_id"] not in blocked_ids]
        rng.shuffle(pool)
        return pool

    chosen: list[dict[str, Any]] = []
    chosen_ids: set[str] = set()

    # 1) Guarantee one of each A-F.
    for anchor in REQUIRED_ANCHORS:
        pool = available(anchor, chosen_ids)
        if not pool:
            # Fallback: allow blocked repeats rather than crash the batch.
            pool = available(anchor, chosen_ids, allow_blocked=True)
        if not pool:
            raise RuntimeError(f"No inventory available for required anchor {anchor!r}.")
        pick = pool[0]
        chosen.append(pick)
        chosen_ids.add(pick["post_id"])

    # 2) Fill the remaining 3 slots, honoring G cap and preferring unblocked.
    remaining = 9 - len(chosen)
    g_count = sum(1 for r in chosen if r["anchor"] == "G")

    # Build a weighted candidate pool: prefer anchors that already got 1 so
    # we spread coverage; prefer unblocked; respect G cap.
    anchor_counts = {a: sum(1 for r in chosen if r["anchor"] == a) for a in set(r["anchor"] for r in INVENTORY)}

    def fill_pool(allow_blocked: bool) -> list[dict[str, Any]]:
        pool: list[dict[str, Any]] = []
        for row in INVENTORY:
            if row["post_id"] in chosen_ids:
                continue
            if row["anchor"] == "G" and g_count >= MAX_G_PER_BATCH:
                continue
            if not allow_blocked and row["post_id"] in blocked_ids:
                continue
            pool.append(row)
        return pool

    for _ in range(remaining):
        pool = fill_pool(allow_blocked=False)
        if not pool:
            pool = fill_pool(allow_blocked=True)
        if not pool:
            # Last-ditch: widen G cap if we truly can't fill 9. This should
            # never hit with an 18-post inventory, but don't crash the batch.
            err("WARN: pick_post_ids could not fill slot within constraints — relaxing G cap.")
            pool = [r for r in INVENTORY if r["post_id"] not in chosen_ids]
            if not pool:
                raise RuntimeError("Inventory exhausted before filling 9 slots.")
        # Prefer lowest-covered anchors first to spread exposure.
        min_count = min(anchor_counts.get(r["anchor"], 0) for r in pool)
        lowest = [r for r in pool if anchor_counts.get(r["anchor"], 0) == min_count]
        rng.shuffle(lowest)
        pick = lowest[0]
        chosen.append(pick)
        chosen_ids.add(pick["post_id"])
        anchor_counts[pick["anchor"]] = anchor_counts.get(pick["anchor"], 0) + 1
        if pick["anchor"] == "G":
            g_count += 1

    # 3) Interleave anchors across slot-times. Group by anchor, then deal
    # one post per time-slot across the 3 days to avoid clustering.
    # Simple approach: shuffle the 9 picks, then ensure no anchor lands in
    # the same slot-time twice in a row where avoidable.
    rng.shuffle(chosen)

    # Attempt a few swaps to reduce same-time-same-anchor collisions.
    # 9 slots = 3 days x 3 times; slot-time index = idx % 3.
    def same_time_collisions(seq: list[dict[str, Any]]) -> int:
        by_time: dict[int, list[str]] = {0: [], 1: [], 2: []}
        for i, r in enumerate(seq):
            by_time[i % 3].append(r["anchor"])
        collisions = 0
        for anchors in by_time.values():
            collisions += len(anchors) - len(set(anchors))
        return collisions

    best = list(chosen)
    best_score = same_time_collisions(best)
    for _ in range(40):
        if best_score == 0:
            break
        candidate = list(best)
        i, j = rng.sample(range(9), 2)
        candidate[i], candidate[j] = candidate[j], candidate[i]
        score = same_time_collisions(candidate)
        if score < best_score:
            best = candidate
            best_score = score

    return best


def build_batch_id(target: str, today: Optional[date] = None) -> str:
    today = today or date.today()
    return f"prpath-batch-{today.strftime('%Y%m%d')}-{target}"


def plan_batch(target: str, dry_run: bool, today: Optional[date] = None) -> dict[str, Any]:
    today = today or date.today()
    target_days = resolve_days(target, today=today)
    batch_id = build_batch_id(target, today=today)
    blocked = recently_used_ids(today=today)

    picks = pick_post_ids(target_days, blocked)

    slots: list[PlannedSlot] = []
    anchor_coverage: dict[str, int] = {}

    now_ct = datetime.now(CT_TZ)
    for d_idx, day in enumerate(target_days):
        for s_idx, slot_time in enumerate(SLOT_TIMES):
            flat_idx = d_idx * 3 + s_idx
            pick = picks[flat_idx]
            slot_id = f"{batch_id}-d{d_idx}-s{s_idx}"
            slide_01 = SHOTS_POSTS_V2 / pick["post_id"] / "slide_01_issue.png"
            slide_02 = SHOTS_POSTS_V2 / pick["post_id"] / "slide_02_solution.png"

            warnings: list[str] = []
            if not slide_01.exists():
                warnings.append(f"MISSING slide_01 at {slide_01}")
            if not slide_02.exists():
                warnings.append(f"MISSING slide_02 at {slide_02}")

            hashtags = HASHTAGS_GIRL if pick.get("women_angled") else HASHTAGS_GUY

            slot = PlannedSlot(
                slot_id=slot_id,
                day=day.isoformat(),
                scheduled_at=make_iso(day, slot_time, now=now_ct),
                post_id=pick["post_id"],
                feature_anchor=pick["anchor"],
                slide_01_path=str(slide_01),
                slide_02_path=str(slide_02),
                caption=None,
                hashtags=list(hashtags),
                scheduled=False,
                post_id_pfm=None,
                warnings=warnings,
            )
            slots.append(slot)
            anchor_coverage[pick["anchor"]] = anchor_coverage.get(pick["anchor"], 0) + 1

    manifest = {
        "batch_id": batch_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "target": target,
        "target_days": [d.isoformat() for d in target_days],
        "dry_run": dry_run,
        "feature_anchor_coverage": anchor_coverage,
        "slots": [s.as_dict() for s in slots],
    }
    return manifest


# ---------------------------------------------------------------------------
# Manifest + picks directory writers
# ---------------------------------------------------------------------------

def batch_dir(batch_id: str) -> Path:
    return BATCHES_ROOT / batch_id


def manifest_path(batch_id: str) -> Path:
    return batch_dir(batch_id) / "manifest.json"


def captions_path(batch_id: str) -> Path:
    return batch_dir(batch_id) / "captions.json"


def vision_task_path(batch_id: str) -> Path:
    return batch_dir(batch_id) / "vision_task.md"


def write_manifest(manifest: dict[str, Any]) -> Path:
    path = manifest_path(manifest["batch_id"])
    write_json(path, manifest)
    # Ensure per-slot pick dirs exist so Cowork-Claude can drop scheduled.json etc.
    for slot in manifest["slots"]:
        slot_pick_dir = PICKS_ROOT / slot["post_id"]
        slot_pick_dir.mkdir(parents=True, exist_ok=True)
    return path


def write_vision_task(manifest: dict[str, Any]) -> Path:
    """Human-readable task file for the Cowork-Claude to fill captions."""
    path = vision_task_path(manifest["batch_id"])
    batch_id = manifest["batch_id"]
    lines: list[str] = []
    lines.append(f"# Vision Gate — {batch_id}")
    lines.append("")
    lines.append("You (Cowork-Claude) are paused at the vision gate for this batch.")
    lines.append("")
    lines.append("## Your job")
    lines.append("")
    lines.append("For each of the 9 slots below:")
    lines.append("")
    lines.append("1. Open `slide_01_path` and `slide_02_path` — confirm both slides render cleanly,")
    lines.append("   no cropping, legible copy.")
    lines.append("2. Draft a caption using the Hevy template:")
    lines.append("")
    lines.append("       \"<feature pain> is not <pain word> if you download PRPath\"")
    lines.append("")
    lines.append("   (see `PRPath - Week 2 Content Strategy (2026-04-20)` for the exact examples per post).")
    lines.append("")
    lines.append("3. Write every caption into the JSON file below. The hashtag set is LOCKED per slot")
    lines.append("   (already populated in the manifest). Do NOT add `#fyp`, `#foryou`, `#viral`, or")
    lines.append("   generic `#fitness`. Do NOT include `#gid_` or other pipeline-ID style tags.")
    lines.append("")
    lines.append("## Output path")
    lines.append("")
    lines.append(f"    {captions_path(batch_id)}")
    lines.append("")
    lines.append("Expected JSON shape:")
    lines.append("")
    lines.append("```json")
    lines.append("{")
    lines.append(f'  "batch_id": "{batch_id}",')
    lines.append('  "captions": {')
    lines.append('    "<slot_id>": "caption text here (>=20 chars, no banned tags)",')
    lines.append('    "<slot_id>": "..."')
    lines.append('  }')
    lines.append("}")
    lines.append("```")
    lines.append("")
    lines.append("## After writing captions.json")
    lines.append("")
    lines.append(f"    python3 run_batch.py --resume {batch_id}")
    lines.append("")
    lines.append("That resume call will:")
    lines.append("  - Validate every slot has a non-empty caption >= 20 chars")
    lines.append("  - Update the manifest with the captions")
    lines.append("  - Schedule each slot to Post for Me via postforme_client.py")
    lines.append("  - Telegram a success (or loud failure) alert")
    lines.append("")
    lines.append("## Slots")
    lines.append("")
    for slot in manifest["slots"]:
        lines.append(f"### {slot['slot_id']}")
        lines.append("")
        lines.append(f"- day: {slot['day']}")
        lines.append(f"- scheduled_at: {slot['scheduled_at']}")
        lines.append(f"- post_id: {slot['post_id']}")
        lines.append(f"- feature_anchor: {slot['feature_anchor']}")
        lines.append(f"- slide_01: {slot['slide_01_path']}")
        lines.append(f"- slide_02: {slot['slide_02_path']}")
        lines.append(f"- hashtags: {' '.join(slot['hashtags'])}")
        if slot.get("warnings"):
            lines.append(f"- WARNINGS: {slot['warnings']}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Resume: validate captions.json and schedule to PFM
# ---------------------------------------------------------------------------

BANNED_HASHTAGS = {"#fyp", "#foryou", "#viral", "#fitness"}


def _caption_is_ok(caption: str) -> tuple[bool, Optional[str]]:
    if not caption or not isinstance(caption, str):
        return False, "empty or non-string"
    if len(caption.strip()) < MIN_CAPTION_LEN:
        return False, f"too short (<{MIN_CAPTION_LEN} chars)"
    lower = caption.lower()
    for bad in BANNED_HASHTAGS:
        if bad in lower:
            return False, f"contains banned tag {bad}"
    if "#gid_" in lower:
        return False, "contains banned pipeline-ID tag (#gid_)"
    return True, None


def load_captions(batch_id: str) -> dict[str, str]:
    cpath = captions_path(batch_id)
    if not cpath.exists():
        raise FileNotFoundError(f"captions.json not found at {cpath}. Vision gate not complete.")
    data = read_json(cpath)
    captions = data.get("captions") if isinstance(data, dict) else None
    if not isinstance(captions, dict):
        raise ValueError(f"captions.json malformed — expected top-level 'captions' object. Got: {type(data).__name__}")
    return {str(k): str(v) for k, v in captions.items()}


def apply_captions(manifest: dict[str, Any], captions: dict[str, str]) -> list[str]:
    """Write captions onto manifest slots. Returns list of validation error strings."""
    errors: list[str] = []
    for slot in manifest["slots"]:
        slot_id = slot["slot_id"]
        caption = captions.get(slot_id, "")
        ok, reason = _caption_is_ok(caption)
        if not ok:
            errors.append(f"{slot_id}: {reason}")
            continue
        slot["caption"] = caption.strip()
    return errors


def schedule_slot_via_pfm(manifest_path_str: str, slot_id: str, *, live: bool) -> dict[str, Any]:
    """Call postforme_client.py to schedule a single slot.

    Contract (expected):
        python3 postforme_client.py schedule --manifest <path> --slot <slot_id> [--dry-run|--live]
        -> prints JSON to stdout with at least {"post_id_pfm": "<id>", "ok": true}
        -> exit 0 on success, non-zero on failure.

    If the client is missing or emits unparseable output, returns a dict
    with ok=False and a descriptive error. We never crash the orchestrator
    here — the caller decides to Telegram + exit.
    """
    if not POSTFORME_CLIENT_PY.exists():
        return {
            "ok": False,
            "error": f"postforme_client.py not found at {POSTFORME_CLIENT_PY}",
        }
    cmd = [
        "python3",
        str(POSTFORME_CLIENT_PY),
        "schedule",
        "--manifest", manifest_path_str,
        "--slot", slot_id,
    ]
    cmd.append("--live" if live else "--dry-run")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "postforme_client.py timed out (>120s)"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"postforme_client.py invocation failed: {exc}"}

    if proc.returncode != 0:
        return {
            "ok": False,
            "error": f"postforme_client.py exit={proc.returncode} stderr={proc.stderr.strip()[:500]}",
        }

    # Try to parse JSON stdout; if parse fails, return raw text.
    try:
        payload = json.loads(proc.stdout.strip())
        if not isinstance(payload, dict):
            raise ValueError("not a JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        return {
            "ok": False,
            "error": f"postforme_client.py stdout not JSON: {exc}; stdout={proc.stdout.strip()[:500]}",
        }
    # Normalize ok flag.
    payload.setdefault("ok", True)
    return payload


def resume_batch(batch_id: str, live_requested: bool, status_only: bool) -> int:
    mpath = manifest_path(batch_id)
    if not mpath.exists():
        err(f"ERROR: manifest not found for batch_id={batch_id} at {mpath}")
        return 1

    manifest = read_json(mpath)

    if status_only:
        print_batch_status(manifest)
        return 0

    # Decide live-ness: manifest captures original intent at creation. --live
    # on resume upgrades from dry-run -> live ONLY if env agrees.
    manifest_dry_run = bool(manifest.get("dry_run", True))
    if live_requested:
        if not env_says_live():
            err("ERROR: --live requested but POSTFORME_DRY_RUN != 'false' in .env. Refusing to flip.")
            notify(f"PRPATH BATCH {batch_id} — REFUSED LIVE — POSTFORME_DRY_RUN not false in .env")
            return 1
        use_dry_run = False
        manifest["dry_run"] = False
    else:
        use_dry_run = manifest_dry_run

    # --- STEP 4: validate captions ---
    try:
        captions = load_captions(batch_id)
    except (FileNotFoundError, ValueError) as exc:
        err(f"ERROR: {exc}")
        _append_vision_task_error(batch_id, str(exc))
        notify(f"PRPATH BATCH {batch_id} — VISION GATE FAIL — {exc}")
        return 1

    errors = apply_captions(manifest, captions)
    if errors:
        msg = "Caption validation failed:\n  - " + "\n  - ".join(errors)
        err(msg)
        _append_vision_task_error(batch_id, msg)
        notify(f"PRPATH BATCH {batch_id} — CAPTION VALIDATION FAIL — {len(errors)} slot(s)")
        # Persist the partial-caption progress so the Cowork-Claude can fix in place.
        write_json(mpath, manifest)
        return 1

    write_json(mpath, manifest)

    # --- STEP 5: schedule each slot ---
    scheduled_count = 0
    failed_slots: list[tuple[str, str]] = []
    for slot in manifest["slots"]:
        if slot.get("scheduled"):
            log(f"SKIP already-scheduled {slot['slot_id']}")
            scheduled_count += 1
            continue
        if slot.get("warnings"):
            log(f"SKIP slot with warnings {slot['slot_id']}: {slot['warnings']}")
            slot["failed"] = True
            slot["error"] = f"pre-schedule warnings: {slot['warnings']}"
            failed_slots.append((slot["slot_id"], slot["error"]))
            continue

        log(f"Scheduling {slot['slot_id']} ({'LIVE' if not use_dry_run else 'DRY-RUN'})...")
        result = schedule_slot_via_pfm(str(mpath), slot["slot_id"], live=not use_dry_run)
        if not result.get("ok"):
            slot["failed"] = True
            slot["error"] = result.get("error", "unknown error")
            failed_slots.append((slot["slot_id"], slot["error"]))
            err(f"FAIL {slot['slot_id']}: {slot['error']}")
            # Write the current manifest state before stopping.
            write_json(mpath, manifest)
            notify(
                f"PRPATH BATCH {batch_id} FAIL — {slot['slot_id']} could not schedule — {slot['error'][:200]}"
            )
            return 1  # Hard rule: do not retry destructively.

        slot["scheduled"] = True
        slot["post_id_pfm"] = result.get("post_id_pfm")
        # Write per-slot scheduled.json for archival.
        slot_pick_dir = PICKS_ROOT / slot["post_id"]
        slot_pick_dir.mkdir(parents=True, exist_ok=True)
        write_json(slot_pick_dir / "scheduled.json", {
            "slot_id": slot["slot_id"],
            "post_id": slot["post_id"],
            "post_id_pfm": slot["post_id_pfm"],
            "scheduled_at": slot["scheduled_at"],
            "dry_run": use_dry_run,
            "batch_id": batch_id,
            "pfm_response": result,
        })
        scheduled_count += 1
        # Persist incrementally to avoid losing progress on a crash mid-batch.
        write_json(mpath, manifest)
        log(f"OK {slot['slot_id']} -> pfm={slot['post_id_pfm']}")

    # --- STEP 6: final Telegram + record used_post_ids ---
    success = scheduled_count == len(manifest["slots"]) and not failed_slots

    if success:
        # Record used post_ids stamped to today.
        try:
            record_used_post_ids(
                [s["post_id"] for s in manifest["slots"]],
                on_day=date.today(),
            )
        except OSError as exc:
            err(f"WARN: could not update used_post_ids.json: {exc}")

        msg = (
            f"PRPATH BATCH {batch_id} OK SCHEDULED — {scheduled_count}/{len(manifest['slots'])} slots, "
            f"dry_run={use_dry_run}"
        )
        log(msg)
        notify(msg)
        return 0
    else:
        msg = (
            f"PRPATH BATCH {batch_id} INCOMPLETE — "
            f"{scheduled_count}/{len(manifest['slots'])} scheduled, {len(failed_slots)} failed"
        )
        err(msg)
        notify(msg)
        return 1


def _append_vision_task_error(batch_id: str, err_msg: str) -> None:
    path = vision_task_path(batch_id)
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError:
        existing = ""
    stamp = datetime.now(timezone.utc).isoformat()
    banner = f"\n\n---\n\n## ERROR on resume — {stamp}\n\n{err_msg}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(existing + banner, encoding="utf-8")


# ---------------------------------------------------------------------------
# Status printer
# ---------------------------------------------------------------------------

def print_batch_status(manifest: dict[str, Any]) -> None:
    log(f"Batch: {manifest['batch_id']}")
    log(f"  created_at   : {manifest.get('created_at')}")
    log(f"  target       : {manifest.get('target')}")
    log(f"  target_days  : {manifest.get('target_days')}")
    log(f"  dry_run      : {manifest.get('dry_run')}")
    log(f"  anchors      : {manifest.get('feature_anchor_coverage')}")
    log("")
    log(f"  {'slot_id':<40} {'post_id':<22} {'anchor':<6} {'scheduled':<9} {'failed':<6} pfm_id")
    for slot in manifest["slots"]:
        log(
            f"  {slot['slot_id']:<40} {slot['post_id']:<22} {slot['feature_anchor']:<6} "
            f"{str(slot.get('scheduled', False)):<9} {str(slot.get('failed', False)):<6} "
            f"{slot.get('post_id_pfm') or '-'}"
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_plan(target: str, live_requested: bool) -> int:
    # Dry-run gate: default dry-run. --live ONLY honored if env agrees.
    if live_requested:
        if not env_says_live():
            err("ERROR: --live requested but POSTFORME_DRY_RUN != 'false' in .env. Refusing to flip.")
            return 1
        dry_run = False
    else:
        dry_run = True

    manifest = plan_batch(target=target, dry_run=dry_run)
    mpath = write_manifest(manifest)
    vpath = write_vision_task(manifest)

    log(f"Wrote manifest: {mpath}")
    log(f"Wrote vision task: {vpath}")

    # Auto-draft captions from the locked plan (Hevy template, no LLM).
    # Skips silently if caption_drafter is unavailable so we never block the batch.
    try:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from caption_drafter import full_caption
        import json as _json
        captions = {}
        for slot in manifest["slots"]:
            cap = full_caption(slot["post_id"], variant_seed=0)
            slot["caption"] = cap
            captions[slot["slot_id"]] = cap
        cpath = captions_path(manifest["batch_id"])
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(_json.dumps(captions, indent=2), encoding="utf-8")
        # Re-write manifest so slot captions are persisted
        mpath.write_text(_json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        log(f"Auto-drafted {len(captions)} captions → {cpath}")
    except Exception as exc:
        log(f"[warn] caption auto-draft skipped: {exc}")

    log("")
    print_batch_status(manifest)

    log("")
    log(f"Captions auto-drafted (Hevy template). Review + edit in the dashboard.")
    log(f"THEN: python3 run_batch.py --resume {manifest['batch_id']}"
        + (" --live" if not dry_run else ""))

    notify(
        f"PRPATH BATCH {manifest['batch_id']} — captions auto-drafted, ready for review "
        f"(dry_run={dry_run})"
    )
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="run_batch.py",
        description="PRPath batch orchestrator — 9 carousel posts per fire.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--target", choices=["sun", "wed"], help="Plan a new batch for sun (Mon/Tue/Wed) or wed (Thu/Fri/Sat).")
    group.add_argument("--resume", metavar="BATCH_ID", help="Resume an existing batch post-vision-gate.")

    parser.add_argument("--dry-run", action="store_true", help="Explicit dry-run (also the default).")
    parser.add_argument("--live", action="store_true", help="Live schedule (requires POSTFORME_DRY_RUN=false in .env).")
    parser.add_argument("--status", action="store_true", help="With --resume: print status table only and exit 0.")

    args = parser.parse_args(argv[1:])

    if args.dry_run and args.live:
        err("ERROR: --dry-run and --live are mutually exclusive.")
        return 2

    BATCHES_ROOT.mkdir(parents=True, exist_ok=True)
    PICKS_ROOT.mkdir(parents=True, exist_ok=True)
    DATA_ROOT.mkdir(parents=True, exist_ok=True)

    if args.target:
        return run_plan(target=args.target, live_requested=args.live)
    else:
        return resume_batch(batch_id=args.resume, live_requested=args.live, status_only=args.status)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
