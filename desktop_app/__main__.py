import argparse
import sys
import json
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from .storage import Storage
from .window import MainWindow, app_icon
from .diagnostics import CrashDiagnostics
from .splash import LaunchScreen
from .theme import resolve_theme


def launch_screen_wanted(settings):
    """Whether to put a launch screen up at all.

    Two switches, and they are not the same question. The stored preference
    is a preference: somebody who does not want a splash should not have
    one. Reduce animations is an accessibility setting, and it wins,
    because a switch that lets an animation play anyway is not doing its
    job.
    """
    if settings.get("reduce_motion"):
        return False
    return settings.get("launch_screen", True) is not False


def main():
    parser = argparse.ArgumentParser(description="Multiplayer AI native desktop workspace")
    parser.add_argument("--data-dir", help="Override the app preferences directory")
    parser.add_argument("--check-startup", help="Write a startup diagnostic JSON file and exit without showing a window")
    args = parser.parse_args()
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Multiplayer AI")
    app.setOrganizationName("MultiplayerAI")
    app.setWindowIcon(app_icon())
    diagnostics = None
    try:
        storage = Storage(args.data_dir)
        lock = QLockFile(str(storage.directory / "desktop.lock"))
        if not lock.tryLock(0):
            QMessageBox.information(None, "Multiplayer AI", "This workspace is already open on this device.")
            return 0
        try:
            diagnostics = CrashDiagnostics(storage.directory)
        except OSError:
            pass
        window = MainWindow(storage)
        if args.check_startup:
            app.setQuitOnLastWindowClosed(False)
            def checked(event, data):
                if event in ("ready", "fatal"):
                    Path(args.check_startup).write_text(json.dumps({"status": "ok" if event == "ready" else "error", "detail": data}), encoding="utf-8")
                    window.close()
            window.network.event.connect(checked)
            window.network.finished.connect(app.quit)
        else:
            splash = None
            if launch_screen_wanted(storage.settings):
                splash = LaunchScreen(resolve_theme(storage.settings))
                splash.begin()
            # The window is shown behind the splash rather than after it,
            # so the handover is a cross-fade between two painted things
            # instead of a gap where neither exists. The splash has already
            # been given its floor, so it stays up long enough to be worth
            # having shown.
            window.show()

            def hand_over(event=None, data=None):
                # Nothing should be able to leave a logo on screen over a
                # broken app, so a fatal error tears it down at once rather
                # than waiting its turn.
                if splash is None or splash.dismissed:
                    return
                if event == "fatal":
                    splash.dismiss()
                else:
                    splash.runtime_ready()

            window.network.event.connect(hand_over)
            if splash is not None:
                # In case the runtime was already up before this connected.
                splash.dismiss_when_floored()
        result = app.exec()
        lock.unlock()
        return result
    except Exception as exc:
        if diagnostics:
            diagnostics.python_exception(type(exc), exc, exc.__traceback__)
        QMessageBox.critical(None, "Multiplayer AI could not start", str(exc))
        return 1
    finally:
        if diagnostics:
            diagnostics.close()


if __name__ == "__main__":
    raise SystemExit(main())
