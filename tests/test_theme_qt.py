"""Theme switching exercised against the real widget tree.

Runs headlessly with the ``offscreen`` Qt platform plugin, so no display
is needed. These tests build a real MainWindow because the point is that
live switching reaches every widget: a stylesheet swap alone leaves
hand-styled widgets on the old colours, and only the assembled window can
show that.

The window is given a MemoryVault rather than a real one. DesktopRuntime
calls ``Storage.save_credentials`` during startup, which writes to the OS
credential store, so using the real Vault would leave a device token in
the developer's keyring every time the suite ran.
"""

from __future__ import annotations

import math
import os

import pytest

# Must be set before QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 is needed for the desktop theme tests")

from PySide6.QtGui import QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop_app.dialogs import _style_error  # noqa: E402
from desktop_app.storage import Storage  # noqa: E402
from desktop_app.theme import DARK, LIGHT, MIKU, color, provider_entry, stylesheet  # noqa: E402
from desktop_app.markdown import MarkdownMessage  # noqa: E402
from desktop_app.widgets import OrbitArt, label  # noqa: E402


class MemoryVault:
    """Stands in for the OS credential store."""

    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value


@pytest.fixture(scope="session")
def qt_app():
    """One QApplication for the session; Qt allows only one."""
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path, MemoryVault())


@pytest.fixture
def window(qt_app, storage, monkeypatch):
    """A real MainWindow, torn down after the test."""
    from desktop_app.window import MainWindow

    instance = MainWindow(storage)
    yield instance
    instance.network.shutdown()
    instance.network.wait(10000)


# --- resolution -----------------------------------------------------------


def test_window_uses_the_stored_theme(qt_app, storage) -> None:
    """A stored theme is applied when the window is built."""
    storage.settings["theme"] = LIGHT
    from desktop_app.window import MainWindow

    instance = MainWindow(storage)
    try:
        assert instance.theme == LIGHT
        assert instance.styleSheet() == stylesheet(LIGHT)
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


def test_window_applies_dark_by_default(qt_app, storage) -> None:
    """With no stored theme and no OS preference the window is dark."""
    from desktop_app.window import MainWindow

    instance = MainWindow(storage)
    try:
        # Headless Qt reports Unknown, so this exercises the fallback.
        assert instance.theme == DARK
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


# --- live switching -------------------------------------------------------


def test_apply_theme_replaces_the_stylesheet(window) -> None:
    """Switching themes changes the stylesheet on the live window."""
    window.apply_theme(LIGHT)
    assert window.styleSheet() == stylesheet(LIGHT)

    window.apply_theme(DARK)
    assert window.styleSheet() == stylesheet(DARK)


def test_apply_theme_switches_provider_logos(window) -> None:
    """Provider controls must remain readable when switching to light."""
    dark = window.provider_logos["openai"].pixmap().toImage()
    window.apply_theme(LIGHT)
    light = window.provider_logos["openai"].pixmap().toImage()
    assert light != dark
    assert not light.isNull()


def test_apply_theme_recolours_hand_styled_widgets(window) -> None:
    """Widgets carrying their own stylesheet pick up the new colours.

    These outrank the application stylesheet and would otherwise keep the
    dark-theme values after a switch. The avatar used to be one of them;
    it is painted now, so it is covered by its own test instead.
    """
    window.apply_theme(LIGHT)
    light_toast = window.toast.styleSheet()

    window.apply_theme(DARK)

    assert window.toast.styleSheet() != light_toast


# --- pieces ---------------------------------------------------------------


def test_orbit_art_accepts_a_theme(qt_app) -> None:
    """OrbitArt stores and repaints for the requested theme.

    Asks for qt_app rather than relying on another test having built the
    QApplication first. Without it, running this test on its own takes the
    whole process down instead of failing.
    """
    art = OrbitArt(theme=LIGHT)
    assert art._theme == LIGHT

    art.set_theme(DARK)
    assert art._theme == DARK


# --- the orbit's motion --------------------------------------------------
#
# Measured from the code that paints, through chip_position, rather than
# from a formula copied into the test. That is the point: a test with its
# own copy of the arithmetic would go on passing after the painting
# changed, which is how the original motion survived every run.


