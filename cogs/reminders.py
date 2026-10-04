"""Persistent reminders: `/remind 2h take the pizza out`."""
from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.timeparse import format_duration, parse_duration

logger = logging.getLogger("discord_bot")
MAX_PER_USER = 25
MAX_DELAY = 365 * 86400


class Reminders(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.check_reminders.start()

    async def cog_unload(self) -> None:
        self.check_reminders.cancel()

    @property
    def db(self):
        return self.bot.db  # type: ignore[attr-defined]

    @tasks.loop(seconds=15)
    async def check_reminders(self) -> None:
        due = await self.db.fetchall(
            "SELECT * FROM reminders WHERE due_at <= ? ORDER BY due_at LIMIT 50", (time.time(),)
        )
        for r in due:
            await self.db.execute("DELETE FROM reminders WHERE id = ?", (r["id"],))
            text = f"⏰ <@{r['user_id']}> reminder: **{r['message']}**"
            sent = False
            channel = self.bot.get_channel(r["channel_id"]) if r["channel_id"] else None
            if channel is not None:
                try:
                    await channel.send(text, allowed_mentions=discord.AllowedMentions(users=True))
                    sent = True
                except discord.HTTPException:
                    pass
            if not sent:
                try:
                    user = await self.bot.fetch_user(r["user_id"])
                    await user.send(f"⏰ Reminder: **{r['message']}**")
                except discord.HTTPException:
                    logger.warning("Couldn't deliver reminder %s", r["id"])

    @check_reminders.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="remind", description="Set a reminder, e.g. /remind 2h take a break")
    @app_commands.describe(when="Like 10m, 2h30m, 1d", message="What should I remind you about?")
    async def remind(
        self, interaction: discord.Interaction, when: str, message: app_commands.Range[str, 1, 500]
    ) -> None:
        seconds = parse_duration(when)
        if seconds is None or seconds > MAX_DELAY:
            await interaction.response.send_message(
                "❌ Use a duration like `10m`, `2h30m` or `1d` (max 1 year).", ephemeral=True
            )
            return
        count = (await self.db.fetchone(
            "SELECT COUNT(*) AS n FROM reminders WHERE user_id = ?", (interaction.user.id,)
        ))["n"]
        if count >= MAX_PER_USER:
            await interaction.response.send_message(f"❌ You already have {MAX_PER_USER} reminders.", ephemeral=True)
            return

        now = time.time()
        due = now + seconds
        cur = await self.db.execute(
            "INSERT INTO reminders (user_id, channel_id, message, due_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (interaction.user.id, interaction.channel_id, message, due, now),
        )
        await interaction.response.send_message(
            f"✅ Got it! I'll remind you <t:{int(due)}:R> (in {format_duration(seconds)}). `ID {cur.lastrowid}`",
            ephemeral=True,
        )

    @app_commands.command(name="reminders", description="List your pending reminders.")
    async def reminders(self, interaction: discord.Interaction) -> None:
        rows = await self.db.fetchall(
            "SELECT id, message, due_at FROM reminders WHERE user_id = ? ORDER BY due_at LIMIT 25",
            (interaction.user.id,),
        )
        if not rows:
            await interaction.response.send_message("You have no pending reminders.", ephemeral=True)
            return
        lines = [f"`{r['id']}` <t:{int(r['due_at'])}:R> — {r['message'][:80]}" for r in rows]
        await interaction.response.send_message(
            embed=discord.Embed(title="⏰ Your reminders", description="\n".join(lines), color=discord.Color.blurple()),
            ephemeral=True,
        )

    @app_commands.command(name="reminderdelete", description="Delete one of your reminders by ID.")
    async def reminderdelete(self, interaction: discord.Interaction, reminder_id: int) -> None:
        cur = await self.db.execute(
            "DELETE FROM reminders WHERE id = ? AND user_id = ?", (reminder_id, interaction.user.id)
        )
        msg = "🗑️ Reminder deleted." if cur.rowcount else "❌ No reminder with that ID."
        await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Reminders(bot))
