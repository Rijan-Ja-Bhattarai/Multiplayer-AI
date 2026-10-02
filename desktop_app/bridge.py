"""Qt signals bridge the UI and a dedicated asyncio networking thread."""
import asyncio
from uuid import uuid4

from PySide6.QtCore import QThread, Signal

from .runtime import DesktopRuntime


class NetworkThread(QThread):
    event = Signal(str, object)
    completed = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, storage, parent=None):
        super().__init__(parent)
        self.runtime = DesktopRuntime(storage, self.event.emit)
        self.loop = None
        self.stop_event = None
        self.stop_requested = False

    def run(self):
        asyncio.run(self._run())

    async def _run(self):
        self.loop = asyncio.get_running_loop()
        self.stop_event = asyncio.Event()
        try:
            if not self.stop_requested:
                await self.runtime.start()
        except Exception as exc:
            self.event.emit("fatal", str(exc))
        try:
            if self.stop_requested:
                self.stop_event.set()
            await self.stop_event.wait()
        finally:
            await self.runtime.close()

    def submit(self, method, *args, **kwargs):
        request_id = uuid4().hex
        async def execute():
            try:
                result = await getattr(self.runtime, method)(*args, **kwargs)
                self.completed.emit(request_id, result)
            except Exception as exc:
                self.failed.emit(request_id, str(exc))
        if not self.loop or not self.isRunning():
            raise RuntimeError("Networking is still starting")
        asyncio.run_coroutine_threadsafe(execute(), self.loop)
        return request_id

    def shutdown(self):
        self.stop_requested = True
        if self.loop and self.stop_event:
            self.loop.call_soon_threadsafe(self.stop_event.set)