def orbit_samples(art, index, steps=400):
    """Every position of one chip across a whole cycle, in seconds."""
    cycle = art._CYCLE_MS / 1000.0
    return [(step / steps * cycle,
             art.chip_position(index, math.tau * step / steps)[0])
            for step in range(steps + 1)]


def test_the_chips_are_not_rounded_to_whole_pixels(qt_app) -> None:
    """Rounding is what made the motion look like an image still loading.

    A chip moving less than a pixel a frame could then only appear as
    still, still, jump. The coordinates have to keep their fractions or the
    antialiasing has nothing to soften.

    Checked by painting rather than by reading the numbers back: two phases
    a fraction of a pixel apart are rendered and compared. Snapped to the
    grid they land on the same pixels and the two pictures are identical;
    painted as floats the antialiasing resolves them differently.
    """
    art = OrbitArt(moving=False, theme=DARK)
    art.resize(260, 240)

    base = 1.0
    nudge = base + 1e-4
    moved = art.chip_position(0, nudge)[0] - art.chip_position(0, base)[0]
    assert 0 < abs(moved) < 1.0, (
        f"this check needs a shift smaller than a pixel, got {moved:.4f}px")

    art.set_phase(base)
    first = art.grab().toImage()
    art.set_phase(nudge)
    second = art.grab().toImage()

    assert first != second, (
        f"a shift of {abs(moved):.4f}px painted identically, so the chips are "
        f"being snapped to the pixel grid and the motion can only step")


def test_the_motion_is_visible_but_not_hurried(qt_app) -> None:
    """The orbit drifted 11px over twelve seconds, which read as a still image.

    Held between two bounds: enough travel that the movement is
    unmistakable, not so much that it pulls the eye off the words beside
    it. Both numbers are a judgement call, so they are written down rather
    than left to whatever the painting happened to produce.
    """
    art = OrbitArt(moving=False)
    art.resize(260, 240)
    cycle = art._CYCLE_MS / 1000.0

    for index in range(len(OrbitArt._CHIPS)):
        samples = orbit_samples(art, index)
        travel = max(x for _, x in samples) - min(x for _, x in samples)
        speeds = [abs(samples[n + 1][1] - samples[n][1]) / (cycle / len(samples))
                  for n in range(len(samples) - 1)]
        peak = max(speeds)

        assert travel >= 25.0, (
            f"chip {index} travels {travel:.1f}px across the cycle, too little "
            f"to read as motion rather than as a still image")
        assert travel <= 70.0, (
            f"chip {index} travels {travel:.1f}px, which is hurried for an "
            f"illustration sitting beside a paragraph")
        assert 3.0 <= peak <= 18.0, (
            f"chip {index} peaks at {peak:.1f}px per second; it should be "
            f"clearly moving but unhurried")


def test_the_chips_do_not_trace_the_same_path(qt_app) -> None:
    """Four chips, four phases, so no two of them move alike.

    The chips used to share one phase, and two of the six pairs then
    traced paths within 7% of each other, which is the same motion twice.
    The other pairs already differed because the chips sit a quarter turn
    apart around the ellipse, so it was the diametrically opposed pairs
    that read as doubled up. A quarter cycle of phase between neighbours
    takes the closest pair to 25% apart, which is the gap the geometry
    allows: two chips half a cycle apart are exactly opposed, and an
    opposed pair on an ellipse is as close to tracing one path as it gets.
    """
    art = OrbitArt(moving=False)
    art.resize(260, 240)
    steps = 400

    def velocity(index):
        xs = [art.chip_position(index, math.tau * step / steps)[0]
              for step in range(steps + 1)]
        return [xs[n + 1] - xs[n] for n in range(steps)]

    paths = [velocity(index) for index in range(len(OrbitArt._CHIPS))]
    for first in range(len(paths)):
        for second in range(first + 1, len(paths)):
            scale = max(abs(v) for v in paths[first])
            apart = max(abs(x - y) for x, y in zip(paths[first], paths[second]))
            assert apart > scale * 0.2, (
                f"chips {first} and {second} trace the same path, within "
                f"{apart / scale * 100:.0f}% of each other, so one looks "
                f"like a copy of the other")


