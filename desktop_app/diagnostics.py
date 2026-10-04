"""Keep Python tracebacks and native crash details in windowed desktop builds."""
import faulthandler
import sys
import traceback
from datetime import datetime

from PySide6.QtCore import qInstallMessageHandler


class CrashDiagnostics:
    def __init__(self, directory):
        self.log = (directory / "desktop-crash.log").open("a", encoding="utf-8", buffering=1)
        self.previous_stderr = sys.stderr
        self.previous_hook = sys.excepthook
        self.previous_qt_handler = qInstallMessageHandler(self.qt_message)
        if sys.stderr is None:
            sys.stderr = self.log
        sys.excepthook = self.python_exception
        faulthandler.enable(file=self.log)

    def qt_message(self, kind, context, message):
        self.log.write(f"{datetime.now().isoformat()} Qt {kind.name}: {message}\n")
        if self.previous_qt_handler:
            self.previous_qt_handler(kind, context, message)

    def python_exception(self, kind, value, trace):
        self.log.write(f"{datetime.now().isoformat()} Unhandled Python exception\n")
        traceback.print_exception(kind, value, trace, file=self.log)
        self.log.flush()
        if self.previous_stderr is not None:
            self.previous_hook(kind, value, trace)

    def close(self):
        qInstallMessageHandler(self.previous_qt_handler)
        sys.excepthook = self.previous_hook
        faulthandler.disable()
        sys.stderr = self.previous_stderr
        self.log.close()
