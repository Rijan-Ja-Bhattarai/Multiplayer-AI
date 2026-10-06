import argparse
import sys
import json
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from .storage import Storage
from .window import MainWindow, app_icon
from .diagnostics import CrashDiagnostics
from .splash import LaunchScreen, StartupStatus
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
            # MainWindow connects its own handler to this signal in its
            # constructor, so only the splash's needs adding here.
            splash = None
            status = None
            if launch_screen_wanted(storage.settings):
                status = StartupStatus()
                splash = LaunchScreen(resolve_theme(storage.settings))
                splash.set_status(status)
                # The window is never shown while the card is up. It is
                # built, populated by the runtime and painted off screen
                # underneath, so the reveal is a fully formed window rather
                # than one that fills in while you look at it. Showing it
                # first and covering it is what made it peek out from
                # behind the card.
                splash.begin(on_done=window.showMaximized)
                window.grab()
            else:
                window.showMaximized()

            def hand_over(event=None, data=None):
                # Nothing should be able to leave a card on screen over a
                # broken app, so a fatal error dismisses it at once rather
                # than waiting out the hold.
                if splash is None or splash.dismissed:
                    return
                # Pushed on every change, not only once the startup has
                # settled: the lines before that are the ones that show
                # the app is doing something.
                if status.observe(event, data):
                    splash.set_status(status)
                if event == "fatal":
                    splash.dismiss()
                elif event == "ready":
                    # Only this event says the network is usable. Calling it
                    # for every other signal made a settled status look like
                    # readiness, and a card could hand over on a report that
                    # never mentioned the network at all.
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
