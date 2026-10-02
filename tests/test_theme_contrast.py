"""Contrast checks for every theme's text pairings.

Kept separate from the palette tests because these assert a quality
property of the colours rather than their shape. The Miku palette was
supplied as thirteen foreground/background pairings, all of which measured
between 1.09:1 and 2.00:1, so a theme built literally from them would be
unreadable. Checking the pairs that actually carry text makes that kind of
mistake a test failure rather than something a user discovers.
"""

from __future__ import annotations

import pytest

from desktop_app import theme


@pytest.mark.parametrize("theme_name", theme.THEME_NAMES)
@pytest.mark.parametrize(
    "foreground,background,minimum,description",
    theme.CONTRAST_PAIRS,
    ids=[f"{p[3]}" for p in theme.CONTRAST_PAIRS],
)
def test_declared_pairs_meet_their_minimum(
    theme_name: str, foreground: str, background: str, minimum: float,
    description: str,
) -> None:
    """Each declared pairing meets the ratio its class requires."""
    actual = theme.contrast_ratio(
        theme.color(theme_name, foreground), theme.color(theme_name, background)
    )

    assert actual >= minimum, (
        f"{theme_name}: {description} uses {foreground} on {background} at "
        f"{actual:.2f}:1, which is below the required {minimum}:1 "
        f"({theme.color(theme_name, foreground)} on {theme.color(theme_name, background)})"
    )


@pytest.mark.parametrize("theme_name", theme.THEME_NAMES)
def test_every_contrast_pair_names_real_tokens(theme_name: str) -> None:
    """A contrast pair referring to a missing token is a silent no-op."""
    for foreground, background, _, _ in theme.CONTRAST_PAIRS:
        assert foreground in theme.palette(theme_name), foreground
        assert background in theme.palette(theme_name), background


def test_contrast_ratio_matches_hand_computed_values() -> None:
    """The ratio implementation is correct, checked against known values.

    Black on white is the maximum at 21:1, and the two colours are the
    same, giving 1:1.
    """
    assert theme.contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.01)
    assert theme.contrast_ratio("#5865f2", "#5865f2") == pytest.approx(1.0)


def test_contrast_ratio_is_order_independent() -> None:
    """Swapping the two colours gives the same ratio."""
    assert theme.contrast_ratio("#dbdee1", "#2b2d31") == pytest.approx(
        theme.contrast_ratio("#2b2d31", "#dbdee1")
    )


@pytest.mark.parametrize("theme_name", theme.THEME_NAMES)
def test_muted_text_is_readable_on_every_surface(theme_name: str) -> None:
    """Muted text is used for subtitles and captions, so it is held to 4.5.

    It is the easiest token to weaken, because muted-by-definition reads
    as "this may be too faint".
    """
    palette = theme.palette(theme_name)
    for surface in ("surface", "surface_base", "surface_sunken"):
        actual = theme.contrast_ratio(palette["text_muted"], palette[surface])
        assert actual >= theme.BODY_TEXT, (
            f"{theme_name}: text_muted on {surface} is {actual:.2f}:1"
        )


@pytest.mark.parametrize("theme_name", theme.THEME_NAMES)
def test_accent_fill_is_readable_with_its_own_label_colour(theme_name: str) -> None:
    """Primary buttons must stay readable in hover and pressed states too."""
    palette = theme.palette(theme_name)
    for accent in ("accent", "accent_hover", "accent_press"):
        actual = theme.contrast_ratio(palette["on_accent"], palette[accent])
        assert actual >= theme.LARGE_MARK, (
            f"{theme_name}: on_accent on {accent} is {actual:.2f}:1"
        )


@pytest.mark.parametrize("theme_name", theme.THEME_NAMES)
def test_provider_accents_are_visible_on_their_own_surface(theme_name: str) -> None:
    """Provider glyphs are drawn on the card colour, so check them there.

    Themes may share an accent set, so legibility against the theme's own
    surface is the invariant rather than being distinct per theme. Miku
    reuses the dark accents, and its card is lighter than dark's, which is
    exactly the case this catches.
    """
    surface = theme.palette(theme_name)["surface"]
    for provider, accent in theme.PROVIDER_ACCENTS[theme_name].items():
        actual = theme.contrast_ratio(accent, surface)
        assert actual >= theme.LARGE_MARK, (
            f"{theme_name}: {provider} accent {accent} on {surface} is {actual:.2f}:1"
        )
