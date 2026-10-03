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

import os

import pytest

# Must be set before QApplication is constructed.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 is needed for the desktop theme tests")

from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop_app.dialogs import _style_error  # noqa: E402
from desktop_app.storage import Storage  # noqa: E402
from desktop_app.theme import DARK, LIGHT, stylesheet  # noqa: E402
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


def test_window_applies_dark_by_default(qt_app, storage) -> None:
    """With no stored theme and no OS preference the window is dark."""
    from desktop_app.window import MainWindow

    instance = MainWindow(storage)
    try:
        # Headless Qt reports Unknown, so this exercises the fallback.
        assert instance.theme == DARK
    finally:
        instance.network.shutdown()


# --- live switching -------------------------------------------------------


def test_apply_theme_replaces_the_stylesheet(window) -> None:
    """Switching themes changes the stylesheet on the live window."""
    window.apply_theme(LIGHT)
    assert window.styleSheet() == stylesheet(LIGHT)

    window.apply_theme(DARK)
    assert window.styleSheet() == stylesheet(DARK)


def test_apply_theme_repaints_the_custom_painted_widget(window) -> None:
    """OrbitArt is hand-painted, so it needs telling about the theme."""
    window.apply_theme(LIGHT)

    assert window.orbit._theme == LIGHT


def test_apply_theme_recolours_hand_styled_widgets(window) -> None:
    """Widgets carrying their own stylesheet pick up the new colours.

    These outrank the application stylesheet and would otherwise keep the
    dark-theme values after a switch.
    """
    window.apply_theme(LIGHT)
    light_avatar = window.avatar.styleSheet()
    light_toast = window.toast.styleSheet()

    window.apply_theme(DARK)

    assert window.avatar.styleSheet() != light_avatar
    assert window.toast.styleSheet() != light_toast


# --- pieces ---------------------------------------------------------------


def test_orbit_art_accepts_a_theme() -> None:
    """OrbitArt stores and repaints for the requested theme."""
    art = OrbitArt(theme=LIGHT)
    assert art._theme == LIGHT

    art.set_theme(DARK)
    assert art._theme == DARK


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
