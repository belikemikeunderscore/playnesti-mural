import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
if not DISCORD_TOKEN:
    raise RuntimeError("DISCORD_TOKEN is not set. Add it to .env or environment variables.")

MEDIA_CHANNEL_ID = int(os.getenv("MEDIA_CHANNEL_ID", "1501957061750296627"))

LFT_ANNOUNCEMENT_CHANNEL_ID = None
raw_lft_channel = os.getenv("LFT_ANNOUNCEMENT_CHANNEL_ID", "1507035149194494083").strip()
if raw_lft_channel:
    try:
        LFT_ANNOUNCEMENT_CHANNEL_ID = int(raw_lft_channel)
    except ValueError:
        raise RuntimeError("LFT_ANNOUNCEMENT_CHANNEL_ID must be a valid integer channel ID.")

WEB_HOST = os.getenv("WEB_HOST", "localhost")
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))

MODERATION_PASSWORD = os.getenv("MODERATION_PASSWORD", "playnesti!26!mural")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".avi", ".mkv"}

# Optional bot presence configuration
# BOT_ACTIVITY can be one of: playing, watching, listening
BOT_STATUS = os.getenv("BOT_STATUS", "Vigiando o PlayNESTI26!")
BOT_ACTIVITY = os.getenv("BOT_ACTIVITY", "playing").lower()
