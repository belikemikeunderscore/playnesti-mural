"""
cogs/vr_expo.py  —  PlayNESTI VR Expo Bridge Cog
─────────────────────────────────────────────────
Exposes a local HTTP server (aiohttp) that the VR dashboard
calls to DM a waitlisted player when it's their turn.

Requirements:
  pip install aiohttp

Config (environment variables or hardcode below):
  VR_BRIDGE_PORT  — port for the local HTTP bridge (default 6001)
  VR_BRIDGE_TOKEN — shared secret so only the dashboard can trigger DMs
"""

import os
import logging
import asyncio
from aiohttp import web
import discord
from discord.ext import commands

log = logging.getLogger(__name__)

BRIDGE_PORT  = int(os.environ.get("VR_BRIDGE_PORT", 6001))
BRIDGE_TOKEN = os.environ.get("VR_BRIDGE_TOKEN", "playnesti-vr-bridge")

DM_TEMPLATE = (
    "Boas {name}! 👋\n\n"
    "O teu lugar na exposição VR vai começar daqui a **{minutes} Minuto{'s' if {minutes} != 1 else ''}**, "
    "vem à bancada e apresenta o teu crachá. "
    "Para todos os efeitos, damos-te **5 MINUTOS** de compensação de atraso.\n\n"
    "Boa LAN! 🎮"
)

def build_dm(name: str, minutes: int) -> str:
    plural = "s" if minutes != 1 else ""
    return (
        f"Boas {name}! 👋\n\n"
        f"O teu lugar na exposição VR vai começar daqui a **{minutes} Minuto{plural}**, "
        f"vem à bancada e apresenta o teu crachá. "
        f"Para todos os efeitos, damos-te **5 MINUTOS** de compensação de atraso.\n\n"
        f"Boa LAN! 🎮"
    )


class VRExpo(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot     = bot
        self._site   = None
        self._runner = None

    async def cog_load(self):
        await self._start_bridge()

    async def cog_unload(self):
        if self._runner:
            await self._runner.cleanup()
            log.info("[VRExpo] Bridge server stopped.")

    # ── HTTP Bridge ───────────────────────────────────────────────────────────

    async def _start_bridge(self):
        app = web.Application()
        app.router.add_post("/notify", self._handle_notify)
        app.router.add_get("/health",  self._handle_health)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", BRIDGE_PORT)
        await self._site.start()
        log.info(f"[VRExpo] Bridge listening on http://127.0.0.1:{BRIDGE_PORT}")

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "bot": str(self.bot.user)})

    async def _handle_notify(self, request: web.Request) -> web.Response:
        # ── Auth ──────────────────────────────────────────────────────────────
        token = request.headers.get("X-Bridge-Token", "")
        if token != BRIDGE_TOKEN:
            return web.json_response({"error": "Unauthorized"}, status=401)

        # ── Payload ───────────────────────────────────────────────────────────
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        discord_handle = (data.get("discord") or "").strip()
        player_name    = (data.get("name")    or "Jogador").strip()
        minutes        = int(data.get("minutes", 5))

        if not discord_handle:
            return web.json_response({"error": "discord handle required"}, status=400)

        # ── Resolve user ──────────────────────────────────────────────────────
        user = await self._resolve_user(discord_handle)

        if user is None:
            log.warning(f"[VRExpo] Could not resolve Discord user: {discord_handle}")
            return web.json_response({
                "ok":    False,
                "error": f"Utilizador '{discord_handle}' não está no servidor ou não pode ser encontrado."
            }, status=404)

        # ── Send DM ───────────────────────────────────────────────────────────
        try:
            await user.send(build_dm(player_name, minutes))
            log.info(f"[VRExpo] DM sent to {user} ({discord_handle})")
            return web.json_response({"ok": True, "sent_to": str(user)})
        except discord.Forbidden:
            return web.json_response({
                "ok":    False,
                "error": f"{discord_handle} tem os DMs desativados."
            }, status=422)
        except Exception as e:
            log.error(f"[VRExpo] DM failed: {e}")
            return web.json_response({"error": str(e)}, status=500)

    # ── User resolution ───────────────────────────────────────────────────────

    async def _resolve_user(self, handle: str) -> discord.User | None:
        """
        Tries in order:
          1. Numeric ID (e.g. "123456789")
          2. New-style username (e.g. "mike" or "mike.playnesti")
          3. Legacy tag (e.g. "Mike#1234")  — still works if discriminator stored
          4. Display name match across all shared guilds
        """
        handle = handle.strip().lstrip("@")

        # 1. Numeric ID
        if handle.isdigit():
            try:
                return await self.bot.fetch_user(int(handle))
            except Exception:
                pass

        # 2 & 3. Search across all guilds the bot is in
        handle_lower = handle.lower()
        name_part, _, discrim = handle.partition("#")
        name_part_lower = name_part.lower()

        for guild in self.bot.guilds:
            # Ensure member cache is populated
            if guild.chunked is False:
                try:
                    await guild.chunk()
                except Exception:
                    pass

            for member in guild.members:
                # Legacy tag match
                if discrim and member.discriminator == discrim:
                    if member.name.lower() == name_part_lower:
                        return member

                # New username match (global_name is display name; name is @username)
                if member.name.lower() == handle_lower:
                    return member

                # Display name / nick match (fallback)
                if (member.display_name.lower() == handle_lower or
                    (member.global_name and member.global_name.lower() == handle_lower)):
                    return member

        return None

async def setup(bot: commands.Bot):
    await bot.add_cog(VRExpo(bot))