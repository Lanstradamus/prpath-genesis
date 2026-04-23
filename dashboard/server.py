"""PRPath Pipeline dashboard server.

FastAPI + htmx + Tailwind (CDN). Single-user, localhost only.

Run locally:
    cd /Users/lancesessions/Developer/prpath-genesis
    python3 -m uvicorn dashboard.server:app --host 127.0.0.1 --port 8080 --reload

Then open http://localhost:8080 in a browser.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dashboard import db, runner, schedule_utils

BASE_DIR = Path(__file__).resolve().parent
GENESIS_ROOT = BASE_DIR.parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="PRPath Pipeline Dashboard", version="0.1.0")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.on_event("startup")
def _startup() -> None:
    db.ensure_schema()


# ---------------------------------------------------------------------------
# Home page
# ---------------------------------------------------------------------------
def _fmt_12h(iso_ts: str, tz_suffix: str) -> str:
    """Convert an ISO timestamp to '2:31pm CT' style."""
    from datetime import datetime as _dt
    try:
        dt = _dt.fromisoformat(iso_ts)
    except Exception:
        return iso_ts
    formatted = dt.strftime("%-I:%M%p").replace("AM", "am").replace("PM", "pm")
    return f"{formatted}{tz_suffix}"


def _fmt_day_12h(iso_ts: str, tz_suffix: str) -> str:
    """Format an ISO timestamp as 'Thu Apr 23 · 10:00am CT' — used on LIVE Posts
    cards where the day-of-week context matters (three 10am slots could be any
    of Thu/Fri/Sat, so bare time alone was confusing)."""
    from datetime import datetime as _dt
    try:
        dt = _dt.fromisoformat(iso_ts)
    except Exception:
        return iso_ts
    day = dt.strftime("%a %b %-d")
    time = dt.strftime("%-I:%M%p").replace("AM", "am").replace("PM", "pm")
    return f"{day} · {time}{tz_suffix}"


def _enrich_last_run(run: dict | None, tz_suffix: str) -> dict | None:
    if not run:
        return run
    run = dict(run)
    if run.get("started_at"):
        run["started_12h"] = _fmt_12h(run["started_at"], tz_suffix)
    if run.get("finished_at"):
        run["finished_12h"] = _fmt_12h(run["finished_at"], tz_suffix)
    return run


def _enriched_schedules() -> list[dict]:
    """Augment schedule_config rows with human-readable time + next-fire info."""
    rows = db.get_schedule_config()
    tz = schedule_utils._local_tz_abbrev()
    tz_suffix = f" {tz}" if tz else ""
    # Look up which batch targets (sun/wed) currently have a LIVE-scheduled batch
    # so the batch-generator tiles can show "LIVE" instead of a stale ERR.
    with db.get_db() as _d:
        live_batch_targets = {
            r["target"]
            for r in _d.execute(
                "SELECT DISTINCT target FROM batches WHERE status = 'scheduled'"
            ).fetchall()
        }
    script_to_target = {"run_batch_sun": "sun", "run_batch_wed": "wed"}
    for s in rows:
        cron = s.get("cron_expr") or ""
        s["cron_human"] = schedule_utils.cron_human(cron) if cron else "unscheduled"
        nf = schedule_utils.next_fire(cron) if cron else None
        if nf:
            formatted = nf.strftime("%a %b %-d %-I:%M%p").replace("AM", "am").replace("PM", "pm")
            s["next_fire"] = f"{formatted}{tz_suffix}"
        else:
            s["next_fire"] = "—"
        s["next_delta"] = schedule_utils.human_delta(nf) if nf else ""
        target = script_to_target.get(s["script"])
        s["has_live_batch"] = bool(target and target in live_batch_targets)
    return rows


@app.get("/", response_class=HTMLResponse)
async def home(request: Request) -> HTMLResponse:
    schedules = _enriched_schedules()
    _tz = schedule_utils._local_tz_abbrev()
    _tz_suffix = f" {_tz}" if _tz else ""
    last_runs = {s["script"]: _enrich_last_run(db.last_run_for(s["script"]), _tz_suffix) for s in schedules}
    feed = db.recent_feed(limit=20)
    for e in feed:
        if e.get("at"):
            e["at_12h"] = _fmt_12h(e["at"], _tz_suffix)
    batches = db.batches_awaiting_approval()
    for b in batches:
        b["slots"] = db.slots_for_batch(b["batch_id"])
    queue = db.queued_slots(days_ahead=7)
    per_post = db.per_post_perf(limit=15)
    live_posts = db.live_slots()
    for s in live_posts:
        if s.get("scheduled_at"):
            s["scheduled_at_12h"] = _fmt_day_12h(s["scheduled_at"], _tz_suffix)

    platform_perf = db.per_platform_perf()

    # Banner state — find the next unfired slot across all LIVE posts so the
    # top-of-page "Next action" can say "next fire Thu 10am" instead of the
    # stale "1 batch awaiting review" after a batch is fully LIVE-scheduled.
    from datetime import datetime as _dt
    now_local = _dt.now().astimezone()
    next_fire = None
    posted_count = 0
    scheduled_count = 0
    failed_count = 0
    for s in live_posts:
        for _, entry in (s.get("platforms") or {}).items():
            if not isinstance(entry, dict):
                continue
            st = entry.get("status", "scheduled")
            if st == "posted":
                posted_count += 1
            elif st == "failed":
                failed_count += 1
            else:
                scheduled_count += 1
        try:
            dt = _dt.fromisoformat(s["scheduled_at"])
            if dt > now_local and (next_fire is None or dt < next_fire):
                next_fire = dt
        except Exception:
            pass
    next_fire_label = None
    if next_fire is not None:
        next_fire_label = _fmt_day_12h(next_fire.isoformat(), _tz_suffix)

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "schedules": schedules,
            "last_runs": last_runs,
            "feed": feed,
            "batches": batches,
            "queue": queue,
            "per_post": per_post,
            "live_posts": live_posts,
            "platform_perf": platform_perf,
            "pfm_post_url_base": PFM_POST_URL_BASE,
            "profile_urls": PROFILE_URLS,
            "banner_next_fire": next_fire_label,
            "banner_posted": posted_count,
            "banner_scheduled": scheduled_count,
            "banner_failed": failed_count,
            "tz": schedule_utils._local_tz_abbrev(),
        },
    )


# ---------------------------------------------------------------------------
# LIVE Posts — what's actually scheduled/posted to PFM
# ---------------------------------------------------------------------------
# Hardcoded profile URLs for PRPath's accounts. Used to build "open the real
# post on TikTok/IG/FB/YT" links from the LIVE Posts cards.
PFM_POST_URL_BASE = "https://app.postforme.dev/team_w9J79mBcWzCL0fpU5vb1A/proj_kcj9rexqZnQ70nwgJAnEu/posts"
PROFILE_URLS = {
    "tiktok":    "https://www.tiktok.com/@prpathapp",
    "instagram": "https://www.instagram.com/prpathapp/",
    "facebook":  "https://www.facebook.com/Prpathapp",
    "youtube":   "https://www.youtube.com/@prpath",
}


@app.get("/live-posts.json", response_class=JSONResponse)
async def live_posts_json() -> JSONResponse:
    """Machine-readable LIVE Posts snapshot — used by Claude to inspect state."""
    slots = db.live_slots()
    return JSONResponse({
        "pfm_post_url_base": PFM_POST_URL_BASE,
        "profile_urls": PROFILE_URLS,
        "slots": slots,
    })


@app.get("/platform-health", response_class=HTMLResponse)
async def platform_health(request: Request) -> HTMLResponse:
    """HTML partial — 4 pills (TT/IG/FB/YT) showing connection + token expiry.

    Dot colors: 🟢 connected + >7d left, 🟡 connected + <7d left, 🔴 disconnected.
    """
    import os
    import sys as _sys
    from datetime import datetime as _dt, timezone as _tz
    _sys.path.insert(0, str(runner.GENESIS_ROOT))
    from postforme_client import resolve_prpath_accounts, ENV_PATH, _fetch_all_accounts, filter_accounts_by_brand  # type: ignore
    from dotenv import load_dotenv

    load_dotenv(ENV_PATH)
    api_key = os.environ.get("POSTFORME_API_KEY")
    platforms_order = ["tiktok", "instagram", "facebook", "youtube"]
    platforms: dict[str, dict] = {p: {"status": "red", "note": "not connected"} for p in platforms_order}
    if api_key:
        try:
            raw = _fetch_all_accounts(api_key)
            filtered = filter_accounts_by_brand(raw, "prpathapp")
            now = _dt.now(_tz.utc)
            for acct in filtered:
                platform = (acct.get("platform") or "").replace("_business", "").lower()
                if platform not in platforms:
                    continue
                expiry_raw = acct.get("access_token_expires_at") or ""
                days_left: float | None = None
                try:
                    # dateutil handles 2-digit fractional seconds that Python 3.9 stdlib doesn't
                    from dateutil import parser as _dp  # type: ignore
                    exp = _dp.parse(expiry_raw)
                    days_left = (exp - now).total_seconds() / 86400
                except Exception:
                    days_left = None
                # PFM auto-refreshes access tokens on each call as long as its
                # own status is "connected" and the refresh_token is still valid.
                # So the truth source is PFM's status field — not the short
                # access_token_expires_at which ticks over hourly for YT/TT.
                if acct.get("status") != "connected":
                    platforms[platform] = {"status": "red", "note": "disconnected — reconnect in PFM"}
                elif days_left is not None and days_left < 0:
                    # Access token expired but PFM will transparently refresh on
                    # next call — flag as yellow so we notice if it sticks around.
                    platforms[platform] = {"status": "green", "note": "connected (auto-refresh)"}
                elif days_left is not None and days_left < 2:
                    platforms[platform] = {"status": "yellow", "note": f"token expires in {days_left:.0f}d"}
                else:
                    platforms[platform] = {"status": "green", "note": f"connected · {days_left:.0f}d" if days_left else "connected"}
        except Exception as exc:
            for p in platforms:
                platforms[p] = {"status": "red", "note": f"PFM error: {exc}"}
    return templates.TemplateResponse(
        "partials/platform_health.html",
        {"request": request, "platforms": platforms, "platforms_order": platforms_order},
    )


def _extract_metrics(pfm_response: dict) -> tuple[int, int, int, int]:
    """Pull views/likes/comments/saves from a PFM post detail response.

    PFM returns platform-specific fields nested under a per-platform result
    object. Field names vary (views/video_views/impressions; likes/like_count).
    This extractor is deliberately tolerant — unknown shapes return zeros.
    """
    def _coerce(d: Any, keys: list[str]) -> int:
        if not isinstance(d, dict):
            return 0
        for k in keys:
            v = d.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        return 0

    # Try several likely container paths
    source = pfm_response
    for path_key in ("analytics", "metrics", "insights", "stats"):
        sub = source.get(path_key) if isinstance(source, dict) else None
        if isinstance(sub, dict) and sub:
            source = sub
            break

    views = _coerce(source, ["views", "video_views", "impressions", "reach", "play_count", "view_count"])
    likes = _coerce(source, ["likes", "like_count", "reactions", "heart_count"])
    comments = _coerce(source, ["comments", "comment_count", "replies"])
    saves = _coerce(source, ["saves", "save_count", "bookmarks", "bookmark_count"])
    return views, likes, comments, saves


@app.post("/metrics/refresh")
async def metrics_refresh() -> JSONResponse:
    """Poll PFM for each slot-platform post and snapshot metrics into SQLite."""
    import os
    import sys as _sys
    _sys.path.insert(0, str(runner.GENESIS_ROOT))
    from postforme_client import _http_get, ENV_PATH  # type: ignore
    from dotenv import load_dotenv
    import json as _json

    load_dotenv(ENV_PATH)
    api_key = os.environ.get("POSTFORME_API_KEY")
    if not api_key:
        return JSONResponse({"ok": False, "error": "POSTFORME_API_KEY missing"}, status_code=500)

    pulled = 0
    skipped = 0
    errors: list[str] = []
    slots = db.live_slots()
    for s in slots:
        for platform, entry in (s.get("platforms") or {}).items():
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            # Bundled PFM posts (IG+FB) share an ID — only fetch once per ID per slot.
            pfm_id = entry["id"]
            try:
                resp = _http_get(f"/social-posts/{pfm_id}", api_key)
                v, l, c, sv = _extract_metrics(resp)
                # If PFM exposes per-platform results array, try to pick the matching one.
                results = resp.get("results") or resp.get("platform_results") or []
                if isinstance(results, list):
                    for r in results:
                        if isinstance(r, dict) and (r.get("platform") or "").lower() == platform:
                            v2, l2, c2, sv2 = _extract_metrics(r)
                            v = max(v, v2); l = max(l, l2); c = max(c, c2); sv = max(sv, sv2)
                db.record_post_metrics(
                    s["slot_id"], platform,
                    views=v, likes=l, comments=c, saves=sv,
                    raw=_json.dumps(resp)[:4000],
                )
                pulled += 1
            except Exception as exc:
                errors.append(f"{s['slot_id']}/{platform}: {exc}")
                skipped += 1
    return JSONResponse({
        "ok": True,
        "pulled": pulled,
        "skipped": skipped,
        "errors": errors[:5],
    })


@app.post("/live-posts/refresh-status")
async def live_posts_refresh_status() -> JSONResponse:
    """Poll PFM for current status of every platform post and persist back to SQLite.

    Returns a summary so the UI can show how many flipped scheduled → posted.
    """
    import os
    import sys as _sys
    _sys.path.insert(0, str(runner.GENESIS_ROOT))
    from postforme_client import _http_get, ENV_PATH  # type: ignore
    from dotenv import load_dotenv

    load_dotenv(ENV_PATH)
    api_key = os.environ.get("POSTFORME_API_KEY")
    if not api_key:
        return JSONResponse({"ok": False, "error": "POSTFORME_API_KEY missing"}, status_code=500)

    updated = 0
    unchanged = 0
    errors: list[str] = []
    slots = db.live_slots()
    for s in slots:
        for platform, entry in (s.get("platforms") or {}).items():
            if not isinstance(entry, dict):
                continue
            pfm_id = entry.get("id")
            if not pfm_id:
                continue
            try:
                resp = _http_get(f"/social-posts/{pfm_id}", api_key)
                # PFM status: scheduled|processing|processed|failed
                raw_status = (resp.get("status") or "").lower()
                if raw_status in ("processed", "posted"):
                    new_status = "posted"
                elif raw_status == "failed":
                    new_status = "failed"
                elif raw_status in ("processing", "scheduled"):
                    new_status = raw_status
                else:
                    new_status = entry.get("status", "scheduled")
                if new_status != entry.get("status"):
                    db.update_slot_platform_status(s["slot_id"], platform, new_status)
                    updated += 1
                else:
                    unchanged += 1
            except Exception as exc:
                errors.append(f"{s['slot_id']}/{platform}: {exc}")
    return JSONResponse({
        "ok": True,
        "updated": updated,
        "unchanged": unchanged,
        "errors": errors[:10],
    })


# ---------------------------------------------------------------------------
# Script execution
# ---------------------------------------------------------------------------
@app.post("/run/{script_key}")
async def run(script_key: str) -> JSONResponse:
    if script_key not in runner.SCRIPT_COMMANDS:
        raise HTTPException(400, f"unknown script: {script_key}")
    run_id = await runner.run_script(script_key=script_key, triggered_by="manual")
    return JSONResponse({"run_id": run_id, "script": script_key, "status": "started"})


@app.get("/stream/{run_id}")
async def stream(run_id: int):
    """Server-sent events stream of a running script's output."""
    async def event_generator():
        async for line in runner.stream_run_output(run_id):
            # SSE format
            safe = line.replace("\n", "\\n")
            yield f"data: {safe}\n\n"
        yield "event: close\ndata: done\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/runs/{run_id}/tail", response_class=HTMLResponse)
