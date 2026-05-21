import asyncio
import discord
from discord.ext import commands
from aiohttp import web

import config
from state import AppState
from web.server import create_app

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.presences = True
intents.presences = True

bot = commands.Bot(command_prefix=None, intents=intents)
bot.state = AppState()


@bot.event
async def on_ready():
    try:
        act = getattr(config, 'BOT_ACTIVITY', 'playing')
        name = getattr(config, 'BOT_STATUS', 'PlayNESTI Mural')
        act = (act or 'playing').lower()
        if act == 'playing':
            activity = discord.Game(name=name)
        elif act == 'watching':
            activity = discord.Activity(type=discord.ActivityType.watching, name=name)
        elif act == 'listening':
            activity = discord.Activity(type=discord.ActivityType.listening, name=name)
        else:
            activity = discord.Game(name=name)

        await bot.change_presence(activity=activity, status=discord.Status.online)
        print(f"Bot ready: {bot.user} — presence set: {act} {name}")
        
        # Sync app commands now that the bot is ready
        try:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} command(s) to Discord")
        except Exception as e:
            print(f"Failed to sync commands: {e}")
    except Exception as e:
        print("Error setting presence:", e)

COGS = [
    "cogs.media_wall",
    "cogs.vr_expo",
    "cogs.server_role_manager",
    "cogs.lft",
]

async def main():
    for cog in COGS:
        await bot.load_extension(cog)

    app = create_app(bot.state)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, config.WEB_HOST, config.WEB_PORT)
    await site.start()
    print(f"Wall: http://{config.WEB_HOST}:{config.WEB_PORT}")
    print(f"Moderation: http://{config.WEB_HOST}:{config.WEB_PORT}/moderation")

    await bot.start(config.DISCORD_TOKEN)


asyncio.run(main())
