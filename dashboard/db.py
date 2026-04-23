"""SQLite state for PRPath dashboard.

Tracks:
- script_runs     — every invocation of a pipeline script (preflight, verify, etc.)
- batches          — generated batches awaiting approval or already scheduled
- slots            — individual slots within a batch (one per post)
- post_metrics     — platform-level metrics per post per pull
- feed_events      — compact activity stream for the dashboard

Single-user, single-machine. No migrations framework — schema changes go in
ensure_schema() and update data in-place if needed. Forever retention.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(__file__).resolve().parent.parent / "pipeline.db"


SCHEMA = """
CREATE TABLE IF NOT EXISTS script_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    script        TEXT    NOT NULL,
    started_at    TEXT    NOT NULL,
    finished_at   TEXT,
    exit_code     INTEGER,
    status        TEXT    NOT NULL,              -- running|ok|warn|error
    output        TEXT    NOT NULL DEFAULT '',
    args          TEXT    NOT NULL DEFAULT '',
    triggered_by  TEXT    NOT NULL DEFAULT 'manual'  -- manual|launchd|api
);

CREATE INDEX IF NOT EXISTS idx_script_runs_started ON script_runs(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_script_runs_script  ON script_runs(script, started_at DESC);

CREATE TABLE IF NOT EXISTS batches (
    batch_id      TEXT    PRIMARY KEY,
    target        TEXT    NOT NULL,              -- sun|wed
    created_at    TEXT    NOT NULL,
    target_days   TEXT    NOT NULL,              -- JSON array
    dry_run       INTEGER NOT NULL DEFAULT 1,    -- 0=live, 1=dry
    status        TEXT    NOT NULL,              -- planning|awaiting_captions|approval_queue|scheduled|failed
    manifest_path TEXT,
    vision_task_path TEXT,
    captions_path TEXT,
    live_scheduled_at TEXT,                       -- when user approved LIVE
    notes         TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_batches_status ON batches(status, created_at DESC);

CREATE TABLE IF NOT EXISTS slots (
    slot_id          TEXT    PRIMARY KEY,
    batch_id         TEXT    NOT NULL REFERENCES batches(batch_id),
    slot_index       INTEGER NOT NULL,
    day              TEXT    NOT NULL,
    scheduled_at     TEXT    NOT NULL,
    post_id          TEXT    NOT NULL,
    feature_anchor   TEXT    NOT NULL,
    slide_01_path    TEXT    NOT NULL,
    slide_02_path    TEXT    NOT NULL,
    caption          TEXT,
    hashtags         TEXT    NOT NULL DEFAULT '',
    approved         INTEGER NOT NULL DEFAULT 0,   -- user clicked approve
    approved_at      TEXT,
    pfm_post_ids     TEXT,                          -- JSON dict {platform: pfm_id}
    status           TEXT    NOT NULL DEFAULT 'drafted',  -- drafted|approved|scheduled|live|failed
    error            TEXT
);

CREATE INDEX IF NOT EXISTS idx_slots_batch ON slots(batch_id, slot_index);
CREATE INDEX IF NOT EXISTS idx_slots_day   ON slots(day);

CREATE TABLE IF NOT EXISTS post_metrics (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    slot_id          TEXT    NOT NULL REFERENCES slots(slot_id),
    pulled_at        TEXT    NOT NULL,
    platform         TEXT    NOT NULL,              -- tiktok|instagram|youtube|facebook
    views            INTEGER NOT NULL DEFAULT 0,
    likes            INTEGER NOT NULL DEFAULT 0,
    comments         INTEGER NOT NULL DEFAULT 0,
    saves            INTEGER NOT NULL DEFAULT 0,
    raw              TEXT                            -- full JSON from PFM
);

CREATE INDEX IF NOT EXISTS idx_metrics_slot ON post_metrics(slot_id, pulled_at DESC);

CREATE TABLE IF NOT EXISTS feed_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT    NOT NULL,
    kind        TEXT    NOT NULL,                    -- info|success|warn|error|action
    title       TEXT    NOT NULL,
    detail      TEXT    NOT NULL DEFAULT '',
    actor       TEXT    NOT NULL DEFAULT 'system'    -- system|user|launchd
);

CREATE INDEX IF NOT EXISTS idx_feed_at ON feed_events(at DESC);

CREATE TABLE IF NOT EXISTS schedule_config (
    script        TEXT PRIMARY KEY,
    enabled       INTEGER NOT NULL DEFAULT 0,
    cron_expr     TEXT,                             -- e.g. "0 10 * * 0"  (Sun 10am)
    display_name  TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT ''
);
"""


# ---------------------------------------------------------------------------
# Connection + schema
# ---------------------------------------------------------------------------
def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, isolation_level=None)  # autocommit
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    c = _conn()
    try:
        yield c
    finally:
        c.close()


def ensure_schema() -> None:
    with get_db() as db:
        db.executescript(SCHEMA)
        _seed_schedule_defaults(db)


def _seed_schedule_defaults(db: sqlite3.Connection) -> None:
    defaults = [
        ("preflight",      "Daily Preflight",     "OAuth + .env + disk health",        "30 8 * * 1-6"),
        ("scout",          "Daily Scout",         "Batch readiness check",              "0 6 * * 1-6"),
        ("verify",         "Daily Verify",        "Yesterday published + 24h metrics",  "0 9 * * 1-6"),
        ("metrics_pulse",  "Evening Metrics",     "6-12h shadow-ban detection",         "0 21 * * 1-6"),
        ("sunday_recap",   "Sunday Recap",        "Weekly saves-primary report",        "0 9 * * 0"),
        ("run_batch_sun",  "Sunday Batch",        "Generate Mon/Tue/Wed posts (Opus)",  "0 10 * * 0"),
        ("run_batch_wed",  "Wednesday Batch",     "Generate Thu/Fri/Sat posts (Opus)",  "0 10 * * 3"),
        ("midweek_pulse",  "Midweek Pulse",       "Mon+Tue early signal before Wed",    "0 9 * * 3"),
        ("monthly_retro",  "Monthly Retro",       "1st Sunday only — anchor cut/keep",  "0 11 1-7 * 0"),
    ]
    for script, display_name, description, cron_expr in defaults:
        db.execute(
            "INSERT OR IGNORE INTO schedule_config (script, display_name, description, cron_expr, enabled) "
            "VALUES (?, ?, ?, ?, 0)",
            (script, display_name, description, cron_expr),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def log_event(kind: str, title: str, detail: str = "", actor: str = "system") -> None:
    with get_db() as db:
        db.execute(
            "INSERT INTO feed_events (at, kind, title, detail, actor) VALUES (?, ?, ?, ?, ?)",
            (now_iso(), kind, title, detail, actor),
        )


def recent_feed(limit: int = 20) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            "SELECT at, kind, title, detail, actor FROM feed_events ORDER BY at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def last_run_for(script: str) -> dict[str, Any] | None:
    with get_db() as db:
        r = db.execute(
            "SELECT id, script, started_at, finished_at, exit_code, status, args FROM script_runs "
            "WHERE script = ? ORDER BY started_at DESC LIMIT 1",
            (script,),
        ).fetchone()
    return dict(r) if r else None


def start_script_run(script: str, args: str = "", triggered_by: str = "manual") -> int:
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO script_runs (script, started_at, status, args, triggered_by) VALUES (?, ?, 'running', ?, ?)",
            (script, now_iso(), args, triggered_by),
        )
        return cur.lastrowid


def finish_script_run(run_id: int, exit_code: int, output: str) -> None:
    # Infer status from exit code and output heuristics (RED/YELLOW/GREEN keywords)
    up = output.upper()
    if exit_code != 0:
        status = "error"
    elif "🔴" in output or " RED " in up or "OVERALL: RED" in up:
        status = "error"
    elif "🟡" in output or " YELLOW " in up or "OVERALL: YELLOW" in up:
        status = "warn"
    else:
        status = "ok"
    with get_db() as db:
        db.execute(
            "UPDATE script_runs SET finished_at = ?, exit_code = ?, status = ?, output = ? WHERE id = ?",
            (now_iso(), exit_code, status, output, run_id),
        )


def update_running_output(run_id: int, output: str) -> None:
    with get_db() as db:
        db.execute("UPDATE script_runs SET output = ? WHERE id = ?", (output, run_id))


def get_schedule_config() -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            "SELECT script, display_name, description, cron_expr, enabled FROM schedule_config ORDER BY display_name"
        ).fetchall()
    return [dict(r) for r in rows]


def set_schedule_enabled(script: str, enabled: bool) -> None:
    with get_db() as db:
        db.execute("UPDATE schedule_config SET enabled = ? WHERE script = ?", (1 if enabled else 0, script))


def set_schedule_cron(script: str, cron_expr: str) -> None:
    with get_db() as db:
        db.execute("UPDATE schedule_config SET cron_expr = ? WHERE script = ?", (cron_expr, script))


# ---------------------------------------------------------------------------
# Batch + slot helpers
# ---------------------------------------------------------------------------
def upsert_batch_from_manifest(manifest_path: Path) -> None:
    """Read a batch manifest written by run_batch.py and reflect into SQLite."""
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    batch_id = data["batch_id"]
    with get_db() as db:
        db.execute(
            """
            INSERT INTO batches (batch_id, target, created_at, target_days, dry_run, status, manifest_path)
            VALUES (?, ?, ?, ?, ?, 'awaiting_captions', ?)
            ON CONFLICT(batch_id) DO UPDATE SET
                target_days = excluded.target_days,
                dry_run = excluded.dry_run,
                manifest_path = excluded.manifest_path
            """,
            (
                batch_id,
                data.get("target", "sun"),
                data.get("created_at", now_iso()),
                json.dumps(data.get("target_days", [])),
                1 if data.get("dry_run", True) else 0,
                str(manifest_path),
            ),
        )
        for i, slot in enumerate(data.get("slots", [])):
            db.execute(
                """
                INSERT INTO slots (
                    slot_id, batch_id, slot_index, day, scheduled_at, post_id,
                    feature_anchor, slide_01_path, slide_02_path, caption, hashtags, status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'drafted')
                ON CONFLICT(slot_id) DO UPDATE SET
                    scheduled_at  = excluded.scheduled_at,
                    caption       = COALESCE(slots.caption, excluded.caption),
                    hashtags      = excluded.hashtags
                """,
                (
                    slot["slot_id"],
                    batch_id,
                    i,
                    slot["day"],
                    slot["scheduled_at"],
                    slot["post_id"],
                    slot["feature_anchor"],
                    slot["slide_01_path"],
                    slot["slide_02_path"],
                    slot.get("caption"),
                    json.dumps(slot.get("hashtags", [])),
                ),
            )


def batches_awaiting_approval() -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM batches WHERE status IN ('awaiting_captions','approval_queue') ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def slots_for_batch(batch_id: str) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM slots WHERE batch_id = ? ORDER BY slot_index", (batch_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def get_batch(batch_id: str) -> dict[str, Any] | None:
    with get_db() as db:
        r = db.execute("SELECT * FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()
    return dict(r) if r else None


def set_slot_caption(slot_id: str, caption: str) -> None:
    with get_db() as db:
        db.execute("UPDATE slots SET caption = ? WHERE slot_id = ?", (caption, slot_id))


def approve_slot(slot_id: str) -> None:
    with get_db() as db:
        db.execute(
            "UPDATE slots SET approved = 1, approved_at = ?, status = 'approved' WHERE slot_id = ?",
            (now_iso(), slot_id),
        )


def set_slot_pfm_ids(
    slot_id: str,
    post_ids: dict[str, str],
    scheduled_at: str | None = None,
) -> None:
    """Persist PFM post IDs + per-platform status to a slot.

    pfm_post_ids is stored as JSON in shape:
      {"tiktok": {"id": "sp_...", "status": "scheduled", "fired_at": "..."}}

    Platforms without an ID in the input dict are not touched — callers can
    fire one platform at a time (e.g. PFM_ONLY_FB) and repeatedly call this
    function to merge each new ID into the slot record.

    Also auto-promotes the parent batch status from approval_queue → scheduled
    once every slot in the batch has at least one platform PFM ID, so the
    dashboard banner flips from "awaiting review" to "N slots LIVE".
    """
    fired_at = now_iso()
    batch_id: str | None = None
    with get_db() as db:
        row = db.execute(
            "SELECT batch_id, pfm_post_ids FROM slots WHERE slot_id = ?", (slot_id,)
        ).fetchone()
        if row:
            batch_id = row["batch_id"]
        existing: dict[str, Any] = {}
        if row and row["pfm_post_ids"]:
            try:
                existing = json.loads(row["pfm_post_ids"])
            except Exception:
                existing = {}
        for platform, pfm_id in (post_ids or {}).items():
            if not pfm_id:
                continue
            existing[platform] = {"id": pfm_id, "status": "scheduled", "fired_at": fired_at}
        db.execute(
            "UPDATE slots SET pfm_post_ids = ?, status = 'scheduled' WHERE slot_id = ?",
            (json.dumps(existing), slot_id),
        )

        # Auto-promote parent batch if every slot is now scheduled with >=1 PFM ID.
        if batch_id:
            all_slots = db.execute(
                "SELECT pfm_post_ids FROM slots WHERE batch_id = ?", (batch_id,)
            ).fetchall()
            all_live = all_slots and all(
                r["pfm_post_ids"] and r["pfm_post_ids"] != "{}" for r in all_slots
            )
            if all_live:
                db.execute(
                    "UPDATE batches SET status = 'scheduled', live_scheduled_at = ? "
                    "WHERE batch_id = ? AND status != 'scheduled'",
                    (fired_at, batch_id),
                )


def update_slot_platform_status(slot_id: str, platform: str, status: str) -> None:
    """Update just one platform's status within a slot's pfm_post_ids blob."""
    with get_db() as db:
        row = db.execute("SELECT pfm_post_ids FROM slots WHERE slot_id = ?", (slot_id,)).fetchone()
        if not row or not row["pfm_post_ids"]:
            return
        blob = json.loads(row["pfm_post_ids"])
        if platform in blob and isinstance(blob[platform], dict):
            blob[platform]["status"] = status
        db.execute(
            "UPDATE slots SET pfm_post_ids = ? WHERE slot_id = ?",
            (json.dumps(blob), slot_id),
        )


def live_slots(batch_id: str | None = None, days_back: int = 14) -> list[dict[str, Any]]:
    """Slots with PFM IDs — what the LIVE Posts dashboard renders.

    If batch_id is None, returns the most recently fired batch's slots.
    """
    with get_db() as db:
        if batch_id is None:
            latest = db.execute(
                "SELECT batch_id FROM batches ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not latest:
                return []
            batch_id = latest["batch_id"]
        rows = db.execute(
            """
            SELECT slot_id, batch_id, slot_index, day, scheduled_at, post_id,
                   feature_anchor, caption, pfm_post_ids, status
            FROM slots
            WHERE batch_id = ?
            ORDER BY slot_index
            """,
            (batch_id,),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["platforms"] = json.loads(d.pop("pfm_post_ids") or "{}")
        except Exception:
            d["platforms"] = {}
        out.append(d)
    return out


def set_batch_status(batch_id: str, status: str, notes: str = "") -> None:
    with get_db() as db:
        if notes:
            db.execute("UPDATE batches SET status = ?, notes = ? WHERE batch_id = ?", (status, notes, batch_id))
        else:
            db.execute("UPDATE batches SET status = ? WHERE batch_id = ?", (status, batch_id))


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------
def weekly_saves_trend(weeks: int = 8) -> list[dict[str, Any]]:
    """Saves per week across all platforms for the last N weeks."""
    with get_db() as db:
        rows = db.execute(
            """
            SELECT strftime('%Y-W%W', s.day) AS week,
                   SUM(pm.saves)             AS total_saves,
                   SUM(pm.views)             AS total_views,
                   COUNT(DISTINCT s.slot_id) AS post_count
            FROM slots s
            LEFT JOIN post_metrics pm ON pm.slot_id = s.slot_id
            WHERE s.status IN ('scheduled','live') AND s.day >= date('now', ?)
            GROUP BY week
            ORDER BY week DESC
            LIMIT ?
            """,
            (f'-{weeks * 7} days', weeks),
        ).fetchall()
    return [dict(r) for r in rows]


def queued_slots(days_ahead: int = 7) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT slot_id, batch_id, day, scheduled_at, post_id, feature_anchor, status, approved
            FROM slots
            WHERE day <= date('now', ?) AND day >= date('now')
            ORDER BY scheduled_at
            """,
            (f'+{days_ahead} days',),
        ).fetchall()
    return [dict(r) for r in rows]


