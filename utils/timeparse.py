"""Parse human durations like ``1h30m`` or ``2d 4h`` into seconds."""
from __future__ import annotations

import re

_UNITS = {"w": 604800, "d": 86400, "h": 3600, "m": 60, "s": 1}
_TOKEN = re.compile(r"(\d+)\s*([wdhms])", re.IGNORECASE)


def parse_duration(text: str) -> int | None:
    """Return the duration in seconds, or ``None`` if ``text`` isn't a valid duration."""
    text = text.strip().lower()
    if not text:
        return None
    matches = list(_TOKEN.finditer(text))
    if not matches:
        return None
    # Everything in the string must be consumed by duration tokens (whitespace aside).
    leftover = _TOKEN.sub("", text).strip()
    if leftover:
        return None
    total = sum(int(n) * _UNITS[u.lower()] for n, u in (m.groups() for m in matches))
    return total or None


def format_duration(seconds: int) -> str:
    """Inverse of :func:`parse_duration` – ``3720`` -> ``'1h 2m'``."""
    seconds = int(seconds)
    if seconds <= 0:
        return "0s"
    parts = []
    for unit, size in (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1)):
        value, seconds = divmod(seconds, size)
        if value:
            parts.append(f"{value}{unit}")
    return " ".join(parts)
