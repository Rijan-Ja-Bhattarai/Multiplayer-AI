"""The Resources and Settings pages, against a real headless window.

Runs through the offscreen Qt platform plugin so no display is needed.
The window is given a MemoryVault rather than a real one: DesktopRuntime
calls Storage.save_credentials while starting up, so a real Vault would
leave a device token in the developer's OS credential store.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 is needed for the desktop page tests")
pytest.importorskip("psutil", reason="psutil backs the Resources page")

from PySide6.QtWidgets import QApplication  # noqa: E402

from desktop_app import window as win  # noqa: E402
from desktop_app.storage import Storage  # noqa: E402
from desktop_app.theme import DARK, LIGHT, MIKU, THEME_CHOICES  # noqa: E402


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
def window(qt_app, storage):
    """A real MainWindow, torn down after the test."""
    instance = win.MainWindow(storage)
    yield instance
    instance.network.shutdown()


# --- navigation ----------------------------------------------------------


def test_pages_and_titles_stay_in_step() -> None:
    """The nav, the stacked pages and the titles must agree in length.

    These were three separate lists before, so adding a page meant fixing
    the title tuple as well and forgetting to do so was an IndexError at
    startup.
    """
    assert len(win.PAGES) == len(win.PAGE_TITLES)
    assert 0 <= win.RESOURCES_PAGE < len(win.PAGES)
    assert 0 <= win.SETTINGS_PAGE < len(win.PAGES)


def test_window_builds_every_page(window) -> None:
    """All six pages are constructed, including the two new ones."""
    assert len(window.nav_buttons) == len(win.PAGES)
    assert window.stack.count() == len(win.PAGES)


@pytest.mark.parametrize("index", range(len(win.PAGES)))
def test_every_page_can_be_opened(window, index: int) -> None:
    """Navigating to each page does not raise."""
    window.navigate(index)

    assert window.stack.currentIndex() == index
    assert window.page_title.text() == win.PAGE_TITLES[index]


def test_controls_moved_off_the_providers_page(window) -> None:
    """Providers holds provider configuration and nothing else."""
    window.navigate(3)

    assert not hasattr(window.stack.currentWidget(), "_holds_settings")


def test_settings_controls_are_on_the_settings_page(window) -> None:
    """Reduce motion and the theme picker live in Settings."""
    window.navigate(win.SETTINGS_PAGE)

    assert window.theme_picker.count() == len(THEME_CHOICES)
    assert window.reduce_motion in window.findChildren(type(window.reduce_motion))


# --- theme picker ---------------------------------------------------------


def test_theme_picker_offers_all_themes(window) -> None:
    """Every theme is selectable, with follow-system first."""
    labels = [window.theme_picker.itemText(i) for i in range(window.theme_picker.count())]
    stored = [window.theme_picker.itemData(i) for i in range(window.theme_picker.count())]

    assert labels[0] == "Follow system"
    assert stored[0] is None
    assert set(stored[1:]) == {DARK, LIGHT, MIKU}


def test_theme_picker_defaults_to_follow_system(window) -> None:
    """With nothing stored the picker shows the system-following entry."""
    assert window.storage.settings.get("theme") is None
    assert window.theme_picker.currentIndex() == 0


def test_choosing_a_theme_applies_and_persists_it(window) -> None:
    """Picking Miku switches the window and remembers the choice."""
    index = [c[1] for c in THEME_CHOICES].index(MIKU)

    window.theme_picker.setCurrentIndex(index)

    assert window.theme == MIKU
    assert window.storage.settings["theme"] == MIKU
    assert window.styleSheet() == win.stylesheet(MIKU)


def test_returning_to_follow_system_removes_the_stored_choice(window) -> None:
    """Follow-system is represented by absence, not a stored value."""
    miku_index = [c[1] for c in THEME_CHOICES].index(MIKU)
    window.theme_picker.setCurrentIndex(miku_index)
    assert window.storage.settings["theme"] == MIKU

    window.theme_picker.setCurrentIndex(0)

    assert "theme" not in window.storage.settings
    assert window.theme in (DARK, LIGHT, MIKU)


def test_theme_hint_explains_the_current_choice(window) -> None:
    """The hint says what the picker is doing."""
    window.update_theme_hint()
    following = window.theme_hint.text()

    window.storage.settings["theme"] = LIGHT
    window.update_theme_hint()
    chosen = window.theme_hint.text()

    assert following != chosen
    assert "system" in following.lower()


def test_switching_theme_does_not_write_the_preference_back(window) -> None:
    """Applying a theme programmatically must not fight the picker.

    apply_theme resets the combo index, and if that emitted currentIndexChanged
    the stored preference would be rewritten from the resolved theme, which
    would silently convert follow-system into an explicit choice.
    """
    window.theme_picker.setCurrentIndex(0)
    window.apply_theme(MIKU)

    assert "theme" not in window.storage.settings
    assert window.theme_picker.currentIndex() == 0


# --- resources page -------------------------------------------------------


def test_resources_page_shows_real_readings(window) -> None:
    """Meters are filled from a live sample, not placeholders."""
    window.navigate(win.RESOURCES_PAGE)

    for key in ("cpu", "memory", "disk"):
        reading, bar = window.resource_rows[key]
        assert reading.text() not in ("", "—"), key
        assert bar.value() >= 0


def test_resources_page_builds_one_bar_per_core(window) -> None:
    """The per-core grid matches the machine."""
    window.navigate(win.RESOURCES_PAGE)

    assert len(window.core_bars) == window.sampler.sample()["core_count"]


def test_polling_runs_only_while_the_page_is_visible(window) -> None:
    """Sampling stops when the page is left.

    A panel that polls while hidden spends the app's own CPU to display
    nothing, on a page about CPU usage.
    """
    window.navigate(win.RESOURCES_PAGE)
    assert window.resource_timer.isActive()

    window.navigate(0)
    assert not window.resource_timer.isActive()

    window.navigate(win.RESOURCES_PAGE)
    assert window.resource_timer.isActive()


def test_manual_refresh_works_from_any_page(window) -> None:
    """The refresh shortcut updates the meters without navigating."""
    window.navigate(0)
    window.refresh_resources()

    assert window.resource_rows["cpu"][0].text() != "—"


# --- window sizing --------------------------------------------------------


def test_window_fits_the_screen_it_opens_on(qt_app, storage) -> None:
    """The opening size never exceeds the usable screen area."""
    instance = win.MainWindow(storage)
    try:
        available = instance.primary_screen_size()
        assert available is not None
        assert instance.width() <= available[0]
        assert instance.height() <= available[1]
        assert instance.minimumWidth() <= available[0]
        assert instance.minimumHeight() <= available[1]
    finally:
        instance.network.shutdown()


def test_window_size_is_remembered(qt_app, storage) -> None:
    """Closing records the size so the next launch opens at the same one."""
    instance = win.MainWindow(storage)
    try:
        instance.resize(1150, 760)
        instance.remember_window_size()
        assert storage.settings["window_size"] == [1150, 760]
    finally:
        instance.network.shutdown()


def test_sidebar_hides_when_the_window_is_narrow(window) -> None:
    """The agent sidebar is dropped when there is no room for it.

    isHidden is checked rather than isVisible because the window is never
    shown in a headless test, which makes every child report as not
    visible regardless of what was asked for.
    """
    window.resize(800, 700)
    window.apply_sidebar_density()
    assert window.sidebar.isHidden() is True

    window.resize(1500, 900)
    window.apply_sidebar_density()
    assert window.sidebar.isHidden() is False
