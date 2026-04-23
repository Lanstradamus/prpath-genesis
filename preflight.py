#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PRPath daily preflight — OAuth / env / disk / manifest health check.

Runs from any CWD. Exit 0 if all green or only yellow. Exit 1 if any red.
Telegram alerts via notify.py. Ports v3 logic from LaunchLens preflight prompt
with the brand swapped to `prpathapp`.

Checks (in order):
    STEP 1  .env sanity               — POSTFORME_API_KEY + GEMINI_API_KEY
    STEP 2  PFM social account status — branched by platform family
    STEP 3  Disk space                — < 5 GB YELLOW, < 1 GB RED
    STEP 4  Today's batch manifest    — YELLOW if missing on post day
    STEP 5  Telegram report           — green/yellow/red ping

Dependencies: python-dotenv (notify.py + postforme_client.py do the rest)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"
NOTIFY_PATH = SCRIPT_DIR / "notify.py"
PFM_CLIENT_PATH = SCRIPT_DIR / "postforme_client.py"
BATCHES_DIR = SCRIPT_DIR / "picks" / "_batches"

BRAND = "prpathapp"

# ---------------------------------------------------------------------------
# OAuth check config
# ---------------------------------------------------------------------------
GOOGLE_STYLE_PLATFORMS = {"youtube", "tiktok_business"}
META_STYLE_PLATFORMS = {"instagram", "facebook"}
REQUIRED_PLATFORMS = {"tiktok", "youtube", "instagram", "facebook"}
TIKTOK_ALIASES = {"tiktok", "tiktok_business"}

META_YELLOW_DAYS = 7
META_RED_DAYS = 1

# Disk thresholds (GB)
DISK_YELLOW_GB = 5
DISK_RED_GB = 1

# Severity ordering
GREEN = 0
YELLOW = 1
RED = 2


