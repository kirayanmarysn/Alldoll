#!/usr/bin/env python3
"""
Reclip Telegram Bot \u2014 with Railway cookie support
Downloads videos from 1000+ sites and sends them in Telegram chat.

Cookie setup on Railway (two formats supported):
  Option A \u2014 Plain Netscape cookies.txt:
    Variables tab \u2192 add COOKIES_TXT \u2192 paste the full contents of your cookies.txt

  Option B \u2014 Base64-encoded cookies (Netscape or JSON):
    Variables tab \u2192 add COOKIES_B64 \u2192 paste your base64 string

How to get cookies.txt:
  1. Install "Get cookies.txt LOCALLY" browser extension
  2. Log in to Instagram in your browser
  3. Export \u2192 copy the full file text
  4. Paste into Railway's COOKIES_TXT variable value
     OR base64-encode it and paste into COOKIES_B64
"""

import os
import re
import json
import base64
import logging
import tempfile
import asyncio
from pathlib import Path

from telegram import Update, constants
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import yt_dlp

# \u2500\u2500 Logging \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# \u2500\u2500 Config \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN environment variable first.")

MAX_FILE_BYTES = 50 * 1024 * 1024
URL_RE = re.compile(r"https?://[^\s]+")

# \u2500\u2500 Write cookies to a temp file once at startup \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
_COOKIES_FILE: str | None = None


def _json_cookies_to_netscape(raw: str) -> str:
    """Convert a JSON cookie export (array or wrapped object) to Netscape format."""
    data = json.loads(raw)
    if isinstance(data, dict):
        # Some exporters wrap as {"cookies": [...]}
        data = data.get("cookies", [data])
    lines = ["# Netscape HTTP Cookie File\n"]
    for c in data:
        domain = c.get("domain", "")
        include_subdomains = "TRUE" if domain.startswith(".") else "FALSE"
        path = c.get("path", "/")
        secure = "TRUE" if c.get("secure", False) else "FALSE"
        expires = str(int(c.get("expirationDate", c.get("expires", 0)) or 0))
        name = c.get("name", "")
        value = c.get("value", "")
        lines.append(
            f"{domain}\t{include_subdomains}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n"
        )
    return "".join(lines)


def _init_cookies() -> None:
    global _COOKIES_FILE

    # \u2500\u2500 Option A: plain Netscape cookies.txt \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    plain = os.environ.get("COOKIES_TXT", "").strip()
    if plain:
        content = plain
        source = "COOKIES_TXT (plain)"

    # \u2500\u2500 Option B: base64-encoded cookies \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
    else:
        b64 = os.environ.get("COOKIES_B64", "").strip()
        if not b64:
            logger.info("No COOKIES_TXT or COOKIES_B64 set \u2014 cookies disabled.")
            return
        try:
            decoded = base64.b64decode(b64).decode("utf-8").strip()
        except Exception as e:
            logger.error("Failed to base64-decode COOKIES_B64: %s", e)
            return

        # Auto-detect JSON vs Netscape
        if decoded.startswith("[") or decoded.startswith("{"):
            logger.info("COOKIES_B64 detected as JSON \u2014 converting to Netscape format.")
            try:
                content = _json_cookies_to_netscape(decoded)
            except Exception as e:
                logger.error("Failed to convert JSON cookies to Netscape format: %s", e)
                return
        else:
            content = decoded

        source = "COOKIES_B64 (base64)"

    try:
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".txt", prefix="cookies_", mode="w"
        )
        tmp.write(content)
        tmp.flush()
        tmp.close()
        _COOKIES_FILE = tmp.name
        logger.info("Cookies written to %s (source: %s)", _COOKIES_FILE, source)
    except Exception as e:
        logger.error("Failed to write cookies file: %s", e)


# \u2500\u2500 yt-dlp helpers \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

def _ydl_opts(out_path: str) -> dict:
    opts = {
        "format": (
            "bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]"
            "/bestvideo[height
