import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("discord_bot")

# Friendly display names and descriptions per cog class name
COG_META = {
    "ArcRaiders": ("🎮 ARC Raiders", "Look up ARC Raiders game data"),
    "Welcome":    ("👋 Welcome",     "Server welcome message configuration"),
    "Tickets":    ("🎫 Tickets",     "Support ticket system"),
    "AI":         ("🤖 AI",          "Chat with Claude"),
    "Leveling":   ("📈 Leveling",    "XP, rank cards and leaderboards"),
    "Moderation": ("🛡️ Moderation",  "Warn, kick, ban, timeout, purge"),
    "Polls":      ("📊 Polls",       "Live polls with buttons"),
    "Reminders":  ("⏰ Reminders",   "Never forget anything"),
    "Utility":    ("🧰 Utility",     "Stats and info commands"),
}

GENERAL_NAMES = {"ping", "hello", "helpme"}


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="helpme", description="Show all available bot commands.")
    async def helpme(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild

        # Get all commands registered to this guild (or global if no guild)
        if guild:
            all_cmds = {
                cmd.name: cmd
                for cmd in self.bot.tree.get_commands(guild=guild)
            }
            # Fall back to global if guild returned nothing
            if not all_cmds:
                all_cmds = {cmd.name: cmd for cmd in self.bot.tree.get_commands()}
        else:
            all_cmds = {cmd.name: cmd for cmd in self.bot.tree.get_commands()}

        logger.info("helpme sees commands: %s", list(all_cmds.keys()))

        embed = discord.Embed(
            title="Commands and Modules",
            description="Use `/helpme` to show this menu 😃",
            color=discord.Color.blurple(),
        )

        # ── General (commands defined in bot.py, not in any cog) ──────────
        general_lines = []
        for name in sorted(GENERAL_NAMES - {"helpme"}):
            cmd = all_cmds.get(name)
            if cmd:
                general_lines.append(f"`/{cmd.name}` {cmd.description}")
        if general_lines:
            embed.add_field(
                name="⚙️ General — *General bot commands*",
                value="\n".join(general_lines),
                inline=False,
            )

        # ── One section per loaded cog (auto-updates as cogs are added) ───
        for cog in self.bot.cogs.values():
            # Skip this Help cog itself
            if isinstance(cog, Help):
                continue

            cog_class = type(cog).__name__
            friendly_name, description = COG_META.get(
                cog_class, (f"📦 {cog_class}", "")
            )

            # Get every app command belonging to this cog
            cog_cmd_names = {c.name for c in cog.get_app_commands()}
            lines = []
            for name in sorted(cog_cmd_names):
                cmd = all_cmds.get(name)
                if cmd:
                    lines.append(f"`/{cmd.name}` {cmd.description}")

            if lines:
                header = friendly_name
                if description:
                    header += f" — *{description}*"
                embed.add_field(name=header, value="\n".join(lines), inline=False)

        # ── About ──────────────────────────────────────────────────────────
        embed.add_field(name="\u200b", value="\u200b", inline=False)
        embed.add_field(
            name="About",
            value="This bot is maintained by **tyler.0001**. For help, please message me!",
            inline=False,
        )
        embed.set_footer(text=f"Bot is running discord.py {discord.__version__}")

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Help(bot))
