import pathlib
import aiohttp
import discord
from discord.ext import commands

import config

MEDIA_DIR = pathlib.Path("media")


def _media_type(filename: str) -> str | None:
    ext = pathlib.Path(filename).suffix.lower()
    if ext in config.IMAGE_EXTENSIONS:
        return "image"
    if ext in config.VIDEO_EXTENSIONS:
        return "video"
    return None


class MediaWall(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        MEDIA_DIR.mkdir(exist_ok=True)

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
                    f"❌ O canal **#{message.channel.name}** só aceita mensagens com imagens ou vídeos."
                )
            except discord.Forbidden:
                pass
            return

        items = await self._download_attachments(media_attachments, message)
        self.bot.state.media_items.extend(items)
        await self._broadcast({"type": "add", "items": items})

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
