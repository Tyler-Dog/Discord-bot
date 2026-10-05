"""Per-guild settings stored in SQLite with an in-memory cache, plus module gating."""
from __future__ import annotations

from typing import Any

import discord
from discord import app_commands

# cog class name -> module name users can toggle with /config module
MODULE_BY_COG = {
    "Leveling": "leveling",
    "AI": "ai",
    "Polls": "polls",
    "Reminders": "reminders",
    "ArcRaiders": "arc",
}
MODULES = sorted(set(MODULE_BY_COG.values()))

DEFAULTS: dict[str, Any] = {
    "xp_min": 15,
    "xp_max": 25,
    "xp_cooldown": 60,
    "level_announce": True,
    "automod": False,
}


class Settings:
    def __init__(self, db) -> None:
        self.db = db
        self._cache: dict[int, dict[str, str]] = {}

    async def _guild(self, guild_id: int) -> dict[str, str]:
        if guild_id not in self._cache:
            rows = await self.db.fetchall("SELECT key, value FROM guild_settings WHERE guild_id = ?", (guild_id,))
            self._cache[guild_id] = {r["key"]: r["value"] for r in rows}
        return self._cache[guild_id]

    async def get(self, guild_id: int, key: str, default: str | None = None) -> str | None:
        return (await self._guild(guild_id)).get(key, default)

    async def get_bool(self, guild_id: int, key: str, default: bool | None = None) -> bool:
        if default is None:
            default = bool(DEFAULTS.get(key, True))
        value = await self.get(guild_id, key)
        return default if value is None else value == "1"

    async def get_int(self, guild_id: int, key: str, default: int | None = None) -> int:
        if default is None:
            default = int(DEFAULTS.get(key, 0))
        value = await self.get(guild_id, key)
        try:
            return default if value is None else int(value)
        except ValueError:
            return default

    async def set(self, guild_id: int, key: str, value: Any) -> None:
        if isinstance(value, bool):
            value = "1" if value else "0"
        value = str(value)
        await self.db.execute(
            "INSERT INTO guild_settings (guild_id, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value",
            (guild_id, key, value),
        )
        (await self._guild(guild_id))[key] = value

    async def delete(self, guild_id: int, key: str) -> None:
        await self.db.execute("DELETE FROM guild_settings WHERE guild_id = ? AND key = ?", (guild_id, key))
        (await self._guild(guild_id)).pop(key, None)

    async def module_enabled(self, guild_id: int | None, module: str) -> bool:
        if guild_id is None:
            return True
        return await self.get_bool(guild_id, f"module_{module}", True)


def module_for_command(command: Any) -> str | None:
    binding = getattr(command, "binding", None)
    return MODULE_BY_COG.get(type(binding).__name__) if binding is not None else None


class BotTree(app_commands.CommandTree):
    """Command tree that blocks commands whose module was disabled with ``/config module``."""

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        module = module_for_command(interaction.command)
        settings = getattr(interaction.client, "settings", None)
        if module and settings and not await settings.module_enabled(interaction.guild_id, module):
            await interaction.response.send_message(
                f"🔕 The **{module}** module is turned off on this server.", ephemeral=True
            )
            return False
        return True
