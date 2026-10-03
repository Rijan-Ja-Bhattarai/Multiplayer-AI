"""The Resources and Settings pages, against a real headless window.

Runs through the offscreen Qt platform plugin so no display is needed.
The window is given a MemoryVault rather than a real one: DesktopRuntime
calls Storage.save_credentials while starting up, so a real Vault would
leave a device token in the developer's OS credential store.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 is needed for the desktop page tests")
pytest.importorskip("psutil", reason="psutil backs the Resources page")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

from desktop_app import window as win  # noqa: E402
from desktop_app.storage import ABSENT, REMOVED, Storage  # noqa: E402
from desktop_app.theme import DARK, LIGHT, MIKU, THEME_CHOICES  # noqa: E402


class MemoryVault:
    """Stands in for the OS credential store."""

    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        return REMOVED if self.values.pop(name, None) is not None else ABSENT



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
    instance.network.wait(10000)


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
        instance.network.wait(10000)


def test_window_size_is_remembered(qt_app, storage) -> None:
    """Closing records the size so the next launch opens at the same one."""
    instance = win.MainWindow(storage)
    try:
        instance.resize(1150, 760)
        instance.remember_window_size()
        assert storage.settings["window_size"] == [1150, 760]
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


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



def test_settings_page_offers_the_identity_reset(window) -> None:
    """Settings is where a user goes to replace an exposed token."""
    window.navigate(win.SETTINGS_PAGE)
    labels = [button.text() for button in window.findChildren(QPushButton)]

    assert any("Reset local identity" in text for text in labels)


def _reset_dialog_answer(window, answer):
    """Run confirm_reset_identity with the dialog auto-answered.

    QMessageBox.exec() blocks, so it is entered through a timer that
    clicks the chosen button. Without this the test would hang forever
    under the offscreen platform, where nobody can dismiss a dialog.
    """
    calls = []
    window.command = calls.append

    def answer_it():
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMessageBox):
                widget.button(answer).click()
                return

    QTimer.singleShot(0, answer_it)
    window.confirm_reset_identity()
    return calls


def test_choosing_reset_dispatches_the_command(window) -> None:
    """Confirming Reset asks the runtime to replace the identity."""
    calls = _reset_dialog_answer(window, QMessageBox.StandardButton.Reset)

    assert calls == ["reset_identity"]


def test_cancelling_does_nothing(window) -> None:
    """Cancelling must leave the identity alone."""
    calls = _reset_dialog_answer(window, QMessageBox.StandardButton.Cancel)

    assert calls == []


def test_the_destructive_dialog_defaults_to_cancel(window) -> None:
    """A stray Enter must not discard the token.

    Otherwise the default button would reset the identity without the
    user having chosen Reset.
"""
    seen = {}
    original = QMessageBox.exec

    def capture(self):
        seen["box"] = self
        return int(QMessageBox.StandardButton.Cancel)

    QMessageBox.exec = capture
    try:
        _reset_dialog_answer(window, QMessageBox.StandardButton.Reset)
    finally:
        QMessageBox.exec = original

    box = seen["box"]
    assert box.defaultButton() is box.button(QMessageBox.StandardButton.Cancel)


def test_follow_system_persists_immediately(window) -> None:
    """Removing the stored theme must reach the file, not just memory.

    Only the explicit-choice branch saved, so picking "Follow system"
    left the old theme in settings.json until some unrelated write
    happened to flush it, and the choice came back on the next launch.
    """
    miku_index = [c[1] for c in THEME_CHOICES].index(MIKU)
    window.theme_picker.setCurrentIndex(miku_index)
    assert json.loads(window.storage.path.read_text(encoding="utf-8"))["theme"] == MIKU

    window.theme_picker.setCurrentIndex(0)

    on_disk = json.loads(window.storage.path.read_text(encoding="utf-8"))
    assert "theme" not in on_disk


def test_a_failed_command_is_reported_in_the_error_colour(window) -> None:
    """A failure must not be painted in the success colours."""
    window.command_failure("no-such-request", "Could not save credentials.")

    assert window.toast_error is True
    assert win.color(window.theme, "error") in window.toast.styleSheet()
    assert win.color(window.theme, "toast_fg") not in window.toast.styleSheet()


def test_a_notice_is_reported_in_the_success_colour(window) -> None:
    """The default stays success-coloured."""
    window.notice("Replaced 2 saved identities.")

    assert window.toast_error is False
    assert win.color(window.theme, "toast_fg") in window.toast.styleSheet()


def test_switching_themes_does_not_turn_an_error_toast_green(window) -> None:
    """apply_theme restyles the toast, so it must keep the error state.

    Otherwise a failure would silently change colour mid-display while
    the user is still reading it.
    """
    window.notice("Could not save credentials.", error=True)

    window.apply_theme(LIGHT)

    assert win.color(LIGHT, "error") in window.toast.styleSheet()
    assert win.color(LIGHT, "toast_fg") not in window.toast.styleSheet()


def test_settings_shows_owed_credentials_until_the_cleanup_completes(window) -> None:
    """State, not a toast, for a condition that outlives the message.

    isHidden is checked rather than isVisible because the window is never
    shown in a headless test, so every child reports as not visible.
    """
    window.navigate(win.SETTINGS_PAGE)

    assert window.pending_cleanup.isHidden(), "nothing is owed after a clean start"

    window.show_pending_cleanup(2)
    assert not window.pending_cleanup.isHidden()
    assert "2 saved credentials" in window.pending_cleanup.text()
    assert "Unlock" in window.pending_cleanup.text()

    window.show_pending_cleanup(1)
    assert "1 saved credential " in window.pending_cleanup.text()

    window.show_pending_cleanup(0)
    assert window.pending_cleanup.isHidden(), "cleared once nothing is owed"


def test_settings_reports_stranded_files_separately_from_credentials(window) -> None:
    """A folder that will not go is a different problem from a locked keyring.

    Folding it into the credential count would either blame the keyring or
    leave the line hidden, and this can last as long as whatever is holding
    the folder does.
    """
    window.navigate(win.SETTINGS_PAGE)

    window.show_pending_cleanup(0, 1)
    assert not window.pending_cleanup.isHidden()
    assert "1 deleted workspace has files" in window.pending_cleanup.text()
    assert "credential" not in window.pending_cleanup.text()

    window.show_pending_cleanup(2, 1)
    assert "2 saved credentials" in window.pending_cleanup.text()
    assert "1 deleted workspace has files" in window.pending_cleanup.text()

    window.show_pending_cleanup(0, 2)
    assert "2 deleted workspaces have files" in window.pending_cleanup.text()

    window.show_pending_cleanup(0, 0)
    assert window.pending_cleanup.isHidden(), "cleared once nothing is owed"


def test_the_pending_line_receives_the_runtime_event(window) -> None:
    """The runtime's pending_cleanup event is what drives the line."""
    window.network_event("pending_cleanup", {"credentials": 3, "files": 0})

    assert not window.pending_cleanup.isHidden()
    assert "3 saved credentials" in window.pending_cleanup.text()

    window.network_event("pending_cleanup", {"credentials": 0, "files": 1})

    assert "1 deleted workspace has files" in window.pending_cleanup.text()
