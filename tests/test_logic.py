import pytest

from cogs.ai import chunk_text
from cogs.arc_raiders import pick_best_match
from cogs.leveling import _font, clean_display_name, level_from_xp, render_rank_card, total_xp_for_level, xp_to_next
from cogs.polls import render_bar
from utils.timeparse import format_duration, parse_duration


def test_parse_duration():
    assert parse_duration("10m") == 600
    assert parse_duration("1h30m") == 5400
    assert parse_duration("2d 4h") == 2 * 86400 + 4 * 3600
    assert parse_duration("1w") == 604800


def test_parse_duration_rejects_garbage():
    for bad in ("", "abc", "10", "10x", "5m soon", "0m"):
        assert parse_duration(bad) is None


def test_format_duration_roundtrip():
    assert format_duration(3720) == "1h 2m"
    assert parse_duration(format_duration(93784)) == 93784


def test_level_math_is_consistent():
    assert level_from_xp(0) == (0, 0, xp_to_next(0))
    for level in range(0, 30):
        need = total_xp_for_level(level)
        assert level_from_xp(need)[0] == level
        if need:
            assert level_from_xp(need - 1)[0] == level - 1


def test_render_rank_card_returns_png():
    png = render_rank_card(name="Tyler", avatar_bytes=None, rank=1, level=5, xp_in_level=40, xp_needed=100, total_xp=1234)
    assert png.startswith(b"\x89PNG")


def test_render_bar():
    assert render_bar(0, 0) == "▱" * 12
    assert render_bar(4, 4) == "▰" * 12
    assert render_bar(1, 2).count("▰") == 6


def test_chunk_text():
    assert chunk_text("hi") == ["hi"]
    text = "\n".join(["x" * 100] * 100)
    pieces = chunk_text(text, limit=1000)
    assert all(len(p) <= 1000 for p in pieces) and len(pieces) > 1


def test_pick_best_match_prefers_exact_then_weapon():
    items = [{"name": "Ferro II", "item_type": "Blueprint"}, {"name": "Ferro", "item_type": "Weapon"}]
    assert pick_best_match(items, "ferro")["name"] == "Ferro"


def test_clean_display_name_drops_undrawable_characters():
    font = _font(40)
    assert clean_display_name("Tyler 👾", font) == "Tyler"          # emoji would render as an empty box
    assert clean_display_name("Ty\u200dler\ufe0f", font) == "Tyler"
    assert clean_display_name("Plain Name", font) == "Plain Name"
    assert clean_display_name("👾👾", font) == "Member"              # nothing drawable left


def test_accented_names_survive_with_a_real_font():
    from PIL import ImageFont

    font = _font(40)
    if not isinstance(font, ImageFont.FreeTypeFont) or "dejavu" not in str(font.path).lower() and "arial" not in str(font.path).lower():
        pytest.skip("no wide-coverage TrueType font installed")
    assert clean_display_name("José Müller", font) == "José Müller"


def test_rank_card_renders_with_emoji_name():
    png = render_rank_card(name="Tyler 👾", avatar_bytes=None, rank=1, level=0, xp_in_level=22, xp_needed=100, total_xp=22)
    assert png.startswith(b"\x89PNG")