def per_platform_perf(limit: int = 200) -> list[dict[str, Any]]:
    """One row per (slot, platform) — the shape the Content Performance table renders.

    Left-joins post_metrics so rows still appear for just-fired posts (the
    dashboard shows "—" for views/likes until the first metrics pull lands).
    """
    with get_db() as db:
        rows = db.execute(
            """
            SELECT s.slot_id, s.post_id, s.feature_anchor, s.day,
                   s.scheduled_at, s.pfm_post_ids,
                   pm.platform   AS metric_platform,
                   pm.views      AS views,
                   pm.likes      AS likes,
                   pm.comments   AS comments,
                   pm.saves      AS saves,
                   pm.pulled_at  AS pulled_at
            FROM slots s
            LEFT JOIN (
                SELECT slot_id, platform,
                       views, likes, comments, saves,
                       MAX(pulled_at) AS pulled_at
                FROM post_metrics
                GROUP BY slot_id, platform
            ) pm ON pm.slot_id = s.slot_id
            WHERE s.pfm_post_ids IS NOT NULL AND s.pfm_post_ids != '' AND s.pfm_post_ids != '{}'
            ORDER BY s.scheduled_at DESC, s.slot_id
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    # Expand each slot into 4 platform rows so even "no data yet" posts render.
    out = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        try:
            platforms_blob = json.loads(d.get("pfm_post_ids") or "{}")
        except Exception:
            platforms_blob = {}
        for platform in ("tiktok", "instagram", "facebook", "youtube"):
            if platform not in platforms_blob:
                continue
            key = (d["slot_id"], platform)
            if key in seen:
                continue
            row = {
                "slot_id": d["slot_id"],
                "post_id": d["post_id"],
                "feature_anchor": d["feature_anchor"],
                "day": d["day"],
                "scheduled_at": d["scheduled_at"],
                "platform": platform,
                "views": 0,
                "likes": 0,
                "comments": 0,
                "saves": 0,
                "pulled_at": None,
                "engagement": 0.0,
            }
            # Overlay metrics if the joined row happens to be this platform.
            if d.get("metric_platform") == platform:
                row["views"] = d.get("views") or 0
                row["likes"] = d.get("likes") or 0
                row["comments"] = d.get("comments") or 0
                row["saves"] = d.get("saves") or 0
                row["pulled_at"] = d.get("pulled_at")
                if row["views"]:
                    engagements = row["likes"] + row["comments"] + row["saves"]
                    row["engagement"] = round(100 * engagements / row["views"], 2)
            seen[key] = row
            out.append(row)
    return out


def record_post_metrics(
    slot_id: str,
    platform: str,
    views: int = 0,
    likes: int = 0,
    comments: int = 0,
    saves: int = 0,
    raw: str = "",
) -> None:
    """Insert a metrics snapshot for a slot-platform. Each call is a new row —
    per_platform_perf() reads only the most recent per (slot, platform)."""
    with get_db() as db:
        db.execute(
            "INSERT INTO post_metrics (slot_id, pulled_at, platform, views, likes, comments, saves, raw) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (slot_id, now_iso(), platform, views, likes, comments, saves, raw),
        )


def per_post_perf(limit: int = 50) -> list[dict[str, Any]]:
    with get_db() as db:
        rows = db.execute(
            """
            SELECT s.slot_id, s.post_id, s.feature_anchor, s.day, s.status,
                   IFNULL(SUM(pm.views), 0)    AS views,
                   IFNULL(SUM(pm.saves), 0)    AS saves,
                   IFNULL(SUM(pm.likes), 0)    AS likes,
                   IFNULL(SUM(pm.comments), 0) AS comments
            FROM slots s
            LEFT JOIN post_metrics pm ON pm.slot_id = s.slot_id
            WHERE s.status IN ('scheduled','live')
            GROUP BY s.slot_id
            ORDER BY s.day DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    ensure_schema()
    print(f"✓ Schema ensured at {DB_PATH}")
