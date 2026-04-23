#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Post for Me API client for the PRPath pipeline.

CLI subcommands:
    accounts                        List social accounts filtered to a brand.
    schedule --manifest <path>      Schedule each slot in a batch manifest.

Reads POSTFORME_API_KEY from /Users/lancesessions/Developer/prpath-genesis/.env

Safety defaults:
    - `schedule` runs --dry-run by default. Must pass --live to actually POST.
    - Brand filter defaults to `prpathapp`; other brands are ignored in
      `accounts` output and rejected for `schedule`.
    - API keys are NEVER printed (not in tracebacks, not in dry-run output).

Dependencies: requests, python-dotenv
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

_URL_RE = re.compile(r"https?://\S+")


def _strip_url_lines(text: str) -> str:
    """Remove any line containing a URL. TikTok captions can't render links."""
    if not text:
        return text
    return "\n".join(ln for ln in text.splitlines() if not _URL_RE.search(ln)).strip()

# ---------------------------------------------------------------------------
# Feature-anchor → Custom Product Page URL (App Store)
# Per [[Claude Handoff]] 2026-04-15 spec — routes CTA traffic to the right CPP.
# ---------------------------------------------------------------------------
CPP_URLS: dict[str, str] = {
    "default":  "https://apps.apple.com/app/id6755009279",
    "ai_coach": "https://apps.apple.com/app/id6755009279?ppid=beeeccff-75c0-460e-9364-15cd99af83c8",
    "macros":   "https://apps.apple.com/app/id6755009279?ppid=4789d995-7f67-4401-8421-e33eec09de90",
    "fasting":  "https://apps.apple.com/app/id6755009279?ppid=e211f9c6-4905-4e9a-9284-ec8543d609f3",
}

# Feature anchor → CPP bucket. A (Atlas), B (Recovery), C (PR graph),
# D (Strength Score), F (Today dashboard), G (women-angled) → ai_coach.
# E (Photo food log) → macros.
ANCHOR_TO_CPP: dict[str, str] = {
    "A": "ai_coach",
    "B": "ai_coach",
    "C": "ai_coach",
    "D": "ai_coach",
    "E": "macros",
    "F": "ai_coach",
    "G": "ai_coach",
}


def cpp_url_for_anchor(feature_anchor: str) -> str:
    bucket = ANCHOR_TO_CPP.get((feature_anchor or "").upper(), "default")
    return CPP_URLS[bucket]


def apply_cpp_url(caption: str, feature_anchor: str) -> str:
    """Append CPP URL to caption (idempotent — skips if a URL is already present)."""
    if not caption:
        return caption
    if "apps.apple.com" in caption:
        return caption
    url = cpp_url_for_anchor(feature_anchor)
    # Put URL on its own line above the hashtags for better tap target
    if "\n\n#" in caption:
        body, tags = caption.split("\n\n#", 1)
        return f"{body}\n\n{url}\n\n#{tags}"
    return f"{caption}\n\n{url}"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"

# Confirmed via existing poster.py + Claude Handoff doc 2026-04-22:
# PFM uses .dev domain (not .com). Dashboard at app.postforme.dev/<team>/<proj>/
POSTFORME_BASE_URL = "https://api.postforme.dev/v1"

DEFAULT_BRAND = "prpathapp"
HTTP_TIMEOUT_SEC = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _load_api_key() -> str:
    """Load POSTFORME_API_KEY from prpath-genesis/.env. Exits 1 if missing."""
    load_dotenv(ENV_PATH)
    api_key = os.environ.get("POSTFORME_API_KEY", "").strip()
    if not api_key:
        print(
            f"ERROR: POSTFORME_API_KEY missing or empty in {ENV_PATH}",
            file=sys.stderr,
        )
        sys.exit(1)
    return api_key


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _redact_error(exc: requests.RequestException) -> str:
    """Produce an error string that cannot leak the API key."""
    msg = str(exc)
    # requests never echoes Authorization headers in __str__, but be defensive.
    return msg.replace(os.environ.get("POSTFORME_API_KEY", "") or "__none__", "<redacted>")


