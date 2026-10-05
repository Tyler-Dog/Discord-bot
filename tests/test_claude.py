import pytest

from tests.fake_anthropic import start_fake, text_reply, tool_reply
from utils.claude import ClaudeClient, ClaudeError, apply_event, finalize_state, new_stream_state

_clients: list[ClaudeClient] = []


def client_for(server) -> ClaudeClient:
    c = ClaudeClient("test-key", "test-model", api_url=str(server.make_url("/v1/messages")))
    _clients.append(c)
    return c


@pytest.fixture(autouse=True)
async def _close_clients():
    yield
    while _clients:
        await _clients.pop().close()


def test_apply_event_accumulates_text_and_tool_json():
    state = new_stream_state()
    out = ""
    for ev in tool_reply("tu_1", "set_reminder", {"duration": "2h", "message": "hi"}, preface="On it. "):
        out += apply_event(state, ev)
    msg = finalize_state(state)
    assert out == "On it. "
    assert msg["stop_reason"] == "tool_use"
    assert msg["content"][0] == {"type": "text", "text": "On it. "}
    assert msg["content"][1] == {"type": "tool_use", "id": "tu_1", "name": "set_reminder",
                                 "input": {"duration": "2h", "message": "hi"}}
    assert msg["usage"] == {"input_tokens": 20, "output_tokens": 9}


async def test_stream_calls_on_text_progressively():
    server, reqs = await start_fake(lambda body, n: {"stream": text_reply("Hello from the fake Claude!")})
    seen = []

    async def on_text(delta):
        seen.append(delta)

    try:
        msg = await client_for(server).stream(messages=[{"role": "user", "content": "hi"}], on_text=on_text)
    finally:
        await server.close()
    assert len(seen) > 1 and "".join(seen) == "Hello from the fake Claude!"
    assert msg["content"] == [{"type": "text", "text": "Hello from the fake Claude!"}]
    assert reqs[0]["stream"] is True and reqs[0]["model"] == "test-model"


async def test_run_executes_tools_then_returns_final_answer():
    def script(body, n):
        if n == 1:
            return {"stream": tool_reply("tu_9", "get_current_time", {}, preface="Checking the clock. ")}
        return {"stream": text_reply("It is noon.")}

    server, reqs = await start_fake(script)
    calls, snapshots = [], []

    async def execute(name, args):
        calls.append((name, args))
        return '{"utc": "2026-01-01T12:00:00+00:00"}'

    async def on_text(full):
        snapshots.append(full)

    try:
        result = await client_for(server).run(
            messages=[{"role": "user", "content": "time?"}], tools=[{"name": "get_current_time"}],
            execute=execute, on_text=on_text,
        )
    finally:
        await server.close()

    assert calls == [("get_current_time", {})]
    assert result.tools_used == ["get_current_time"]
    assert result.text == "Checking the clock.\n\nIt is noon."
    assert result.rounds == 2 and result.input_tokens == 31 and result.output_tokens == 14
    assert snapshots[-1] == "Checking the clock.\n\nIt is noon."
    # the 2nd request must carry the assistant tool_use turn and our tool_result
    second = reqs[1]["messages"]
    assert second[1]["role"] == "assistant" and second[1]["content"][-1]["type"] == "tool_use"
    assert second[2]["content"][0]["type"] == "tool_result" and second[2]["content"][0]["tool_use_id"] == "tu_9"


async def test_tool_errors_are_reported_to_the_model_not_raised():
    def script(body, n):
        return {"stream": tool_reply("t", "boom", {})} if n == 1 else {"stream": text_reply("Sorry, that failed.")}

    server, reqs = await start_fake(script)

    async def execute(name, args):
        raise RuntimeError("kaput")

    try:
        result = await client_for(server).run(messages=[{"role": "user", "content": "x"}], tools=[{}], execute=execute)
    finally:
        await server.close()
    block = reqs[1]["messages"][-1]["content"][0]
    assert block["is_error"] is True and "kaput" in block["content"]
    assert result.text == "Sorry, that failed."


async def test_api_errors_raise_claude_error():
    server, _ = await start_fake(lambda b, n: {"status": 401, "json": {"error": {"message": "invalid x-api-key"}}})
    try:
        with pytest.raises(ClaudeError, match="invalid x-api-key"):
            await client_for(server).create(messages=[{"role": "user", "content": "x"}])
    finally:
        await server.close()


async def test_run_stops_at_max_rounds():
    server, _ = await start_fake(lambda b, n: {"stream": tool_reply(f"t{n}", "loop", {})})

    async def execute(name, args):
        return "ok"

    try:
        result = await client_for(server).run(messages=[{"role": "user", "content": "x"}], tools=[{}], execute=execute, max_rounds=3)
    finally:
        await server.close()
    assert result.rounds == 3 and "tool-call limit" in result.text
