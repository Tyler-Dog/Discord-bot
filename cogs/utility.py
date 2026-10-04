"""Utility commands: /botstats, /avatar, /serverinfo, /userinfo."""
from __future__ import annotations

import platform
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils.timeparse import format_duration

try:  # `resource` is Unix-only; degrade gracefully on Windows
    import resource
except ImportError:  # pragma: no cover
    resource = None


class Utility(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.started_at = time.time()

    @app_commands.command(name="botstats", description="Uptime, latency and other bot statistics.")
    async def botstats(self, interaction: discord.Interaction) -> None:
        # ru_maxrss is KiB on Linux
        mem = f"{resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.1f} MB" if resource else "n/a"
        embed = discord.Embed(title="📈 Bot statistics", color=discord.Color.blurple())
        embed.add_field(name="Uptime", value=format_duration(time.time() - self.started_at))
        embed.add_field(name="Latency", value=f"{round(self.bot.latency * 1000)} ms")
        embed.add_field(name="Memory", value=mem)
        embed.add_field(name="Servers", value=str(len(self.bot.guilds)))
        embed.add_field(name="Members", value=f"{sum(g.member_count or 0 for g in self.bot.guilds):,}")
        cmds = self.bot.tree.get_commands(guild=interaction.guild) if interaction.guild else []
        embed.add_field(name="Commands", value=str(len(cmds or self.bot.tree.get_commands())))
        embed.add_field(name="Python", value=platform.python_version())
        embed.add_field(name="discord.py", value=discord.__version__)
        embed.add_field(name="Cogs", value=", ".join(sorted(self.bot.cogs)), inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="avatar", description="Show a member's avatar in full size.")
    async def avatar(self, interaction: discord.Interaction, member: discord.User | None = None) -> None:
        member = member or interaction.user
        embed = discord.Embed(title=f"{member.display_name}'s avatar", color=discord.Color.blurple())
        embed.set_image(url=member.display_avatar.replace(size=1024).url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverinfo", description="Information about this server.")
    @app_commands.guild_only()
    async def serverinfo(self, interaction: discord.Interaction) -> None:
        g = interaction.guild
        embed = discord.Embed(title=g.name, description=g.description or None, color=discord.Color.blurple())
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="Owner", value=f"<@{g.owner_id}>")
        embed.add_field(name="Members", value=f"{g.member_count:,}")
        embed.add_field(name="Created", value=f"<t:{int(g.created_at.timestamp())}:D>")
        embed.add_field(name="Channels", value=f"{len(g.text_channels)} text · {len(g.voice_channels)} voice")
        embed.add_field(name="Roles", value=str(len(g.roles)))
        embed.add_field(name="Boosts", value=f"{g.premium_subscription_count} (tier {g.premium_tier})")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="userinfo", description="Information about a member.")
    @app_commands.guild_only()
    async def userinfo(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        member = member or interaction.user  # type: ignore[assignment]
        embed = discord.Embed(title=str(member), color=member.color)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID", value=str(member.id))
        embed.add_field(name="Created", value=f"<t:{int(member.created_at.timestamp())}:R>")
        if member.joined_at:
            embed.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>")
        roles = [r.mention for r in reversed(member.roles) if r != interaction.guild.default_role]
        embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles[:15]) or "*(none)*", inline=False)
        await interaction.response.send_message(embed=embed, allowed_mentions=discord.AllowedMentions.none())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Utility(bot))