def test_one_cycle_is_long_enough_to_breathe(qt_app) -> None:
    """A full breath, not a fidget: the chip rests at each end of its travel."""
    art = OrbitArt(moving=False)
    cycle_ms = art._CYCLE_MS
    assert 10000 <= cycle_ms <= 25000, (
        f"a {cycle_ms}ms cycle is {'too quick to settle' if cycle_ms < 10000 else 'too slow to notice'}")


@pytest.mark.parametrize("name", [LIGHT, MIKU, DARK])
def test_model_controls_use_the_stored_theme_on_first_paint(
    qt_app, storage, name: str
) -> None:
    """The palette must reach controls at construction, before any switch."""
    storage.settings["theme"] = name
    from desktop_app.window import MainWindow

    instance = MainWindow(storage)
    try:
        image = instance.provider_logos["openai"].pixmap().toImage()
        pixels = [image.pixelColor(x, y) for x in range(image.width()) for y in range(image.height())
                  if image.pixelColor(x, y).alpha() > 128]
        assert pixels, "The real logo must be visible on first paint"
        brightness = sum(pixel.lightness() for pixel in pixels) / len(pixels)
        assert (brightness > 128) == (name != LIGHT), "Use a contrasting brand mark for each surface"
        assert instance.palette().color(QPalette.ColorRole.PlaceholderText).name() == color(name, "text_muted")
        # The orbit is hand-painted, so the stylesheet never reaches it and
        # apply_theme() does not run during startup. It used to be asserted
        # absent, which is how it stayed unmounted for so long: a later
        # commit replaced the hero panel it lived in and the assertion was
        # written to match what was left rather than to what was meant.
        assert instance.findChildren(OrbitArt), "the orbit art is not in the window"
        assert instance.welcome_art._theme == name, (
            "the orbit art was built with the wrong palette, so the welcome "
            "panel shows another theme's colours until the theme is switched")
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


def test_orbit_art_defaults_to_dark_for_a_caller_that_forgets(qt_app) -> None:
    """The fallback stays, so a future hand-painted widget cannot crash.

    Dark is the palette every token has a value for, so a missing
    argument degrades to a readable widget instead of an AttributeError
    during startup.
    """
    assert OrbitArt()._theme == DARK


def test_dialog_error_label_follows_the_window_theme(qt_app) -> None:
    """A dialog's error label is coloured from the window's live theme."""

    class StubWindow:
        theme = DARK

    window_stub = StubWindow()
    widget = label("")

    _style_error(widget, window_stub)
    dark = widget.styleSheet()

    window_stub.theme = LIGHT
    _style_error(widget, window_stub)

    assert widget.styleSheet() != dark
    assert "color: " in widget.styleSheet()


def test_app_icon_differs_between_themes(qt_app) -> None:
    """The painted window icon follows the accent colour."""
    from desktop_app.window import app_icon

    dark = app_icon(DARK).pixmap(64, 64).toImage().pixelColor(32, 32)
    light = app_icon(LIGHT).pixmap(64, 64).toImage().pixelColor(32, 32)

    assert dark.name() != light.name()


@pytest.mark.parametrize("name", [LIGHT, MIKU, DARK])
def test_theme_switch_preserves_markdown_links_and_chat_history(window, name) -> None:
    """A saved reply follows the theme without changing its content."""
    reply = "[Website](https://example.test) and **a saved reply**"
    window.selected = "model"
    window.chats = {"model": {"messages": [("assistant", reply)], "history": []}}
    window.apply_theme(name)
    body = window.messages.itemAt(0).widget().findChild(MarkdownMessage)
    assert body is not None
    assert body.palette().color(QPalette.ColorRole.Base).name() == color(name, "surface")
    link_format = body.document().find("Website").charFormat()
    assert link_format.anchorHref() == "https://example.test"
    assert link_format.foreground().color().name() == color(name, "agent_title")
    assert window.chats["model"]["messages"] == [("assistant", reply)]