# ---------------------------------------------------------------------------
# Result accumulator
# ---------------------------------------------------------------------------
class Result:
    """Collects per-check status + issue strings, then emits overall color."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.issues_red: list[str] = []
        self.issues_yellow: list[str] = []
        self.severity: int = GREEN

    def log(self, line: str) -> None:
        self.lines.append(line)

    def mark_yellow(self, issue: str) -> None:
        self.issues_yellow.append(issue)
        if self.severity < YELLOW:
            self.severity = YELLOW

    def mark_red(self, issue: str) -> None:
        self.issues_red.append(issue)
        self.severity = RED

    def overall(self) -> str:
        if self.severity == RED:
            return "RED"
        if self.severity == YELLOW:
            return "YELLOW"
        return "GREEN"


# ---------------------------------------------------------------------------
# STEP 1 — .env sanity
# ---------------------------------------------------------------------------
def check_env(result: Result) -> None:
    result.log("STEP 1 — .env sanity")
    if not ENV_PATH.exists():
        result.log(f"  .env MISSING at {ENV_PATH}")
        result.mark_red(f".env missing")
        return

    # dotenv_values gives {key: value|None} without polluting os.environ.
    values = dotenv_values(ENV_PATH)
    required = ["POSTFORME_API_KEY", "GEMINI_API_KEY"]
    missing: list[str] = []
    for key in required:
        val = (values.get(key) or "").strip()
        if not val:
            missing.append(key)

    if missing:
        result.log(f"  MISSING or EMPTY: {', '.join(missing)}")
        result.mark_red(f".env missing {'+'.join(missing)}")
    else:
        result.log("  OK — POSTFORME_API_KEY + GEMINI_API_KEY present")


# ---------------------------------------------------------------------------
# STEP 2 — PFM social-account status
# ---------------------------------------------------------------------------
def _run_postforme_accounts() -> dict[str, Any] | None:
    """Invoke postforme_client.py accounts --brand prpathapp. Return parsed JSON."""
    try:
        proc = subprocess.run(
            [sys.executable, str(PFM_CLIENT_PATH), "--brand", BRAND, "accounts"],
            cwd=str(SCRIPT_DIR),
            capture_output=True,
            text=True,
            timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"  postforme_client invocation failed: {exc}")
        return None

    if proc.returncode != 0:
        # Show stderr but never the command's full env.
        snippet = (proc.stderr or "").strip().splitlines()[-1:] or [""]
        print(f"  postforme_client exit {proc.returncode}: {snippet[0]}")
        return None

    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        print(f"  postforme_client returned non-JSON: {exc}")
        return None


def _days_until(iso_ts: str | None) -> float | None:
    """Days between now (UTC) and an ISO-8601 timestamp. None on parse failure.

    Uses dateutil because Python 3.9's fromisoformat is strict about fractional
    seconds (requires exactly 3 or 6 digits). PFM returns 2-digit precision for
    some platforms (e.g. Facebook: '.54') and 3-digit for others (IG: '.432').
    """
    if not iso_ts:
        return None
    try:
        from dateutil import parser as _dtparser
        dt = _dtparser.parse(iso_ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - datetime.now(timezone.utc)
        return delta.total_seconds() / 86400.0
    except Exception:
        return None


def _status_ok(status: str | None) -> bool:
    return (status or "").lower() == "connected"


def _platform_id(acct: dict[str, Any]) -> str:
    """Pull the platform identifier from a PFM account record."""
    return (acct.get("platform") or acct.get("provider") or "").lower()


def _check_google_style(acct: dict[str, Any], platform_label: str, result: Result) -> None:
    status = acct.get("status")
    refresh_token = acct.get("refresh_token")
    if _status_ok(status) and refresh_token:
        result.log(f"  {platform_label}: OK")
        return
    reason_parts = []
    if not _status_ok(status):
        reason_parts.append(f"status={status or 'unknown'}")
    if not refresh_token:
        reason_parts.append("refresh_token missing")
    reason = ", ".join(reason_parts) or "unknown"
    result.log(f"  {platform_label}: RED — {reason}")
    result.mark_red(f"{platform_label} {reason}")


def _check_meta_style(acct: dict[str, Any], platform_label: str, result: Result) -> None:
    status = acct.get("status")
    if not _status_ok(status):
        result.log(f"  {platform_label}: RED — status={status or 'unknown'}")
        result.mark_red(f"{platform_label} status={status or 'unknown'}")
        return

    days = _days_until(acct.get("access_token_expires_at"))
    if days is None:
        result.log(f"  {platform_label}: RED — access_token_expires_at unreadable")
        result.mark_red(f"{platform_label} expiry unreadable")
        return

    days_rounded = max(0, int(days))
    if days > META_YELLOW_DAYS:
        result.log(f"  {platform_label}: OK ({days_rounded}d)")
    elif days > META_RED_DAYS:
        result.log(f"  {platform_label}: YELLOW ({days_rounded}d until expiry)")
        result.mark_yellow(f"{platform_label} expires in {days_rounded}d")
    else:
        result.log(f"  {platform_label}: RED ({days_rounded}d until expiry)")
        result.mark_red(f"{platform_label} expires in {days_rounded}d")


def check_postforme_accounts(result: Result) -> None:
    result.log("STEP 2 — Post for Me accounts (brand=prpathapp)")
    payload = _run_postforme_accounts()
    if payload is None:
        result.log("  RED — failed to fetch accounts from PFM")
        result.mark_red("PFM accounts call failed")
        return

    accounts: list[dict[str, Any]] = payload.get("social_accounts") or []
    if not accounts:
        result.log("  RED — no prpathapp accounts returned")
        result.mark_red("no prpathapp accounts")
        return

    # Index by canonical platform key. Treat tiktok_business as tiktok.
    by_platform: dict[str, dict[str, Any]] = {}
    for acct in accounts:
        pid = _platform_id(acct)
        if pid in TIKTOK_ALIASES:
            by_platform["tiktok"] = acct
            by_platform["tiktok_business"] = acct
        else:
            by_platform[pid] = acct

    # YouTube (Google-style)
    if "youtube" in by_platform:
        _check_google_style(by_platform["youtube"], "YouTube", result)
    else:
        result.log("  YouTube: RED — account absent")
        result.mark_red("YouTube absent")

    # TikTok (Google-style, either alias OK)
    tiktok = by_platform.get("tiktok_business") or by_platform.get("tiktok")
    if tiktok:
        _check_google_style(tiktok, "TikTok", result)
    else:
        result.log("  TikTok: RED — account absent")
        result.mark_red("TikTok absent")

    # Instagram (Meta-style)
    if "instagram" in by_platform:
        _check_meta_style(by_platform["instagram"], "Instagram", result)
    else:
        result.log("  Instagram: RED — account absent")
        result.mark_red("Instagram absent")

    # Facebook (Meta-style)
    if "facebook" in by_platform:
        _check_meta_style(by_platform["facebook"], "Facebook", result)
    else:
        result.log("  Facebook: RED — account absent")
        result.mark_red("Facebook absent")


# ---------------------------------------------------------------------------
# STEP 3 — Disk space
# ---------------------------------------------------------------------------
def check_disk(result: Result) -> None:
    result.log("STEP 3 — Disk space")
    try:
        usage = shutil.disk_usage(str(SCRIPT_DIR))
    except OSError as exc:
        result.log(f"  RED — disk_usage failed: {exc}")
        result.mark_red("disk_usage failed")
        return

    free_gb = usage.free / (1024**3)
    total_gb = usage.total / (1024**3)
    result.log(f"  Free: {free_gb:.1f} GB of {total_gb:.1f} GB")

    if free_gb < DISK_RED_GB:
        result.mark_red(f"disk {free_gb:.1f}GB free")
    elif free_gb < DISK_YELLOW_GB:
        result.mark_yellow(f"disk {free_gb:.1f}GB free")


# ---------------------------------------------------------------------------
# STEP 4 — Today's batch manifest
# ---------------------------------------------------------------------------
def check_manifest(result: Result) -> None:
    result.log("STEP 4 — Today's batch manifest")
    today = datetime.now().date()
    today_str = today.isoformat()
    # weekday(): Mon=0 .. Sun=6. Sunday is ops-only, no posts.
    is_sunday = today.weekday() == 6

    if not BATCHES_DIR.exists():
        msg = f"batches dir missing ({BATCHES_DIR})"
        result.log(f"  {msg}")
        if is_sunday:
            result.log("  OK — Sunday is ops-only, no batch expected")
            return
        result.mark_yellow(msg)
        return

    # Look for any batch dir containing today's date in its name that holds a
    # readable manifest.json referencing today.
    candidates = sorted(BATCHES_DIR.glob(f"*{today_str}*"))
    found_manifest: Path | None = None
    for cand in candidates:
        mf = cand / "manifest.json"
        if mf.exists():
            found_manifest = mf
            break

    if found_manifest is None:
        msg = f"no manifest covering {today_str}"
        if is_sunday:
            result.log(f"  OK — Sunday ops-only, no batch needed ({msg})")
            return
        result.log(f"  YELLOW — {msg}")
        result.mark_yellow(msg)
        return

    try:
        with found_manifest.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"manifest unparseable: {exc}"
        result.log(f"  YELLOW — {msg} ({found_manifest})")
        result.mark_yellow(msg)
        return

    slots = data.get("slots") or []
    today_slot_hit = any(
        (slot.get("day") == today_str) or (slot.get("scheduled_at", "").startswith(today_str))
        for slot in slots
    )
    if not today_slot_hit and not is_sunday:
        msg = f"manifest {found_manifest.parent.name} has no slot for {today_str}"
        result.log(f"  YELLOW — {msg}")
        result.mark_yellow(msg)
    else:
        result.log(f"  OK — {found_manifest.parent.name}")


# ---------------------------------------------------------------------------
# STEP 5 — Telegram
# ---------------------------------------------------------------------------
def send_telegram(result: Result) -> None:
    overall = result.overall()
    if overall == "RED":
        issues = ", ".join(result.issues_red) or "unspecified"
        message = f"PRPATH PREFLIGHT 🔴 RED — {issues}"
    elif overall == "YELLOW":
        issues = ", ".join(result.issues_yellow) or "unspecified"
        message = f"PRPATH PREFLIGHT 🟡 YELLOW — {issues}"
    else:
        message = "PRPATH PREFLIGHT ✅ GREEN — env+oauth+disk+manifest OK"

    try:
        subprocess.run(
            [sys.executable, str(NOTIFY_PATH), message],
            cwd=str(SCRIPT_DIR),
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        # Don't let a Telegram outage mask the real preflight result.
        print(f"WARN: notify.py invocation failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def main() -> int:
    result = Result()
    check_env(result)
    check_postforme_accounts(result)
    check_disk(result)
    check_manifest(result)

    print("\n".join(result.lines))
    print(f"\nOVERALL: {result.overall()}")

    send_telegram(result)

    return 1 if result.severity == RED else 0


if __name__ == "__main__":
    raise SystemExit(main())
