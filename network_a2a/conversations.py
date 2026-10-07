"""Shared conversation state owned by the workspace relay."""
import json
from uuid import uuid4

from starlette.responses import JSONResponse

from .persistence import model_context
from .content import MAX_MESSAGE_BYTES, validate_content


class Conversations:
    def __init__(self, relay, store=None):
        self.relay = relay
        self.store = store
        self.rooms = store.load("rooms") if store else {}
        for room in self.rooms.values():
            if room.get("pending"):
                room["pending"] = False
                self._append(room, "error", "The host stopped during this request. It was not replayed.", room["target"])

    def save(self, room):
        if self.store:
            self.store.save("rooms", room["id"], room)

    def create(self, owner, target, messages=()):
        if not self.relay.allowed(owner, target):
            raise ValueError("Choose an agent in this workspace")
        if len(self.rooms) >= 100:
            raise ValueError("This workspace has reached its conversation limit")
        room = {"id": "conversation-" + uuid4().hex, "title": f"Chat with {target}",
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

    def delete(self, conversation_id, source):
        """Remove a conversation for everyone, owner only.

        The rule mirrors the workspace's: whoever started it may end it, and a
        member leaves instead. Returns the reason it could not be done, or
        None on success, so the caller can tell a refusal from a room that is
        simply not there.
        """
        room = self.get(conversation_id, source)
        if not room:
            return "Conversation is unavailable for this device"
        if room["owner"] != source:
            return "Only the conversation's host can delete it. Leave it instead."
        self.rooms.pop(conversation_id, None)
        # The record goes with it. Left behind, the room would come back the
        # next time the relay started.
        if self.store:
            self.store.delete("rooms", conversation_id)
        return None

    def leave(self, conversation_id, source):
        """Take one member out of one conversation, leaving the rest of it alone.

        Not ``remove_member``, which strips a member from every conversation at
        once because that is what being removed from the workspace means.
        Leaving a conversation is not that: the room and its history stay for
        everyone else, and the owner has to be told to delete it.
        """
        room = self.get(conversation_id, source)
        if not room:
            return "Conversation is unavailable for this device"
        if room["owner"] == source:
            return "The host must delete this conversation rather than leave it"
        if source in room["members"]:
            room["members"].remove(source)
            room["revision"] += 1
            self.save(room)
        return None

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

    async def http_delete(self, request):
        """DELETE: end a conversation for everyone. Host only."""
        source = self._source(request)
        if not source:
            return JSONResponse({"error": "Unauthorized"}, 401)
        error = self.delete(request.path_params["conversation_id"], source)
        if error:
            # 403 when it exists and is not the caller's to delete, which is a
            # different thing from it not being there at all.
            return JSONResponse({"error": error},
                                403 if "host" in error else 404)
        return JSONResponse({"status": "deleted"})

    async def http_leave(self, request):
        """POST: step out of a conversation, leaving it for everyone else."""
        source = self._source(request)
        if not source:
            return JSONResponse({"error": "Unauthorized"}, 401)
        error = self.leave(request.path_params["conversation_id"], source)
        if error:
            return JSONResponse({"error": error},
                                403 if "host" in error else 404)
        return JSONResponse({"status": "left"})

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
        if room["target"] not in self.relay.peers:
            return JSONResponse({"error": "The conversation's agent is offline"}, 503)
        room["pending"] = True
        self._append(room, "user", text, source)
        history = model_context(room["messages"])
        try:
            result = await self.relay.invoke(source, room["target"], {"messages": history}, conversation_id=room["id"])
            reply = result.get("text") if isinstance(result, dict) else result
            if not isinstance(reply, str):
                reply = json.dumps(result)
            self._append(room, "assistant", reply, room["target"])
        except (PermissionError, ConnectionError, OverflowError, TimeoutError) as exc:
            error = str(exc) or "The request timed out; it was not replayed"
            self._append(room, "error", error, room["target"])
            return JSONResponse({"error": error}, 503)
        finally:
            room["pending"] = False
            room["revision"] += 1
            self.save(room)
        return JSONResponse(room)
