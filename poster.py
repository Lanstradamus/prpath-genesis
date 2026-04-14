"""
PRPath GENESIS Poster — Posts carousels to TikTok + video to YT/IG/FB.

Two Post for Me API calls per carousel:
  1. TikTok: Upload PNGs as carousel images (auto_add_music: true)
  2. YT + IG + FB: Upload slideshow MP4 video

Usage:
  python -X utf8 poster.py --list-accounts          # Show connected accounts
  python -X utf8 poster.py --post gen000_01_squat_180  # Post a single carousel NOW
  python -X utf8 poster.py --post-all                # Post all Gen 0 carousels (spaced out)
  python -X utf8 poster.py --dry-run gen000_01_squat_180  # Preview without posting

Requires: POSTFORME_API_KEY in .env (shares with LaunchLens)
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

# Load API key — check prpath-genesis/.env first, fall back to LaunchLens .env
SELF_ENV = Path(__file__).parent / ".env"
LL_ENV = Path(__file__).parent.parent / "LaunchLens Projects" / "LaunchLens Marketing" / "Dashboard Skeleton" / "content-engine" / ".env"

API_KEY = None
for env_path in [SELF_ENV, LL_ENV]:
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("POSTFORME_API_KEY="):
                API_KEY = line.split("=", 1)[1].strip()
                break
    if API_KEY:
        break

BASE_URL = "https://api.postforme.dev/v1"
SLIDES_DIR = Path(__file__).parent / "slides"
DATA_DIR = Path(__file__).parent / "data"


def _headers():
    if not API_KEY:
        sys.exit("[error] POSTFORME_API_KEY not found. Create .env in prpath-genesis/ or check LaunchLens .env")
    return {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}


def _request(method, path, body=None, params=None):
    url = f"{BASE_URL}{path}"
    if params:
        url += "?" + urlencode(params)
    data = json.dumps(body).encode("utf-8") if body else None
    req = Request(url, data=data, headers=_headers(), method=method)
    try:
        with urlopen(req) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        error_body = e.read().decode("utf-8") if e.fp else str(e)
        print(f"[error] {method} {path} -> {e.code}: {error_body}", file=sys.stderr)
        raise


def list_accounts():
    """List all connected social accounts."""
    data = _request("GET", "/social-accounts", params={"status": "connected", "limit": 50})
    accounts = data.get("data", data) if isinstance(data, dict) else data
    return accounts


def get_prpath_accounts():
    """Get PRPath-specific account IDs. Returns dict like {tiktok: "spc_...", instagram: "spc_...", ...}"""
    accounts = list_accounts()
    prpath = {}
    for acc in accounts:
        username = (acc.get("username") or "").lower()
        # Match PRPath accounts (not LaunchLens)
        if "prpath" in username:
            platform = acc.get("platform", "").replace("_business", "")
            prpath[platform] = acc.get("id")
    return prpath


def upload_media(file_path: str, content_type: str = "image/png") -> str:
    """Upload a file to Post for Me and return the permanent URL."""
    path = Path(file_path)

    # Step 1: Get signed upload URL
    result = _request("POST", "/media/create-upload-url", {
        "filename": path.name,
        "content_type": content_type,
    })

    upload_url = result.get("upload_url") or result.get("url") or result.get("signed_url")
    media_url = result.get("media_url") or result.get("public_url") or result.get("url")

    if not upload_url:
        print(f"[debug] API response: {json.dumps(result, indent=2)}", file=sys.stderr)
        sys.exit("[error] No upload_url in response")

    # Step 2: PUT file to signed URL
    with open(file_path, "rb") as f:
        file_data = f.read()

    put_req = Request(upload_url, data=file_data, method="PUT")
    put_req.add_header("Content-Type", content_type)
    put_req.add_header("Content-Length", str(len(file_data)))

    with urlopen(put_req) as resp:
        if resp.status not in (200, 201, 204):
            print(f"[warn] Upload returned HTTP {resp.status}")

    return media_url


def post_carousel_to_tiktok(
    slides_dir: str,
    caption: str,
    tiktok_account_id: str,
    scheduled_at: Optional[str] = None,
    dry_run: bool = False,
) -> Optional[dict]:
    """Upload carousel PNGs and post to TikTok."""
    slides = sorted(Path(slides_dir).glob("slide_*.png"))
    if not slides:
        print(f"[error] No slides found in {slides_dir}")
        return None

    print(f"  [tiktok] Uploading {len(slides)} slides...")

    if dry_run:
        print(f"  [dry-run] Would upload {len(slides)} PNGs to TikTok")
        print(f"  [dry-run] Caption: {caption[:80]}...")
        return {"dry_run": True}

    # Upload each slide
    media_urls = []
    for slide in slides:
        url = upload_media(str(slide), "image/png")
        media_urls.append({"url": url})
        print(f"    Uploaded: {slide.name}")

    # Create carousel post
    body = {
        "caption": caption,
        "social_accounts": [tiktok_account_id],
        "media": media_urls,
        "platform_configurations": {
            "tiktok": {"auto_add_music": True, "privacy_status": "public"},
        },
    }
    if scheduled_at:
        body["scheduled_at"] = scheduled_at

    result = _request("POST", "/social-posts", body=body)
    print(f"  [tiktok] Posted! Post ID: {result.get('id', 'unknown')}")
    return result


def post_video_to_platforms(
    video_path: str,
    caption: str,
    account_ids: list[str],
    title: Optional[str] = None,
    scheduled_at: Optional[str] = None,
    dry_run: bool = False,
) -> Optional[dict]:
    """Upload video and post to YouTube/Instagram/Facebook."""
    if not Path(video_path).exists():
        print(f"  [video] Skipping — no video file at {video_path}")
        return None

    if dry_run:
        print(f"  [dry-run] Would upload video to {len(account_ids)} platforms")
        print(f"  [dry-run] Caption: {caption[:80]}...")
        return {"dry_run": True}

    print(f"  [video] Uploading {Path(video_path).name}...")
    video_url = upload_media(video_path, "video/mp4")

    body = {
        "caption": caption,
        "social_accounts": account_ids,
        "media": [{"url": video_url}],
    }
    if title:
        body["platform_configurations"] = {
            "youtube": {"title": title, "privacy_status": "public"},
        }
    if scheduled_at:
        body["scheduled_at"] = scheduled_at

    result = _request("POST", "/social-posts", body=body)
    print(f"  [video] Posted! Post ID: {result.get('id', 'unknown')}")
    return result


def build_captions(genome_id: str, exercise_key: str = "squat", carousel_type: str = "score_rank", bodyweight: str = "180"):
    """Build platform-specific captions using the caption generator (2026 best practices)."""
    from caption_generator import generate_captions
    return generate_captions(exercise_key, carousel_type, genome_id, bodyweight)


def post_genome(genome_id: str, gen_dir: str = None, dry_run: bool = False, scheduled_at: str = None):
    """Post a single genome — carousel to TikTok, video to other platforms.

    Args:
        scheduled_at: ISO 8601 UTC timestamp (e.g. "2026-04-14T17:00:00Z") to schedule,
                      or None to post immediately.
    """
    if not gen_dir:
        gen_dir = SLIDES_DIR / "gen_000"
    slides_dir = Path(gen_dir) / genome_id

    if not slides_dir.exists():
        print(f"[error] Slides not found: {slides_dir}")
        return

    # Determine exercise key and type from genome_id
    exercise_key = "squat"
    exercise_display = "Squat"
    carousel_type = "score_rank"
    bodyweight = "180"

    key_map = {
        "squat": "squat", "bench": "bench_press", "deadlift": "deadlift",
        "ohp": "overhead_press", "hipthrust": "hip_thrust", "pullup": "pull_up",
        "row": "barbell_row", "frontsquat": "front_squat",
    }

    parts = genome_id.split("_")
    for p in parts:
        if p in key_map:
            exercise_key = key_map[p]
            break

    # Extract bodyweight from genome_id (e.g. gen000_01_squat_180 → "180")
    for p in parts:
        if p in ("150", "180", "200"):
            bodyweight = p
            break

    if "bodymap" in genome_id:
        carousel_type = "body_map"

    captions = build_captions(genome_id, exercise_key, carousel_type, bodyweight)

    print(f"\n{'='*50}")
    print(f"Posting: {genome_id}")
    print(f"Slides: {slides_dir}")
    print(f"Type: {carousel_type} | Exercise: {exercise_key}")
    if scheduled_at:
        print(f"Scheduled for: {scheduled_at}")

    # Get PRPath accounts
    prpath_accounts = get_prpath_accounts()
    if not prpath_accounts and not dry_run:
        print("[warn] No PRPath accounts found in Post for Me!")
        print("       Connect @prpathapp accounts at app.postforme.dev")
        print("       Running in dry-run mode instead.\n")
        dry_run = True

    # Post to TikTok (carousel)
    tiktok_id = prpath_accounts.get("tiktok")
    if tiktok_id:
        post_carousel_to_tiktok(
            str(slides_dir), captions["tiktok"], tiktok_id,
            scheduled_at=scheduled_at, dry_run=dry_run,
        )
    else:
        print("  [tiktok] Skipped — no PRPath TikTok account connected")

    # Post to YT/IG/FB (video)
    video_path = slides_dir / f"{genome_id}.mp4"
    other_ids = [v for k, v in prpath_accounts.items() if k != "tiktok" and v]
    if other_ids and video_path.exists():
        yt_data = captions.get("youtube", {})
        yt_title = yt_data.get("title", "") if isinstance(yt_data, dict) else str(yt_data)
        ig_caption = captions.get("instagram", captions["tiktok"])
        post_video_to_platforms(
            str(video_path), ig_caption,
            other_ids, title=yt_title,
            scheduled_at=scheduled_at, dry_run=dry_run,
        )
    else:
        print("  [video] Skipped — no video file found")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="PRPath GENESIS Poster")
    parser.add_argument("--list-accounts", action="store_true", help="List connected accounts")
    parser.add_argument("--post", type=str, help="Post a single genome by ID")
    parser.add_argument("--post-all", action="store_true", help="Post all Gen 0 carousels")
    parser.add_argument("--dry-run", action="store_true", help="Preview without actually posting")
    parser.add_argument("--gen-dir", type=str, help="Generation directory override")
    parser.add_argument("--schedule", type=str,
                        help="Schedule for ISO 8601 UTC time (e.g. '2026-04-14T17:00:00Z') "
                             "or local-CT time (e.g. '2026-04-14 12:00' — assumes Central)")
    args = parser.parse_args()

    # Parse schedule time (convert local CT to UTC if needed)
    scheduled_at = None
    if args.schedule:
        s = args.schedule.strip()
        if s.endswith("Z") or "+" in s or s.endswith("UTC"):
            scheduled_at = s.replace("UTC", "").strip()
            if not scheduled_at.endswith("Z"):
                scheduled_at += "Z"
        else:
            # Assume Central Time (CDT = UTC-5 in April)
            from datetime import datetime, timedelta
            local = datetime.fromisoformat(s.replace("T", " "))
            utc = local + timedelta(hours=5)  # CDT → UTC
            scheduled_at = utc.strftime("%Y-%m-%dT%H:%M:%SZ")
            print(f"[schedule] Converted {s} CT → {scheduled_at} UTC")

    if args.list_accounts:
        accounts = list_accounts()
        prpath = get_prpath_accounts()
        print("\nAll connected accounts:")
        for acc in accounts:
            marker = " <-- PRPath" if "prpath" in (acc.get("username") or "").lower() else ""
            print(f"  {acc['platform']:20s} @{acc.get('username', '?'):20s} {acc['id']}{marker}")

        print(f"\nPRPath accounts: {prpath if prpath else 'NONE — connect at app.postforme.dev'}")

    elif args.post:
        post_genome(args.post, gen_dir=args.gen_dir, dry_run=args.dry_run or False, scheduled_at=scheduled_at)

    elif args.post_all:
        gen_dir = Path(args.gen_dir) if args.gen_dir else SLIDES_DIR / "gen_000"
        manifest_path = gen_dir / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            for genome in manifest["carousels"]:
                post_genome(genome["id"], gen_dir=str(gen_dir), dry_run=args.dry_run or False)
        else:
            # Post all subdirectories
            for subdir in sorted(gen_dir.iterdir()):
                if subdir.is_dir() and subdir.name.startswith("gen000"):
                    post_genome(subdir.name, gen_dir=str(gen_dir), dry_run=args.dry_run or False)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
