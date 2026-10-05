"""A tiny in-process fake of the Anthropic Messages API (streaming + non-streaming) for tests."""
import json
from typing import Callable

from aiohttp import web
from aiohttp.test_utils import TestServer


def sse(events: list[dict]) -> str:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)


def text_reply(text: str, *, chunk: int = 7) -> list[dict]:
    parts = [text[i:i + chunk] for i in range(0, len(text), chunk)]
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 11}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    ]
    events += [{"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": p}} for p in parts]
    events += [
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 5}},
        {"type": "message_stop"},
    ]
    return events


def tool_reply(tool_id: str, name: str, args: dict, preface: str = "") -> list[dict]:
    raw = json.dumps(args)
    mid = len(raw) // 2
    events = [{"type": "message_start", "message": {"usage": {"input_tokens": 20}}}]
    idx = 0
    if preface:
        events += [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": preface}},
            {"type": "content_block_stop", "index": 0},
        ]
        idx = 1
    events += [
        {"type": "content_block_start", "index": idx, "content_block": {"type": "tool_use", "id": tool_id, "name": name, "input": {}}},
        {"type": "content_block_delta", "index": idx, "delta": {"type": "input_json_delta", "partial_json": raw[:mid]}},
        {"type": "content_block_delta", "index": idx, "delta": {"type": "input_json_delta", "partial_json": raw[mid:]}},
        {"type": "content_block_stop", "index": idx},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 9}},
        {"type": "message_stop"},
    ]
    return events


async def start_fake(script: Callable[[dict, int], dict]) -> tuple[TestServer, list[dict]]:
    """``script(request_json, call_number)`` returns {'stream': [events]} / {'json': {...}} / {'status': 4xx, 'json': {...}}."""
    requests: list[dict] = []

    async def handler(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        requests.append(body)
        out = script(body, len(requests))
        if "status" in out:
            return web.json_response(out["json"], status=out["status"])
        if "json" in out:
            return web.json_response(out["json"])
        resp = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        await resp.write(sse(out["stream"]).encode())
        return resp

    app = web.Application()
    app.router.add_post("/v1/messages", handler)
    server = TestServer(app)
    await server.start_server()
    return server, requests
