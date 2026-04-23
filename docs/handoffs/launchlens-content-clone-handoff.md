# LaunchLens Content Pipeline — Handoff from PRPath Content

**For:** Windows Claude working on LaunchLens
**From:** Mac Claude (prpath-content session, 2026-04-23)
**Purpose:** Clone the PRPath Content dashboard + pipeline for LaunchLens. 95% of the engineering is the same — only the content generation layer changes.

---

## Read this first

This doc describes a working, live-tested social content pipeline that shipped 9 posts × 4 platforms = 27 live scheduled posts on 2026-04-23 for PRPath. Lance wants the same system for LaunchLens but with LaunchLens-themed content.

**The reference implementation is at** `/Users/lancesessions/Developer/prpath-content/` on Mac. All code mentioned below is in that folder. If you need to see actual source, ask Lance to share specific files.

---

## What to clone (95% identical)

1. **FastAPI + Jinja2 + htmx + Tailwind dashboard** at `localhost:8081` (use a different port from PRPath's 8080 so you can run both)
2. **SQLite (WAL mode) backend** — `pipeline.db` with tables: `batches`, `slots`, `post_metrics`, `feed_events`, `schedule_config`, `script_runs`
3. **Post for Me (PFM) v1 integration** — at `api.postforme.dev` (note: `.dev` not `.com`)
4. **FFmpeg video stitcher** — PNGs → 1080x1920 MP4, 2.5s per slide, cut transition, random music from `assets/music/`
5. **launchd (Mac) or Task Scheduler (Windows) auto-fire** for scheduled scripts
6. **Per-platform posting strategy**:
   - **TikTok**: photo carousel (2 PNGs), native auto-music
   - **Instagram**: Reel (stitched MP4 w/ baked music)
   - **Facebook**: Reel (same MP4, bundled w/ IG in one PFM call)
   - **YouTube**: Short (same MP4, explicit title from first caption line)

## What changes (5% different)

1. **Content generation** — LaunchLens is a YouTube optimization SaaS, not a fitness app
   - Captions, hashtags, hooks, anchors all need LaunchLens-specific taxonomy
2. **Brand filter** in PFM — probably `launchlens` as username/external_id (verify in PFM dashboard)
3. **CPP URL** — LaunchLens is a web SaaS, not an App Store app
   - Replace `apps.apple.com/app/id...` with `launchlens.tech` or `app.launchlens.tech`
   - No need for Custom Product Page variants (A/B/C/...)
4. **Post frequency / cadence** — confirm with Lance (PRPath does 2x/week batches = 18 posts/week)

---

## Architecture (one picture)

```
Pipeline Scripts          SQLite (pipeline.db)      Dashboard (FastAPI)
 preflight ────┐          ┌─ batches              localhost:8081
 scout         │          │  slots                 ├─ Overview (tiles)
 run_batch     ├──writes─▶│  post_metrics ◀──reads─┤  LIVE Posts
 verify        │          │  feed_events           │  Approval Queue
 metrics_pulse ┘          │  schedule_config       │  Metrics
                          │  script_runs           │  Schedule
                          └────────────────────────┘  Feed

Stitch + Upload + Post flow:
 slide_01.png ──┐
 slide_02.png ──┤──▶ video_stitcher ──▶ 5s MP4 ──▶ upload_media ──▶ PFM URL
               │                                                       │
               │                                                       ▼
               └──▶ upload_media ──▶ PNG URLs ─┐                 /social-posts
                                               │                       │
                                               ▼                       ▼
                                        4 parallel PFM calls    scheduled on PFM
                                        (TT / IG+FB / YT)              │
                                                                       ▼
                                                        TT / IG / FB / YT native APIs
                                                        at scheduled_at time
```

---

## File-by-file guide

### `postforme_client.py` (core PFM client — ~600 lines)

**What it does:**
- Loads `.env` (POSTFORME_API_KEY, POSTFORME_DRY_RUN)
- `resolve_prpath_accounts()` — fetches all accounts, filters by brand (username or external_id matching), returns `{platform: spc_id}` dict
- `upload_media()` — 2-step PFM signed URL flow: POST `/media/create-upload-url` → PUT to signed URL
- `stitch_slides_to_video()` — thin wrapper calling `video_stitcher.stitch_video()` with `transition="cut"`, `slide_duration=2.5`, random music
- `_schedule_single_slot()` — orchestrates one slot: stitch video → upload video + slides → 3 parallel PFM post calls (TT, IG+FB bundled, YT)
- CLI: `python3 postforme_client.py schedule --manifest <path> --slot <slot_id> --live`

**Locked TikTok payload** (tonight's bugfix):
```python
"platform_configurations": {
    "tiktok": {
        "title": first_line[:90],          # short hook, no URL, no emoji
        "privacy_status": "public",
        "auto_add_music": True,            # native TT track; False = no sound bug
    }
}
# caption field = hashtags only (URL stripped; TT caption doesn't clickable links)
```

**Bundled IG+FB** (single PFM call, two `social_accounts`):
```python
body = {
    "caption": full_caption,               # WITH CPP URL — FB makes them clickable
    "social_accounts": [ig_spc_id, fb_spc_id],
    "media": [{"url": mp4_url}],
}
```

**YouTube**:
```python
"platform_configurations": {
    "youtube": {"title": first_line[:100], "privacy_status": "public"}
}
```

**Env flags for surgical re-fires** (critical for debugging):
- `PFM_ONLY_TIKTOK=1` — skips everything except TikTok
- `PFM_ONLY_FB=1` — skips everything except FB (used when IG posted OK but FB failed perm)

**Auto-write to SQLite after fire:**
```python
from dashboard import db
db.set_slot_pfm_ids(slot_id, post_ids, scheduled_at=scheduled_at)
```
This populates the LIVE Posts dashboard tab.

### `video_stitcher.py` (FFmpeg glue — ~200 lines)

**Critical scale/pad filter** (handles mixed slide aspect ratios):
```
[{i}:v]scale=1080:1920:force_original_aspect_ratio=decrease,
        pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=#1C1C1E,setsar=1[v{i}]
```
**Why this specifically**: Designed slides are 1080x1350 (4:5) but iPhone screenshots can be 1206x2622 (taller than 9:16). The `decrease` fit + pad-to-1080x1920 gracefully handles both without crashes like "Could not open encoder before EOF".

**Transition modes:**
- `"cut"` — concat filter, abrupt (our default — 2.5s/slide × 2 = 5s total)
- `"fade"` — xfade filter (0.5s crossfade; for longer videos)

**Music picker** (`pick_music(genre=None)`):
```python
# Reads assets/music/*.mp3 — files named {genre}_{nn}.mp3
# Genres we use: hype, upbeat, chill
# Volume: 50% (MUSIC_VOLUME=0.5), fade out last 1.5s
```

### `dashboard/server.py` (FastAPI — ~500 lines)

**Routes:**
- `GET /` — home page (dashboard.html)
- `GET /tiles` — htmx partial for auto-refreshing pipeline script tiles
- `GET /feed` — htmx partial for activity feed
- `GET /platform-health` — htmx partial for TT/IG/FB/YT connection pills
- `POST /run/{script_key}` — spawn a pipeline script as subprocess
- `GET /stream/{run_id}` — SSE stream of subprocess output
- `GET /runs/{id}/tail` — HTML log viewer
- `POST /batches/{batch_id}/slot/{slot_id}/approve` — mark slot approved
- `POST /batches/{batch_id}/approve-all` — bulk approve
- `POST /live-posts/refresh-status` — polls PFM for every scheduled post, updates SQLite
- `POST /metrics/refresh` — polls PFM `/social-posts/{id}` for analytics (empty for now since PFM doesn't expose native metrics)
- `GET /live-posts.json` — machine-readable snapshot (for Claude inspection)
- `GET /healthz` — `{"ok": true}` liveness

**Hardcoded profile URLs** (update for LaunchLens):
```python
PFM_POST_URL_BASE = "https://app.postforme.dev/team_<TEAM>/proj_<PROJ>/posts"
PROFILE_URLS = {
    "tiktok":    "https://www.tiktok.com/@<HANDLE>",
    "instagram": "https://www.instagram.com/<HANDLE>/",
    "facebook":  "https://www.facebook.com/<HANDLE>",
    "youtube":   "https://www.youtube.com/@<HANDLE>",
}
```

### `dashboard/db.py` (SQLite helpers — ~450 lines)

**Schema** (all tables defined in the `SCHEMA` constant — `ensure_schema()` is idempotent, safe to call on every startup):
- `batches`: batch_id PK, target (sun/wed), status (planning|awaiting_captions|approval_queue|scheduled|failed), manifest_path
- `slots`: slot_id PK, batch_id FK, scheduled_at, post_id, feature_anchor, caption, approved, pfm_post_ids (JSON blob: `{platform: {id, status, fired_at}}`), status
- `post_metrics`: slot_id FK, platform, views/likes/comments/saves, pulled_at, raw (JSON blob)
- `feed_events`: activity stream
- `script_runs`: subprocess execution history

**Key helpers to copy exactly:**
- `set_slot_pfm_ids(slot_id, post_ids, scheduled_at)` — merges PFM IDs into slot, auto-promotes batch status to 'scheduled' when all slots have IDs
- `update_slot_platform_status(slot_id, platform, status)` — flips one platform's status (scheduled/posted/failed)
- `live_slots(batch_id=None)` — returns slots with parsed platforms JSON; used by LIVE Posts tab
- `per_platform_perf()` — LEFT JOIN against post_metrics; returns 1 row per (slot, platform) for the Content Performance table

### `dashboard/runner.py` (subprocess spawner — ~160 lines)

**Hardcoded allowlist** (prevents shell injection from user button clicks):
```python
SCRIPT_COMMANDS = {
    "preflight":     ["python3", "preflight.py"],
    "scout":         ["python3", "scout.py"],
    "verify":        ["python3", "verify.py"],
    "metrics_pulse": ["python3", "metrics_pulse.py"],
    "run_batch_sun": ["python3", "run_batch.py", "--target", "sun", "--dry-run"],
    "run_batch_wed": ["python3", "run_batch.py", "--target", "wed", "--dry-run"],
    # ...
}
```
Uses `asyncio.create_subprocess_exec` (list argv, no shell).

### `dashboard/templates/`

- `base.html` — page shell, top nav with brand + platform health strip + UP indicator
- `dashboard.html` — main page with all sections (banner, tabs, tiles grid, LIVE Posts, Approval Queue, Content Performance, Schedule Config, Data Sources, Feed)
- `partials/tiles.html` — 9 pipeline script tiles (auto-refresh 4s via htmx)
- `partials/feed.html` — activity stream (auto-refresh 5s)
- `partials/platform_health.html` — 4 pills showing TT/IG/FB/YT connection status

---

## Dashboard tabs + what each shows

| Tab | Purpose |
|---|---|
| **Overview** | Pipeline Scripts tiles + cron status. Each tile = one script (preflight, scout, run_batch, verify, metrics_pulse). Click "Run now" to spawn. |
| **LIVE Posts** | 9 slot cards × 4 platform chips. Shows fire status (🕐/🟢/❌) + PFM link + profile link per chip. "Refresh status" polls PFM for current state. |
| **Approval Queue** | New batches needing caption approval before LIVE. Per-slot edit + bulk approve. |
| **Metrics** | Content Performance table. Filter chips by Platform + Anchor. Color-coded engagement %. "Pull metrics" button. |
| **Schedule** | Cron config for launchd. Toggle AUTO ON per script. Edit cron expressions. |
| **Data Sources** | Reference panel — paths to SQLite, Obsidian reports, manifests, slide sources, launchd plists. |
| **Feed** | Activity stream — runs, approvals, fires, batch imports. |

---

## Per-platform gotchas (discovered tonight — all fixed)

### TikTok
1. **Duplicate title/description bug**: If you only pass `caption`, PFM puts it in BOTH title and description fields. Fix: explicit `platform_configurations.tiktok.title = short_hook`.
2. **Two songs playing**: `auto_add_music: false` kills music entirely. Must be `true` to get TikTok's native track.
3. **CPP URL ugly**: TikTok captions don't have clickable links. Strip `apps.apple.com` / any URL from the TikTok caption. Keep in bio.
4. **CAPTCHA on public scrape**: can't programmatically read public profile. Must use TikTok Studio (logged-in).

### Instagram + Facebook (bundled)
1. **One PFM call, two platforms**: pass `social_accounts: [ig_id, fb_id]`. PFM splits into separate IG + FB posts behind the scenes.
2. **FB permissions scope**: PFM's FB OAuth must include `pages_manage_posts`. If missing, FB fails silently with `pages_read_engagement...` error.
3. **FB Reels don't show in timeline**: they live under the Reels tab on the Page. Users expect timeline — explain this.
4. **Meta Business Suite view**: switch to the correct Page portfolio (default shows the most recent business).

### YouTube
1. **Needs explicit title**: `platform_configurations.youtube.title = first_line[:100]`. Without this, title is weird.
2. **Public scrape works**: `youtube.com/channel/<ID>/shorts` shows video thumbnails + view counts. Studio requires auth.

---

## The "never lose context" stack (copy this too)

Create for LaunchLens:

1. **Project `CLAUDE.md`** at repo root — what the project is, architecture, gotchas, current campaign
2. **Slash command** `~/.claude/commands/launchlens-content.md` — same structure as `prpath-content.md`, rehydrates context
3. **In-repo session log** — `logs/sessions/YYYY-MM-DD.md` at each session end. Next session reads it first.
4. **Memory entry** — `project_launchlens_content_repo.md` in `~/.claude/projects/.../memory/`, index it in `MEMORY.md`

---

## Step-by-step build plan (for Windows Claude)

### Phase 1 — Scaffold (half a day)
1. `cp -r /path/to/prpath-content /path/to/launchlens-content` (or clone via git)
2. Purge PRPath-specific data: `rm pipeline.db picks/_batches/* captions/*`
3. Replace brand: `grep -rln "prpathapp" . | xargs sed -i '' 's/prpathapp/launchlens/g'`
4. Update CPP URLs in `postforme_client.py`: replace `apps.apple.com/app/id6755009279` dict with `launchlens.tech` or remove anchor routing entirely if not needed
5. Update profile URLs in `dashboard/server.py` (PROFILE_URLS dict)
6. Update PFM team/proj IDs in `PFM_POST_URL_BASE` — get fresh ones from LaunchLens's PFM dashboard
7. Port 8080 → 8081 (or whatever) to avoid collision with PRPath dashboard
8. Start dashboard: `python3 -m uvicorn dashboard.server:app --port 8081 --reload`

### Phase 2 — Connect PFM (half a day)
1. Grab PFM API key — likely same one as PRPath (shared across Lance's brands, filtered by username)
2. Run `python3 postforme_client.py accounts --brand launchlens` — verify all 4 platforms show up as `connected`
3. If FB shows disconnected or missing scopes → reconnect in PFM with full page permissions
4. Dry-run a single slot: `python3 postforme_client.py schedule --manifest picks/_batches/<batch>/manifest.json --slot <slot_id>` (no `--live`)

### Phase 3 — Content generation (1-2 days)
This is the biggest delta from PRPath. You'll need:
1. **LaunchLens post inventory** — hooks and captions, likely YouTube-metadata-themed
2. **Feature anchors** — map of product pillars (e.g. A/B/C for different LaunchLens features)
3. **Slide design** — background, typography, layout for 1080x1350 PNGs (use PRPath's `PRPathShots/samples/posts_v2/` folder structure as template)
4. **Caption drafter** — port `caption_drafter.py` but rewrite INVENTORY dict with LaunchLens posts

### Phase 4 — Launch day (half a day)
1. Generate first batch: `python3 run_batch.py --target wed` (or whatever day works)
2. Approve all in dashboard (or individually)
3. Fire 1 test slot LIVE: `python3 postforme_client.py schedule ... --slot <slot_id> --live`
4. Check PFM dashboard — all 4 platforms should show scheduled
5. If anything broken, check `postforme_client.py` TikTok config (the tonight's fixes)
6. Fire remaining 8 slots

### Phase 5 — Context infra (1 hour)
- Write project CLAUDE.md
- Add slash command
- First session log
- Memory entry

---

## What not to copy from PRPath

- `evolution/` folder — that was the old "Genesis" AI content evolution experiment. Retired. Don't bring it over.
- `assets/music/` — need LaunchLens-appropriate music (SaaS marketing vibe vs. gym hype)
- `picks/_batches/prpath-batch-*` — historical, not useful for LaunchLens
- PRPath-specific feature anchors (A=Atlas, B=Recovery, C=PR Graph, etc.)
- App Store CPP URL routing — not applicable to web SaaS

---

## Open questions for Lance (ask before building)

1. **Cadence** — same 2x/week (Sun + Wed) or different?
2. **Platforms** — same 4 (TT/IG/FB/YT) or subset?
3. **Post count** — same 9 per batch or different?
4. **Content type** — LaunchLens is a SaaS tool. Is the content educational (tutorials, tips) or brand-focused (case studies, testimonials)?
5. **Slide format** — iPhone-style mockups (like PRPath) or desktop/browser mockups?
6. **Account handles** — need TikTok/IG/FB/YT usernames for LaunchLens to update profile URLs
7. **PFM separate project** — does LaunchLens have its own PFM project, or shared with PRPath under different brand?

---

## Quick reference: key env vars + API patterns

```bash
# .env
POSTFORME_API_KEY=pfm_live_...       # shared across Lance's brands
POSTFORME_DRY_RUN=false               # flip to true for dry runs
GEMINI_API_KEY=AIzaSy...              # for caption generation if using Gemini

# CLI patterns
python3 postforme_client.py accounts --brand launchlens
python3 postforme_client.py schedule --manifest <path> --slot <slot_id> --live
PFM_ONLY_TIKTOK=1 python3 postforme_client.py schedule ... --live  # test TT only
PFM_ONLY_FB=1 python3 postforme_client.py schedule ... --live      # retry FB after perm fix

# PFM API endpoints used (at api.postforme.dev — note .dev not .com)
GET  /social-accounts?status=connected&limit=50
POST /media/create-upload-url          # returns signed URL for PUT
POST /social-posts                      # create a scheduled post
GET  /social-posts/{id}                 # get status + (sparse) analytics

# PFM account brand filter (case-insensitive match on username OR external_id)
def _brand_matches(account, brand):
    b = brand.lower()
    return (account.get("username", "").lower() == b or
            account.get("external_id", "").lower() == b)
```

---

## Numbers to calibrate expectations

First PRPath post (Thu 10am Apr 23, 2026) — ~90 min after fire:
- **TikTok**: 198 views ✅ (distribution winner)
- **Facebook**: 27 reach, 30s watch
- **Instagram**: 1 reach (IG cold-start is harsh)
- **YouTube**: 0 views (new channel, no subs)

**Implication**: Expect TikTok to be 10-100x IG in early weeks. Plan content for TikTok primarily; IG is slow-build.

---

## Session log for today (for reference)

See `/Users/lancesessions/Developer/prpath-content/logs/sessions/2026-04-23.md` for the full story of what was built, what broke, and how each issue was fixed. Worth reading if you want deeper context on the PRPath-side decisions before cloning.

---

**You have everything needed to get to ~95% in a day or two. The remaining 5% is tuning the content generation layer for LaunchLens brand voice.**

Ask Lance the 7 questions above, then start with Phase 1.
