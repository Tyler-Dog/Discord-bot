"""In-memory activity feed + command counters, surfaced on the web dashboard."""
from __future__ import annotations

import time
from collections import Counter, deque
from typing import Any


class ActivityLog:
    def __init__(self, maxlen: int = 100) -> None:
        self.events: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self.commands: Counter[str] = Counter()
        self.started_at = time.time()

    def add(self, kind: str, text: str) -> None:
        self.events.appendleft({"ts": time.time(), "kind": kind, "text": text})

    def command(self, name: str) -> None:
        self.commands[name] += 1

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self.events)[:limit]
