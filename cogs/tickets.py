import asyncio
import html
import io
import json
import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.db import DATA_DIR
from utils.triage import PRIORITY_EMOJI, triage_ticket

logger = logging.getLogger("discord_bot")

CONFIG_PATH = DATA_DIR / "ticket_config.json"
MAX_TICKETS_DEFAULT = 10


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with CONFIG_PATH.open() as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(config: dict) -> None:
    with CONFIG_PATH.open("w") as f:
        json.dump(config, f, indent=2)


def get_guild_cfg(config: dict, guild_id: int) -> dict:
    return config.setdefault(str(guild_id), {})


def build_transcript(channel: discord.TextChannel, messages: list[discord.Message]) -> str:
    lines = [f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Transcript — {html.escape(channel.name)}</title>
<style>
  body {{ font-family: Arial, sans-serif; background: #313338; color: #dbdee1; margin: 0; padding: 20px; }}
  h1 {{ color: #fff; border-bottom: 1px solid #3f4147; padding-bottom: 10px; }}
  .meta {{ color: #949ba4; font-size: 13px; margin-bottom: 20px; }}
  .message {{ display: flex; gap: 12px; margin-bottom: 16px; }}
  .avatar {{ width: 40px; height: 40px; border-radius: 50%; background: #5865f2; display: flex; align-items: center; justify-content: center; font-weight: bold; color: #fff; flex-shrink: 0; font-size: 16px; }}
  .content {{ flex: 1; }}
  .author {{ font-weight: bold; color: #fff; margin-right: 8px; }}
  .timestamp {{ font-size: 11px; color: #949ba4; }}
  .text {{ margin-top: 4px; white-space: pre-wrap; word-break: break-word; }}
  .bot-tag {{ background: #5865f2; color: #fff; font-size: 10px; padding: 1px 4px; border-radius: 3px; margin-left: 4px; vertical-align: middle; }}
</style>
</head>
<body>
<h1>📋 Transcript — #{html.escape(channel.name)}</h1>
<div class="meta">
  Server: {html.escape(channel.guild.name)} &nbsp;|&nbsp;
  Channel: #{html.escape(channel.name)} &nbsp;|&nbsp;
  Exported: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")} &nbsp;|&nbsp;
  Messages: {len(messages)}
</div>
"""]

    for msg in messages:
        initial = html.escape((msg.author.display_name[0] if msg.author.display_name else "?").upper())
        bot_tag = '<span class="bot-tag">BOT</span>' if msg.author.bot else ""
        ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        content = html.escape(msg.content) if msg.content else "<em>(no text content)</em>"
        lines.append(f"""<div class="message">
  <div class="avatar">{initial}</div>
  <div class="content">
    <span class="author">{html.escape(msg.author.display_name)}</span>{bot_tag}
    <span class="timestamp">{ts}</span>
    <div class="text">{content}</div>
  </div>
</div>""")

    lines.append("</body></html>")
    return "\n".join(lines)


# ── Persistent Views ─────────────────────────────────────────────────────────

class TicketModal(discord.ui.Modal, title="Open a ticket"):
    subject = discord.ui.TextInput(label="Subject", max_length=100, placeholder="Short title for your issue")
    description = discord.ui.TextInput(
        label="What's going on?", style=discord.TextStyle.paragraph, max_length=1000,
        placeholder="Give staff the details they need.",
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await create_ticket(interaction, str(self.subject), str(self.description))


def _open_channels(guild: discord.Guild, guild_cfg: dict, user_id: int) -> list[str]:
    """The user's still-existing ticket channel ids (prunes manually deleted ones)."""
    open_tickets: dict = guild_cfg.setdefault("open_tickets", {})
    channels = open_tickets.get(str(user_id), [])
    valid = [cid for cid in channels if guild.get_channel(int(cid)) is not None]
    if len(valid) != len(channels):
        open_tickets[str(user_id)] = valid
    return valid


async def create_ticket(interaction: discord.Interaction, subject: str, description: str) -> None:
    guild = interaction.guild
    member = interaction.user

    config = load_config()
    guild_cfg = get_guild_cfg(config, guild.id)
    category_id = guild_cfg.get("category_id")
    support_role_ids = guild_cfg.get("support_role_ids", [])
    max_tickets = guild_cfg.get("max_tickets_per_user", MAX_TICKETS_DEFAULT)

    user_channels = _open_channels(guild, guild_cfg, member.id)
    if len(user_channels) >= max_tickets:
        await interaction.followup.send(f"You already have {len(user_channels)} open ticket(s) (max {max_tickets}).", ephemeral=True)
        return

    category = guild.get_channel(int(category_id)) if category_id else None
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True, manage_messages=True),
    }
    for role_id in support_role_ids:
        role = guild.get_role(int(role_id))
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_messages=True)

    ticket_number = guild_cfg.get("ticket_count", 0) + 1
    guild_cfg["ticket_count"] = ticket_number
    channel_name = f"ticket-{ticket_number:04d}-{member.name}"

    channel = await guild.create_text_channel(
        name=channel_name,
        category=category,
        overwrites=overwrites,
        topic=f"Ticket by {member} ({member.id}) | Opened {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
    )

    user_channels.append(str(channel.id))
    guild_cfg["open_tickets"][str(member.id)] = user_channels
    save_config(config)

    embed = discord.Embed(
        title=f"🎫 Ticket #{ticket_number:04d} — {subject}",
        description=(
            f"Welcome {member.mention}! Staff will be with you shortly.\n\n"
            f"**Your message**\n{description}\n\n"
            "When your issue is resolved, click **Close Ticket** below."
        ),
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Opened by {member.display_name}")
    await channel.send(content=member.mention, embed=embed, view=CloseTicketView())
    await interaction.followup.send(f"Your ticket has been created: {channel.mention}", ephemeral=True)
    logger.info("Ticket %s created by %s (%s)", channel_name, member, member.id)

    activity = getattr(interaction.client, "activity_log", None)
    if activity:
        activity.add("ticket", f"{member.display_name} opened ticket #{ticket_number:04d}")

    cog = interaction.client.get_cog("Tickets")
    if cog is not None:
        asyncio.create_task(cog.run_triage(channel, subject, description))


class OpenTicketView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🎫 Open Ticket",
        style=discord.ButtonStyle.primary,
        custom_id="ticket:open",
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild = interaction.guild
        config = load_config()
        guild_cfg = get_guild_cfg(config, guild.id)
        max_tickets = guild_cfg.get("max_tickets_per_user", MAX_TICKETS_DEFAULT)

        user_channels = _open_channels(guild, guild_cfg, interaction.user.id)
        save_config(config)
        if len(user_channels) >= max_tickets:
            mentions = " ".join(f"<#{cid}>" for cid in user_channels)
            await interaction.response.send_message(
                f"You already have {len(user_channels)} open ticket(s) (max {max_tickets}):\n{mentions}",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(TicketModal())


class TriageView(discord.ui.View):
    """Persistent button that reveals Claude's suggested reply to staff only (ephemeral)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="💡 Suggested reply (staff)", style=discord.ButtonStyle.secondary, custom_id="ticket:suggest")
    async def suggest(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        guild_cfg = get_guild_cfg(load_config(), interaction.guild_id)
        support_ids = {int(r) for r in guild_cfg.get("support_role_ids", [])}
        member = interaction.user
        is_staff = member.guild_permissions.manage_channels or any(r.id in support_ids for r in member.roles)
        if not is_staff:
            await interaction.response.send_message("Only staff can view this.", ephemeral=True)
            return
        row = await interaction.client.db.fetchone(
            "SELECT suggestion FROM ticket_triage WHERE channel_id = ?", (interaction.channel_id,)
        )
        if row is None:
            await interaction.response.send_message("No suggestion stored for this ticket.", ephemeral=True)
            return
        await interaction.response.send_message(f"**Suggested reply** (review before sending):\n\n{row['suggestion']}", ephemeral=True)


class CloseTicketView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="🔒 Close Ticket",
        style=discord.ButtonStyle.danger,
        custom_id="ticket:close",
    )
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        channel = interaction.channel
        guild = interaction.guild

        config = load_config()
        guild_cfg = get_guild_cfg(config, guild.id)
        transcript_channel_id = guild_cfg.get("transcript_channel_id")

        await interaction.response.send_message("🔒 Closing ticket and saving transcript...")

        messages = []
        async for msg in channel.history(limit=500, oldest_first=True):
            messages.append(msg)

        html = build_transcript(channel, messages)
        transcript_file = discord.File(
            fp=io.BytesIO(html.encode()),
            filename=f"transcript-{channel.name}.html",
        )

        if transcript_channel_id:
            transcript_channel = guild.get_channel(int(transcript_channel_id))
            if transcript_channel:
                embed = discord.Embed(
                    title=f"📋 Transcript — #{channel.name}",
                    description=f"Closed by {interaction.user.mention}",
                    color=discord.Color.orange(),
                    timestamp=datetime.now(timezone.utc),
                )
                embed.add_field(name="Messages", value=str(len(messages)), inline=True)
                embed.add_field(name="Opened by", value=channel.topic or "Unknown", inline=False)
                await transcript_channel.send(embed=embed, file=transcript_file)

        # Remove this channel from every user's open ticket list
        open_tickets: dict = guild_cfg.get("open_tickets", {})
        for uid, channel_list in list(open_tickets.items()):
            if isinstance(channel_list, list):
                if str(channel.id) in channel_list:
                    channel_list.remove(str(channel.id))
                    if not channel_list:
                        del open_tickets[uid]
            # Handle old single-string format gracefully
            elif channel_list == str(channel.id):
                del open_tickets[uid]

        guild_cfg["open_tickets"] = open_tickets
        save_config(config)

        await interaction.client.db.execute("DELETE FROM ticket_triage WHERE channel_id = ?", (channel.id,))
        logger.info("Ticket %s closed by %s (%s)", channel.name, interaction.user, interaction.user.id)
        await channel.delete(reason=f"Ticket closed by {interaction.user}")


# ── Cog ──────────────────────────────────────────────────────────────────────

class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        bot.add_view(OpenTicketView())
        bot.add_view(CloseTicketView())
        bot.add_view(TriageView())

    async def run_triage(self, channel: discord.TextChannel, subject: str, description: str) -> None:
        """Ask Claude to categorise the ticket and post the result (no-op without an API key)."""
        client = getattr(self.bot, "claude", None)
        if client is None or not client.enabled or not await self.bot.settings.module_enabled(channel.guild.id, "ai"):
            return
        try:
            result = await triage_ticket(client, subject, description)
            await self.bot.db.execute(
                "INSERT OR REPLACE INTO ticket_triage (channel_id, category, priority, summary, suggestion) VALUES (?, ?, ?, ?, ?)",
                (channel.id, result["category"], result["priority"], result["summary"], result["suggestion"]),
            )
            embed = discord.Embed(title="🤖 AI triage", description=result["summary"], color=discord.Color.from_str("#D97757"))
            embed.add_field(name="Category", value=result["category"].title())
            embed.add_field(name="Priority", value=f"{PRIORITY_EMOJI[result['priority']]} {result['priority'].title()}")
            embed.set_footer(text="Automated suggestion — staff should verify")
            await channel.send(embed=embed, view=TriageView())
        except Exception:
            logger.exception("Ticket triage failed for %s", channel.id)

    @app_commands.command(
        name="ticketsetup",
        description="Post the ticket panel and configure the ticket system.",
    )
    @app_commands.describe(
        panel_channel="Channel where the Open Ticket button will be posted.",
        ticket_category="Category where ticket channels will be created.",
        transcript_channel="Channel where transcripts are saved when a ticket is closed.",
        max_tickets="Max open tickets per user at once (default 10).",
    )
    @app_commands.default_permissions(administrator=True)
    async def ticketsetup(
        self,
        interaction: discord.Interaction,
        panel_channel: discord.TextChannel,
        ticket_category: discord.CategoryChannel,
        transcript_channel: discord.TextChannel,
        max_tickets: int = 10,
    ) -> None:
        guild_id = str(interaction.guild_id)
        config = load_config()
        config.setdefault(guild_id, {})
        config[guild_id]["category_id"] = str(ticket_category.id)
        config[guild_id]["transcript_channel_id"] = str(transcript_channel.id)
        config[guild_id]["max_tickets_per_user"] = max_tickets
        save_config(config)

        embed = discord.Embed(
            title="🎫 Support Tickets",
            description=(
                "Need help? Click the button below to open a ticket.\n"
                "A staff member will assist you as soon as possible."
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text=f"Up to {max_tickets} ticket(s) open per user at a time.")
        await panel_channel.send(embed=embed, view=OpenTicketView())

        confirm = discord.Embed(title="✅ Ticket system configured!", color=discord.Color.green())
        confirm.add_field(name="Panel Channel", value=panel_channel.mention, inline=True)
        confirm.add_field(name="Ticket Category", value=ticket_category.mention, inline=True)
        confirm.add_field(name="Transcript Channel", value=transcript_channel.mention, inline=True)
        confirm.add_field(name="Max Tickets Per User", value=str(max_tickets), inline=True)
        confirm.set_footer(text="Use /ticketsetrole to add support roles")
        await interaction.response.send_message(embed=confirm, ephemeral=True)

    @app_commands.command(
        name="ticketsetrole",
        description="Add or remove a role from ticket access.",
    )
    @app_commands.describe(role="The role to toggle ticket access for.")
    @app_commands.default_permissions(administrator=True)
    async def ticketsetrole(self, interaction: discord.Interaction, role: discord.Role) -> None:
        guild_id = str(interaction.guild_id)
        config = load_config()
        config.setdefault(guild_id, {})
        role_ids: list = config[guild_id].setdefault("support_role_ids", [])

        if str(role.id) in role_ids:
            role_ids.remove(str(role.id))
            action = "removed from"
        else:
            role_ids.append(str(role.id))
            action = "added to"

        save_config(config)

        current_roles = [
            interaction.guild.get_role(int(rid)).mention
            for rid in role_ids
            if interaction.guild.get_role(int(rid))
        ] or ["*(none)*"]

        embed = discord.Embed(title=f"✅ Role {action} ticket access", color=discord.Color.green())
        embed.add_field(name="Role", value=role.mention, inline=True)
        embed.add_field(name="Action", value=action.capitalize(), inline=True)
        embed.add_field(name="Current Support Roles", value="\n".join(current_roles), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="ticketshow",
        description="Show the current ticket system configuration.",
    )
    @app_commands.default_permissions(administrator=True)
    async def ticketshow(self, interaction: discord.Interaction) -> None:
        guild_id = str(interaction.guild_id)
        config = load_config()
        guild_cfg = config.get(guild_id, {})

        if not guild_cfg:
            await interaction.response.send_message(
                "Ticket system not configured yet. Use `/ticketsetup` first.", ephemeral=True
            )
            return

        category = interaction.guild.get_channel(int(guild_cfg["category_id"])) if guild_cfg.get("category_id") else None
        transcript_ch = interaction.guild.get_channel(int(guild_cfg["transcript_channel_id"])) if guild_cfg.get("transcript_channel_id") else None
        role_ids = guild_cfg.get("support_role_ids", [])
        roles = [
            interaction.guild.get_role(int(rid)).mention
            for rid in role_ids
            if interaction.guild.get_role(int(rid))
        ] or ["*(none)*"]

        # Count total open tickets across all users
        open_tickets = guild_cfg.get("open_tickets", {})
        total_open = sum(len(v) if isinstance(v, list) else 1 for v in open_tickets.values())

        embed = discord.Embed(title="🎫 Ticket System Configuration", color=discord.Color.blurple())
        embed.add_field(name="Ticket Category", value=category.mention if category else "*(not set)*", inline=True)
        embed.add_field(name="Transcript Channel", value=transcript_ch.mention if transcript_ch else "*(not set)*", inline=True)
        embed.add_field(name="Max Per User", value=str(guild_cfg.get("max_tickets_per_user", MAX_TICKETS_DEFAULT)), inline=True)
        embed.add_field(name="Support Roles", value="\n".join(roles), inline=False)
        embed.add_field(name="Total Tickets Created", value=str(guild_cfg.get("ticket_count", 0)), inline=True)
        embed.add_field(name="Currently Open", value=str(total_open), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tickets(bot))
