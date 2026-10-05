import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from cogs.automod import SpamTracker, check_content
from cogs.config import parse_word_list
from cogs.dashboard import LATENCY_KEY, create_app, resolve_config
from utils.activity import ActivityLog
from utils.ai_tools import ToolContext, execute_tool
from utils.db import Database
from utils.settings import MODULES, Settings
from utils.triage import normalize_triage

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
async def db():
    d = await Database.open(":memory:")
    yield d
    await d.close()


# ── settings ────────────────────────────────────────────────────────────────
async def test_settings_defaults_roundtrip_and_cache(db):
    s = Settings(db)
    assert await s.get_int(1, "xp_min") == 15 and await s.get_bool(1, "automod") is False
    assert await s.module_enabled(1, "leveling") is True
    await s.set(1, "module_leveling", False)
    await s.set(1, "xp_min", 30)
    assert await s.module_enabled(1, "leveling") is False and await s.get_int(1, "xp_min") == 30
    assert await s.module_enabled(2, "leveling") is True          # other guilds unaffected
    # persisted: a fresh Settings sees the same data
    assert await Settings(db).get_int(1, "xp_min") == 30
    await s.delete(1, "xp_min")
    assert await s.get_int(1, "xp_min") == 15
    assert await s.module_enabled(None, "ai") is True and "ai" in MODULES


# ── automod ─────────────────────────────────────────────────────────────────
def test_spam_tracker_trips_only_on_bursts():
    t = SpamTracker(limit=3, window=5)
    assert [t.hit((1, 1), now=x) for x in (0, 1, 2)] == [False, False, True]
    assert t.hit((1, 1), now=3) is False                  # counter reset after tripping
    slow = SpamTracker(limit=3, window=5)
    assert not any(slow.hit((1, 1), now=x) for x in (0, 10, 20, 30))
    assert not any(slow.hit((1, 2), now=x) for x in (0, 1))   # per-user buckets


def test_check_content():
    assert check_content("join discord.gg/abc123", []) == "Posted a Discord invite link"
    assert check_content("https://discord.com/invite/xyz", []) is not None
    assert check_content("you are a Scrub", ["scrub"]) == "Used a blocked word"
    assert check_content("scrubbing the deck", ["scrub"]) is None      # whole words only
    assert check_content("", ["x"]) is None and check_content("hello", []) is None


def test_parse_word_list():
    assert parse_word_list(" Foo, bar ,,FOO,baz ") == ["foo", "bar", "baz"]


# ── triage ──────────────────────────────────────────────────────────────────
def test_normalize_triage_clamps_bad_model_output():
    out = normalize_triage({"category": "WEIRD", "priority": "URGENT", "summary": "x" * 999})
    assert out["category"] == "other" and out["priority"] == "urgent"
    assert len(out["summary"]) == 300 and out["suggestion"] == "No suggestion available."


# ── AI tools ────────────────────────────────────────────────────────────────
def tool_ctx(db):
    guild = SimpleNamespace(id=9, name="G", member_count=2, text_channels=[1], voice_channels=[],
                            created_at=__import__("datetime").datetime(2021, 5, 4), get_member=lambda uid: None)
    return ToolContext(SimpleNamespace(db=db), guild, SimpleNamespace(id=5), 77)


async def test_tool_set_reminder_and_validation(db):
    ctx = tool_ctx(db)
    import json
    ok = json.loads(await execute_tool(ctx, "set_reminder", {"duration": "2h", "message": "stretch"}))
    assert ok["ok"] and ok["due_in"] == "2h"
    row = await db.fetchone("SELECT * FROM reminders WHERE id = ?", (ok["reminder_id"],))
    assert row["user_id"] == 5 and row["channel_id"] == 77 and row["due_at"] > time.time() + 7000
    bad = json.loads(await execute_tool(ctx, "set_reminder", {"duration": "soon", "message": "x"}))
    assert "error" in bad


async def test_tool_rank_leaderboard_and_unknown(db):
    import json
    ctx = tool_ctx(db)
    await db.execute("INSERT INTO levels (guild_id, user_id, xp, last_xp_at) VALUES (9, 5, 250, 0), (9, 6, 900, 0)")
    rank = json.loads(await execute_tool(ctx, "get_my_rank", {}))
    assert rank["rank"] == 2 and rank["level"] == 1 and rank["xp"] == 250
    board = json.loads(await execute_tool(ctx, "get_leaderboard", {"limit": 99}))["leaderboard"]
    assert [b["rank"] for b in board] == [1, 2] and board[0]["xp"] == 900
    assert "utc" in json.loads(await execute_tool(ctx, "get_current_time", {}))
    assert json.loads(await execute_tool(ctx, "get_server_info", {}))["members"] == 2
    with pytest.raises(ValueError):
        await execute_tool(ctx, "format_disk", {})


