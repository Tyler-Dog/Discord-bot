"""Live polls with persistent buttons, one-vote-per-user, change-your-vote and auto-close."""
from __future__ import annotations

import json
import logging
import re
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.timeparse import parse_duration

logger = logging.getLogger("discord_bot")
NUMBER_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣"]


def render_bar(count: int, total: int, width: int = 12) -> str:
    """``render_bar(3, 4)`` -> ``'▰▰▰▰▰▰▰▰▰▱▱▱'``."""
    filled = 0 if total <= 0 else round(width * count / total)
    return "▰" * filled + "▱" * (width - filled)


def build_poll_embed(
    question: str, options: list[str], counts: list[int], *, ends_at: float | None, closed: bool, author: str
) -> discord.Embed:
    total = sum(counts)
    lines = []
    for i, (opt, n) in enumerate(zip(options, counts)):
        pct = 0 if total == 0 else round(100 * n / total)
        lines.append(f"{NUMBER_EMOJI[i]} **{opt}**\n`{render_bar(n, total)}` {n} vote{'s' if n != 1 else ''} ({pct}%)")

    embed = discord.Embed(
        title=f"📊 {question}",
        description="\n\n".join(lines),
        color=discord.Color.dark_grey() if closed else discord.Color.blurple(),
    )
    if closed:
        footer = f"Poll closed · {total} total vote{'s' if total != 1 else ''}"
    elif ends_at:
        embed.add_field(name="Ends", value=f"<t:{int(ends_at)}:R>", inline=False)
        footer = f"Poll by {author} · click a button to vote (click again to remove)"
    else:
        footer = f"Poll by {author} · click a button to vote (click again to remove)"
    embed.set_footer(text=footer)
    return embed


class PollButton(discord.ui.DynamicItem[discord.ui.Button], template=r"poll:(?P<idx>\d+)"):
    """One button per option. The option index lives in the custom_id, so these survive restarts."""

    def __init__(self, idx: int, *, disabled: bool = False) -> None:
        super().__init__(
            discord.ui.Button(
                emoji=NUMBER_EMOJI[idx],
                style=discord.ButtonStyle.secondary,
                custom_id=f"poll:{idx}",
                disabled=disabled,
            )
        )
        self.idx = idx

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str]):
        return cls(int(match["idx"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: Polls = interaction.client.get_cog("Polls")  # type: ignore[assignment]
        await cog.handle_vote(interaction, self.idx)


def poll_view(n_options: int, *, disabled: bool = False) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for i in range(n_options):
        view.add_item(PollButton(i, disabled=disabled))
    return view


class Polls(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(PollButton)
        self.expire_polls.start()

    async def cog_unload(self) -> None:
        self.expire_polls.cancel()

    @property
    def db(self):
        return self.bot.db  # type: ignore[attr-defined]

    async def _counts(self, message_id: int, n: int) -> list[int]:
        rows = await self.db.fetchall(
            "SELECT option_idx, COUNT(*) AS n FROM poll_votes WHERE message_id = ? GROUP BY option_idx",
            (message_id,),
        )
        counts = [0] * n
        for r in rows:
            if 0 <= r["option_idx"] < n:
                counts[r["option_idx"]] = r["n"]
        return counts

    async def _embed_for(self, poll, *, closed: bool) -> discord.Embed:
        options = json.loads(poll["options"])
        counts = await self._counts(poll["message_id"], len(options))
        author = self.bot.get_user(poll["author_id"])
        return build_poll_embed(
            poll["question"], options, counts,
            ends_at=poll["ends_at"], closed=closed, author=author.display_name if author else "someone",
        )

    async def handle_vote(self, interaction: discord.Interaction, idx: int) -> None:
        msg_id = interaction.message.id
        poll = await self.db.fetchone("SELECT * FROM polls WHERE message_id = ?", (msg_id,))
        if poll is None or poll["closed"]:
            await interaction.response.send_message("This poll is closed.", ephemeral=True)
            return

        existing = await self.db.fetchone(
            "SELECT option_idx FROM poll_votes WHERE message_id = ? AND user_id = ?", (msg_id, interaction.user.id)
        )
        if existing and existing["option_idx"] == idx:
            await self.db.execute(
                "DELETE FROM poll_votes WHERE message_id = ? AND user_id = ?", (msg_id, interaction.user.id)
            )
        else:
            await self.db.execute(
                "INSERT OR REPLACE INTO poll_votes (message_id, user_id, option_idx) VALUES (?, ?, ?)",
                (msg_id, interaction.user.id, idx),
            )
        await interaction.response.edit_message(embed=await self._embed_for(poll, closed=False))

    async def close_poll(self, poll) -> None:
        await self.db.execute("UPDATE polls SET closed = 1 WHERE message_id = ?", (poll["message_id"],))
        channel = self.bot.get_channel(poll["channel_id"])
        if channel is None:
            return
        try:
            message = await channel.fetch_message(poll["message_id"])
            n = len(json.loads(poll["options"]))
            await message.edit(embed=await self._embed_for(poll, closed=True), view=poll_view(n, disabled=True))
        except discord.HTTPException:
            logger.warning("Couldn't update closed poll %s", poll["message_id"])

    @tasks.loop(seconds=30)
    async def expire_polls(self) -> None:
        rows = await self.db.fetchall(
            "SELECT * FROM polls WHERE closed = 0 AND ends_at IS NOT NULL AND ends_at <= ?", (time.time(),)
        )
        for poll in rows:
            await self.close_poll(poll)

    @expire_polls.before_loop
    async def _wait(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="poll", description="Create a live poll with up to 5 options.")
    @app_commands.describe(
        question="What are you asking?",
        option1="First option", option2="Second option",
        option3="Third option", option4="Fourth option", option5="Fifth option",
        duration="Auto-close after e.g. 30m, 2h, 1d (optional)",
    )
    @app_commands.guild_only()
    async def poll(
        self,
        interaction: discord.Interaction,
        question: app_commands.Range[str, 1, 200],
        option1: app_commands.Range[str, 1, 80],
        option2: app_commands.Range[str, 1, 80],
        option3: app_commands.Range[str, 1, 80] | None = None,
        option4: app_commands.Range[str, 1, 80] | None = None,
        option5: app_commands.Range[str, 1, 80] | None = None,
        duration: str | None = None,
    ) -> None:
        options = [o for o in (option1, option2, option3, option4, option5) if o]
        ends_at = None
        if duration:
            seconds = parse_duration(duration)
            if seconds is None:
                await interaction.response.send_message("❌ Duration like `30m`, `2h` or `1d`.", ephemeral=True)
                return
            ends_at = time.time() + seconds

        embed = build_poll_embed(
            question, options, [0] * len(options),
            ends_at=ends_at, closed=False, author=interaction.user.display_name,
        )
        await interaction.response.send_message(embed=embed, view=poll_view(len(options)))
        message = await interaction.original_response()
        await self.db.execute(
            "INSERT INTO polls (message_id, channel_id, guild_id, author_id, question, options, ends_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (message.id, message.channel.id, interaction.guild_id, interaction.user.id,
             question, json.dumps(options), ends_at),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Polls(bot))
