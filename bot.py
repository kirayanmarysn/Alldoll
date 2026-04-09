#!/usr/bin/env python3
"""
Reclip Telegram Bot — with Railway cookie support
Downloads videos from 1000+ sites and sends them in Telegram chat.

Cookie setup on Railway:
  Variables tab → add COOKIES_TXT → paste the full contents of your cookies.txt

How to get cookies.txt:
  1. Install "Get cookies.txt LOCALLY" browser extension
  2. Log in to Instagram in your browser
  3. Export → copy the full file text
  4. Paste into Railway's COOKIES_TXT variable value
"""

import os
import re
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

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN environment variable first.")

MAX_FILE_BYTES = 50 * 1024 * 1024
URL_RE = re.compile(r"https?://[^\s]+")

# ── Write COOKIES_TXT env var to a temp file once at startup ──────────────────
_COOKIES_FILE: str | None = None

def _init_cookies() -> None:
    global _COOKIES_FILE
    content = os.environ.get("COOKIES_TXT", "").strip()
    if not content:
        logger.info("No COOKIES_TXT set — cookies disabled.")
        return
    try:
        tmp = tempfile.NamedTemporaryFile(
            delete=False, suffix=".txt", prefix="cookies_", mode="w"
        )
        tmp.write(content)
        tmp.flush()
        tmp.close()
        _COOKIES_FILE = tmp.name
        logger.info("Cookies written to %s", _COOKIES_FILE)
    except Exception as e:
        logger.error("Failed to write cookies: %s", e)


# ── yt-dlp helpers ────────────────────────────────────────────────────────────

def _ydl_opts(out_path: str) -> dict:
    opts = {
        "format": (
            "bestvideo[ext=mp4][height<=480]+bestaudio[ext=m4a]"
            "/bestvideo[height<=480]+bestaudio"
            "/best[height<=480]"
            "/best"
        ),
        "outtmpl": out_path,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "max_filesize": MAX_FILE_BYTES,
        "postprocessors": [
            {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}
        ],
    }
    if _COOKIES_FILE:
        opts["cookiefile"] = _COOKIES_FILE
    return opts


def download_video(url: str, tmpdir: str) -> tuple[str | None, str | None]:
    out_template = os.path.join(tmpdir, "%(id)s.%(ext)s")
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(out_template)) as ydl:
            info = ydl.extract_info(url, download=True)
            filepath = ydl.prepare_filename(info)
            if not os.path.exists(filepath):
                for f in Path(tmpdir).iterdir():
                    if f.suffix in (".mp4", ".mkv", ".webm", ".mov"):
                        filepath = str(f)
                        break
            return filepath, info.get("title", "video")
    except yt_dlp.utils.DownloadError as e:
        return None, str(e)
    except Exception as e:
        return None, f"Unexpected error: {e}"


# ── Telegram handlers ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cookies_status = "✅ Cookies loaded" if _COOKIES_FILE else "❌ No cookies (Instagram may fail)"
    await update.message.reply_text(
        "👋 *Reclip Bot* — powered by yt-dlp\n\n"
        "Send me any video link and I'll download it for you.\n\n"
        "Supported: YouTube, TikTok, Instagram, Twitter/X, Reddit, "
        "Facebook, Vimeo, Twitch, Dailymotion, Loom, and 1000+ more.\n\n"
        f"🍪 {cookies_status}\n\n"
        "⚠️ Videos are capped at 50 MB (Telegram's limit).",
        parse_mode=constants.ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Just paste a video URL — that's it!\n\n"
        "Commands:\n"
        "  /start  — welcome & cookie status\n"
        "  /help   — this message\n\n"
        "🍪 Instagram not working?\n"
        "On Railway: Variables → add COOKIES_TXT → paste your cookies.txt contents.\n\n"
        "Limitations:\n"
        "• Max 50 MB per video\n"
        "• Max quality 480p\n"
        "• No playlist support (first video only)"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    match = URL_RE.search(text)

    if not match:
        await update.message.reply_text(
            "Please send a video URL (YouTube, TikTok, Instagram, etc.)"
        )
        return

    url = match.group(0)
    status_msg = await update.message.reply_text("⏳ Downloading… please wait.")
    loop = asyncio.get_event_loop()

    with tempfile.TemporaryDirectory() as tmpdir:
        filepath, info = await loop.run_in_executor(
            None, download_video, url, tmpdir
        )

        if filepath is None:
            await status_msg.edit_text(f"❌ Download failed:\n{info}")
            return

        size = os.path.getsize(filepath)
        if size > MAX_FILE_BYTES:
            await status_msg.edit_text(
                f"❌ File too large ({size // 1_048_576} MB). "
                "Telegram bots are limited to 50 MB."
            )
            return

        await status_msg.edit_text("📤 Uploading…")
        try:
            with open(filepath, "rb") as video_file:
                await update.message.reply_video(
                    video=video_file,
                    caption=f"🎬 {info}",
                    supports_streaming=True,
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=60,
                )
            await status_msg.delete()
        except Exception as e:
            logger.exception("Upload failed")
            await status_msg.edit_text(f"❌ Upload failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    _init_cookies()  # Load cookies from env var at startup

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    logger.info("Bot is running. Press Ctrl-C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