def _http_get(path: str, api_key: str) -> Any:
    url = f"{POSTFORME_BASE_URL}{path}"
    try:
        resp = requests.get(url, headers=_auth_headers(api_key), timeout=HTTP_TIMEOUT_SEC)
    except requests.RequestException as exc:
        print(f"ERROR: GET {path} failed: {_redact_error(exc)}", file=sys.stderr)
        sys.exit(1)

    if not resp.ok:
        snippet = resp.text[:500] if resp.text else ""
        print(
            f"ERROR: GET {path} returned HTTP {resp.status_code}: {snippet}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        return resp.json()
    except ValueError:
        print(f"ERROR: GET {path} returned non-JSON body", file=sys.stderr)
        sys.exit(1)


def _http_post(path: str, payload: dict[str, Any], api_key: str) -> Any:
    url = f"{POSTFORME_BASE_URL}{path}"
    try:
        resp = requests.post(
            url,
            headers=_auth_headers(api_key),
            data=json.dumps(payload),
            timeout=HTTP_TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        print(f"ERROR: POST {path} failed: {_redact_error(exc)}", file=sys.stderr)
        sys.exit(1)

    if not resp.ok:
        snippet = resp.text[:500] if resp.text else ""
        print(
            f"ERROR: POST {path} returned HTTP {resp.status_code}: {snippet}",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text}


def _brand_matches(account: dict[str, Any], brand: str) -> bool:
    """Case-insensitive match on username OR external_id."""
    b = brand.lower()
    username = (account.get("username") or "").lower()
    external_id = (account.get("external_id") or "").lower()
    return username == b or external_id == b


def filter_accounts_by_brand(accounts: list[dict[str, Any]], brand: str) -> list[dict[str, Any]]:
    """Return only accounts whose username or external_id matches `brand`."""
    return [acct for acct in accounts if _brand_matches(acct, brand)]


def _fetch_all_accounts(api_key: str) -> list[dict[str, Any]]:
    """GET /social-accounts and unwrap the common {data: [...]} / {social_accounts: [...]} shapes."""
    data = _http_get("/social-accounts?status=connected&limit=50", api_key)
    if isinstance(data, dict):
        return (
            data.get("social_accounts")
            or data.get("data")
            or data.get("accounts")
            or []
        )
    return data if isinstance(data, list) else []


def resolve_prpath_accounts(api_key: str, brand: str = DEFAULT_BRAND) -> dict[str, str]:
    """Return {platform: spc_id} for each platform the brand has connected.

    Platforms normalize from PFM names — 'tiktok_business' → 'tiktok'.
    """
    filtered = filter_accounts_by_brand(_fetch_all_accounts(api_key), brand)
    out: dict[str, str] = {}
    for acct in filtered:
        platform = (acct.get("platform") or "").replace("_business", "").lower()
        spc_id = acct.get("id")
        if platform and spc_id:
            out[platform] = spc_id
    return out


# ---------------------------------------------------------------------------
# Media upload (signed URL → PUT → permanent URL)
# ---------------------------------------------------------------------------
def upload_media(file_path: Path, api_key: str, content_type: str = "image/png") -> str:
    """Upload a local file to PFM's media service. Returns the permanent media URL.

    Two-step:
      1. POST /media/create-upload-url → {upload_url, media_url}
      2. PUT file_bytes to upload_url with correct Content-Type
    """
    file_path = file_path.resolve()
    if not file_path.is_file():
        raise FileNotFoundError(f"media not found: {file_path}")

    # Step 1: request a signed upload URL
    result = _http_post(
        "/media/create-upload-url",
        {"filename": file_path.name, "content_type": content_type},
        api_key,
    )
    upload_url = result.get("upload_url") or result.get("url") or result.get("signed_url")
    media_url = result.get("media_url") or result.get("public_url") or result.get("url")
    if not upload_url:
        raise RuntimeError(
            f"PFM /media/create-upload-url returned no upload_url. Response keys: {list(result)}"
        )
    if not media_url:
        # Fall back to upload_url stripped of query string if media_url missing
        media_url = upload_url.split("?", 1)[0]

    # Step 2: PUT the file to the signed URL (no Authorization header — it's pre-signed)
    data = file_path.read_bytes()
    put_resp = requests.put(
        upload_url,
        data=data,
        headers={"Content-Type": content_type, "Content-Length": str(len(data))},
        timeout=HTTP_TIMEOUT_SEC * 2,  # uploads can be slower
    )
    if put_resp.status_code not in (200, 201, 204):
        raise RuntimeError(
            f"PUT upload failed HTTP {put_resp.status_code}: {put_resp.text[:200]}"
        )

    return media_url


# ---------------------------------------------------------------------------
# Video stitch (for YouTube Shorts — 2 PNGs → 1 MP4)
# ---------------------------------------------------------------------------
def stitch_slides_to_video(slides_dir: Path, output_path: Path) -> Path:
    """Stitch slide PNGs into an 8s 9:16 MP4 with random YT-safe background music.

    Locked 2026-04-22 per Lance:
      - 2.5s per slide, 2 slides = 5s total (Q3 — approved after preview)
      - Cut transition, no crossfade (Q4 = b — abrupt hook→payoff)
      - Random music from any genre (Q1 = a)
    """
    import sys as _sys
    _sys.path.insert(0, str(SCRIPT_DIR))
    from video_stitcher import stitch_video, pick_music  # type: ignore
    output_path.parent.mkdir(parents=True, exist_ok=True)
    random_track = pick_music(None)  # random across hype/upbeat/chill
    stitch_video(
        slides_dir=str(slides_dir),
        output_path=str(output_path),
        music_path=str(random_track) if random_track else None,
        transition="cut",
        slide_duration=2.5,
    )
    if not output_path.is_file():
        raise RuntimeError(f"video_stitcher did not produce {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# Subcommand: accounts
# ---------------------------------------------------------------------------
def cmd_accounts(args: argparse.Namespace) -> int:
    """Fetch social accounts from PFM, filter by brand, pretty-print JSON."""
    api_key = _load_api_key()
    # Confirmed via existing poster.py + 404 smoke test 2026-04-22:
    # PFM uses /social-accounts (hyphen). Filter to status=connected at API level
    # to mirror poster.py behavior.
    data = _http_get("/social-accounts?status=connected&limit=50", api_key)

    # PFM commonly wraps lists in {"data": [...]} or {"social_accounts": [...]}
    if isinstance(data, dict):
        raw_accounts = (
            data.get("social_accounts")
            or data.get("data")
            or data.get("accounts")
            or []
        )
    elif isinstance(data, list):
        raw_accounts = data
    else:
        raw_accounts = []

    filtered = filter_accounts_by_brand(raw_accounts, args.brand)

    output = {
        "brand": args.brand,
        "count": len(filtered),
        "social_accounts": filtered,
    }
    print(json.dumps(output, indent=2, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: schedule
# ---------------------------------------------------------------------------
def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    if not manifest_path.exists():
        print(f"ERROR: manifest not found: {manifest_path}", file=sys.stderr)
        sys.exit(1)
    try:
        with manifest_path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: failed to parse manifest {manifest_path}: {exc}", file=sys.stderr)
        sys.exit(1)


def _schedule_single_slot(
    slot: dict[str, Any],
    accounts: dict[str, str],
    api_key: str,
    live: bool,
) -> dict[str, Any]:
    """Schedule one slot across TikTok + IG + FB + YT.

    Media comes from a MediaRenderer (see media_renderer.py):
      - media_kind='carousel' → TikTok gets a photo carousel, reels use a
        stitched MP4 (PRPath's flow).
      - media_kind='video'    → all 4 platforms use a single MP4 (LaunchLens).

    Returns per-platform PFM post IDs on success, or error details.
    """
    import sys as _sys
    _sys.path.insert(0, str(SCRIPT_DIR))
    from media_renderer import renderer_for_slot  # type: ignore

    slot_id = slot["slot_id"]
    post_id = slot.get("post_id") or slot_id
    scheduled_at = slot.get("scheduled_at")
    feature_anchor = (slot.get("feature_anchor") or "").upper()
    caption = apply_cpp_url(slot.get("caption") or "", feature_anchor)
    media_kind = (slot.get("media_kind") or "carousel").lower()

    post_ids: dict[str, str | None] = {}
    errors: list[str] = []
    only_tiktok = os.environ.get("PFM_ONLY_TIKTOK", "").lower() in ("1", "true", "yes")
    only_fb = os.environ.get("PFM_ONLY_FB", "").lower() in ("1", "true", "yes")

    # ---- DRY-RUN short-circuit: no rendering, no uploads, no POSTs
    if not live:
        tt_plan = (
            {"kind": "photo_carousel", "assets": 2, "music": "TikTok native auto-add"}
            if media_kind == "carousel"
            else {"kind": "single_video", "music": "baked (Remotion)"}
        )
        return {
            "ok": True,
            "slot": slot_id,
            "status": "dry_run",
            "post_id": post_id,
            "scheduled_at": scheduled_at,
            "feature_anchor": feature_anchor,
            "caption": caption,
            "media_kind": media_kind,
            "accounts": {k: accounts.get(k) for k in ("tiktok", "instagram", "facebook", "youtube")},
            "platforms_planned": {
                "tiktok": tt_plan,
                "youtube_short": {"kind": "single_video"},
                "instagram_reel": {"kind": "single_video"},
                "facebook_reel": {"kind": "single_video"},
            },
        }

    # ---- LIVE: render, upload, post.
    try:
        rendered = renderer_for_slot(slot).render_for_slot(slot)
    except Exception as exc:
        return {"ok": False, "slot": slot_id, "error": f"media render failed: {exc}"}

    # Upload video once; re-use URL across IG/FB/YT and (if no carousel) TikTok.
    video_url: str | None = None
    if rendered.video_mp4 and not only_tiktok:
        try:
            video_url = upload_media(rendered.video_mp4, api_key, "video/mp4")
        except Exception as exc:
            return {"ok": False, "slot": slot_id, "error": f"video upload failed: {exc}"}

    # Upload carousel PNGs for TikTok. Skipped in FB-only re-fire mode.
    carousel_urls: list[str] = []
    if rendered.carousel_pngs and not only_fb:
        try:
            for png in rendered.carousel_pngs:
                carousel_urls.append(upload_media(png, api_key, "image/png"))
        except Exception as exc:
            return {"ok": False, "slot": slot_id, "error": f"slide upload failed: {exc}"}

    video_media = [{"url": video_url}] if video_url else []
    first_line = caption.split("\n", 1)[0][:100]

    # 1) TikTok. Carousel (PRPath) or single video (LaunchLens).
    # 2026-04-22 TT payload bugfix (still applies): passing only `caption`
    # duplicated title/description; auto_add_music=True layered on top of
    # TikTok's own native track. Locked fix for carousels: explicit short
    # title, URL-stripped description, auto_add_music=True. For videos with
    # baked audio (Remotion), auto_add_music must be False so TT doesn't
    # layer a second track on top.
    tiktok_id = None if only_fb else accounts.get("tiktok")
    tiktok_title = first_line.rstrip(" 💪")[:90]
    _desc_raw = (caption.split("\n", 1)[1].lstrip("\n") if "\n" in caption else caption)
    tiktok_description = _strip_url_lines(_desc_raw)
    if tiktok_id:
        if carousel_urls:
            tt_media = [{"url": u} for u in carousel_urls]
            tt_auto_music = True   # photo carousel → let TikTok add its native track
        elif video_url:
            tt_media = [{"url": video_url}]
            tt_auto_music = False  # video already has baked music — don't layer
        else:
            tt_media = []
            tt_auto_music = True

        if tt_media:
            try:
                body = {
                    "caption": tiktok_description,
                    "social_accounts": [tiktok_id],
                    "media": tt_media,
                    "platform_configurations": {
                        "tiktok": {
                            "title": tiktok_title,
                            "privacy_status": "public",
                            "auto_add_music": tt_auto_music,
                        }
                    },
                }
                if scheduled_at:
                    body["scheduled_at"] = scheduled_at
                resp = _http_post("/social-posts", body, api_key)
                post_ids["tiktok"] = resp.get("id") or resp.get("post_id_pfm")
            except SystemExit:
                errors.append("tiktok POST failed (see stderr above)")
            except Exception as exc:
                errors.append(f"tiktok: {exc}")
        else:
            errors.append("tiktok: renderer produced no media")
    elif not only_fb:
        errors.append("tiktok account not connected for brand")

    # 2) Instagram + Facebook Reels — bundled into one PFM call.
    # FB-only mode (PFM_ONLY_FB=1) skips IG so a re-fire doesn't double-post IG.
    if only_tiktok:
        ig_fb_ids = []
    elif only_fb:
        ig_fb_ids = [accounts["facebook"]] if accounts.get("facebook") else []
    else:
        ig_fb_ids = [accounts[k] for k in ("instagram", "facebook") if accounts.get(k)]
    if ig_fb_ids:
        try:
            body = {
                "caption": caption,
                "social_accounts": ig_fb_ids,
                "media": video_media,
            }
            if scheduled_at:
                body["scheduled_at"] = scheduled_at
            resp = _http_post("/social-posts", body, api_key)
            pfm_id = resp.get("id") or resp.get("post_id_pfm")
            platforms_posted = ["facebook"] if only_fb else ("instagram", "facebook")
            for platform in platforms_posted:
                if accounts.get(platform):
                    post_ids[platform] = pfm_id
        except SystemExit:
            errors.append("ig+fb POST failed (see stderr above)")
        except Exception as exc:
            errors.append(f"ig+fb: {exc}")
    elif not only_tiktok and not only_fb:
        errors.append("ig/fb accounts not connected for brand")

    # 3) YouTube Short — same MP4, title set from hook line, public privacy.
    yt_id = None if (only_tiktok or only_fb) else accounts.get("youtube")
    if yt_id:
        try:
            body = {
                "caption": caption,
                "social_accounts": [yt_id],
                "media": video_media,
                "platform_configurations": {
                    "youtube": {"title": first_line, "privacy_status": "public"}
                },
            }
            if scheduled_at:
                body["scheduled_at"] = scheduled_at
            resp = _http_post("/social-posts", body, api_key)
            post_ids["youtube"] = resp.get("id") or resp.get("post_id_pfm")
        except SystemExit:
            errors.append("youtube POST failed (see stderr above)")
        except Exception as exc:
            errors.append(f"youtube: {exc}")
    elif not only_tiktok and not only_fb:
        errors.append("youtube account not connected for brand")

    # Persist PFM IDs back to SQLite so the dashboard can render a LIVE Posts view
    # without re-fetching from PFM. Best-effort — never blocks a successful fire.
    if post_ids:
        try:
            from dashboard import db as _db  # type: ignore
            _db.set_slot_pfm_ids(slot_id, post_ids, scheduled_at=scheduled_at)
        except Exception as exc:
            print(f"[warn] dashboard sync failed: {exc}", file=sys.stderr)

    return {
        "ok": not errors or bool(post_ids),
        "slot": slot_id,
        "status": "scheduled" if post_ids else "failed",
        "post_id_pfm": post_ids,
        "errors": errors,
    }


def cmd_schedule(args: argparse.Namespace) -> int:
    """Schedule slot(s) in a batch manifest to Post for Me.

    - Dry-run default: prints planned payload shape, makes no API calls.
    - Live: uploads slide PNGs, stitches YT short video, posts to 4 platforms.

    Per-slot behavior:
      - TikTok: carousel of 2 PNGs (auto-add music, public privacy)
      - IG + FB: carousel of 2 PNGs (bundled API call)
      - YT: stitched MP4 Short (via video_stitcher.py)

    CPP URL is appended to caption automatically based on feature_anchor.
    """
    manifest_path = Path(args.manifest).expanduser().resolve()
    manifest = _load_manifest(manifest_path)

    slots = manifest.get("slots") or []
    if not slots:
        print(f"ERROR: manifest has no 'slots' array: {manifest_path}", file=sys.stderr)
        return 1

    live = bool(args.live)
    manifest_dry = bool(manifest.get("dry_run", True))
    if live and manifest_dry:
        print(
            "ERROR: manifest has dry_run=true; refusing to --live. "
            "Flip manifest.dry_run to false first.",
            file=sys.stderr,
        )
        return 1

    # API key required for both dry-run (to fetch accounts) and live (to post).
    # We still skip uploads + POSTs in dry-run, so no real side effects.
    api_key = _load_api_key()

    # Resolve PRPath brand accounts once per invocation — expensive call otherwise.
    accounts = resolve_prpath_accounts(api_key, args.brand)

    # Optional single-slot filter
    slot_filter = getattr(args, "slot", None)
    targets = [s for s in slots if not slot_filter or s.get("slot_id") == slot_filter]
    if slot_filter and not targets:
        print(f"ERROR: no slot with slot_id={slot_filter}", file=sys.stderr)
        return 1

    results: list[dict[str, Any]] = []
    for slot in targets:
        results.append(_schedule_single_slot(slot, accounts, api_key, live))

    # Single-slot run: emit that slot's result as the top-level payload
    if slot_filter and len(results) == 1:
        print(json.dumps(results[0], indent=2))
        return 0 if results[0].get("ok") else 1

    # Multi-slot run: envelope
    envelope = {
        "manifest": str(manifest_path),
        "live": live,
        "slots_processed": len(results),
        "accounts_resolved": {k: accounts.get(k) for k in ("tiktok", "instagram", "facebook", "youtube")},
        "results": results,
    }
    print(json.dumps(envelope, indent=2))
    any_failed = any(not r.get("ok") for r in results)
    return 1 if any_failed else 0


# ---------------------------------------------------------------------------
# Argparse harness
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="postforme_client",
        description="PRPath Post for Me API client.",
    )
    parser.add_argument(
        "--brand",
        default=DEFAULT_BRAND,
        help="Brand filter (username/external_id). Default: prpathapp.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("accounts", help="List social accounts for a brand.")

    sched = sub.add_parser("schedule", help="Schedule slots from a batch manifest.")
    sched.add_argument("--manifest", required=True, help="Path to batch manifest JSON.")
    sched.add_argument("--slot", default=None, help="Optional slot_id filter — schedule only this slot.")
    mode = sched.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="dry_run", action="store_true", default=True)
    mode.add_argument("--live", dest="live", action="store_true", default=False)

    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv[1:])

    if args.cmd == "accounts":
        return cmd_accounts(args)
    if args.cmd == "schedule":
        return cmd_schedule(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
