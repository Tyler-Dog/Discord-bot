import logging
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("discord_bot")
BASE_URL = "https://metaforge.app/api/arc-raiders"


async def fetch_json(endpoint: str, params: dict | None = None):
    url = f"{BASE_URL}{endpoint}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            return await resp.json()


def pick_best_match(items: list[dict], search: str, *, prefer_non_blueprint: bool = False, prefer_weapon: bool = False) -> Optional[dict]:
    if not items:
        return None

    search = search.lower().strip()

    exact = [i for i in items if i.get("name", "").lower() == search]
    starts = [i for i in items if i.get("name", "").lower().startswith(search)]
    contains = [i for i in items if search in i.get("name", "").lower()]

    candidates = exact or starts or contains or items[:]

    if prefer_weapon:
        weapon_candidates = [i for i in candidates if i.get("item_type") == "Weapon"]
        if weapon_candidates:
            return weapon_candidates[0]

    if prefer_non_blueprint:
        non_blueprints = [i for i in candidates if i.get("item_type") != "Blueprint"]
        if non_blueprints:
            return non_blueprints[0]

    return candidates[0]


def find_crafted_item_from_blueprint(blueprint: dict, items: list[dict]) -> Optional[dict]:
    blueprint_id = blueprint.get("id", "")
    blueprint_name = blueprint.get("name", "")

    crafted_id = blueprint_id.replace("-recipe", "")
    crafted_name = blueprint_name.replace(" Blueprint", "").strip().lower()

    for item in items:
        if item.get("id") == crafted_id:
            return item

    for item in items:
        if item.get("name", "").lower() == crafted_name:
            return item

    for item in items:
        if item.get("name", "").lower().startswith(crafted_name):
            return item

    return None


def format_stat_lines(stat_block: dict) -> list[str]:
    if not isinstance(stat_block, dict):
        return []

    stat_labels = {
        "damage": "Damage",
        "range": "Range",
        "fireRate": "Fire Rate",
        "stability": "Stability",
        "magazineSize": "Magazine",
        "weight": "Weight",
        "agility": "Agility",
        "stealth": "Stealth",
        "healing": "Healing",
        "duration": "Duration",
        "radius": "Radius",
        "shield": "Shield",
        "health": "Health",
    }

    lines = []
    for key, label in stat_labels.items():
        value = stat_block.get(key)
        if value not in (None, "", 0, [], {}):
            lines.append(f"**{label}:** {value}")

    return lines[:12]


def build_item_embed(item: dict, *, crafted_item: Optional[dict] = None) -> discord.Embed:
    display_item = crafted_item or item

    embed = discord.Embed(
        title=item.get("name", "Unknown Item"),
        description=item.get("description") or "No description available.",
    )

    embed.add_field(name="ID", value=str(item.get("id", "Unknown")), inline=False)
    embed.add_field(name="Type", value=str(item.get("item_type", "Unknown")), inline=True)
    embed.add_field(name="Rarity", value=str(item.get("rarity", "Unknown")), inline=True)
    embed.add_field(name="Value", value=str(item.get("value", "Unknown")), inline=True)

    embed.add_field(name="Category", value=str(display_item.get("subcategory") or "Unknown"), inline=True)
    embed.add_field(name="Ammo", value=str(display_item.get("ammo_type") or "None"), inline=True)
    embed.add_field(name="Workbench", value=str(display_item.get("workbench") or "Unknown"), inline=True)

    if crafted_item:
        embed.add_field(name="Crafts", value=str(crafted_item.get("name", "Unknown")), inline=False)

    stat_lines = format_stat_lines(display_item.get("stat_block", {}))
    if stat_lines:
        embed.add_field(name="Stats", value="\n".join(stat_lines), inline=False)

    if item.get("item_type") == "Blueprint":
        embed.add_field(name="Crafting Materials", value="Not available in the current item response.", inline=False)

    icon = item.get("icon") or display_item.get("icon")
    if icon:
        embed.set_thumbnail(url=icon)

    embed.set_footer(text="Data from MetaForge")
    return embed


async def search_items(query: str) -> list[dict]:
    data = await fetch_json("/items", {"search": query})
    if isinstance(data, dict):
        return data.get("data", [])
    if isinstance(data, list):
        return data
    return []


class ArcRaiders(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="arcitem", description="Look up any ARC Raiders item")
    @app_commands.describe(name="Item name to search for")
    async def arcitem(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer()

        try:
            items = await search_items(name)
        except Exception as e:
            await interaction.followup.send(f"API error: {e}")
            return

        if not items:
            await interaction.followup.send("No items found.")
            return

        best_item = pick_best_match(items, name, prefer_non_blueprint=True)
        if best_item is None:
            await interaction.followup.send("No items found.")
            return

        crafted_item = None
        if best_item.get("item_type") == "Blueprint":
            crafted_item = find_crafted_item_from_blueprint(best_item, items)

        embed = build_item_embed(best_item, crafted_item=crafted_item)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="arcweapon", description="Look up an ARC Raiders weapon")
    @app_commands.describe(name="Weapon name to search for")
    async def arcweapon(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer()

        try:
            items = await search_items(name)
        except Exception as e:
            await interaction.followup.send(f"API error: {e}")
            return

        best_item = pick_best_match(items, name, prefer_weapon=True, prefer_non_blueprint=True)
        if not best_item:
            await interaction.followup.send("No weapons found.")
            return

        embed = build_item_embed(best_item)
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="arcsearch", description="Search ARC Raiders items")
    @app_commands.describe(name="Item name to search for")
    async def arcsearch(self, interaction: discord.Interaction, name: str) -> None:
        await interaction.response.defer()

        try:
            items = await search_items(name)
        except Exception as e:
            await interaction.followup.send(f"API error: {e}")
            return

        if not items:
            await interaction.followup.send("No results found.")
            return

        embed = discord.Embed(
            title=f"Search Results for '{name}'",
            description=f"Found {len(items)} result(s). Showing up to 10.",
        )

        for item in items[:10]:
            embed.add_field(
                name=item.get("name", "Unknown"),
                value=(
                    f"**Type:** {item.get('item_type', 'Unknown')}\n"
                    f"**Rarity:** {item.get('rarity', 'Unknown')}\n"
                    f"**Value:** {item.get('value', 'Unknown')}"
                ),
                inline=False,
            )

        embed.set_footer(text="Data from MetaForge")
        await interaction.followup.send(embed=embed)

    @app_commands.command(name="arctraders", description="Show ARC Raiders traders and some of their items")
    async def arctraders(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        try:
            data = await fetch_json("/traders")
        except Exception as e:
            await interaction.followup.send(f"API error: {e}")
            return

        trader_data = data.get("data", {})
        if not isinstance(trader_data, dict) or not trader_data:
            await interaction.followup.send("No trader data found.")
            return

        embed = discord.Embed(
            title="ARC Raiders Traders",
            description="Showing traders and a few sample items from each inventory.",
        )

        for trader_name, inventory in trader_data.items():
            if not isinstance(inventory, list):
                continue

            lines = []
            for item in inventory[:5]:
                lines.append(
                    f"• **{item.get('name', 'Unknown')}** — "
                    f"{item.get('rarity', 'Unknown')} — "
                    f"{item.get('trader_price', 'Unknown')}"
                )

            if not lines:
                lines.append("No items found.")

            embed.add_field(name=trader_name, value="\n".join(lines)[:1024], inline=False)

        embed.set_footer(text="Data from MetaForge")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ArcRaiders(bot))