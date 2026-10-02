"""Single-process relay. All device connections are outbound WebSockets."""
import asyncio
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass, field
from uuid import uuid4

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocketDisconnect


MAX_BYTES = 262144


@dataclass
class Peer:
    socket: object
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, message):
        async with self.lock:
            await asyncio.wait_for(self.socket.send_json(message), 5)


class Relay:
    def __init__(self, credentials, timeout=60, max_pending=256):
        if not credentials or not 1 <= timeout <= 300 or max_pending < 1:
            raise ValueError("Credentials required; timeout must be 1..300 and max_pending positive")
        self.credentials = credentials
        tokens = set()
        for agent, config in credentials.items():
            token = config.get("token", "")
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", agent):
                raise ValueError("Agent IDs must contain 1..64 letters, digits, underscores or hyphens")
            if not isinstance(token, str) or len(token) < 32 or token in tokens:
                raise ValueError("Each agent needs a unique token of at least 32 characters")
            tokens.add(token)
            if not isinstance(config.get("group"), str) or not config["group"]:
                raise ValueError("Each agent requires a nonempty group")
        self.timeout = timeout
        self.max_pending = max_pending
        self.peers = {}
        self.pending = {}
        self.buckets = {}

    def authenticate(self, header):
        token = header.removeprefix("Bearer ") if header.startswith("Bearer ") else ""
        digest = hashlib.sha256(token.encode()).digest()
        for agent, config in self.credentials.items():
            if hmac.compare_digest(digest, hashlib.sha256(config["token"].encode()).digest()):
                return agent
        return None

    def allowed(self, source, target):
        return target in self.credentials and self.credentials[source]["group"] == self.credentials[target]["group"]

    def admit(self, source):
        now = time.monotonic()
        tokens, previous = self.buckets.get(source, (30, now))
        tokens = min(30, tokens + (now - previous) * 5)
        self.buckets[source] = (tokens - 1 if tokens >= 1 else tokens, now)
        return tokens >= 1

    async def invoke(self, source, target, payload):
        if not self.allowed(source, target):
            raise PermissionError("Target is unavailable or outside your group")
        peer = self.peers.get(target)
        if not peer:
            raise ConnectionError("Target agent is offline")
        if len(self.pending) >= self.max_pending or not self.admit(source):
            raise OverflowError("Relay capacity exceeded; retry later")
        request_id = str(uuid4())
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = (target, peer, future)
        try:
            await peer.send({"type": "request", "id": request_id, "from": source, "payload": payload})
            return await asyncio.wait_for(future, self.timeout)
        except (TimeoutError, ConnectionError):
            raise
        except (OSError, RuntimeError) as exc:
            raise ConnectionError("Target connection failed") from exc
        finally:
            self.pending.pop(request_id, None)

    async def websocket(self, socket):
        agent = self.authenticate(socket.headers.get("authorization", ""))
        if not agent:
            await socket.close(code=1008)
            return
        await socket.accept()
        if agent in self.peers:
            await socket.close(code=1008, reason="Agent ID already connected")
            return
        peer = Peer(socket)
        self.peers[agent] = peer
        tasks = set()

        async def route(frame):
            try:
                result = await self.invoke(agent, frame["to"], frame.get("payload"))
                await peer.send({"type": "response", "id": frame["id"], "payload": result})
            except (PermissionError, ConnectionError, OverflowError, TimeoutError) as exc:
                await peer.send({"type": "response", "id": frame["id"], "error": str(exc) or "Request timed out"})

        async def guarded_route(frame):
            try:
                await route(frame)
            except (OSError, RuntimeError, TimeoutError):
                pass

        try:
            await peer.send({"type": "connected", "agent_id": agent})
            while True:
                raw = await socket.receive_text()
                if len(raw.encode()) > MAX_BYTES:
                    await socket.close(code=1009)
                    break
                try:
                    frame = json.loads(raw)
                    if not isinstance(frame, dict):
                        raise ValueError()
                    kind = frame.get("type")
                    request_id = frame.get("id")
                    if kind in ("request", "response") and (not isinstance(request_id, str) or not 1 <= len(request_id) <= 128):
                        raise ValueError()
                    if kind == "request":
                        if not isinstance(frame.get("to"), str):
                            raise ValueError()
                        if len(tasks) >= 32:
                            await peer.send({"type": "response", "id": request_id, "error": "Too many requests"})
                            continue
                        task = asyncio.create_task(guarded_route(frame))
                        tasks.add(task)
                        task.add_done_callback(tasks.discard)
                    elif kind == "response":
                        entry = self.pending.get(request_id)
                        if entry and entry[0] == agent and entry[1] is peer and not entry[2].done():
                            if "error" in frame:
                                code = frame.get("code")
                                safe_codes = ("authentication", "rate_limit", "http_error", "timeout", "connection",
                                              "invalid_input", "invalid_response", "no_text")
                                detail = f"Remote provider error: {code}" if isinstance(code, str) and code in safe_codes else "Remote agent failed"
                                entry[2].set_exception(ConnectionError(detail))
                            else:
                                entry[2].set_result(frame.get("payload"))
                    else:
                        raise ValueError()
                except (ValueError, TypeError):
                    await peer.send({"type": "error", "error": "Invalid frame"})
        except (WebSocketDisconnect, OSError, RuntimeError, TimeoutError):
            pass
        finally:
            self.peers.pop(agent, None)
            for _, target_peer, future in list(self.pending.values()):
                if target_peer is peer and not future.done():
                    future.set_exception(ConnectionError("Target disconnected"))
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def health(self, request):
        return JSONResponse({"status": "ok"})

    async def agents(self, request):
        source = self.authenticate(request.headers.get("authorization", ""))
        if not source:
            return JSONResponse({"error": "Unauthorized"}, 401, headers={"WWW-Authenticate": "Bearer"})
        return JSONResponse({"self": source, "agents": [{"id": agent, "online": agent in self.peers}
            for agent in self.credentials if self.allowed(source, agent)]}, headers={"Cache-Control": "no-store"})

    async def http_invoke(self, request):
        source = self.authenticate(request.headers.get("authorization", ""))
        if not source:
            return JSONResponse({"error": "Unauthorized"}, 401)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_BYTES:
                return JSONResponse({"error": "Payload too large"}, 413)
        try:
            payload = json.loads(raw)
        except ValueError:
            return JSONResponse({"error": "Invalid JSON"}, 400)
        try:
            result = await self.invoke(source, request.path_params["agent"], payload)
            return JSONResponse(result)
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, 403)
        except ConnectionError as exc:
            return JSONResponse({"error": str(exc)}, 503)
        except OverflowError as exc:
            return JSONResponse({"error": str(exc)}, 429)
        except TimeoutError:
            return JSONResponse({"error": "Request timed out"}, 504)


def create_app(credentials, timeout=60, max_pending=256):
    relay = Relay(credentials, timeout, max_pending)
    app = Starlette(routes=[Route("/health", relay.health), Route("/agents", relay.agents),
        Route("/agents/{agent}/invoke", relay.http_invoke, methods=["POST"]),
        WebSocketRoute("/connect", relay.websocket)])
    app.state.relay = relay
    return app


def app_from_env():
    path = os.environ.get("A2A_CREDENTIALS_FILE")
    if not path:
        raise ValueError("Set A2A_CREDENTIALS_FILE to your credentials JSON file")
    with open(path, encoding="utf-8") as file:
        credentials = json.load(file)
    return create_app(credentials, float(os.getenv("A2A_REQUEST_TIMEOUT", "60")),
                      int(os.getenv("A2A_MAX_PENDING", "256")))
