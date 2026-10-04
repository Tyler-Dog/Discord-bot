import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from utils.db import Database

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("discord_bot")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # privileged: needed for welcome messages and member lookups
intents.message_content = False  # not needed — XP only counts messages, never reads them

EXTENSIONS = (
    "cogs.arc_raiders",
    "cogs.welcome",
    "cogs.help",
    "cogs.tickets",
    "cogs.ai",
    "cogs.leveling",
    "cogs.moderation",
    "cogs.polls",
    "cogs.reminders",
    "cogs.utility",
)


class MyBot(commands.Bot):
    db: Database

    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents)
        self.tree.on_error = self.on_app_command_error

    async def setup_hook(self) -> None:
        self.db = await Database.open()
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
                logger.info("Loaded %s", ext)
            except Exception:
                logger.exception("Failed to load %s", ext)

        # Sync once at startup (not on every gateway reconnect).
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %s command(s) to guild %s (instant)", len(synced), GUILD_ID)
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            logger.info("Cleared global commands")
        else:
            synced = await self.tree.sync()
            logger.info("Synced %s global command(s) (may take up to 1 hour to appear)", len(synced))

    async def close(self) -> None:
        await super().close()
        await self.db.close()

    async def on_ready(self) -> None:
        if self.user is None:
            return
        logger.info("Logged in as %s (%s)", self.user, self.user.id)
        await self.change_presence(
            status=discord.Status.online, activity=discord.Game(name="/helpme for commands")
        )

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Turn the usual failures into friendly ephemeral messages instead of silent timeouts."""
        if isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ Slow down! Try again in **{error.retry_after:.0f}s**."
        elif isinstance(error, app_commands.MissingPermissions):
            msg = "🚫 You don't have permission to do that."
        elif isinstance(error, app_commands.BotMissingPermissions):
            missing = ", ".join(p.replace("_", " ") for p in error.missing_permissions)
            msg = f"🚫 I'm missing permissions: **{missing}**."
        elif isinstance(error, app_commands.NoPrivateMessage):
            msg = "This command only works in a server."
        else:
            logger.exception("Unhandled error in /%s", getattr(interaction.command, "name", "?"), exc_info=error)
            msg = "💥 Something went wrong. It's been logged."

        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass


bot = MyBot()


@bot.tree.command(name="ping", description="Check whether the bot is online.")
async def ping(interaction: discord.Interaction) -> None:
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"Pong! `{latency_ms} ms`")


@bot.tree.command(name="hello", description="Say hello to the bot.")
async def hello(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(f"Hey {interaction.user.mention}, I am online and ready.")


if __name__ == "__main__":
    if not TOKEN:
        raise RuntimeError("DISCORD_TOKEN is missing. Add it to your .env file.")
    bot.run(TOKEN, log_handler=None)
