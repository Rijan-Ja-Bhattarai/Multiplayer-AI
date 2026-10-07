"""General chat over real desktop relays, WebSockets and mocked provider HTTP."""
import json
import tempfile
import unittest
from pathlib import Path

from starlette.responses import JSONResponse
from starlette.routing import Route

from desktop_app.runtime import DesktopRuntime
from desktop_app.storage import Storage
from network_a2a.orchestration import GENERAL_TARGET
from tests.test_desktop_runtime import MemoryVault


class OrchestrationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.calls = []
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", MemoryVault()))
        self.guest = DesktopRuntime(Storage(Path(self.directory.name) / "guest", MemoryVault()))
        await self.host.start()
        await self.guest.start()

        async def provider(request):
            body = await request.json()
            self.calls.append(body)
            content = body["messages"][-1]["content"]
            if content.startswith("You are Jev, the task coordinator"):
                text = json.dumps({"tasks": [{"agent_id": "specialist", "task_type": "debugging",
                                               "instruction": "Debug the original Python code"}],
                                   "reason": "User chose this model for debugging", "reply": ""})
            else:
                text = "The fixed code is ready."
            return JSONResponse({"choices": [{"message": {"content": text}, "finish_reason": "stop"}]})

        self.host.app.router.routes.append(Route("/mock/v1/chat/completions", provider, methods=["POST"]))
        self.profile = {"id": "specialist", "provider": "bionic", "model": "any-user-model-id",
                        "base_url": f"http://127.0.0.1:{self.host.port}/mock/v1", "autostart": True,
                        "purpose": "Python coding and debugging", "tasks": ["coding", "debugging"], "delegation_enabled": True}
        await self.host.save_agent(self.profile, "mock-key")
        self.assertEqual(self.host.app.state.relay.workspace["coordinator"], "specialist")

    async def asyncTearDown(self):
        await self.guest.close()
        await self.host.close()
        self.directory.cleanup()

    async def test_direct_general_chat_preserves_code_followups_and_provider_context(self):
        code = "Debug y\n```python\ndef f():\n    return 1 / 0\n```"
        result = await self.host.send(GENERAL_TARGET, {"text": code})
        self.assertEqual(result["text"], "The fixed code is ready.")
        self.assertEqual(result["routing"]["assignments"][0]["agent_id"], "specialist")
        self.assertEqual(self.calls[1]["messages"][0]["content"], code)
        followup = [{"role": "user", "content": code}, {"role": "assistant", "content": result["text"]},
                    {"role": "user", "content": "Now handle zero inputs"}]
        await self.host.send(GENERAL_TARGET, {"messages": followup})
        self.assertEqual(self.calls[-1]["messages"][:-1], followup)

    async def test_guest_uses_hosts_coordinator_and_sees_same_routing_and_history(self):
        invitation = await self.host.invite("guest", self.host.active_url, target=GENERAL_TARGET)
        room_id = invitation["conversation_id"]
        await self.guest.join(invitation["url"], invitation["token"], conversation_id=room_id)
        with self.assertRaisesRegex(ValueError, "owner"):
            await self.guest.set_coordinator("specialist")
        result = await self.guest.send_conversation(room_id, "Debug this Python function")
        self.assertEqual(result["title"], "General chat")
        self.assertEqual(result["messages"][-1]["content"], "The fixed code is ready.")
        self.assertEqual(result["routing"]["coordinator"], "specialist")
        self.assertEqual(result["messages"][0]["from"], "guest")
        stored = self.host.history_store.load("rooms")[room_id]
        self.assertEqual(stored, result)
        await self.host.send_conversation(room_id, "Debug the follow-up too")
        self.assertEqual(self.calls[-1]["messages"][0]["content"], "Debug this Python function")

    async def test_shared_general_chat_works_without_coordinator_or_delegation_setup(self):
        await self.host.save_agent({**self.profile, "purpose": "", "tasks": [], "delegation_enabled": False})
        self.host.app.state.relay.workspace["coordinator"] = None
        invitation = await self.host.invite("guest", self.host.active_url, target=GENERAL_TARGET)
        room_id = invitation["conversation_id"]
        await self.guest.join(invitation["url"], invitation["token"], conversation_id=room_id)
        result = await self.guest.send_conversation(room_id, "Make a hello world program")
        self.assertEqual(result["messages"][-1]["content"], "The fixed code is ready.")
        self.assertEqual(result["routing"]["mode"], "direct")
        self.assertEqual(result["routing"]["coordinator"], "specialist")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["messages"], [{"role": "user", "content": "Make a hello world program"}])

    async def test_coordinator_and_purpose_preferences_survive_restart(self):
        room = self.host.app.state.relay.conversations.create(self.host.active_id, GENERAL_TARGET)
        room["title"] = "General chat · Jev"
        self.host.history_store.save("rooms", room["id"], room)
        revision = room["revision"]
        storage = self.host.storage
        await self.host.close()
        self.host = DesktopRuntime(Storage(storage.directory, storage.vault))
        await self.host.start()
        self.assertEqual(self.host.app.state.relay.workspace["coordinator"], "specialist")
        self.assertEqual(self.host.storage.settings["agents"][0]["purpose"], self.profile["purpose"])
        self.assertEqual(self.host.app.state.relay.agent_description("specialist")["tasks"], ["coding", "debugging"])
        restored = self.host.app.state.relay.conversations.rooms[room["id"]]
        self.assertEqual(restored["title"], "General chat")
        self.assertEqual(restored["revision"], revision + 1)
        self.assertEqual(self.host.history_store.load("rooms")[room["id"]]["title"], "General chat")

    async def test_additional_models_preserve_default_and_explicit_coordinator_choices(self):
        await self.host.save_agent({**self.profile, "id": "second-model"}, "second-mock-key")
        self.assertEqual(self.host.app.state.relay.workspace["coordinator"], "specialist")
        await self.host.set_coordinator("second-model")
        await self.host.save_agent(self.profile)
        self.assertEqual(self.host.app.state.relay.workspace["coordinator"], "second-model")
        self.assertEqual(self.host.storage.settings["coordinator"], "second-model")

    async def test_failed_shared_planning_records_error_and_unblocks_chat_without_worker_call(self):
        invitation = await self.host.invite("guest", self.host.active_url, target=GENERAL_TARGET)
        room_id = invitation["conversation_id"]
        await self.guest.join(invitation["url"], invitation["token"], conversation_id=room_id)
        self.host.app.state.relay.set_agent_profile("specialist", {**self.host.public_profile(self.profile), "tasks": ["general"]})
        with self.assertRaisesRegex(RuntimeError, "invalid assignment"):
            await self.guest.send_conversation(room_id, "Debug this code")
        room = self.host.app.state.relay.conversations.rooms[room_id]
        self.assertFalse(room["pending"])
        self.assertEqual(room["messages"][-1]["role"], "error")
        self.assertEqual(len(self.calls), 1)

    async def test_invalid_preferences_do_not_mutate_profile_or_credentials(self):
        before = json.loads(self.host.storage.path.read_text())
        with self.assertRaisesRegex(ValueError, "permitted tasks"):
            await self.host.save_agent({**self.profile, "tasks": []}, "replacement-key")
        self.assertEqual(self.host.storage.vault.get("provider:specialist"), "mock-key")
        self.assertEqual(json.loads(self.host.storage.path.read_text()), before)
