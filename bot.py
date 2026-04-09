#!/usr/bin/env python3
"""
Reclip Telegram Bot
Downloads videos from 1000+ sites (YouTube, TikTok, Instagram, Twitter/X, Reddit, etc.)
and sends them directly in the Telegram chat.

Usage:
  1. Install deps: pip install python-telegram-bot yt-dlp
  2. Set your bot token: export TELEGRAM_BOT_TOKEN="your_token_here"
  3. Run: python bot.py
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

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("Set TELEGRAM_BOT_TOKEN environment variable first.")

# Telegram file-size limit for bots (50 MB)
MAX_FILE_BYTES = 50 * 1024 * 1024

# Regex: extract the first URL from a message
URL_RE = re.compile(r"https?://[^\s]+")

# ── yt-dlp helpers ────────────────────────────────────────────────────────────

def _ydl_opts(out_path: str) -> dict:
    """Return yt-dlp options that produce a single MP4 ≤ 50 MB."""
    return {
        # Best video+audio merged into mp4, capped at ~480p to stay under 50 MB
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
        # Cap single-file size; yt-dlp will abort if exceeded
        # (not all extractors honour this, but it helps)
        "max_filesize": MAX_FILE_BYTES,
        "postprocessors": [
            {
                "key": "FFmpegVideoConvertor",
                "preferedformat": "mp4",
            }
        ],
    }


def download_video(url: str, tmpdir: str) -> tuple[str | None, str | None]:
    """
    Download *url* into *tmpdir*.

    Returns (filepath, title) on success, or (None, error_message) on failure.
    """
    out_template = os.path.join(tmpdir, "%(id)s.%(ext)s")
    opts = _ydl_opts(out_template)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # Resolve the actual output filename
            filepath = ydl.prepare_filename(info)
            # yt-dlp may change extension after merge
            if not os.path.exists(filepath):
                for f in Path(tmpdir).iterdir():
                    if f.suffix in (".mp4", ".mkv", ".webm", ".mov"):
                        filepath = str(f)
                        break
            title = info.get("title", "video")
            return filepath, title
    except yt_dlp.utils.DownloadError as e:
        return None, str(e)
    except Exception as e:
        return None, f"Unexpected error: {e}"


# ── Telegram handlers ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Reclip Bot* — powered by yt-dlp\n\n"
        "Send me any video link and I'll download it for you.\n\n"
        "Supported: YouTube, TikTok, Instagram, Twitter/X, Reddit, "
        "Facebook, Vimeo, Twitch, Dailymotion, Loom, and 1000+ more.\n\n"
        "⚠️ Videos are capped at 50 MB (Telegram's limit). "
        "For very long videos I'll try a lower quality automatically.",
        parse_mode=constants.ParseMode.MARKDOWN,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Just paste a video URL in the chat — that's it!\n\n"
        "Commands:\n"
        "  /start — welcome message\n"
        "  /help  — this message\n\n"
        "Limitations:\n"
        "• Max file size: 50 MB\n"
        "• Private/age-gated content may not work\n"
        "• Playlists are not supported (first video only)"
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    match = URL_RE.search(text)

    if not match:
        await update.message.reply_text(
            "Please send a video URL (e.g. a YouTube, TikTok or Instagram link)."
        )
        return

    url = match.group(0)
    status_msg = await update.message.reply_text("⏳ Downloading… please wait.")

    loop = asyncio.get_event_loop()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Run blocking yt-dlp download in a thread pool
        filepath, info = await loop.run_in_executor(
            None, download_video, url, tmpdir
        )

        if filepath is None:
            await status_msg.edit_text(f"❌ Download failed:\n{info}")
            return

        # Check file size
        size = os.path.getsize(filepath)
        if size > MAX_FILE_BYTES:
            await status_msg.edit_text(
                f"❌ File is too large ({size // 1_048_576} MB). "
                "Telegram bots can only send files up to 50 MB.\n"
                "Try a shorter clip or use /start to learn more."
            )
            return

        await status_msg.edit_text("📤 Uploading to Telegram…")

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
            logger.exception("Failed to send video")
            await status_msg.edit_text(f"❌ Upload failed: {e}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    logger.info("Bot is running. Press Ctrl-C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
