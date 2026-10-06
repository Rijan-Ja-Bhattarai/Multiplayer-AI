"""A crash log must not prevent the desktop window from starting."""
import errno
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("psutil")

from desktop_app import __main__ as startup


@pytest.fixture
def startup_flow(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["MultiplayerAI"])
    # A real dict, so the launch screen's decision is a real decision. A
    # MagicMock answers every .get() with a truthy mock, which would read
    # as "reduce motion is on" and quietly skip the splash every time.
    storage = MagicMock(directory=tmp_path, settings={})
    app = MagicMock()
    app.exec.return_value = 0
    lock = MagicMock()
    lock.tryLock.return_value = True
    window = MagicMock()
    messages = MagicMock()
    monkeypatch.setattr(startup, "Storage", MagicMock(return_value=storage))
    monkeypatch.setattr(startup, "QApplication", MagicMock(return_value=app))
    monkeypatch.setattr(startup, "app_icon", MagicMock())
    monkeypatch.setattr(startup, "QLockFile", MagicMock(return_value=lock))
    monkeypatch.setattr(startup, "MainWindow", MagicMock(return_value=window))
    monkeypatch.setattr(startup, "QMessageBox", messages)
    monkeypatch.setattr(startup, "LaunchScreen", MagicMock())
    return storage, app, lock, window, messages


def window_is_going_to_be_shown(window, splash=None):
    """Whether the window is shown now, or handed over once the card has gone.

    These tests are about a crash log that cannot be opened still letting
    the app start, so what matters is that the window is on its way, not
    whether it is on screen yet. When the launch screen is up the window is
    shown from the card's handover, which has not happened by the time
    main() returns.
    """
    if window.showMaximized.call_count:
        return True
    if splash is None:
        return False
    return splash.begin.call_args.kwargs.get("on_done") == window.showMaximized


@pytest.mark.parametrize("error", [PermissionError("Access denied"),
                                 IsADirectoryError("Crash log is a directory"),
                                 OSError(errno.ENOSPC, "No space left on device")])
def test_crash_log_open_failure_still_starts_window(monkeypatch, startup_flow, error):
    storage, app, lock, window, messages = startup_flow
    # Exercise the real diagnostics constructor failing before it installs hooks.
    def fail_open(path, *args, **kwargs):
        assert path == storage.directory / "desktop-crash.log"
        raise error
    monkeypatch.setattr(Path, "open", fail_open)

    assert startup.main() == 0

    startup.MainWindow.assert_called_once_with(storage)
    assert window_is_going_to_be_shown(window, startup.LaunchScreen.return_value)
    app.exec.assert_called_once_with()
    lock.unlock.assert_called_once_with()
    messages.critical.assert_not_called()


def test_successful_diagnostics_are_closed_after_normal_startup(monkeypatch, startup_flow):
    storage, app, lock, window, messages = startup_flow
    diagnostics = MagicMock()
    factory = MagicMock(return_value=diagnostics)
    monkeypatch.setattr(startup, "CrashDiagnostics", factory)

    assert startup.main() == 0

    factory.assert_called_once_with(storage.directory)
    startup.MainWindow.assert_called_once_with(storage)
    diagnostics.close.assert_called_once_with()
    diagnostics.python_exception.assert_not_called()
    messages.critical.assert_not_called()


def test_window_startup_failure_still_uses_successful_diagnostics(monkeypatch, startup_flow):
    storage, app, lock, window, messages = startup_flow
    diagnostics = MagicMock()
    monkeypatch.setattr(startup, "CrashDiagnostics", MagicMock(return_value=diagnostics))
    error = RuntimeError("Window could not start")
    startup.MainWindow.side_effect = error

    assert startup.main() == 1

    diagnostics.python_exception.assert_called_once()
    assert diagnostics.python_exception.call_args.args[:2] == (RuntimeError, error)
    diagnostics.close.assert_called_once_with()
    messages.critical.assert_called_once_with(None, "Multiplayer AI could not start", str(error))
    app.exec.assert_not_called()


# --- the launch screen --------------------------------------------------


@pytest.mark.parametrize("settings,expected", [
    ({}, True),
    ({"launch_screen": True}, True),
    ({"launch_screen": False}, False),
    # Reduce animations is an accessibility setting and outranks the
    # preference: a switch that let the animation play anyway would not be
    # doing its job.
    ({"launch_screen": True, "reduce_motion": True}, False),
    ({"reduce_motion": True}, False),
])
def test_whether_the_launch_screen_is_wanted(settings, expected):
    assert startup.launch_screen_wanted(settings) is expected


