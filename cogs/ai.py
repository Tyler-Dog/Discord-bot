"""`/ask` – chat with Claude straight from Discord (optional; needs ANTHROPIC_API_KEY)."""
from __future__ import annotations

import logging
import os

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("discord_bot")

API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-4-5"
SYSTEM_PROMPT = (
    "You are a helpful assistant living inside a Discord server. Keep answers concise "
    "and friendly, use Discord markdown where useful, and stay under about 1500 characters "
    "unless the user clearly needs more."
)
EMBED_LIMIT = 4000


def chunk_text(text: str, limit: int = EMBED_LIMIT) -> list[str]:
    """Split ``text`` into pieces of at most ``limit`` chars, preferring newline boundaries."""
    text = text.strip() or "(empty response)"
    chunks: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n")
    chunks.append(text)
    return chunks


class AI(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.api_key = os.getenv("ANTHROPIC_API_KEY")
        self.model = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)
        if not self.api_key:
            logger.warning("ANTHROPIC_API_KEY not set — /ask will reply that it's disabled.")

    async def _complete(self, prompt: str) -> str:
        payload = {
            "model": self.model,
            "max_tokens": 1024,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": prompt}],
        }
        headers = {
            "x-api-key": self.api_key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(API_URL, json=payload, headers=headers) as resp:
                data = await resp.json()
                if resp.status != 200:
                    msg = data.get("error", {}).get("message", f"HTTP {resp.status}")
                    raise RuntimeError(msg)
        return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")

    @app_commands.command(name="ask", description="Ask Claude anything.")
    @app_commands.describe(prompt="What do you want to ask?", private="Only show the answer to you.")
    @app_commands.checks.cooldown(1, 10.0, key=lambda i: i.user.id)
    async def ask(self, interaction: discord.Interaction, prompt: str, private: bool = False) -> None:
        if not self.api_key:
            await interaction.response.send_message(
                "AI chat isn't enabled on this bot (no `ANTHROPIC_API_KEY` configured).", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=private, thinking=True)
        try:
            answer = await self._complete(prompt)
        except Exception as exc:  # network / API errors shouldn't crash the command
            logger.exception("Claude request failed")
            await interaction.followup.send(f"⚠️ Couldn't reach Claude: `{exc}`", ephemeral=True)
            return

        pieces = chunk_text(answer)
        for i, piece in enumerate(pieces):
            embed = discord.Embed(description=piece, color=discord.Color.from_str("#D97757"))
            if i == 0:
                embed.set_author(name=f"{interaction.user.display_name} asked:")
                embed.add_field(name="Question", value=prompt[:1000], inline=False)
            if i == len(pieces) - 1:
                embed.set_footer(text=f"Powered by Claude · {self.model}")
            await interaction.followup.send(embed=embed, ephemeral=private)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AI(bot))
