"""Built-in web dashboard (aiohttp) — live stats, leaderboard and activity feed.

Binds to 127.0.0.1 on port 8787 by default (change with DASHBOARD_PORT). Set DASHBOARD_TOKEN to require
``?token=...`` / a Bearer header; a token is mandatory if you bind to anything other than localhost. Disable with DASHBOARD_ENABLED=false.
"""
from __future__ import annotations

import hmac
import logging
import math
import os
import platform
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import discord
from aiohttp import web
from discord.ext import commands, tasks

from cogs.leveling import level_from_xp

logger = logging.getLogger("discord_bot")
DEFAULT_PORT = 8787
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def resolve_config(env=os.environ) -> tuple[str, int, str | None]:
    """Work out (host, port, token) from the environment.

    The port comes from DASHBOARD_PORT, then the PORT variable that hosts like Railway inject, then 8787.
    Refuses to listen on a non-local address without a token, so the dashboard can't be exposed by accident.
    """
    host = env.get("DASHBOARD_HOST", "127.0.0.1")
    port = int(env.get("DASHBOARD_PORT") or env.get("PORT") or DEFAULT_PORT)
    token = env.get("DASHBOARD_TOKEN") or None
    allow_open = env.get("DASHBOARD_ALLOW_NO_TOKEN", "").lower() in ("1", "true", "yes")
    if host not in LOOPBACK_HOSTS and not token and not allow_open:
        raise ValueError(
            f"DASHBOARD_HOST={host} is reachable from other machines, so DASHBOARD_TOKEN must be set "
            "(or set DASHBOARD_ALLOW_NO_TOKEN=true if the port is only published to localhost)."
        )
    return host, port, token


BOT_KEY = web.AppKey("bot", object)
TOKEN_KEY = web.AppKey("token", str)
LATENCY_KEY = web.AppKey("latency", deque)
INDEX_PATH = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"


@web.middleware
async def auth_middleware(request: web.Request, handler):
    token: str | None = request.app.get(TOKEN_KEY)
    if token and request.path != "/healthz":
        supplied = request.query.get("token") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(supplied.encode(), token.encode()):
            return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


async def index(request: web.Request) -> web.Response:
    return web.Response(text=INDEX_PATH.read_text(encoding="utf-8"), content_type="text/html")


async def healthz(request: web.Request) -> web.Response:
    ready = request.app[BOT_KEY].is_ready()
    return web.Response(text="ok" if ready else "starting", status=200 if ready else 503)


async def stats(request: web.Request) -> web.Response:
    bot = request.app[BOT_KEY]
    log = bot.activity_log
    latency = bot.latency
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = await bot.db.fetchone("SELECT COALESCE(SUM(count), 0) AS n FROM ai_usage WHERE day = ?", (day,))
    claude = getattr(bot, "claude", None)
    return web.json_response({
        "uptime": int(time.time() - log.started_at),
        "latency_ms": round(latency * 1000) if math.isfinite(latency) else None,
        "latency_history": list(request.app[LATENCY_KEY]),
        "guilds": [{"id": str(g.id), "name": g.name, "members": g.member_count or 0} for g in bot.guilds],
        "members": sum(g.member_count or 0 for g in bot.guilds),
        "commands_total": sum(log.commands.values()),
        "top_commands": log.commands.most_common(8),
        "ai_requests_today": row["n"],
        "ai_model": claude.model if claude and claude.enabled else None,
        "cogs": sorted(bot.cogs),
        "python": platform.python_version(),
        "discord_py": discord.__version__,
    })


async def leaderboard(request: web.Request) -> web.Response:
    bot = request.app[BOT_KEY]
    try:
        guild_id = int(request.query.get("guild", ""))
    except ValueError:
        return web.json_response({"error": "guild query parameter required"}, status=400)
    guild = bot.get_guild(guild_id)
    if guild is None:
        return web.json_response({"error": "unknown guild"}, status=404)
    rows = await bot.db.fetchall("SELECT user_id, xp FROM levels WHERE guild_id = ? ORDER BY xp DESC LIMIT 10", (guild_id,))
    out = []
    for i, r in enumerate(rows, 1):
        member = guild.get_member(r["user_id"])
        out.append({"rank": i, "name": member.display_name if member else f"user {r['user_id']}",
                    "level": level_from_xp(r["xp"])[0], "xp": r["xp"]})
    return web.json_response(out)


async def activity(request: web.Request) -> web.Response:
    return web.json_response(request.app[BOT_KEY].activity_log.recent(50))


def create_app(bot, token: str | None = None) -> web.Application:
    app = web.Application(middlewares=[auth_middleware])
    app[BOT_KEY] = bot
    if token:
        app[TOKEN_KEY] = token
    app[LATENCY_KEY] = deque(maxlen=60)
    app.add_routes([
        web.get("/", index),
        web.get("/healthz", healthz),
        web.get("/api/stats", stats),
        web.get("/api/leaderboard", leaderboard),
        web.get("/api/activity", activity),
    ])
    return app


class Dashboard(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.runner: web.AppRunner | None = None
        self.app: web.Application | None = None

    async def cog_load(self) -> None:
        if os.getenv("DASHBOARD_ENABLED", "true").lower() in ("0", "false", "no"):
            logger.info("Dashboard disabled (DASHBOARD_ENABLED=false)")
            return
        try:
            host, port, token = resolve_config()
        except ValueError as exc:
            logger.error("Dashboard not started: %s", exc)
            return
        self.app = create_app(self.bot, token)
        self.runner = web.AppRunner(self.app, access_log=None)
        await self.runner.setup()
        try:
            await web.TCPSite(self.runner, host, port).start()
        except OSError as exc:  # never let a busy port take the bot down
            logger.error("Dashboard couldn't bind %s:%s (%s) — set DASHBOARD_PORT to another port", host, port, exc)
            await self.runner.cleanup()
            self.runner = None
            return
        self.sample_latency.start()
        logger.info("Dashboard listening on http://%s:%s", host, port)

    async def cog_unload(self) -> None:
        self.sample_latency.cancel()
        if self.runner:
            await self.runner.cleanup()

    @tasks.loop(seconds=15)
    async def sample_latency(self) -> None:
        latency = self.bot.latency
        if self.app is not None and math.isfinite(latency):
            self.app[LATENCY_KEY].append(round(latency * 1000))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Dashboard(bot))
