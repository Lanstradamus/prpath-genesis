# PRPath Content — Project Context

This file loads automatically when Claude works in `/Users/lancesessions/Developer/prpath-content/`.
Read it fully before doing anything. It's the project-local override of the global CLAUDE.md.

---

## What This Is

The **PRPath Content** pipeline — generates, schedules, fires, and measures social media posts for the PRPath iOS fitness app. Runs locally at `localhost:8080`.

**Not to be confused with PRPath the app itself** — that code lives at `/Users/lancesessions/app-portfolio/apps/prpath/code/PRPath/` (SwiftUI iOS project). This directory is *about* promoting that app, not building it.

**Old name (retired 2026-04-23):** `prpath-genesis` — don't reference it. Folder was renamed to `prpath-content` to remove confusion with the "Genesis" experiment.

---

## Goal

Generate 18 posts/week (9 slots × 2 days of the week: Sunday batch covers Mon/Tue/Wed, Wednesday batch covers Thu/Fri/Sat) and distribute each across 4 platforms (TikTok, Instagram, Facebook, YouTube). Measure what's performing. Iterate weekly.

**Success metric:** paid installs of PRPath attributable to content. Long-term lever: pick anchors/hooks that convert best.

---

## Architecture

```
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│ Pipeline Scripts │────▶│ SQLite (state)   │◀────│ Dashboard (FastAPI)│
│ preflight, scout │     │ pipeline.db       │     │ localhost:8080    │
│ run_batch, verify│     │ batches / slots   │     │ tabs, live posts  │
│ metrics_pulse    │     │ post_metrics      │     │ platform health   │
└──────────────────┘     └──────────────────┘     └──────────────────┘
         │                                                  │
         ▼                                                  ▼
  ┌──────────────┐                               ┌──────────────────┐
  │ video_stitch │                               │ postforme_client │
  │ 1080x1920,   │──── slide PNGs ─────┐         │ upload → schedule│
  │ 2.5s/slide,  │                     │         │ to 4 platforms   │
  │ random music │                     ▼         └──────┬───────────┘
  └──────────────┘             ┌──────────────┐         │
                               │ Post for Me  │◀────────┘
                               │ api.postforme│
                               │ .dev         │
                               └──────┬───────┘
                                      │
                                      ▼
                      TikTok / Instagram / Facebook / YouTube
```

**Data sources:**
- `pipeline.db` — SQLite with WAL. Source of truth for batch state, slots, PFM IDs, metrics.
- `picks/_batches/<batch_id>/manifest.json` — Per-batch plan: 9 slots, captions, scheduled_at.
- `/Users/lancesessions/Developer/PRPathShots/samples/posts_v2/<post_id>/` — Pre-rendered slide PNGs (outside this repo).
- `.env` — API keys (POSTFORME_API_KEY, GEMINI_API_KEY). Never commit.

---

## Dashboard Tabs

- **Overview** — pipeline script tiles + scheduled cron status
- **LIVE Posts** — 9 slots × 4 platforms, shows status + PFM link + profile link per chip
- **Approval Queue** — new batches needing captions/approval before firing
- **Metrics** — Content Performance table with per-platform engagement + filters
- **Schedule** — cron config for launchd auto-fire
- **Data Sources** — pointers to manifests, posted archive, Obsidian, slide library
- **Feed** — activity stream (runs, approvals, fires)

---

## Per-Platform Posting Behavior

Locked 2026-04-23 after live debugging tonight's first batch:

| Platform | Media | Caption handling | Music |
|---|---|---|---|
| **TikTok** | Photo carousel (2 PNGs) | `title` = short hook; `caption` = hashtags only, URL stripped | `auto_add_music: true` (native TikTok track) |
| **Instagram** | Reel (5s stitched MP4) | Full caption including CPP URL | Baked-in via video_stitcher |
| **Facebook** | Reel (same MP4 as IG — bundled PFM call) | Same as IG | Same |
| **YouTube** | Short (same MP4) | Full caption + title (first line cap 100 chars) | Same |

**Env flags for testing:**
- `PFM_ONLY_TIKTOK=1` — fire TikTok only (skips stitching)
- `PFM_ONLY_FB=1` — fire Facebook only (for retry after permission fix)

---

## Known Gotchas

- **TikTok CAPTCHA** blocks public profile scraping. Use TikTok Studio (logged-in) instead.
- **Meta Business Suite** requires switching to the `prpathapp` portfolio — default opens on `coachlances`.
- **PFM tokens** expire hourly for TT/YT but auto-refresh — red strip color does NOT mean broken.
- **IG+FB bundled**: one PFM post ID covers both platforms. Re-firing just FB uses `PFM_ONLY_FB=1`.
- **CPP URL**: stripped from TikTok caption only (ugly there, bio link covers it). Kept on IG/FB/YT.

---

## Quick Commands

```bash
# Start dashboard
cd /Users/lancesessions/Developer/prpath-content
python3 -m uvicorn dashboard.server:app --host 127.0.0.1 --port 8080 --reload

# Fire a single slot (live)
python3 postforme_client.py schedule --manifest picks/_batches/<batch>/manifest.json --slot <slot_id> --live

# FB-only retry (after bundled post failed)
PFM_ONLY_FB=1 python3 postforme_client.py schedule --manifest ... --slot ... --live

# Inspect SQLite state
sqlite3 pipeline.db ".schema slots"
sqlite3 pipeline.db "SELECT slot_id, status FROM slots WHERE batch_id LIKE 'prpath-batch-%' ORDER BY slot_index;"

# Restart dashboard after pulling changes
pkill -f "uvicorn dashboard.server" ; sleep 1 ; python3 -m uvicorn dashboard.server:app --host 127.0.0.1 --port 8080 --reload &
```

---

## Current Campaign — Week 2 (2026-04-20 to 2026-04-25)

- **Batch**: `prpath-batch-20260422-wed` (Thu/Fri/Sat posts)
- **Status**: 9 slots LIVE-scheduled to PFM across 4 platforms
- **First fire**: Thu Apr 23 10am CT — `11_mfp_tedious` ("Tracking macros on a cut isn't 7 minutes per meal if you download PRPath 💪")
- **Early data (90min in)**: TikTok 198 views, FB 27 reach, IG 1 reach, YT 0 views. TikTok is the clear distribution winner.
- **Remaining fires**: Thu 3pm (01_atlas_skip_leg), Thu 8pm (16_what_to_train), Fri ×3, Sat ×3.

---

## Rituals

- **Sunday 10am CT** — "Draft Next Batch" (Mon/Tue/Wed posts). Review captions, approve, fire.
- **Wednesday 10am CT** — Same ritual for Thu/Fri/Sat posts.
- **Sunday 9am CT** — Weekly metrics pull (Claude via chrome scrape of TT Studio + Meta Business Suite + YT Studio).
- **Whenever curious** — open `localhost:8080`, check LIVE Posts tab.

## Session Continuity

Claude logs every session to `logs/sessions/YYYY-MM-DD.md` with:
- What happened
- Numbers pulled
- Decisions locked
- Open threads for next session

When starting a new conversation, type `/prpath-content` — Claude reads this file + the most recent session log + current SQLite state, then reports a brief.

---

**Last updated:** 2026-04-23 by Claude during folder rename session
