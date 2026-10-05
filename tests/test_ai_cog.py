"""End-to-end /ask against the fake API: streaming edit, tool use, quota, history, vision guard."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

from cogs.ai import AI, build_messages
from tests.fake_anthropic import start_fake, text_reply, tool_reply
from utils.activity import ActivityLog
from utils.claude import ClaudeClient
from utils.db import Database
from utils.settings import Settings


def make_interaction(user_id=42):
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id, display_name="Tyler"),
        guild=SimpleNamespace(name="Test Server", id=7, member_count=3, text_channels=[], voice_channels=[],
                              created_at=__import__("datetime").datetime(2020, 1, 1)),
        channel_id=555,
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


async def make_bot(server_url):
    db = await Database.open(":memory:")
    bot = SimpleNamespace(
        db=db, settings=Settings(db), activity_log=ActivityLog(),
        claude=ClaudeClient("k", "fake-model", api_url=server_url),
    )
    return bot, db


async def test_ask_streams_uses_tools_and_remembers():
    def script(body, n):
        if n == 1:
            return {"stream": tool_reply("t1", "get_current_time", {}, preface="Let me check. ")}
        return {"stream": text_reply("It's 12:00 UTC.")}

    server, reqs = await start_fake(script)
    bot, db = await make_bot(str(server.make_url("/v1/messages")))
    try:
        cog = AI(bot)
        cog.daily_limit = 3
        it = make_interaction()
        await AI.ask.callback(cog, it, "what time is it?", None, False)

        final = it.edit_original_response.await_args_list[-1].kwargs["embed"]
        assert final.description == "Let me check.\n\nIt's 12:00 UTC."
        assert "clock" in final.footer.text and "2 asks left today" in final.footer.text
        assert reqs[0]["tools"] and "Tyler" in reqs[0]["system"]

        hist = await db.fetchall("SELECT role, text FROM ai_history ORDER BY id")
        assert [r["role"] for r in hist] == ["user", "assistant"]

        # second question carries the remembered turn
        reqs.clear()
        await AI.ask.callback(cog, make_interaction(), "and tomorrow?", None, False)
        sent = reqs[0]["messages"]
        assert [m["role"] for m in sent[:3]] == ["user", "assistant", "user"]
        assert sent[0]["content"] == "what time is it?"

        # /askreset forgets
        it2 = make_interaction()
        await AI.askreset.callback(cog, it2)
        assert (await db.fetchone("SELECT COUNT(*) AS n FROM ai_history"))["n"] == 0
    finally:
        await bot.claude.close()
        await server.close()
        await db.close()


async def test_ask_enforces_daily_quota():
    server, reqs = await start_fake(lambda b, n: {"stream": text_reply("ok")})
    bot, db = await make_bot(str(server.make_url("/v1/messages")))
    try:
        cog = AI(bot)
        cog.daily_limit = 1
        await AI.ask.callback(cog, make_interaction(), "one", None, False)
        it = make_interaction()
        await AI.ask.callback(cog, it, "two", None, False)
        assert len(reqs) == 1                      # second call never reached the API
        assert "used your 1 AI requests" in it.followup.send.await_args.args[0]
    finally:
        await bot.claude.close()
        await server.close()
        await db.close()


async def test_ask_without_api_key_is_polite():
    db = await Database.open(":memory:")
    bot = SimpleNamespace(db=db, settings=Settings(db), activity_log=ActivityLog(), claude=ClaudeClient(None))
    try:
        it = make_interaction()
        await AI.ask.callback(AI(bot), it, "hello", None, False)
        assert "isn't enabled" in it.response.send_message.await_args.args[0]
    finally:
        await db.close()


async def test_ask_rejects_bad_attachment():
    db = await Database.open(":memory:")
    bot = SimpleNamespace(db=db, settings=Settings(db), activity_log=ActivityLog(), claude=ClaudeClient("k"))
    try:
        it = make_interaction()
        bad = SimpleNamespace(content_type="application/pdf", size=10)
        await AI.ask.callback(AI(bot), it, "look", bad, False)
        assert "PNG, JPEG, GIF or WebP" in it.response.send_message.await_args.args[0]
    finally:
        await db.close()


def test_build_messages_repairs_history():
    hist = [{"role": "assistant", "text": "orphan"}, {"role": "user", "text": "q1"},
            {"role": "assistant", "text": "a1"}, {"role": "user", "text": "dangling"}]
    msgs = build_messages(hist, "new")
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[0]["content"] == "q1" and msgs[-1]["content"] == "new"