def test_the_launch_screen_is_shown_by_default(monkeypatch, startup_flow):
    storage, app, lock, window, messages = startup_flow
    assert startup.main() == 0

    startup.LaunchScreen.assert_called_once()
    splash = startup.LaunchScreen.return_value
    # Shown from the card's handover rather than straight away, so the
    # window is never on screen behind it. Maximised, not shown, because the
    # app always opens filling the screen.
    splash.begin.assert_called_once_with(on_done=window.showMaximized)
    assert not window.showMaximized.called, (
        "the window is shown before the card, so it peeks out from behind it")
    # And painted while hidden, so the frame it appears with is complete.
    window.grab.assert_called()


def test_the_launch_screen_can_be_turned_off(monkeypatch, startup_flow):
    """No card still means maximised, not an ordinary window."""
    storage, app, lock, window, messages = startup_flow
    storage.settings["launch_screen"] = False

    assert startup.main() == 0

    startup.LaunchScreen.assert_not_called()
    window.showMaximized.assert_called_once_with()
    window.show.assert_not_called()


def test_reduce_motion_suppresses_the_launch_screen(monkeypatch, startup_flow):
    storage, app, lock, window, messages = startup_flow
    storage.settings["reduce_motion"] = True

    assert startup.main() == 0

    startup.LaunchScreen.assert_not_called()


def test_the_startup_check_never_shows_the_launch_screen(monkeypatch, startup_flow, tmp_path):
    """--check-startup exists to report without showing anything."""
    storage, app, lock, window, messages = startup_flow
    monkeypatch.setattr(sys, "argv", ["MultiplayerAI", "--check-startup", str(tmp_path / "out.json")])
    window.network.event.connect.side_effect = lambda handler: handler("ready", {"port": 1})

    startup.main()

    startup.LaunchScreen.assert_not_called()
    window.showMaximized.assert_not_called()


def test_a_fatal_error_tears_the_launch_screen_down(monkeypatch, startup_flow):
    """A splash over a broken app is worse than no splash.

    The floor would otherwise hold the logo on screen for the rest of the
    session while the error went unseen behind it.
    """
    storage, app, lock, window, messages = startup_flow
    handlers = []

    class Signal:
        def connect(self, handler):
            handlers.append(handler)

    window.network.event = Signal()
    assert startup.main() == 0

    splash = startup.LaunchScreen.return_value
    splash.dismissed = False
    handlers[-1]("fatal", "the relay refused the connection")

    splash.dismiss.assert_called_once_with()
    splash.runtime_ready.assert_not_called()


def test_readiness_hands_the_launch_screen_over(monkeypatch, startup_flow):
    storage, app, lock, window, messages = startup_flow
    handlers = []

    class Signal:
        def connect(self, handler):
            handlers.append(handler)

    window.network.event = Signal()
    assert startup.main() == 0

    splash = startup.LaunchScreen.return_value
    splash.dismissed = False
    handlers[-1]("ready", {"port": 1234, "device_id": "device-1"})

    splash.runtime_ready.assert_called_once_with()
    splash.dismiss.assert_not_called()


def test_the_startup_only_takes_ready_as_readiness(monkeypatch, startup_flow):
    """Every other signal used to be treated as readiness.

    runtime_ready was in the else of the fatal branch, so workspace, agents,
    pending_cleanup and notice all counted as "the network is usable". Only
    the ready event says that, and it is the signal the card now needs before
    it will leave on the floor.
    """
    storage, app, lock, window, messages = startup_flow
    handlers = []

    class Signal:
        def connect(self, handler):
            handlers.append(handler)

    window.network.event = Signal()
    assert startup.main() == 0

    splash = startup.LaunchScreen.return_value
    splash.dismissed = False
    for event, data in (("workspace", {"name": "Mine", "id": "w"}),
                        ("agents", {"agents": [], "self": "d", "connected": True}),
                        ("pending_cleanup", {"credentials": 0, "files": 0}),
                        ("notice", "something")):
        handlers[-1](event, data)
        assert not splash.runtime_ready.called, (
            f"a {event!r} signal was taken as readiness")

    handlers[-1]("ready", {"port": 1234, "device_id": "device-1"})
    splash.runtime_ready.assert_called_once_with()


def test_the_launch_screen_leaves_even_if_the_runtime_was_already_up(monkeypatch, startup_flow):
    """A runtime that reported ready before the handler connected.

    Nothing will ever fire in that case, so the splash has to be told to
    go on the strength of having waited its floor. Otherwise it stays up
    over a perfectly working app until the window is closed.
    """
    storage, app, lock, window, messages = startup_flow

    class Signal:
        def connect(self, handler):
            pass

    window.network.event = Signal()
    assert startup.main() == 0

    splash = startup.LaunchScreen.return_value
    splash.dismiss_when_floored.assert_called()
