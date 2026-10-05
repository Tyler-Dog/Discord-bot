import logging
import os

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from utils.activity import ActivityLog
from utils.claude import ClaudeClient
from utils.db import Database
from utils.logging_setup import setup_logging
from utils.settings import BotTree, Settings

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

setup_logging()
logger = logging.getLogger("discord_bot")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True  # privileged: needed for welcome messages and member lookups
# Privileged and OFF by default. Required only for /summarize and AutoMod's invite/word filters.
# Enable it in the Developer Portal first, then set MESSAGE_CONTENT_INTENT=true.
intents.message_content = os.getenv("MESSAGE_CONTENT_INTENT", "false").lower() in ("1", "true", "yes")

EXTENSIONS = (
    "cogs.arc_raiders",
    "cogs.welcome",
    "cogs.help",
    "cogs.tickets",
    "cogs.ai",
    "cogs.leveling",
    "cogs.moderation",
    "cogs.automod",
    "cogs.config",
    "cogs.polls",
    "cogs.reminders",
    "cogs.utility",
    "cogs.dashboard",
    "cogs.health",
)


class MyBot(commands.Bot):
    db: Database
    settings: Settings
    claude: ClaudeClient
    activity_log: ActivityLog

    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents, tree_cls=BotTree)
        self.tree.on_error = self.on_app_command_error
        self.activity_log = ActivityLog()
        self.claude = ClaudeClient.from_env()

    async def setup_hook(self) -> None:
        self.db = await Database.open()
        self.settings = Settings(self.db)
        if not self.claude.enabled:
            logger.warning("ANTHROPIC_API_KEY not set — AI features (/ask, /summarize, ticket triage) are disabled.")
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
        await self.claude.close()
        await self.db.close()

    async def on_ready(self) -> None:
        if self.user is None:
            return
        logger.info("Logged in as %s (%s)", self.user, self.user.id)
        await self.change_presence(
            status=discord.Status.online, activity=discord.Game(name="/helpme for commands")
        )

    async def on_app_command_completion(self, interaction: discord.Interaction, command) -> None:
        self.activity_log.command(command.qualified_name)
        where = interaction.guild.name if interaction.guild else "DM"
        self.activity_log.add("command", f"{interaction.user.display_name} used /{command.qualified_name} in {where}")

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
        elif isinstance(error, app_commands.CheckFailure):
            if interaction.response.is_done():
                return  # a check (e.g. a disabled module) already answered
            msg = "🚫 You can't use that command here."
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
