import json
import logging
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("discord_bot")

CONFIG_PATH = Path("/app/data/welcome_config.json")


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open() as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(config: dict) -> None:
    with CONFIG_PATH.open("w") as f:
        json.dump(config, f, indent=2)


def build_welcome_embed(member: discord.Member, extra_message: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        description=(
            f"Welcome to **{member.guild.name}** {member.mention}\n\n"
            f"**{member.guild.member_count}th Member Joined!**"
        ),
        color=discord.Color.blurple(),
    )
    embed.set_author(name=f"Welcome {member.display_name}", icon_url=member.display_avatar.url)
    embed.set_thumbnail(url=member.display_avatar.url)

    if extra_message:
        embed.add_field(name="\u200b", value=extra_message, inline=False)

    embed.set_footer(text=f"{member.display_name} enjoy your stay 🙌")

    return embed


class Welcome(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.config: dict = load_config()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        guild_id = str(member.guild.id)
        guild_cfg = self.config.get(guild_id)

        if not guild_cfg:
            return

        channel_id = guild_cfg.get("channel_id")
        if not channel_id:
            return

        channel = member.guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            logger.warning("Welcome channel %s not found or not a text channel.", channel_id)
            return

        extra_message = guild_cfg.get("extra_message")
        embed = build_welcome_embed(member, extra_message)
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning("Missing permissions to send welcome message in channel %s.", channel_id)

    @app_commands.command(
        name="welcomeset",
        description="Set the channel where welcome messages will be sent.",
    )
    @app_commands.describe(
        channel="The channel to send welcome messages in.",
        message="Optional message inside the embed. You can mention channels like #rules here.",
    )
    @app_commands.default_permissions(administrator=True)
    async def welcomeset(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        message: str | None = None,
    ) -> None:
        guild_id = str(interaction.guild_id)
        self.config[guild_id] = {
            "channel_id": str(channel.id),
            "extra_message": message,
        }
        save_config(self.config)

        embed = discord.Embed(
            title="✅ Welcome message configured!",
            color=discord.Color.green(),
        )
        embed.add_field(name="Channel", value=channel.mention, inline=False)
        embed.add_field(name="Extra Message", value=message if message else "*(none set)*", inline=False)
        embed.set_footer(text="Use /welcometest to send a live preview")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="welcometest",
        description="Send a test welcome message to the configured channel.",
    )
    @app_commands.default_permissions(administrator=True)
    async def welcometest(self, interaction: discord.Interaction) -> None:
        guild_id = str(interaction.guild_id)
        guild_cfg = self.config.get(guild_id)

        if not guild_cfg:
            await interaction.response.send_message(
                "No welcome channel configured yet. Use `/welcomeset` first.",
                ephemeral=True,
            )
            return

        channel_id = guild_cfg.get("channel_id")
        channel = interaction.guild.get_channel(int(channel_id))

        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "The configured welcome channel no longer exists. Please run `/welcomeset` again.",
                ephemeral=True,
            )
            return

        extra_message = guild_cfg.get("extra_message")
        embed = build_welcome_embed(interaction.user, extra_message)
        try:
            await channel.send(content="*(Test)*", embed=embed)
            await interaction.response.send_message(
                f"Test welcome sent to {channel.mention}.", ephemeral=True
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                f"I don't have permission to send messages in {channel.mention}.",
                ephemeral=True,
            )

    @app_commands.command(
        name="welcomeshow",
        description="Show the current welcome message configuration.",
    )
    @app_commands.default_permissions(administrator=True)
    async def welcomeshow(self, interaction: discord.Interaction) -> None:
        guild_id = str(interaction.guild_id)
        guild_cfg = self.config.get(guild_id)

        if not guild_cfg:
            await interaction.response.send_message(
                "No welcome message configured yet. Use `/welcomeset` to set one up.",
                ephemeral=True,
            )
            return

        channel_id = guild_cfg.get("channel_id")
        channel = interaction.guild.get_channel(int(channel_id))
        channel_display = channel.mention if channel else f"*(deleted — id {channel_id})*"
        extra_message = guild_cfg.get("extra_message") or "*(none set)*"

        embed = discord.Embed(title="Welcome Message Settings", color=discord.Color.blurple())
        embed.add_field(name="Channel", value=channel_display, inline=False)
        embed.add_field(name="Extra Message", value=extra_message, inline=False)
        embed.set_footer(text="Use /welcometest to preview • /welcomeset to change")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="welcomeclear",
        description="Disable the welcome message for this server.",
    )
    @app_commands.default_permissions(administrator=True)
    async def welcomeclear(self, interaction: discord.Interaction) -> None:
        guild_id = str(interaction.guild_id)

        if guild_id in self.config:
            del self.config[guild_id]
            save_config(self.config)
            await interaction.response.send_message(
                "Welcome message has been disabled for this server.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "There was no welcome message configured.", ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))