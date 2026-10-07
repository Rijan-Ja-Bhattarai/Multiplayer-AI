"""Shared conversation state owned by the workspace relay."""
import json
from uuid import uuid4

from starlette.responses import JSONResponse

from .persistence import model_context
from .content import MAX_MESSAGE_BYTES, validate_content
from .orchestration import GENERAL_TARGET


class Conversations:
    def __init__(self, relay, store=None):
        self.relay = relay
        self.store = store
        self.rooms = store.load("rooms") if store else {}
        for room in self.rooms.values():
            if room["target"] == GENERAL_TARGET and room["title"] == "General chat · Jev":
                room["title"] = "General chat"
                room["revision"] += 1
                self.save(room)
            if room.get("pending"):
                room["pending"] = False
                self._append(room, "error", "The host stopped during this request. It was not replayed.", room["target"])

    def save(self, room):
        if self.store:
            self.store.save("rooms", room["id"], room)

    def create(self, owner, target, messages=()):
        if not self.relay.allowed(owner, owner if target == GENERAL_TARGET else target):
            raise ValueError("Choose an agent in this workspace")
        if len(self.rooms) >= 100:
            raise ValueError("This workspace has reached its conversation limit")
        room = {"id": "conversation-" + uuid4().hex, "title": "General chat" if target == GENERAL_TARGET else f"Chat with {target}",
                "target": target, "owner": owner, "members": [owner],
                "messages": [], "pending": False, "revision": 0}
        for message in messages:
            if (not isinstance(message, dict) or message.get("role") not in ("user", "assistant")
                    or "content" not in message):
                raise ValueError("Conversation history must contain user and assistant text")
            content = validate_content(message["content"], message["role"])
            self._append(room, message["role"], content,
                         owner if message["role"] == "user" else target)
        self.rooms[room["id"]] = room
        self.save(room)
        return room

    def invite(self, conversation_id, owner, member):
        room = self.rooms.get(conversation_id)
        if not room or room["owner"] != owner or not self.relay.allowed(owner, member):
            raise ValueError("Only the conversation's host can invite a device")
        if member not in room["members"]:
            room["members"].append(member)
            room["revision"] += 1
            self.save(room)

    def remove_member(self, member):
        for room in self.rooms.values():
            if member in room["members"]:
                room["members"].remove(member)
                room["revision"] += 1
                self.save(room)

    def get(self, conversation_id, source):
        room = self.rooms.get(conversation_id)
        if (not room or source not in room["members"]
                or not self.relay.allowed(source, room["owner"])):
            return None
        return room

    def _append(self, room, role, text, source):
        while isinstance(text, str) and len(json.dumps(text).encode()) > 180000:
            text = text[:len(text) // 2] + "\n[Response shortened]"
        room["messages"].append({"id": uuid4().hex, "role": role,
                                 "content": text, "from": source})
        room["revision"] += 1
        if room["id"] in self.rooms:
            self.save(room)

    def _source(self, request):
        return self.relay.authenticate(request.headers.get("authorization", ""))

    async def listing(self, request):
        source = self._source(request)
        if not source:
            return JSONResponse({"error": "Unauthorized"}, 401)
        return JSONResponse({"conversations": [room for room in self.rooms.values()
                             if self.get(room["id"], source)]}, headers={"Cache-Control": "no-store"})

    async def detail(self, request):
        source = self._source(request)
        if not source:
            return JSONResponse({"error": "Unauthorized"}, 401)
        room = self.get(request.path_params["conversation_id"], source)
        if not room:
            return JSONResponse({"error": "Conversation is unavailable for this device"}, 404)
        return JSONResponse(room, headers={"Cache-Control": "no-store"})

    async def send(self, request):
        source = self._source(request)
        if not source:
            return JSONResponse({"error": "Unauthorized"}, 401)
        room = self.get(request.path_params["conversation_id"], source)
        if not room:
            return JSONResponse({"error": "Conversation is unavailable for this device"}, 404)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_MESSAGE_BYTES:
                return JSONResponse({"error": "Message is too large"}, 413)
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict) or set(payload) not in ({"text"}, {"content"}):
                raise ValueError()
            text = payload.get("content", payload.get("text"))
            if isinstance(text, str) and len(json.dumps(text).encode()) > 180000:
                return JSONResponse({"error": "Message is too large"}, 413)
            text = validate_content(text)
        except (ValueError, TypeError):
            return JSONResponse({"error": "Enter a message to send"}, 400)
        if room["pending"]:
            return JSONResponse({"error": "Wait for the current AI reply before sending another message"}, 409)
        general = room["target"] == GENERAL_TARGET
        if general:
            try:
                self.relay.orchestrator.coordinator(source)
            except ConnectionError as exc:
                return JSONResponse({"error": str(exc)}, 503)
        elif room["target"] not in self.relay.peers:
            return JSONResponse({"error": "The conversation's agent is offline"}, 503)
        room["pending"] = True
        self._append(room, "user", text, source)
        history = model_context(room["messages"])
        try:
            if general:
                room.pop("routing", None)
                def on_plan(routing):
                    room["routing"] = routing
                    room["revision"] += 1
                    self.save(room)
                result = await self.relay.orchestrator.run(source, {"messages": history}, room["id"], on_plan)
            else:
                result = await self.relay.invoke(source, room["target"], {"messages": history}, conversation_id=room["id"])
            reply = result.get("text") if isinstance(result, dict) else result
            if not isinstance(reply, str):
                reply = json.dumps(result)
            self._append(room, "assistant", reply, room["target"])
        except (PermissionError, ConnectionError, OverflowError, TimeoutError, ValueError) as exc:
            error = str(exc) or "The request timed out; it was not replayed"
            self._append(room, "error", error, room["target"])
            return JSONResponse({"error": error}, 503)
        finally:
            room["pending"] = False
            room["revision"] += 1
            self.save(room)
        return JSONResponse(room)
