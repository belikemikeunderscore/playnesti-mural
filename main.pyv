import asyncio
import discord
from discord.ext import commands
from aiohttp import web

import config
from state import AppState
from web.server import create_app

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
bot.state = AppState()

COGS = [
    "cogs.media_wall",
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

    await bot.start(config.DISCORD_TOKEN)


asyncio.run(main())