# ── dashboard ───────────────────────────────────────────────────────────────
async def make_dashboard(db, token=None):
    member = SimpleNamespace(display_name="Tyler")
    guild = SimpleNamespace(id=123, name="Test Guild", member_count=40, get_member=lambda uid: member if uid == 1 else None)
    log = ActivityLog()
    for name in ("ask", "ask", "rank"):
        log.command(name)
    log.add("command", "Tyler used /ask in Test Guild")
    bot = SimpleNamespace(
        latency=0.045, guilds=[guild], cogs={"AI": 1, "Polls": 1}, activity_log=log, db=db,
        claude=SimpleNamespace(enabled=True, model="m"), is_ready=lambda: True,
        get_guild=lambda gid: guild if gid == 123 else None,
    )
    app = create_app(bot, token)
    app[LATENCY_KEY].extend([40, 45, 50])
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


async def test_dashboard_endpoints(db):
    await db.execute("INSERT INTO levels (guild_id, user_id, xp, last_xp_at) VALUES (123, 1, 400, 0)")
    c = await make_dashboard(db)
    try:
        stats = await (await c.get("/api/stats")).json()
        assert stats["latency_ms"] == 45 and stats["commands_total"] == 3
        assert stats["top_commands"][0] == ["ask", 2] and stats["guilds"][0]["name"] == "Test Guild"
        assert stats["ai_model"] == "m" and stats["latency_history"] == [40, 45, 50]
        board = await (await c.get("/api/leaderboard?guild=123")).json()
        assert board == [{"rank": 1, "name": "Tyler", "level": 2, "xp": 400}]
        assert (await c.get("/api/leaderboard?guild=nope")).status == 400
        assert (await c.get("/api/leaderboard?guild=999")).status == 404
        feed = await (await c.get("/api/activity")).json()
        assert feed[0]["text"].startswith("Tyler used /ask")
        page = await c.get("/")
        assert page.status == 200 and "Bot Dashboard" in await page.text()
        assert (await c.get("/healthz")).status == 200
    finally:
        await c.close()


async def test_dashboard_token_auth(db):
    c = await make_dashboard(db, token="s3cret")
    try:
        assert (await c.get("/api/stats")).status == 401
        assert (await c.get("/api/stats?token=wrong")).status == 401
        assert (await c.get("/api/stats?token=s3cret")).status == 200
        assert (await c.get("/api/stats", headers={"Authorization": "Bearer s3cret"})).status == 200
        assert (await c.get("/healthz")).status == 200            # health probe stays open
    finally:
        await c.close()


# ── Docker healthcheck ──────────────────────────────────────────────────────
def run_healthcheck(data_dir) -> int:
    env = {"DATA_DIR": str(data_dir), "PATH": "/usr/bin:/bin"}
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "healthcheck.py")], env=env).returncode


def test_healthcheck_fresh_stale_and_missing(tmp_path):
    assert run_healthcheck(tmp_path) == 1                              # no heartbeat yet
    (tmp_path / ".heartbeat").write_text(str(time.time()))
    assert run_healthcheck(tmp_path) == 0
    (tmp_path / ".heartbeat").write_text(str(time.time() - 600))
    assert run_healthcheck(tmp_path) == 1


# ── dashboard config ────────────────────────────────────────────────────────
def test_dashboard_defaults_to_localhost_8787():
    assert resolve_config({}) == ("127.0.0.1", 8787, None)


def test_dashboard_port_precedence():
    assert resolve_config({"PORT": "3000"})[1] == 3000                          # host-injected PORT
    assert resolve_config({"PORT": "3000", "DASHBOARD_PORT": "9000"})[1] == 9000   # explicit wins


def test_dashboard_refuses_public_bind_without_token():
    with pytest.raises(ValueError, match="DASHBOARD_TOKEN"):
        resolve_config({"DASHBOARD_HOST": "0.0.0.0"})
    assert resolve_config({"DASHBOARD_HOST": "0.0.0.0", "DASHBOARD_TOKEN": "x"}) == ("0.0.0.0", 8787, "x")
    assert resolve_config({"DASHBOARD_HOST": "0.0.0.0", "DASHBOARD_ALLOW_NO_TOKEN": "true"})[2] is None
