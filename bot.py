import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv
from discord import app_commands

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = os.getenv("GUILD_ID")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing. Add it to your .env file.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("discord_bot")

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = False


class MyBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.arc_raiders")
        await self.load_extension("cogs.welcome")
        await self.load_extension("cogs.help")
        await self.load_extension("cogs.tickets")

    async def on_ready(self) -> None:
        if self.user is None:
            return
        logger.info("Logged in as %s (%s)", self.user, self.user.id)
        activity = discord.Game(name="/helpme for commands")
        await self.change_presence(status=discord.Status.online, activity=activity)

        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            all_cmds = self.tree.get_commands()
            logger.info("Commands in tree before sync: %s", [c.name for c in all_cmds])
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %s command(s) to guild %s (instant)", len(synced), GUILD_ID)
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
            logger.info("Cleared global commands")
        else:
            synced = await self.tree.sync()
            logger.info("Synced %s global command(s) (may take up to 1 hour to appear)", len(synced))


bot = MyBot()


@bot.tree.command(name="ping", description="Check whether the bot is online.")
async def ping(interaction: discord.Interaction) -> None:
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"Pong! `{latency_ms} ms`")


@bot.tree.command(name="hello", description="Say hello to the bot.")
async def hello(interaction: discord.Interaction) -> None:
    await interaction.response.send_message(f"Hey {interaction.user.mention}, I am online and ready.")

if __name__ == "__main__":
    bot.run(TOKEN, log_handler=None)