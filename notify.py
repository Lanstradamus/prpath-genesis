#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram alerter used by every PRPath Cowork session.

Usage:
    python3 notify.py "MESSAGE"

Exit codes:
    0 — message delivered
    1 — delivery failed (placeholder creds, HTTP error, network error, or bad args)

Why are the Telegram creds hardcoded instead of in .env?
    Gotcha #5 in PRPath Cowork Scheduled Sessions — the .env sanity check in
    preflight.py would otherwise yell about missing TELEGRAM_* keys on every
    run. Keeping them here means notify.py stays standalone and the .env check
    stays focused on API keys that actually rotate.

Dependencies: requests
"""

from __future__ import annotations

import sys
from typing import Final

import requests

# ---------------------------------------------------------------------------
# Telegram credentials
# ---------------------------------------------------------------------------
# HOW TO FILL THESE IN:
#   1. Message @BotFather on Telegram -> /newbot (or reuse existing bot token)
#      -> copy the "HTTP API" token into BOT_TOKEN below.
#   2. Send any message to your bot from the Telegram chat you want alerts in.
#   3. Visit https://api.telegram.org/bot<TOKEN>/getUpdates in a browser.
#      Find the "chat":{"id": ...} value — that is your CHAT_ID.
#   4. Replace the placeholders below with the real values.
# ---------------------------------------------------------------------------
BOT_TOKEN: Final[str] = "REPLACE_ME_TELEGRAM_BOT_TOKEN"
CHAT_ID: Final[str] = "REPLACE_ME_TELEGRAM_CHAT_ID"

TELEGRAM_API_TIMEOUT_SEC: Final[int] = 15


def _creds_are_placeholders() -> bool:
    """Return True if either BOT_TOKEN or CHAT_ID still holds the placeholder."""
    return BOT_TOKEN.startswith("REPLACE_ME_") or CHAT_ID.startswith("REPLACE_ME_")


def send_message(message: str) -> bool:
    """Send `message` to Telegram. Returns True on HTTP 2xx, False otherwise.

    Does not raise on network or HTTP errors — logs to stderr and returns False
    so callers can continue their flow even when Telegram is unreachable.
    """
    if _creds_are_placeholders():
        print(
            "ERROR: notify.py BOT_TOKEN / CHAT_ID are still placeholders. "
            "Edit /Users/lancesessions/Developer/prpath-genesis/notify.py and "
            "fill them in per the instructions at the top of the file.",
            file=sys.stderr,
        )
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}

    try:
        resp = requests.post(url, data=payload, timeout=TELEGRAM_API_TIMEOUT_SEC)
    except requests.RequestException as exc:
        print(f"ERROR: Telegram request failed: {exc}", file=sys.stderr)
        return False

    if not resp.ok:
        # Avoid leaking the token; only surface status + response body snippet.
        snippet = resp.text[:500] if resp.text else ""
        print(
            f"ERROR: Telegram returned HTTP {resp.status_code}: {snippet}",
            file=sys.stderr,
        )
        return False

    return True


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print('Usage: python3 notify.py "MESSAGE"', file=sys.stderr)
        return 1

    message = argv[1]
    if not message.strip():
        print("ERROR: refusing to send empty message.", file=sys.stderr)
        return 1

    if send_message(message):
        print(message)
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
