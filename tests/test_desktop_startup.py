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
    storage = MagicMock(directory=tmp_path)
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
    return storage, app, lock, window, messages


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
    window.show.assert_called_once_with()
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
