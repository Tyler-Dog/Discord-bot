"""XP / levels with a generated rank-card image and a leaderboard."""
from __future__ import annotations

import asyncio
import io
import logging
import random
import time

import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger("discord_bot")

XP_COOLDOWN_SECONDS = 60
XP_RANGE = (15, 25)


# ── Pure maths (unit-tested) ─────────────────────────────────────────────────

def xp_to_next(level: int) -> int:
    """XP needed to go from ``level`` to ``level + 1``."""
    return 5 * level**2 + 50 * level + 100


def total_xp_for_level(level: int) -> int:
    """Cumulative XP required to *reach* ``level``."""
    return sum(xp_to_next(n) for n in range(level))


def level_from_xp(xp: int) -> tuple[int, int, int]:
    """Return ``(level, xp_into_level, xp_needed_for_next)`` for a total XP value."""
    level = 0
    remaining = max(0, xp)
    while remaining >= xp_to_next(level):
        remaining -= xp_to_next(level)
        level += 1
    return level, remaining, xp_to_next(level)


# ── Rank card rendering ──────────────────────────────────────────────────────

def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:  # Pillow >= 10.1 ships a scalable default font
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - very old Pillow
        return ImageFont.load_default()


def render_rank_card(
    *,
    name: str,
    avatar_bytes: bytes | None,
    rank: int,
    level: int,
    xp_in_level: int,
    xp_needed: int,
    total_xp: int,
) -> bytes:
    """Draw a 900x260 rank card and return PNG bytes."""
    w, h = 900, 260
    img = Image.new("RGB", (w, h), (30, 31, 43))
    draw = ImageDraw.Draw(img)

    # Soft gradient background
    for x in range(w):
        t = x / w
        draw.line([(x, 0), (x, h)], fill=(int(30 + 40 * t), int(31 + 20 * t), int(43 + 90 * t)))

    # Card panel
    panel = Image.new("RGB", (w - 40, h - 40), (12, 12, 20))
    mask = Image.new("L", panel.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *panel.size), radius=24, fill=165)  # ~65% opaque
    img.paste(panel, (20, 20), mask)

    # Avatar
    size = 160
    if avatar_bytes:
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((size, size))
    else:
        avatar = Image.new("RGBA", (size, size), (88, 101, 242, 255))
    circle = Image.new("L", (size, size), 0)
    ImageDraw.Draw(circle).ellipse((0, 0, size, size), fill=255)
    img.paste(avatar, (50, 50), circle)

    # Text
    draw.text((250, 52), name[:22], font=_font(40), fill=(255, 255, 255))
    draw.text((250, 105), f"RANK #{rank}", font=_font(26), fill=(180, 190, 255))
    draw.text((420, 105), f"LEVEL {level}", font=_font(26), fill=(120, 255, 190))
    draw.text((250, 140), f"{total_xp:,} total XP", font=_font(20), fill=(190, 190, 205))

    # Progress bar
    bx0, by0, bx1, by1 = 250, 185, 850, 215
    draw.rounded_rectangle((bx0, by0, bx1, by1), radius=15, fill=(60, 62, 80))
    pct = 0 if xp_needed <= 0 else min(1.0, xp_in_level / xp_needed)
    if pct > 0:
        fill_to = max(bx0 + 30, bx0 + int((bx1 - bx0) * pct))
        draw.rounded_rectangle((bx0, by0, fill_to, by1), radius=15, fill=(88, 101, 242))
    label = f"{xp_in_level:,} / {xp_needed:,} XP"
    draw.text((bx1 - 4, by0 - 26), label, font=_font(18), fill=(200, 200, 215), anchor="ra")

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


# ── Cog ──────────────────────────────────────────────────────────────────────

class Leveling(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        db = self.bot.db  # type: ignore[attr-defined]
        now = time.time()
        row = await db.fetchone(
            "SELECT xp, last_xp_at FROM levels WHERE guild_id = ? AND user_id = ?",
            (message.guild.id, message.author.id),
        )
        old_xp, last = (row["xp"], row["last_xp_at"]) if row else (0, 0.0)
        if now - last < XP_COOLDOWN_SECONDS:
            return

        new_xp = old_xp + random.randint(*XP_RANGE)
        await db.execute(
            """INSERT INTO levels (guild_id, user_id, xp, last_xp_at) VALUES (?, ?, ?, ?)
               ON CONFLICT(guild_id, user_id) DO UPDATE SET xp = excluded.xp, last_xp_at = excluded.last_xp_at""",
            (message.guild.id, message.author.id, new_xp, now),
        )

        old_level = level_from_xp(old_xp)[0]
        new_level = level_from_xp(new_xp)[0]
        if new_level > old_level:
            try:
                await message.channel.send(
                    f"🎉 {message.author.mention} just reached **level {new_level}**!",
                    allowed_mentions=discord.AllowedMentions(users=[message.author]),
                )
            except discord.HTTPException:
                logger.debug("Couldn't announce level-up in %s", message.channel.id)

    async def _rank_of(self, guild_id: int, user_id: int) -> tuple[int, int]:
        db = self.bot.db  # type: ignore[attr-defined]
        row = await db.fetchone(
            "SELECT xp FROM levels WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        xp = row["xp"] if row else 0
        ahead = await db.fetchone(
            "SELECT COUNT(*) AS n FROM levels WHERE guild_id = ? AND xp > ?", (guild_id, xp)
        )
        return xp, ahead["n"] + 1

    @app_commands.command(name="rank", description="Show your (or someone's) level as a rank card.")
    @app_commands.describe(member="Whose rank to show (defaults to you).")
    @app_commands.guild_only()
    async def rank(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        await interaction.response.defer()
        member = member or interaction.user  # type: ignore[assignment]
        xp, rank = await self._rank_of(interaction.guild_id, member.id)
        level, into, needed = level_from_xp(xp)

        try:
            avatar_bytes = await member.display_avatar.replace(size=256, format="png").read()
        except discord.HTTPException:
            avatar_bytes = None

        png = await asyncio.to_thread(
            render_rank_card,
            name=member.display_name,
            avatar_bytes=avatar_bytes,
            rank=rank,
            level=level,
            xp_in_level=into,
            xp_needed=needed,
            total_xp=xp,
        )
        await interaction.followup.send(file=discord.File(io.BytesIO(png), filename="rank.png"))

    @app_commands.command(name="leaderboard", description="Top 10 most active members in this server.")
    @app_commands.guild_only()
    async def leaderboard(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.fetchall(  # type: ignore[attr-defined]
            "SELECT user_id, xp FROM levels WHERE guild_id = ? ORDER BY xp DESC LIMIT 10",
            (interaction.guild_id,),
        )
        if not rows:
            await interaction.response.send_message("Nobody has earned XP yet — start chatting!", ephemeral=True)
            return

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, row in enumerate(rows):
            level = level_from_xp(row["xp"])[0]
            prefix = medals[i] if i < 3 else f"`#{i + 1}`"
            lines.append(f"{prefix} <@{row['user_id']}> — level **{level}** · {row['xp']:,} XP")

        embed = discord.Embed(
            title=f"🏆 {interaction.guild.name} Leaderboard",
            description="\n".join(lines),
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(
            embed=embed, allowed_mentions=discord.AllowedMentions.none()
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Leveling(bot))
