"""Smoke tests: every cog loads into a real discord.py Bot and the DB layer works."""
import time

import discord
import pytest
from discord.ext import commands

from utils.db import Database
from utils.settings import BotTree, Settings

EXTENSIONS = [
    "cogs.arc_raiders", "cogs.welcome", "cogs.help", "cogs.tickets", "cogs.ai",
    "cogs.leveling", "cogs.moderation", "cogs.polls", "cogs.reminders", "cogs.utility",
    "cogs.automod", "cogs.config", "cogs.health",
]


@pytest.mark.asyncio
async def test_all_cogs_load_and_commands_register():
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default(), tree_cls=BotTree)
    bot.db = await Database.open(":memory:")
    bot.settings = Settings(bot.db)
    for ext in EXTENSIONS:
        await bot.load_extension(ext)
    names = {c.qualified_name for c in bot.tree.walk_commands()}
    expected = {"ask", "askreset", "summarize", "rank", "leaderboard", "warn", "ban", "cases", "poll", "remind",
                "botstats", "arcitem", "config module", "config modlog", "config automod", "config levelrole_add"}
    assert expected <= names
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


@pytest.mark.asyncio
async def test_every_extension_in_bot_py_loads(monkeypatch):
    """Guards against typos / missing modules in the real EXTENSIONS list used at startup."""
    monkeypatch.setenv("DASHBOARD_ENABLED", "false")
    from bot import EXTENSIONS

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.default(), tree_cls=BotTree)
    bot.db = await Database.open(":memory:")
    bot.settings = Settings(bot.db)
    try:
        for ext in EXTENSIONS:
            await bot.load_extension(ext)
        assert {"Dashboard", "Health", "AutoMod", "Config", "AI"} <= set(bot.cogs)
    finally:
        for ext in list(bot.extensions):
            await bot.unload_extension(ext)
        await bot.db.close()
