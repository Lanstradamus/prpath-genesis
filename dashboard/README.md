# PRPath Pipeline Dashboard

Local web dashboard for running + monitoring the PRPath content pipeline scripts.
FastAPI backend, htmx + Tailwind frontend, SQLite state. No Claude API — any AI
tasks (caption drafting, vision gates) pause and are completed in Claude Code CLI.

## Quick start

### Manual run (development / testing)
```bash
cd /Users/lancesessions/Developer/prpath-genesis
python3 -m uvicorn dashboard.server:app --host 127.0.0.1 --port 8080 --reload
```
Then open <http://localhost:8080> in a browser.

### Auto-start on login (launchd)
```bash
# install
cp dashboard/launchd/app.prpath.dashboard.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/app.prpath.dashboard.plist

# check status
launchctl list | grep prpath.dashboard

# stop / reload
launchctl unload ~/Library/LaunchAgents/app.prpath.dashboard.plist
launchctl load   ~/Library/LaunchAgents/app.prpath.dashboard.plist
```

## What works in v0.1

- ✅ All 8 pipeline script tiles (preflight / scout / verify / metrics_pulse / sunday_recap / run_batch_sun / run_batch_wed / midweek_pulse + monthly_retro)
- ✅ Click "Run now" → spawns subprocess, output captured to SQLite
- ✅ Activity feed auto-refreshes every 5s
- ✅ Tile status auto-refreshes every 4s (OK / WARN / ERR / running)
- ✅ `/runs/<id>/tail` — full script output view
- ✅ Schedule config table (enable/disable toggles persist to SQLite)
- ✅ Approval queue renders when `run_batch.py` has populated batches (empty otherwise)
- ✅ Per-post metrics table (populated by `verify.py` + `metrics_pulse.py` once posts exist)

## What's coming in v0.2

- launchd plist auto-generation from schedule_config table → actual auto-firing
- SSE live-streaming terminal output (currently uses HTTP polling)
- Gemini 3 Pro raw-POV slide generation button
- Mac notifications via osascript
- CSV export
- Metrics charts (Chart.js)
- Inline caption drafting via Claude Code CLI integration

## Files

| Path | Purpose |
|---|---|
| `dashboard/server.py` | FastAPI routes |
| `dashboard/db.py` | SQLite helpers + schema |
| `dashboard/runner.py` | Safe subprocess spawner + output capture |
| `dashboard/templates/base.html` | Shared shell + Tailwind config |
| `dashboard/templates/dashboard.html` | Main page |
| `dashboard/templates/partials/*.html` | htmx-swappable fragments |
| `dashboard/launchd/app.prpath.dashboard.plist` | macOS auto-start config |
| `pipeline.db` | SQLite state (at genesis root, not `dashboard/`) |

## Security notes

- Binds to `127.0.0.1` only — not accessible from other machines without Tailscale / port forwarding
- Script invocation uses a hardcoded allowlist (`runner.SCRIPT_COMMANDS`) — user input never becomes argv
- Slide image endpoint validates paths against the PRPathShots samples sandbox
- `.env` is gitignored; Telegram creds live in `notify.py` (not `.env`)

## Troubleshooting

- **Port 8080 already in use:** another instance is running. `lsof -i :8080` to find, `kill <pid>` to stop.
- **Tile status shows "never run":** click the tile's "Run now" button.
- **htmx updates stop:** refresh the page; check `dashboard.error.log` for exceptions.
- **Subprocess hangs:** check that the underlying script isn't waiting for stdin.
