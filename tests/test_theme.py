"""Tests for the theme palettes and stylesheets.

These deliberately avoid importing Qt. The theme module is strings and
dicts, so the parity rules that matter can be checked without a display,
and the desktop test suite has no skip logic to lean on.
"""

from __future__ import annotations

import re

import pytest

from desktop_app import theme


def selectors(sheet: str) -> set:
    """The set of ``QType#name`` selectors declared in a stylesheet.

    Pseudo-states and sub-control selectors are ignored: the comparison is
    about which widgets a theme actually covers, not about every rule it
    spells out.
    """
    found = set()
    for block in re.findall(r"([^{}]+)\{", sheet):
        for part in block.split(","):
            name = part.strip().split(":")[0].strip()
            if name.startswith("Q"):
                found.add(name)
    return found


# --- parity ---------------------------------------------------------------


def test_both_palettes_define_the_same_tokens() -> None:
    """No token may exist in one theme and be missing from the other.

    A missing token falls back to the dark value, so the symptom would be a
    light theme with dark accents rather than an error, which is easy to
    miss by eye.
    """
    assert set(theme.PALETTES[theme.DARK]) == set(theme.PALETTES[theme.LIGHT])


def test_palette_values_are_hex_colours() -> None:
    """Every token is a #rrggbb value, since Qt accepts no other form here."""
    for name, tokens in theme.PALETTES.items():
        for token, value in tokens.items():
            assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), f"{name}.{token}={value}"


def test_both_themes_cover_the_same_widgets() -> None:
    """The light stylesheet styles every widget the dark one does.

    This is the check that catches a new widget being added and themed
    in only one of the two sheets.
    """
    dark = selectors(theme.THEMES[theme.DARK])
    light = selectors(theme.THEMES[theme.LIGHT])

    assert dark, "selector extraction found nothing in the dark theme"
    assert dark == light, (
        "light theme is missing: "
        f"{sorted(dark - light)}; light theme has extra: {sorted(light - dark)}"
    )


@pytest.mark.parametrize(
    "name",
    ["rail", "sidebar", "topbar", "profile", "card", "stat", "settings", "hero",
     "title", "heroTitle", "heading", "muted", "eyebrow", "statValue", "online",
     "primary", "nav", "workspace", "ghost", "danger"],
)
def test_named_widget_styled_in_both_themes(name: str) -> None:
    """Each objectName used in the UI is styled in both themes.

    Kept as an explicit list so adding a widget without theming it fails
    here rather than showing up as an unstyled box.
    """
    for theme_name, sheet in theme.THEMES.items():
        assert f"#{name}" in sheet, f"{theme_name} theme does not style #{name}"


def test_every_provider_has_an_accent_in_both_themes() -> None:
    """Provider accents exist per theme, so light does not borrow dark hues."""
    for theme_name in theme.THEME_NAMES:
        accents = theme.PROVIDER_ACCENTS[theme_name]
        assert set(accents) == set(theme.PROVIDER_NAMES)
        for key, value in accents.items():
            assert re.fullmatch(r"#[0-9a-fA-F]{6}", value), f"{theme_name}.{key}={value}"


# --- accessors ------------------------------------------------------------


def test_palette_falls_back_to_dark_for_unknown_theme() -> None:
    """A bad theme name must not raise.

    The value comes from a user-editable settings file, so a typo there
    should degrade to a working window rather than a crash on startup.
    """
    assert theme.palette("nonsense") == theme.PALETTES[theme.DARK]


def test_color_raises_on_an_unknown_token() -> None:
    """An unknown token raises rather than silently substituting a colour.

    A theme name can come from a user-edited settings file and so must
    degrade quietly. A token can only be wrong because of a typo, and
    quietly returning some other colour would ship an invisible mistake.
    """
    with pytest.raises(KeyError):
        theme.color(theme.LIGHT, "not_a_token")


def test_stylesheet_falls_back_to_dark() -> None:
    """An unknown theme name returns the dark stylesheet."""
    assert theme.stylesheet("nonsense") == theme.THEME


def test_light_accent_differs_from_dark() -> None:
    """The accent is re-tuned per theme, not merely copied.

    A single shared accent would make light mode look washed out, and
    nothing else in this file would catch that.
    """
    assert theme.color(theme.DARK, "accent") != theme.color(theme.LIGHT, "accent")


@pytest.mark.parametrize("token", ["text", "text_muted", "success", "error", "agent_title"])
def test_light_text_differs_from_dark(token: str) -> None:
    """Text-bearing tokens differ per theme, so contrast is retuned."""
    assert theme.color(theme.DARK, token) != theme.color(theme.LIGHT, token)


# --- theme resolution -----------------------------------------------------


def test_resolve_theme_honours_an_explicit_choice() -> None:
    """A stored choice wins over the OS preference."""
    for name in theme.THEME_NAMES:
        assert theme.resolve_theme({"theme": name}, app=object()) == name


def test_resolve_theme_follows_the_system_when_unset(monkeypatch) -> None:
    """With no stored choice the OS preference is used."""
    monkeypatch.setattr(theme, "system_theme", lambda app=None: theme.LIGHT)
    assert theme.resolve_theme({}) == theme.LIGHT


def test_resolve_theme_falls_back_to_dark(monkeypatch) -> None:
    """An unknown stored value, or no OS preference, means dark."""
    monkeypatch.setattr(theme, "system_theme", lambda app=None: None)
    assert theme.resolve_theme({"theme": "chartreuse"}) == theme.DARK
    assert theme.resolve_theme({}) == theme.DARK


def test_resolve_theme_tolerates_missing_settings() -> None:
    """None settings must not raise."""
    assert theme.resolve_theme(None) in theme.THEME_NAMES


# --- providers ------------------------------------------------------------


def test_provider_names_keeps_its_shape() -> None:
    """Each entry stays a 4-tuple, as the UI unpacks it positionally."""
    for key, entry in theme.PROVIDER_NAMES.items():
        assert isinstance(entry, tuple) and len(entry) == 4, key


def test_provider_entry_is_theme_aware() -> None:
    """The same provider reports a different accent per theme."""
    dark = theme.provider_entry(theme.DARK, "openai")
    light = theme.provider_entry(theme.LIGHT, "openai")

    assert dark[3] != light[3]
    # Name, blurb and glyph are theme-independent.
    assert dark[:3] == light[:3]


def test_unknown_provider_falls_back_without_raising() -> None:
    """An agent record naming a provider this build lacks still renders."""
    entry = theme.provider_entry(theme.LIGHT, "a-provider-from-the-future")

    assert entry[0] == "Connectivity agent"
    assert entry[2] == "⌘"  # preserved from the pre-theme agent list
    assert entry[3] == theme.color(theme.LIGHT, "provider_fallback")
