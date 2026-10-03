import argparse
import sys
import json
from pathlib import Path

from PySide6.QtCore import QLockFile
from PySide6.QtWidgets import QApplication, QMessageBox

from .storage import Storage
from .window import MainWindow, app_icon
from .diagnostics import CrashDiagnostics


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
        diagnostics = CrashDiagnostics(storage.directory)
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
            window.show()
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
