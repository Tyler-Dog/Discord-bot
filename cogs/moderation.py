"""Moderation toolkit: warn / warnings / kick / ban / timeout / purge."""
from __future__ import annotations

import logging
import time
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

from utils.timeparse import format_duration, parse_duration

logger = logging.getLogger("discord_bot")
MAX_TIMEOUT = 28 * 86400  # Discord's hard limit


def hierarchy_error(
    interaction: discord.Interaction, target: discord.Member
) -> str | None:
    """Return a human-readable reason the action is not allowed, else ``None``."""
    guild = interaction.guild
    actor = interaction.user
    if target.id == actor.id:
        return "You can't do that to yourself."
    if target.id == guild.owner_id:
        return "You can't moderate the server owner."
    if target.id == interaction.client.user.id:
        return "Nice try."
    if actor.id != guild.owner_id and target.top_role >= actor.top_role:
        return "That member's top role is equal to or above yours."
    if target.top_role >= guild.me.top_role:
        return "That member's top role is equal to or above mine — move my role higher."
    return None


async def dm_user(member: discord.abc.User, text: str) -> None:
    try:
        await member.send(text)
    except discord.HTTPException:
        pass  # DMs closed — nothing to do


class Moderation(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self):
        return self.bot.db  # type: ignore[attr-defined]

    # ── warnings ────────────────────────────────────────────────────────────
    @app_commands.command(name="warn", description="Warn a member and log it.")
    @app_commands.describe(member="Who to warn", reason="Why")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warn(self, interaction: discord.Interaction, member: discord.Member, reason: str) -> None:
        if (err := hierarchy_error(interaction, member)):
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return
        await self.db.execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (interaction.guild_id, member.id, interaction.user.id, reason[:500], time.time()),
        )
        count = (await self.db.fetchone(
            "SELECT COUNT(*) AS n FROM warnings WHERE guild_id = ? AND user_id = ?",
            (interaction.guild_id, member.id),
        ))["n"]
        await dm_user(member, f"⚠️ You were warned in **{interaction.guild.name}**: {reason}")
        embed = discord.Embed(title="⚠️ Member warned", color=discord.Color.orange())
        embed.add_field(name="Member", value=member.mention)
        embed.add_field(name="Total warnings", value=str(count))
        embed.add_field(name="Reason", value=reason, inline=False)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="warnings", description="List a member's warnings.")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def warnings(self, interaction: discord.Interaction, member: discord.Member) -> None:
        rows = await self.db.fetchall(
            "SELECT id, moderator_id, reason, created_at FROM warnings "
            "WHERE guild_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT 15",
            (interaction.guild_id, member.id),
        )
        if not rows:
            await interaction.response.send_message(f"{member.mention} has a clean record. ✨", ephemeral=True)
            return
        lines = [
            f"`#{r['id']}` <t:{int(r['created_at'])}:d> by <@{r['moderator_id']}> — {r['reason']}"
            for r in rows
        ]
        embed = discord.Embed(
            title=f"Warnings for {member.display_name}",
            description="\n".join(lines)[:4000],
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(
            embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @app_commands.command(name="clearwarnings", description="Remove all warnings for a member.")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def clearwarnings(self, interaction: discord.Interaction, member: discord.Member) -> None:
        cur = await self.db.execute(
            "DELETE FROM warnings WHERE guild_id = ? AND user_id = ?", (interaction.guild_id, member.id)
        )
        await interaction.response.send_message(
            f"🧹 Removed {cur.rowcount} warning(s) from {member.mention}.", ephemeral=True
        )

    # ── kick / ban / timeout ─────────────────────────────────────────────────
    @app_commands.command(name="kick", description="Kick a member.")
    @app_commands.default_permissions(kick_members=True)
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.checks.bot_has_permissions(kick_members=True)
    @app_commands.guild_only()
    async def kick(self, interaction: discord.Interaction, member: discord.Member, reason: str = "No reason given") -> None:
        if (err := hierarchy_error(interaction, member)):
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return
        await dm_user(member, f"👢 You were kicked from **{interaction.guild.name}**: {reason}")
        await member.kick(reason=f"{interaction.user}: {reason}")
        await interaction.response.send_message(f"👢 Kicked **{member}** — {reason}")

    @app_commands.command(name="ban", description="Ban a member.")
    @app_commands.describe(delete_days="Days of their messages to delete (0-7).")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.checks.bot_has_permissions(ban_members=True)
    @app_commands.guild_only()
    async def ban(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        reason: str = "No reason given",
        delete_days: app_commands.Range[int, 0, 7] = 0,
    ) -> None:
        if (err := hierarchy_error(interaction, member)):
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return
        await dm_user(member, f"🔨 You were banned from **{interaction.guild.name}**: {reason}")
        await member.ban(reason=f"{interaction.user}: {reason}", delete_message_seconds=delete_days * 86400)
        await interaction.response.send_message(f"🔨 Banned **{member}** — {reason}")

    @app_commands.command(name="timeout", description="Time a member out, e.g. 10m, 2h, 1d.")
    @app_commands.describe(duration="Like 10m, 2h30m, 1d (max 28d)")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def timeout(
        self, interaction: discord.Interaction, member: discord.Member, duration: str, reason: str = "No reason given"
    ) -> None:
        if (err := hierarchy_error(interaction, member)):
            await interaction.response.send_message(f"❌ {err}", ephemeral=True)
            return
        seconds = parse_duration(duration)
        if seconds is None or seconds > MAX_TIMEOUT:
            await interaction.response.send_message(
                "❌ Use a duration like `10m`, `2h30m` or `1d` (max 28 days).", ephemeral=True
            )
            return
        await member.timeout(timedelta(seconds=seconds), reason=f"{interaction.user}: {reason}")
        await interaction.response.send_message(
            f"🔇 Timed out **{member}** for **{format_duration(seconds)}** — {reason}"
        )

    @app_commands.command(name="untimeout", description="Remove a member's timeout.")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.checks.bot_has_permissions(moderate_members=True)
    @app_commands.guild_only()
    async def untimeout(self, interaction: discord.Interaction, member: discord.Member) -> None:
        await member.timeout(None, reason=f"Untimeout by {interaction.user}")
        await interaction.response.send_message(f"🔊 Removed timeout from **{member}**.")

    @app_commands.command(name="purge", description="Bulk-delete recent messages in this channel.")
    @app_commands.describe(amount="How many messages to delete (1-100)", member="Only delete this member's messages")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
    @app_commands.guild_only()
    async def purge(
        self,
        interaction: discord.Interaction,
        amount: app_commands.Range[int, 1, 100],
        member: discord.Member | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        check = (lambda m: m.author.id == member.id) if member else None
        deleted = await interaction.channel.purge(limit=amount, check=check)
        await interaction.followup.send(f"🧹 Deleted {len(deleted)} message(s).", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
