import json
import tempfile
import unittest
from pathlib import Path

from desktop_app.runtime import DesktopRuntime
from desktop_app.storage import Storage
from tests.test_desktop_runtime import MemoryVault


class WorkspacePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.events = []
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", self.vault),
                                   lambda event, data: self.events.append((event, data)))
        await self.host.start()
        self.guest = None

    async def asyncTearDown(self):
        if self.guest:
            await self.guest.close()
        await self.host.close()
        self.directory.cleanup()

    async def attach_model(self, runtime, handler):
        if "model" not in runtime.credentials:
            await runtime.invite("model", runtime.active_url)
        await runtime._attach("model", runtime.credentials["model"]["token"], handler)

    async def guest_join(self, invitation):
        self.guest = DesktopRuntime(Storage(Path(self.directory.name) / "guest", MemoryVault()))
        await self.guest.start()
        await self.guest.join(invitation["url"], invitation["token"], conversation_id=invitation.get("conversation_id"))

    async def test_full_history_survives_host_restart_and_model_context_is_restored(self):
        calls = []
        async def model(payload, source):
            calls.append(payload["messages"])
            return {"text": "Saved AI reply", "provider": "test"}
        await self.attach_model(self.host, model)
        history = [message for index in range(60) for message in (
            {"role": "user", "content": f"Remember fact {index}"},
            {"role": "assistant", "content": f"Acknowledged {index}"})]
        invitation = await self.host.invite("guest", self.host.active_url, target="model", messages=history)
        room_id = invitation["conversation_id"]
        await self.host.send_conversation(room_id, "Before closing")
        await self.host.close()
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", self.vault))
        await self.host.start()
        await self.attach_model(self.host, model)
        await self.guest_join(invitation)
        result = await self.guest.send_conversation(room_id, "After reopening")
        self.assertEqual(len(result["messages"]), 124)
        self.assertEqual(result["messages"][0]["content"], "Remember fact 0")
        self.assertEqual(calls[-1][-3:], [{"role": "user", "content": "Before closing"},
            {"role": "assistant", "content": "Saved AI reply"}, {"role": "user", "content": "After reopening"}])
        self.assertLessEqual(len(calls[-1]), 100)
        self.assertLess(len(json.dumps(calls[-1]).encode()), 190000)

    async def test_workspaces_are_named_isolated_and_remain_hosted_when_switched(self):
        first_id = self.host.active_workspace_id
        first_url, first_device = self.host.active_url, self.host.active_id
        await self.host.rename_workspace("Research")
        invitation = await self.host.invite("guest", first_url)
        await self.guest_join(invitation)
        second = await self.host.create_workspace("Writing")
        self.assertNotEqual(first_url, self.host.active_url)
        self.assertNotEqual(first_device, self.host.active_id)
        self.assertNotIn("guest", self.host.credentials)
        result = await self.guest.send(first_device, {"text": "Works while owner views another workspace"})
        self.assertIn("Works while", result["text"])
        await self.host.close()
        await self.guest.close()
        self.guest = None
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", self.vault))
        await self.host.start()
        self.assertEqual(self.host.active_workspace_id, second["id"])
        self.assertEqual([entry["name"] for entry in self.host.catalog["workspaces"]], ["Research", "Writing"])
        await self.host.switch_workspace(first_id)
        self.assertEqual(self.host.active_id, first_device)
        self.assertIn("guest", self.host.credentials)
        saved = self.host.engine.history_store.load("ui")["state"]["chats"]["guest"]
        self.assertIn("Works while owner views another workspace", saved["messages"][0][1])

    async def test_member_removal_revokes_tokens_and_shared_history_access_permanently(self):
        invitation = await self.host.invite("guest", self.host.active_url, target=self.host.active_id)
        await self.guest_join(invitation)
        await self.host.remove_member("guest")
        self.assertNotIn("guest", self.host.credentials)
        self.assertNotIn("relay:guest", self.vault.values)
        base = self.host.active_url.replace("ws://", "http://").removesuffix("/connect")
        headers = {"Authorization": "Bearer " + invitation["token"]}
        self.assertEqual((await self.host.http.get(base + "/agents", headers=headers)).status_code, 401)
        self.assertEqual((await self.host.http.get(base + "/conversations", headers=headers)).status_code, 401)
        await self.host.close()
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", self.vault))
        await self.host.start()
        self.assertNotIn("guest", self.host.credentials)
        self.assertNotIn("guest", self.host.app.state.relay.conversations.rooms[invitation["conversation_id"]]["members"])

    async def test_guests_cannot_rename_or_remove_members_and_can_leave(self):
        invitation = await self.host.invite("guest", self.host.active_url)
        await self.guest_join(invitation)
        with self.assertRaisesRegex(ValueError, "owner"):
            await self.guest.rename_workspace("Unauthorised")
        with self.assertRaisesRegex(ValueError, "owner"):
            await self.guest.remove_member(self.host.active_id)
        remote_id = self.guest.active_workspace_id
        await self.guest.delete_workspace()
        self.assertNotIn(remote_id, {entry["id"] for entry in self.guest.catalog["workspaces"]})
        self.assertNotIn("guest", self.host.credentials)
        self.assertFalse(self.guest.remote)

    async def test_deletion_clears_only_selected_workspace_and_last_workspace_has_replacement(self):
        root_id = self.host.active_workspace_id
        self.host.engine.history_store.save("ui", "state", {"chats": {"saved": {"messages": [["user", "Keep this"]]}}})
        second = await self.host.create_workspace("Temporary")
        store = self.host.engine.history_store
        store.save("ui", "state", {"chats": {"deleted": {"messages": [["user", "Delete this"]]}}})
        await self.host.delete_workspace()
        self.assertEqual(self.host.active_workspace_id, root_id)
        self.assertEqual(store.load("ui"), {})
        self.assertIn("Keep this", json.dumps(self.host.engine.history_store.load("ui")))
        self.assertNotIn(second["id"], {entry["id"] for entry in self.host.catalog["workspaces"]})
        await self.host.delete_workspace()
        self.assertEqual(len(self.host.catalog["workspaces"]), 1)
        self.assertNotEqual(self.host.active_workspace_id, root_id)
        self.assertFalse(self.host.engine.history_store.load("ui").get("state", {}).get("chats"))
        self.assertEqual(self.host.app.state.relay.conversations.rooms, {})

    async def test_guest_can_forget_an_offline_workspace_without_losing_owned_workspaces(self):
        invitation = await self.host.invite("guest", self.host.active_url)
        await self.guest_join(invitation)
        owned_id = self.guest.catalog["workspaces"][0]["id"]
        remote_id, store = self.guest.active_workspace_id, self.guest.engine.history_store
        await self.host.close()
        await self.guest.delete_workspace()
        self.assertEqual(self.guest.active_workspace_id, owned_id)
        self.assertNotIn(remote_id, {entry["id"] for entry in self.guest.catalog["workspaces"]})
        self.assertEqual(store.load("ui"), {})
        self.assertIsNone(self.guest.storage.vault.get(remote_id + ":remote-token"))
        self.assertIn("guest", self.host.credentials)

    async def test_operator_managed_relays_allow_leaving_locally(self):
        invitation = await self.host.invite("guest", self.host.active_url)
        await self.guest_join(invitation)
        self.host.app.state.relay.member_remover = None
        remote_id = self.guest.active_workspace_id
        await self.guest.delete_workspace()
        self.assertNotIn(remote_id, {entry["id"] for entry in self.guest.catalog["workspaces"]})
        self.assertIn("guest", self.host.credentials)

    async def test_legacy_remote_connection_and_provider_migrate_into_the_workspace_catalog(self):
        invitation = await self.host.invite("legacy-guest", self.host.active_url)
        vault = MemoryVault()
        storage = Storage(Path(self.directory.name) / "guest", vault)
        storage.settings.update(remote={"url": invitation["url"], "allow_insecure": False}, remote_agent={
            "id": "legacy-guest", "provider": "bionic", "model": "test-model", "base_url": "https://model.example/v1",
            "autostart": True, "relay": invitation["url"]})
        vault.set("remote-token", invitation["token"])
        vault.set("provider:legacy-guest", "legacy-api-key")
        storage.save()
        self.guest = DesktopRuntime(storage)
        await self.guest.start()
        remote_id = self.guest.active_workspace_id
        self.assertTrue(self.guest.remote)
        self.assertEqual(len(self.guest.catalog["workspaces"]), 2)
        self.assertEqual(self.guest.engine.storage.vault.get("provider:legacy-guest"), "legacy-api-key")
        self.assertNotIn(invitation["token"], self.guest.catalog_path.read_text())
        await self.guest.delete_workspace()
        self.assertIsNone(vault.get(remote_id + ":provider:legacy-guest"))
        self.assertIsNone(vault.get("provider:legacy-guest"))
        self.assertIsNone(vault.get("remote-token"))

    async def test_interrupted_request_is_recovered_without_replaying_model(self):
        invitation = await self.host.invite("guest", self.host.active_url, target=self.host.active_id)
        room = self.host.app.state.relay.conversations.rooms[invitation["conversation_id"]]
        room["pending"] = True
        self.host.app.state.relay.conversations.save(room)
        await self.host.close()
        self.host = DesktopRuntime(Storage(Path(self.directory.name) / "host", self.vault))
        await self.host.start()
        restored = self.host.app.state.relay.conversations.rooms[room["id"]]
        self.assertFalse(restored["pending"])
        self.assertEqual(restored["messages"][-1]["role"], "error")
        self.assertIn("not replayed", restored["messages"][-1]["content"])
