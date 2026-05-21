"""
LFT (Looking For Team) Command
Allows players to announce they're looking for a team for a specific game.
"""

import logging
import discord
import config
from discord.ext import commands
from discord import app_commands
from typing import Optional

log = logging.getLogger("playnesti.lft")

# Common LAN party games - customize as needed
GAMES = {
    "CS2": {"emoji": "🎮", "color": discord.Color.red()},
    "Valorant": {"emoji": "🎯", "color": discord.Color.red()},
    "League of Legends": {"emoji": "👑", "color": discord.Color.red()},
}

# Role mention names for each game. The bot will try to mention the matching role in the guild.
GAME_ROLE_MENTIONS = {
    "CS2": "LFT - Counter-Strike 2",
    "Valorant": "LFT - Valorant",
    "League of Legends": "LFT -  League of Legends",
}



class GameSelectView(discord.ui.View):
    """View with buttons for game selection."""
    
    def __init__(self, user: discord.User, timeout: int = 300):
        super().__init__(timeout=timeout)
        self.user = user
        self.selected_game: Optional[str] = None
        
    async def on_timeout(self):
        """Called when the view times out."""
        for item in self.children:
            item.disabled = True
        message = getattr(self, "message", None)
        if message is not None:
            try:
                await message.edit(view=self)
            except Exception:
                pass
    
    @discord.ui.button(label="Counter-Strike 2", emoji="💣", style=discord.ButtonStyle.secondary)
    async def cs2_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_game_selection(interaction, "CS2")
    
    @discord.ui.button(label="Valorant", emoji="🎯", style=discord.ButtonStyle.secondary)
    async def valorant_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_game_selection(interaction, "Valorant")
    
    @discord.ui.button(label="League of Legends", emoji="👑", style=discord.ButtonStyle.secondary)
    async def lol_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_game_selection(interaction, "League of Legends")
    
    def get_role_mention(self, guild: discord.Guild, game: str) -> str:
        role_name = GAME_ROLE_MENTIONS.get(game)
        if not role_name or guild is None:
            return f"**{game}**"
        role = discord.utils.get(guild.roles, name=role_name)
        return role.mention if role else f"**{game}**"
    
    async def handle_game_selection(self, interaction: discord.Interaction, game: str):
        """Handle game selection and send LFT announcement."""
        if interaction.user.id != self.user.id:
            await interaction.response.send_message(
                "💢 Apenas o utilizador que iniciou o comando pode selecionar um jogo!",
                ephemeral=True
            )
            return
        
        self.selected_game = game
        
        # Defer privately so we can update the original ephemeral message
        await interaction.response.defer(ephemeral=True)
        
        # Disable all buttons on the ephemeral menu
        for item in self.children:
            item.disabled = True
        try:
            await interaction.edit_original_response(view=self)
        except Exception:
            pass
        
        # Get game config and role mention
        game_config = GAMES.get(game, {"emoji": "🎮", "color": discord.Color.blurple()})
        role_mention = self.get_role_mention(interaction.guild, game)
        
        announcement_embed = discord.Embed(
            title=f"{interaction.user.display_name} está à procura de jogadores para {game}!",
            description=f"Os jogadores com o cargo {role_mention} foram notificados.\nQueres ser notificado? Reage a esta mensagem para receberes os cargos de LFT para os jogos!",
            color=game_config["color"],
            timestamp=discord.utils.utcnow()
        )
        announcement_embed.set_thumbnail(url=interaction.user.display_avatar.url)
        announcement_embed.set_footer(text="Usa /LFT para encontrares os teus próximos parceiros!")

        announcement_content = f"🔍 {role_mention} — {interaction.user.mention} está à procura de equipa para {game}!"
        send_kwargs = {
            "content": announcement_content,
            "embed": announcement_embed,
        }

        if config.LFT_ANNOUNCEMENT_CHANNEL_ID and interaction.guild is not None:
            channel = interaction.guild.get_channel(config.LFT_ANNOUNCEMENT_CHANNEL_ID)
            if channel is not None:
                await channel.send(**send_kwargs)
                return

            log.warning(
                "[LFT] Announcement channel %s not found in guild %s; sending in command channel instead.",
                config.LFT_ANNOUNCEMENT_CHANNEL_ID,
                interaction.guild.id,
            )

        await interaction.followup.send(
            **send_kwargs,
            ephemeral=False
        )


class LFTCog(commands.Cog):
    """Looking For Team command cog."""
    
    def __init__(self, bot: commands.Bot):
        self.bot = bot
    
    @app_commands.command(name="lft", description="Anuncia que estás à procura de equipa para um jogo")
    async def lft(self, interaction: discord.Interaction):
        """Main LFT command - shows game selection buttons."""
        embed = discord.Embed(
            title="🎮 Para qual jogo estás à procura de equipa?",
            description="Clica num botão abaixo; o anúncio será enviado depois de clicares.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow()
        )
        embed.set_footer(text="Escolhe um jogo para continuar")
        
        view = GameSelectView(interaction.user, timeout=300)
        
        await interaction.response.send_message(
            embed=embed,
            view=view,
            ephemeral=True
        )


async def setup(bot: commands.Bot):
    """Load the LFT cog."""
    await bot.add_cog(LFTCog(bot))
    log.info("[LFT] Cog loaded successfully")
