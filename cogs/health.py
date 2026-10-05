"""Writes a heartbeat file while the gateway connection is healthy (used by the Docker HEALTHCHECK)."""
from __future__ import annotations

import time

from discord.ext import commands, tasks

from utils.db import DATA_DIR

HEARTBEAT_PATH = DATA_DIR / ".heartbeat"


class Health(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.beat.start()

    async def cog_unload(self) -> None:
        self.beat.cancel()

    @tasks.loop(seconds=30)
    async def beat(self) -> None:
        if self.bot.is_ready():
            HEARTBEAT_PATH.parent.mkdir(parents=True, exist_ok=True)
            HEARTBEAT_PATH.write_text(str(time.time()))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Health(bot))
