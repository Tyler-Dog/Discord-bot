"""`/config` — per-server settings: modules, XP tuning, mod-log, automod and level-role rewards."""
from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from utils.settings import DEFAULTS, MODULES


def parse_word_list(raw: str) -> list[str]:
    """``"foo, Bar ,,baz"`` -> ``['foo', 'bar', 'baz']`` (lower-cased, de-duplicated, order kept)."""
    seen: dict[str, None] = {}
    for word in raw.split(","):
        word = word.strip().lower()
        if word:
            seen[word] = None
    return list(seen)


@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
class Config(commands.GroupCog, group_name="config"):
    """Server configuration (needs Manage Server)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def settings(self):
        return self.bot.settings  # type: ignore[attr-defined]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.manage_guild:
            return True
        raise app_commands.MissingPermissions(["manage_guild"])

    @app_commands.command(name="show", description="Show this server's current settings.")
    async def show(self, interaction: discord.Interaction) -> None:
        gid = interaction.guild_id
        s = self.settings
        modules = []
        for m in MODULES:
            on = await s.module_enabled(gid, m)
            modules.append(f"{'🟢' if on else '🔴'} {m}")

        modlog_id = await s.get(gid, "modlog_channel")
        words = parse_word_list(await s.get(gid, "automod_blocked_words", "") or "")
        rows = await self.bot.db.fetchall(
            "SELECT level, role_id FROM level_roles WHERE guild_id = ? ORDER BY level", (gid,)
        )

        embed = discord.Embed(title="⚙️ Server settings", color=discord.Color.blurple())
        embed.add_field(name="Modules", value="\n".join(modules))
        embed.add_field(
            name="XP",
            value=(
                f"{await s.get_int(gid, 'xp_min')}–{await s.get_int(gid, 'xp_max')} per message\n"
                f"{await s.get_int(gid, 'xp_cooldown')}s cooldown\n"
                f"Level-up messages: {'on' if await s.get_bool(gid, 'level_announce') else 'off'}"
            ),
        )
        embed.add_field(
            name="Moderation",
            value=(
                f"Mod-log: {f'<#{modlog_id}>' if modlog_id else '*(not set)*'}\n"
                f"AutoMod: {'on' if await s.get_bool(gid, 'automod') else 'off'}\n"
                f"Blocked words: {len(words)}"
            ),
        )
        embed.add_field(
            name="Level roles",
            value="\n".join(f"Level {r['level']} → <@&{r['role_id']}>" for r in rows) or "*(none)*",
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="module", description="Turn a feature module on or off.")
    @app_commands.choices(module=[app_commands.Choice(name=m, value=m) for m in MODULES])
    async def module(self, interaction: discord.Interaction, module: app_commands.Choice[str], enabled: bool) -> None:
        await self.settings.set(interaction.guild_id, f"module_{module.value}", enabled)
        await interaction.response.send_message(
            f"{'🟢 Enabled' if enabled else '🔴 Disabled'} the **{module.value}** module.", ephemeral=True
        )

    @app_commands.command(name="xp", description="Tune how much XP members earn.")
    @app_commands.describe(minimum="Minimum XP per message", maximum="Maximum XP per message", cooldown="Seconds between XP gains")
    async def xp(
        self,
        interaction: discord.Interaction,
        minimum: app_commands.Range[int, 1, 500] = DEFAULTS["xp_min"],
        maximum: app_commands.Range[int, 1, 500] = DEFAULTS["xp_max"],
        cooldown: app_commands.Range[int, 5, 3600] = DEFAULTS["xp_cooldown"],
    ) -> None:
        if minimum > maximum:
            await interaction.response.send_message("❌ Minimum can't be higher than maximum.", ephemeral=True)
            return
        gid = interaction.guild_id
        await self.settings.set(gid, "xp_min", minimum)
        await self.settings.set(gid, "xp_max", maximum)
        await self.settings.set(gid, "xp_cooldown", cooldown)
        await interaction.response.send_message(
            f"✅ XP is now **{minimum}–{maximum}** per message with a **{cooldown}s** cooldown.", ephemeral=True
        )

    @app_commands.command(name="announce", description="Toggle level-up announcements.")
    async def announce(self, interaction: discord.Interaction, enabled: bool) -> None:
        await self.settings.set(interaction.guild_id, "level_announce", enabled)
        await interaction.response.send_message(
            f"Level-up messages are now **{'on' if enabled else 'off'}**.", ephemeral=True
        )

    @app_commands.command(name="modlog", description="Set (or clear) the channel where moderation cases are posted.")
    async def modlog(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        if channel is None:
            await self.settings.delete(interaction.guild_id, "modlog_channel")
            await interaction.response.send_message("Mod-log cleared.", ephemeral=True)
            return
        perms = channel.permissions_for(interaction.guild.me)
        if not (perms.send_messages and perms.embed_links):
            await interaction.response.send_message(
                f"❌ I need **Send Messages** and **Embed Links** in {channel.mention}.", ephemeral=True
            )
            return
        await self.settings.set(interaction.guild_id, "modlog_channel", channel.id)
        await interaction.response.send_message(f"📋 Moderation cases will be posted in {channel.mention}.", ephemeral=True)

    @app_commands.command(name="automod", description="Turn AutoMod (spam, mass mentions, invites, blocked words) on or off.")
    async def automod(self, interaction: discord.Interaction, enabled: bool) -> None:
        await self.settings.set(interaction.guild_id, "automod", enabled)
        note = ""
        if enabled and not self.bot.intents.message_content:
            note = "\nℹ️ Invite and blocked-word filters need the Message Content intent; spam and mass-mention checks work without it."
        await interaction.response.send_message(f"🛡️ AutoMod is now **{'on' if enabled else 'off'}**.{note}", ephemeral=True)

    @app_commands.command(name="blockedwords", description="Set AutoMod's blocked words (comma-separated, or 'clear').")
    async def blockedwords(self, interaction: discord.Interaction, words: str) -> None:
        if words.strip().lower() == "clear":
            await self.settings.delete(interaction.guild_id, "automod_blocked_words")
            await interaction.response.send_message("Blocked words cleared.", ephemeral=True)
            return
        parsed = parse_word_list(words)
        await self.settings.set(interaction.guild_id, "automod_blocked_words", ",".join(parsed))
        await interaction.response.send_message(f"Saved **{len(parsed)}** blocked word(s).", ephemeral=True)

    @app_commands.command(name="levelrole_add", description="Give a role when members reach a level.")
    async def levelrole_add(
        self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 200], role: discord.Role
    ) -> None:
        guild = interaction.guild
        if role.managed or role >= guild.me.top_role or role.is_default():
            await interaction.response.send_message(
                "❌ I can't assign that role (it's managed, `@everyone`, or above my highest role).", ephemeral=True
            )
            return
        await self.bot.db.execute(
            "INSERT INTO level_roles (guild_id, level, role_id) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, level) DO UPDATE SET role_id = excluded.role_id",
            (guild.id, level, role.id),
        )
        await interaction.response.send_message(
            f"🎖️ Members reaching **level {level}** will receive {role.mention}.",
            ephemeral=True, allowed_mentions=discord.AllowedMentions.none(),
        )

    @app_commands.command(name="levelrole_remove", description="Remove the reward role for a level.")
    async def levelrole_remove(self, interaction: discord.Interaction, level: app_commands.Range[int, 1, 200]) -> None:
        cur = await self.bot.db.execute(
            "DELETE FROM level_roles WHERE guild_id = ? AND level = ?", (interaction.guild_id, level)
        )
        await interaction.response.send_message(
            "Removed." if cur.rowcount else "No reward is set for that level.", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Config(bot))
