import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from desktop_app.runtime import DesktopRuntime
from desktop_app.storage import Storage
from tests.test_desktop_runtime import MemoryVault


class SharedConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.events = []
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", MemoryVault()))
        self.guest = DesktopRuntime(Storage(Path(self.directory.name) / "guest", MemoryVault()),
                                    lambda kind, data: self.events.append((kind, data)))
        await self.host.start()
        await self.guest.start()

    async def asyncTearDown(self):
        await self.guest.close()
        await self.host.close()
        self.directory.cleanup()

    async def model(self, handler):
        invitation = await self.host.invite("model", self.host.active_url)
        await self.host._attach("model", invitation["token"], handler)

    async def invitation(self, history=()):
        return await self.host.invite("guest", self.host.active_url, target="model", messages=history)

    async def join(self, invitation):
        await self.guest.join(invitation["url"], invitation["token"],
                              conversation_id=invitation["conversation_id"])

    async def test_join_adds_same_history_and_both_devices_extend_one_model_context(self):
        calls = []
        async def model(payload, source):
            calls.append(payload["messages"])
            return {"text": "AI: " + payload["messages"][-1]["content"], "provider": "test"}
        await self.model(model)
        history = [{"role": "user", "content": "Before inviting"},
                   {"role": "assistant", "content": "Existing AI reply"}]
        invitation = await self.invitation(history)
        await self.join(invitation)
        room_id = invitation["conversation_id"]
        rooms = [data for kind, data in self.events if kind == "conversations"][-1]
        self.assertEqual(rooms[0]["id"], room_id)
        self.assertEqual([message["content"] for message in rooms[0]["messages"]],
                         [message["content"] for message in history])
        guest_reply = await self.guest.send_conversation(room_id, "From the guest")
        host_reply = await self.host.send_conversation(room_id, "From the host")
        self.assertFalse(guest_reply["pending"])
        self.assertFalse(host_reply["pending"])
        self.assertEqual(calls[-1], history + [
            {"role": "user", "content": "From the guest"},
            {"role": "assistant", "content": "AI: From the guest"},
            {"role": "user", "content": "From the host"}])
        await self.guest.refresh()
        rooms = [data for kind, data in self.events if kind == "conversations"][-1]
        self.assertEqual(rooms[0], host_reply)
        self.assertEqual(rooms[0]["messages"][-2]["from"], self.host.active_id)
        self.assertNotIn(invitation["token"], json.dumps(rooms))

    async def test_uninvited_device_cannot_read_or_send_to_conversation(self):
        async def model(payload, source):
            return {"text": "reply"}
        await self.model(model)
        invitation = await self.invitation()
        outsider = await self.host.invite("outsider", self.host.active_url)
        base = f"http://127.0.0.1:{self.host.port}"
        headers = {"Authorization": "Bearer " + outsider["token"]}
        room_url = base + "/conversations/" + invitation["conversation_id"]
        self.assertEqual((await self.host.http.get(base + "/conversations", headers=headers)).json(),
                         {"conversations": []})
        self.assertEqual((await self.host.http.get(room_url, headers=headers)).status_code, 404)
        self.assertEqual((await self.host.http.post(room_url + "/messages", headers=headers,
                                                   json={"text": "uninvited"})).status_code, 404)
        self.assertEqual((await self.host.http.get(room_url)).status_code, 401)
        identity = self.guest.active_id
        with self.assertRaisesRegex(ValueError, "unavailable"):
            await self.guest.join(outsider["url"], outsider["token"],
                                  conversation_id=invitation["conversation_id"])
        self.assertEqual(self.guest.active_id, identity)
        self.assertFalse(self.guest.remote)

    async def test_simultaneous_message_is_rejected_without_duplicate_model_call(self):
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def model(payload, source):
            calls.append(payload)
            entered.set()
            await release.wait()
            return {"text": "one reply"}
        await self.model(model)
        invitation = await self.invitation()
        await self.join(invitation)
        room_id = invitation["conversation_id"]
        request = asyncio.create_task(self.guest.send_conversation(room_id, "First"))
        try:
            await asyncio.wait_for(entered.wait(), 3)
            await self.guest.refresh()
            self.assertTrue([data for kind, data in self.events if kind == "conversations"][-1][0]["pending"])
            with self.assertRaisesRegex(RuntimeError, "Wait for"):
                await self.host.send_conversation(room_id, "Concurrent")
        finally:
            release.set()
            await request
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.host.app.state.relay.conversations.rooms[room_id]["messages"]), 2)

    async def test_saved_guest_recovers_conversation_after_restarting(self):
        async def model(payload, source):
            return {"text": "saved reply"}
        await self.model(model)
        invitation = await self.invitation()
        await self.join(invitation)
        await self.guest.send_conversation(invitation["conversation_id"], "Before reconnect")
        storage = self.guest.storage
        await self.guest.close()
        self.events.clear()
        self.guest = DesktopRuntime(storage, lambda kind, data: self.events.append((kind, data)))
        await self.guest.start()
        rooms = [data for kind, data in self.events if kind == "conversations"][-1]
        self.assertTrue(self.guest.remote)
        self.assertEqual(rooms[0]["messages"][-1]["content"], "saved reply")

    async def test_failed_ai_reply_is_visible_to_every_member_and_unblocks_chat(self):
        async def model(payload, source):
            raise RuntimeError("private failure")
        await self.model(model)
        invitation = await self.invitation()
        await self.join(invitation)
        with self.assertRaisesRegex(RuntimeError, "failed"):
            await self.guest.send_conversation(invitation["conversation_id"], "A request")
        rooms = [data for kind, data in self.events if kind == "conversations"][-1]
        self.assertEqual(rooms[0]["messages"][-1]["role"], "error")
        self.assertFalse(rooms[0]["pending"])
        self.assertNotIn("private failure", json.dumps(rooms))
