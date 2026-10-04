"""Smoke tests: every cog loads into a real discord.py Bot and the DB layer works."""
import time

import discord
import pytest
from discord.ext import commands

from utils.db import Database

EXTENSIONS = [
    "cogs.arc_raiders", "cogs.welcome", "cogs.help", "cogs.tickets", "cogs.ai",
    "cogs.leveling", "cogs.moderation", "cogs.polls", "cogs.reminders", "cogs.utility",
]


@pytest.mark.asyncio
async def test_all_cogs_load_and_commands_register():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    bot.db = await Database.open(":memory:")
    for ext in EXTENSIONS:
        await bot.load_extension(ext)
    names = {c.name for c in bot.tree.walk_commands()}
    assert {"ask", "rank", "leaderboard", "warn", "ban", "poll", "remind", "botstats", "arcitem"} <= names
    for ext in EXTENSIONS:
        await bot.unload_extension(ext)
    await bot.db.close()


@pytest.mark.asyncio
async def test_database_schema_and_roundtrip():
    db = await Database.open(":memory:")
    await db.execute("INSERT INTO reminders (user_id, channel_id, message, due_at, created_at) VALUES (1, 2, 'x', ?, ?)", (time.time(), time.time()))
    row = await db.fetchone("SELECT * FROM reminders")
    assert row["message"] == "x"
    await db.close()
