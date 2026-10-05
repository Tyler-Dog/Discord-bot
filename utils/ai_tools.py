"""Tools Claude can call from `/ask`. Every tool acts only on behalf of the invoking user."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import discord

from utils.timeparse import format_duration, parse_duration

MAX_REMINDERS_PER_USER = 25

TOOLS: list[dict[str, Any]] = [
    {
        "name": "lookup_arc_item",
        "description": "Look up an ARC Raiders game item (weapon, material, blueprint, ...) by name and return its stats.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Item name or partial name"}},
            "required": ["name"],
        },
    },
    {
        "name": "get_my_rank",
        "description": "Get the asking user's XP, level and leaderboard rank in this server.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_leaderboard",
        "description": "Get the top members by XP in this server.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 10}},
        },
    },
    {
        "name": "set_reminder",
        "description": "Create a reminder for the asking user. Duration looks like '30m', '2h', '1d' or '1h30m'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "duration": {"type": "string", "description": "How long from now, e.g. 2h30m"},
                "message": {"type": "string", "description": "What to remind them about"},
            },
            "required": ["duration", "message"],
        },
    },
    {
        "name": "get_server_info",
        "description": "Basic facts about the current Discord server (name, member count, channels).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_current_time",
        "description": "Get the current date and time in UTC.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

TOOL_LABELS = {
    "lookup_arc_item": "🔎 ARC item lookup",
    "get_my_rank": "📈 rank",
    "get_leaderboard": "🏆 leaderboard",
    "set_reminder": "⏰ reminder",
    "get_server_info": "🏠 server info",
    "get_current_time": "🕒 clock",
}


@dataclass
class ToolContext:
    bot: Any
    guild: discord.Guild | None
    user: discord.abc.User
    channel_id: int | None


async def _rank(ctx: ToolContext) -> dict:
    from cogs.leveling import level_from_xp  # local import: avoids cog import cycles

    if ctx.guild is None:
        return {"error": "Not available outside a server."}
    db = ctx.bot.db
    row = await db.fetchone("SELECT xp FROM levels WHERE guild_id = ? AND user_id = ?", (ctx.guild.id, ctx.user.id))
    xp = row["xp"] if row else 0
    ahead = await db.fetchone("SELECT COUNT(*) AS n FROM levels WHERE guild_id = ? AND xp > ?", (ctx.guild.id, xp))
    level, into, needed = level_from_xp(xp)
    return {"xp": xp, "level": level, "xp_into_level": into, "xp_needed_for_next_level": needed, "rank": ahead["n"] + 1}


async def _leaderboard(ctx: ToolContext, limit: int = 5) -> dict:
    from cogs.leveling import level_from_xp

    if ctx.guild is None:
        return {"error": "Not available outside a server."}
    limit = max(1, min(int(limit), 10))
    rows = await ctx.bot.db.fetchall(
        "SELECT user_id, xp FROM levels WHERE guild_id = ? ORDER BY xp DESC LIMIT ?", (ctx.guild.id, limit)
    )
    out = []
    for i, r in enumerate(rows, 1):
        member = ctx.guild.get_member(r["user_id"])
        out.append({"rank": i, "name": member.display_name if member else "unknown user",
                    "level": level_from_xp(r["xp"])[0], "xp": r["xp"]})
    return {"leaderboard": out}


async def _arc_item(name: str) -> dict:
    from cogs.arc_raiders import pick_best_match, search_items

    items = await search_items(name)
    best = pick_best_match(items, name, prefer_non_blueprint=True)
    if not best:
        return {"error": f"No ARC Raiders item matching '{name}'."}
    return {
        "name": best.get("name"),
        "type": best.get("item_type"),
        "rarity": best.get("rarity"),
        "value": best.get("value"),
        "category": best.get("subcategory"),
        "ammo_type": best.get("ammo_type"),
        "workbench": best.get("workbench"),
        "description": (best.get("description") or "")[:400],
        "stats": best.get("stat_block") or {},
    }


async def _set_reminder(ctx: ToolContext, duration: str, message: str) -> dict:
    seconds = parse_duration(duration)
    if seconds is None or seconds > 365 * 86400:
        return {"error": "Invalid duration. Use something like 30m, 2h or 1d."}
    db = ctx.bot.db
    count = (await db.fetchone("SELECT COUNT(*) AS n FROM reminders WHERE user_id = ?", (ctx.user.id,)))["n"]
    if count >= MAX_REMINDERS_PER_USER:
        return {"error": f"The user already has {MAX_REMINDERS_PER_USER} reminders."}
    now = time.time()
    cur = await db.execute(
        "INSERT INTO reminders (user_id, channel_id, message, due_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (ctx.user.id, ctx.channel_id, message[:500], now + seconds, now),
    )
    return {"ok": True, "reminder_id": cur.lastrowid, "due_in": format_duration(seconds)}


async def execute_tool(ctx: ToolContext, name: str, args: dict[str, Any]) -> str:
    """Run a tool and return a JSON string for the model. Unknown tools raise."""
    if name == "lookup_arc_item":
        result = await _arc_item(str(args.get("name", "")))
    elif name == "get_my_rank":
        result = await _rank(ctx)
    elif name == "get_leaderboard":
        result = await _leaderboard(ctx, args.get("limit", 5))
    elif name == "set_reminder":
        result = await _set_reminder(ctx, str(args.get("duration", "")), str(args.get("message", "")))
    elif name == "get_server_info":
        g = ctx.guild
        result = {"error": "Not in a server."} if g is None else {
            "name": g.name, "members": g.member_count,
            "text_channels": len(g.text_channels), "voice_channels": len(g.voice_channels),
            "created": g.created_at.date().isoformat(),
        }
    elif name == "get_current_time":
        result = {"utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    else:
        raise ValueError(f"unknown tool {name}")
    return json.dumps(result, ensure_ascii=False)
