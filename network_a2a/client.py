"""Reusable client; handlers run on the device that owns the agent."""
import asyncio
import json
from uuid import uuid4
from urllib.parse import urlsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from .adapters import ProviderError
from .content import MAX_FRAME_BYTES


class AgentClient:
    def __init__(self, url, token, handler=None, allow_insecure=False, timeout=75, frame_handler=None):
        parsed = urlsplit(url)
        if parsed.scheme not in ("ws", "wss") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Use a ws:// or wss:// relay URL without embedded credentials")
        if parsed.scheme == "ws" and parsed.hostname not in ("localhost", "127.0.0.1", "::1") and not allow_insecure:
            raise ValueError("Remote connections require wss://; use allow_insecure only on a trusted LAN")
        self.url, self.token, self.handler, self.timeout = url, token, handler, timeout
        self.frame_handler = frame_handler
        self.socket = None
        self.ready = asyncio.Event()
        self.pending = {}
        self.agent_id = None
        self.lock = asyncio.Lock()

    async def _send(self, frame):
        async with self.lock:
            if self.socket is None:
                raise ConnectionError("Relay disconnected")
            await self.socket.send(json.dumps(frame))

    async def request(self, target, payload):
        await asyncio.wait_for(self.ready.wait(), self.timeout)
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self._send({"type": "request", "id": request_id, "to": target, "payload": payload})
            return await asyncio.wait_for(future, self.timeout)
        finally:
            self.pending.pop(request_id, None)

    async def _handle(self, frame):
        try:
            if self.handler is None and self.frame_handler is None:
                raise ValueError("Agent has no request handler")
            request = self.frame_handler(frame) if self.frame_handler else self.handler(frame["payload"], frame["from"])
            result = await asyncio.wait_for(request, self.timeout)
            response = {"type": "response", "id": frame["id"], "payload": result}
        except ProviderError as exc:
            response = {"type": "response", "id": frame["id"], "error": str(exc), "code": exc.code}
        except Exception:
            response = {"type": "response", "id": frame["id"], "error": "Handler failed"}
        try:
            await self._send(response)
        except Exception:
            pass

    async def run(self):
        """Reconnect with library backoff; never replay potentially executed requests."""
        async for socket in connect(self.url, additional_headers={"Authorization": f"Bearer {self.token}"},
                                    max_size=MAX_FRAME_BYTES, ping_interval=20, ping_timeout=20):
            jobs = set()
            try:
                self.socket = socket
                hello = json.loads(await asyncio.wait_for(socket.recv(), 10))
                self.agent_id = hello["agent_id"]
                self.ready.set()
                async for raw in socket:
                    frame = json.loads(raw)
                    if frame["type"] == "request":
                        if len(jobs) >= 32:
                            await self._send({"type": "response", "id": frame["id"], "error": "Agent is busy"})
                            continue
                        job = asyncio.create_task(self._handle(frame))
                        jobs.add(job)
                        job.add_done_callback(jobs.discard)
                    elif frame["type"] == "response":
                        future = self.pending.get(frame["id"])
                        if future and not future.done():
                            if "error" in frame:
                                future.set_exception(RuntimeError(frame["error"]))
                            else:
                                future.set_result(frame.get("payload"))
            except Exception as exc:
                if not isinstance(exc, ConnectionClosed) or exc.rcvd is not None and exc.rcvd.code == 1008:
                    raise
            finally:
                self.ready.clear()
                self.socket = None
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(ConnectionError("Relay connection lost; request was not replayed"))
                for job in jobs:
                    job.cancel()
                await asyncio.gather(*jobs, return_exceptions=True)
            await asyncio.sleep(1)
