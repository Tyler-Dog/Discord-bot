"""AutoMod: spam, mass mentions, invite links and blocked words. Off by default; enable with /config automod."""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord.ext import commands

from utils.modlog import log_case

logger = logging.getLogger("discord_bot")

INVITE_RE = re.compile(r"(?:discord\.gg|discord(?:app)?\.com/invite)/[\w-]+", re.IGNORECASE)
MENTION_LIMIT = 5
SPAM_LIMIT = 6           # messages ...
SPAM_WINDOW = 6.0        # ... within this many seconds
SPAM_TIMEOUT = timedelta(minutes=5)


class SpamTracker:
    """Sliding-window rate counter keyed by (guild, user)."""

    def __init__(self, limit: int = SPAM_LIMIT, window: float = SPAM_WINDOW) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[tuple[int, int], deque[float]] = defaultdict(deque)

    def hit(self, key: tuple[int, int], now: float | None = None) -> bool:
        """Record a message; return True if the user just exceeded the limit."""
        now = time.monotonic() if now is None else now
        q = self._hits[key]
        q.append(now)
        while q and now - q[0] > self.window:
            q.popleft()
        if len(q) >= self.limit:
            q.clear()
            return True
        return False


def check_content(content: str, blocked_words: list[str]) -> str | None:
    """Return a violation reason for message text, or ``None`` if it's clean."""
    if not content:
        return None
    if INVITE_RE.search(content):
        return "Posted a Discord invite link"
    lowered = content.lower()
    for word in blocked_words:
        if re.search(rf"(?<!\w){re.escape(word)}(?!\w)", lowered):
            return "Used a blocked word"
    return None


class AutoMod(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.spam = SpamTracker()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not isinstance(message.author, discord.Member):
            return
        guild = message.guild
        if not await self.bot.settings.get_bool(guild.id, "automod"):
            return
        if message.author.guild_permissions.manage_messages or message.author.guild_permissions.administrator:
            return

        # 1) Spam (works without the message-content intent)
        if self.spam.hit((guild.id, message.author.id)):
            await self._punish(message, "Spam: sending messages too fast", timeout=True)
            return

        # 2) Mass mentions
        if len(message.mentions) + len(message.role_mentions) >= MENTION_LIMIT:
            await self._punish(message, f"Mass mention ({MENTION_LIMIT}+ mentions)")
            return

        # 3) Content filters (need the message-content intent to see any text)
        if message.content:
            raw = await self.bot.settings.get(guild.id, "automod_blocked_words", "") or ""
            reason = check_content(message.content, [w for w in raw.split(",") if w])
            if reason:
                await self._punish(message, reason)

    async def _punish(self, message: discord.Message, reason: str, *, timeout: bool = False) -> None:
        member = message.author
        try:
            await message.delete()
        except discord.HTTPException:
            pass

        action_note = ""
        if timeout:
            try:
                await member.timeout(SPAM_TIMEOUT, reason=f"AutoMod: {reason}")
                action_note = " (timed out 5m)"
            except discord.HTTPException:
                logger.warning("AutoMod couldn't time out %s", member.id)

        await self.bot.db.execute(
            "INSERT INTO warnings (guild_id, user_id, moderator_id, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (message.guild.id, member.id, self.bot.user.id, f"AutoMod: {reason}", time.time()),
        )
        await log_case(
            self.bot, message.guild, "automod",
            user_id=member.id, user_label=str(member), moderator_id=self.bot.user.id,
            reason=f"{reason}{action_note}",
        )
        try:
            await message.channel.send(
                f"🛡️ {member.mention} — message removed: {reason}.", delete_after=8,
                allowed_mentions=discord.AllowedMentions(users=[member]),
            )
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoMod(bot))