async def run_tail(request: Request, run_id: int) -> HTMLResponse:
    """HTML partial showing a run's latest output — used by htmx polling."""
    with db.get_db() as d:
        row = d.execute(
            "SELECT script, started_at, finished_at, exit_code, status, output FROM script_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404)
    return templates.TemplateResponse(
        "partials/run_tail.html", {"request": request, "run": dict(row), "run_id": run_id}
    )


# ---------------------------------------------------------------------------
# Activity feed partial (htmx poll)
# ---------------------------------------------------------------------------
@app.get("/feed", response_class=HTMLResponse)
async def feed_partial(request: Request) -> HTMLResponse:
    feed = db.recent_feed(limit=20)
    tz = schedule_utils._local_tz_abbrev()
    tz_suffix = f" {tz}" if tz else ""
    for e in feed:
        if e.get("at"):
            e["at_12h"] = _fmt_12h(e["at"], tz_suffix)
    return templates.TemplateResponse("partials/feed.html", {"request": request, "feed": feed})


# ---------------------------------------------------------------------------
# Script tiles partial (htmx poll)
# ---------------------------------------------------------------------------
@app.get("/tiles", response_class=HTMLResponse)
async def tiles_partial(request: Request) -> HTMLResponse:
    schedules = _enriched_schedules()
    _tz = schedule_utils._local_tz_abbrev()
    _tz_suffix = f" {_tz}" if _tz else ""
    last_runs = {s["script"]: _enrich_last_run(db.last_run_for(s["script"]), _tz_suffix) for s in schedules}
    tz = schedule_utils._local_tz_abbrev()
    return templates.TemplateResponse(
        "partials/tiles.html",
        {"request": request, "schedules": schedules, "last_runs": last_runs, "tz": tz},
    )


# ---------------------------------------------------------------------------
# Batch approval
# ---------------------------------------------------------------------------
@app.post("/batches/{batch_id}/slot/{slot_id}/caption")
async def set_caption(batch_id: str, slot_id: str, caption: str = Form(...)) -> JSONResponse:
    db.set_slot_caption(slot_id, caption)
    return JSONResponse({"ok": True})


@app.post("/batches/{batch_id}/slot/{slot_id}/regen")
async def regen_caption(batch_id: str, slot_id: str) -> JSONResponse:
    """Reroll a single slot's caption to a different Hevy variant."""
    import sys as _sys
    _sys.path.insert(0, str(GENESIS_ROOT))
    from caption_drafter import full_caption
    # Look up the slot's post_id + current variant_seed
    with db.get_db() as d:
        row = d.execute("SELECT post_id, caption FROM slots WHERE slot_id = ?", (slot_id,)).fetchone()
    if not row:
        raise HTTPException(404)
    post_id = row["post_id"]
    # Bump the seed to cycle through variants deterministically
    import random
    new_seed = random.randint(1, 10_000)
    new_caption = full_caption(post_id, variant_seed=new_seed)
    db.set_slot_caption(slot_id, new_caption)
    db.log_event("action", f"Caption regenerated for {post_id}", f"seed={new_seed}", actor="user")
    return JSONResponse({"ok": True, "caption": new_caption})


@app.post("/batches/{batch_id}/draft-all")
async def draft_all(batch_id: str) -> JSONResponse:
    """Auto-draft captions for every slot in a batch using the locked plan."""
    import sys as _sys
    _sys.path.insert(0, str(GENESIS_ROOT))
    from caption_drafter import full_caption
    slots = db.slots_for_batch(batch_id)
    drafted = 0
    for s in slots:
        cap = full_caption(s["post_id"], variant_seed=0)
        db.set_slot_caption(s["slot_id"], cap)
        drafted += 1
    db.log_event("success", f"Drafted {drafted} captions for {batch_id}", "Hevy template + locked tags", actor="user")
    return JSONResponse({"ok": True, "drafted": drafted})


@app.post("/batches/{batch_id}/slot/{slot_id}/approve")
async def approve(batch_id: str, slot_id: str) -> JSONResponse:
    db.approve_slot(slot_id)
    db.log_event("success", f"Slot {slot_id} approved", actor="user")
    return JSONResponse({"ok": True})


@app.post("/batches/{batch_id}/approve-all")
async def approve_all_and_live(batch_id: str) -> JSONResponse:
    """Approve all slots, materialize captions.json from SQLite, kick PFM scheduling.

    Respects POSTFORME_DRY_RUN in .env — nothing actually posts unless that's false.
    """
    import json
    slots = db.slots_for_batch(batch_id)
    for s in slots:
        db.approve_slot(s["slot_id"])
    db.set_batch_status(batch_id, status="approval_queue", notes="approved by user")
    db.log_event("action", f"Batch {batch_id}: approved {len(slots)} slots", actor="user")

    # Materialize captions from SQLite → captions.json on disk.
    # The resume path reads {"captions": {slot_id: caption, ...}} from this file.
    batch_dir = GENESIS_ROOT / "picks" / "_batches" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    captions_path = batch_dir / "captions.json"
    captions_payload = {"captions": {s["slot_id"]: (s["caption"] or "") for s in slots}}
    captions_path.write_text(json.dumps(captions_payload, indent=2), encoding="utf-8")
    db.log_event("info", f"captions.json written for {batch_id}", f"{len(slots)} slots", "system")

    # Validate minimum caption length to avoid resume-time hard-fail (each must be >= 20 chars)
    too_short = [s["slot_id"] for s in slots if not s.get("caption") or len(s["caption"]) < 20]
    if too_short:
        db.log_event("error", f"Approve-all blocked: {len(too_short)} captions < 20 chars", ", ".join(too_short), "system")
        return JSONResponse(
            {"ok": False, "error": "Some captions are empty or <20 chars. Fix them before approving.", "slots": too_short},
            status_code=400,
        )

    extra = ["--resume", batch_id]
    run_id = await runner.run_script(
        script_key="run_batch_resume",
        extra_args=extra,
        triggered_by="user",
    )
    return JSONResponse({"ok": True, "run_id": run_id})


# ---------------------------------------------------------------------------
# Schedule editing
# ---------------------------------------------------------------------------
@app.post("/schedule/{script}/toggle")
async def schedule_toggle(script: str, enabled: bool = Form(...)) -> JSONResponse:
    db.set_schedule_enabled(script, enabled)
    db.log_event("info", f"Schedule {script} {'enabled' if enabled else 'disabled'}", actor="user")
    return JSONResponse({"ok": True, "enabled": enabled})


@app.post("/schedule/{script}/cron")
async def schedule_cron(script: str, cron_expr: str = Form(...)) -> JSONResponse:
    # Validate first so bad input doesn't write
    try:
        schedule_utils.parse_cron(cron_expr)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": f"invalid cron: {exc}"}, status_code=400)
    db.set_schedule_cron(script, cron_expr)
    db.log_event("info", f"Schedule {script} cron updated → {cron_expr}", actor="user")

    # If this script already has a launchd plist installed, re-install so the new cron takes effect.
    label = f"{schedule_utils.LABEL_PREFIX}.{script}"
    plist_path = schedule_utils.LAUNCH_AGENTS_DIR / f"{label}.plist"
    if plist_path.exists():
        argv = list(runner.SCRIPT_COMMANDS.get(script, []))
        if argv and argv[0] == "python3":
            argv = ["/usr/bin/env", "python3", *argv[1:]]
        ok, msg = schedule_utils.install_plist(script, argv, cron_expr)
        db.log_event(
            "info" if ok else "warn",
            f"launchd re-installed for {script}" if ok else f"launchd re-install failed for {script}",
            msg, actor="system",
        )
    return JSONResponse({"ok": True, "cron_expr": cron_expr})


@app.post("/schedule/{script}/install-launchd")
async def install_launchd(script: str) -> JSONResponse:
    """Write + load a launchd plist so this script auto-fires on its cron."""
    cfg = next((s for s in db.get_schedule_config() if s["script"] == script), None)
    if not cfg:
        return JSONResponse({"ok": False, "error": "unknown script"}, status_code=404)
    cron = cfg.get("cron_expr") or ""
    if not cron:
        return JSONResponse({"ok": False, "error": "no cron set"}, status_code=400)
    argv = list(runner.SCRIPT_COMMANDS.get(script, []))
    if not argv:
        return JSONResponse({"ok": False, "error": "no command for script"}, status_code=400)
    if argv[0] == "python3":
        argv = ["/usr/bin/env", "python3", *argv[1:]]
    ok, msg = schedule_utils.install_plist(script, argv, cron)
    db.log_event(
        "success" if ok else "error",
        f"launchd {'installed' if ok else 'install failed'} for {script}",
        msg, actor="user",
    )
    db.set_schedule_enabled(script, ok)
    return JSONResponse({"ok": ok, "message": msg})


@app.post("/schedule/{script}/uninstall-launchd")
async def uninstall_launchd(script: str) -> JSONResponse:
    ok, msg = schedule_utils.uninstall_plist(script)
    db.log_event(
        "info" if ok else "warn",
        f"launchd {'uninstalled' if ok else 'uninstall failed'} for {script}",
        msg, actor="user",
    )
    db.set_schedule_enabled(script, False)
    return JSONResponse({"ok": ok, "message": msg})


# ---------------------------------------------------------------------------
# Slide image serving (so the approval page can show them)
# ---------------------------------------------------------------------------
@app.get("/slide")
async def slide(path: str) -> FileResponse:
    """Serve a slide image. Path is validated to live inside the PRPathShots samples dir."""
    allowed_root = Path("/Users/lancesessions/Developer/PRPathShots/samples").resolve()
    resolved = Path(path).resolve()
    if not str(resolved).startswith(str(allowed_root)):
        raise HTTPException(403, "path outside allowed sandbox")
    if not resolved.is_file():
        raise HTTPException(404, "slide not found")
    return FileResponse(resolved)


# ---------------------------------------------------------------------------
# Health / version
# ---------------------------------------------------------------------------
@app.get("/healthz", response_class=JSONResponse)
async def healthz() -> JSONResponse:
    return JSONResponse({"ok": True, "version": "0.1.0"})
