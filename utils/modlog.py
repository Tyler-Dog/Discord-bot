"""Moderation case logging: persists a case row and posts it to the configured mod-log channel."""
from __future__ import annotations

import logging
import time

import discord

logger = logging.getLogger("discord_bot")

ACTION_COLORS = {
    "warn": discord.Color.orange(),
    "kick": discord.Color.dark_orange(),
    "ban": discord.Color.red(),
    "timeout": discord.Color.gold(),
    "untimeout": discord.Color.green(),
    "clearwarnings": discord.Color.green(),
    "purge": discord.Color.light_grey(),
    "automod": discord.Color.purple(),
}


async def log_case(
    bot,
    guild: discord.Guild,
    action: str,
    *,
    user_id: int,
    user_label: str,
    moderator_id: int,
    reason: str,
) -> int:
    """Insert a case, announce it in the mod-log channel (if set) and return the case id."""
    cur = await bot.db.execute(
        "INSERT INTO mod_cases (guild_id, user_id, moderator_id, action, reason, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (guild.id, user_id, moderator_id, action, reason[:500], time.time()),
    )
    case_id = cur.lastrowid

    activity = getattr(bot, "activity_log", None)
    if activity:
        activity.add("moderation", f"Case #{case_id}: {action} {user_label}")

    channel_id = await bot.settings.get(guild.id, "modlog_channel")
    channel = guild.get_channel(int(channel_id)) if channel_id else None
    if isinstance(channel, discord.TextChannel):
        embed = discord.Embed(
            title=f"Case #{case_id} · {action.title()}",
            color=ACTION_COLORS.get(action, discord.Color.blurple()),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Member", value=f"{user_label} (`{user_id}`)")
        embed.add_field(name="Moderator", value="AutoMod" if moderator_id == bot.user.id else f"<@{moderator_id}>")
        embed.add_field(name="Reason", value=reason[:1000] or "—", inline=False)
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            logger.warning("Couldn't post case #%s to mod-log channel %s", case_id, channel.id)
    return case_id
