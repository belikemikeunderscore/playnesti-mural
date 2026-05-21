import json
import pathlib
import aiohttp
import discord
from discord.ext import commands

import config

MEDIA_DIR = pathlib.Path("media")
METADATA_PATH = MEDIA_DIR / "metadata.json"


def _media_type(filename: str) -> str | None:
    ext = pathlib.Path(filename).suffix.lower()
    if ext in config.IMAGE_EXTENSIONS:
        return "image"
    if ext in config.VIDEO_EXTENSIONS:
        return "video"
    return None


def _item_from_filename(filename: str) -> dict | None:
    media_type = _media_type(filename)
    if not media_type:
        return None
    return {
        "url": f"/media/{filename}",
        "type": media_type,
        "author": "",
        "text": "",
    }


class MediaWall(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        MEDIA_DIR.mkdir(exist_ok=True)
        self.message_media = {}
        self.bot.state.media_items = self._load_metadata()

    def _load_metadata(self) -> list[dict]:
        items = []
        message_media = {}

        if METADATA_PATH.exists():
            try:
                data = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
                items = [item for item in data.get("items", []) if self._valid_item(item)]
                raw_message_media = data.get("message_media", {}) or {}
                for key, urls in raw_message_media.items():
                    try:
                        message_media[int(key)] = [url for url in urls if self._url_exists(url)]
                    except ValueError:
                        continue
            except Exception:
                items = []
                message_media = {}

        # Include any files on disk not in metadata yet.
        disk_files = {
            path.name
            for path in MEDIA_DIR.iterdir()
            if path.is_file() and path.name != METADATA_PATH.name
        }
        known_urls = {item["url"] for item in items}
        for filename in sorted(disk_files):
            url = f"/media/{filename}"
            if url not in known_urls:
                extra_item = _item_from_filename(filename)
                if extra_item:
                    items.append(extra_item)

        self.message_media = message_media
        self._save_metadata(items, message_media)
        return items

    def _save_metadata(self, items: list[dict], message_media: dict[str, list[str]]):
        payload = {
            "items": items,
            "message_media": {str(key): urls for key, urls in message_media.items()},
        }
        METADATA_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _valid_item(self, item: dict) -> bool:
        if not isinstance(item, dict):
            return False
        url = item.get("url")
        if not isinstance(url, str):
            return False
        return self._url_exists(url)

    def _url_exists(self, url: str) -> bool:
        if not url.startswith("/media/"):
            return False
        return (MEDIA_DIR / url.removeprefix("/media/")).exists()

    async def _save_state(self):
        self._save_metadata(self.bot.state.media_items, self.message_media)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.channel.id != config.MEDIA_CHANNEL_ID:
            return

        media_attachments = [
            (a, _media_type(a.filename))
            for a in message.attachments
            if _media_type(a.filename)
        ]

        if not media_attachments:
            await message.delete()
            try:
                await message.author.send(
                    f"❌ O mural só aceita mensagens com imagens ou vídeos!"
                )
            except discord.Forbidden:
                pass
            return

        items = await self._download_attachments(media_attachments, message)
        self.bot.state.media_items.extend(items)
        self.message_media[message.id] = [item["url"] for item in items]
        self._save_metadata(self.bot.state.media_items, self.message_media)
        await self._broadcast({"type": "add", "items": items})

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.channel.id != config.MEDIA_CHANNEL_ID:
            return

        if message.id not in self.message_media:
            return

        media_urls = self.message_media.pop(message.id)
        for url in media_urls:
            self.bot.state.media_items = [
                item for item in self.bot.state.media_items
                if item["url"] != url
            ]
            try:
                path = MEDIA_DIR / url.removeprefix("/media/")
                if path.exists():
                    path.unlink()
            except Exception:
                pass
            await self._broadcast({"type": "remove", "id": url})

        self._save_metadata(self.bot.state.media_items, self.message_media)

    async def _download_attachments(
        self,
        media_attachments: list[tuple[discord.Attachment, str]],
        message: discord.Message,
    ) -> list[dict]:
        items = []
        async with aiohttp.ClientSession() as session:
            for attachment, media_type in media_attachments:
                ext = pathlib.Path(attachment.filename).suffix.lower()
                filename = f"{attachment.id}{ext}"
                dest = MEDIA_DIR / filename

                async with session.get(attachment.url) as resp:
                    if resp.status == 200:
                        dest.write_bytes(await resp.read())

                items.append({
                    "url": f"/media/{filename}",
                    "type": media_type,
                    "author": message.author.display_name,
                    "text": message.content.strip() if message.content else "",
                    "message_id": message.id,
                })

        return items

    async def _broadcast(self, data: dict):
        dead = set()
        for ws in self.bot.state.ws_clients:
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        self.bot.state.ws_clients -= dead


async def setup(bot: commands.Bot):
    await bot.add_cog(MediaWall(bot))
