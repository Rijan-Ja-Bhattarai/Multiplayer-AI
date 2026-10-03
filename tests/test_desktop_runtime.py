import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from desktop_app.runtime import DesktopRuntime, relay_http_url
from desktop_app.storage import Storage


class MemoryVault:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


class DesktopRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.events = []
        self.storage = Storage(self.directory.name, self.vault)
        self.runtime = DesktopRuntime(self.storage, lambda kind, data: self.events.append((kind, data)))
        await self.runtime.start()
        self.other = None

    async def asyncTearDown(self):
        if self.other:
            await self.other.close()
        await self.runtime.close()
        self.directory.cleanup()

    async def test_launch_sets_up_relay_identity_and_real_connectivity(self):
        self.assertTrue(any(kind == "ready" for kind, _ in self.events))
        result = await self.runtime.send(self.runtime.active_id, {"text": "Hello desktop"})
        self.assertIn("Hello desktop", result["text"])
        async with httpx.AsyncClient() as http:
            response = await http.get(f"http://127.0.0.1:{self.runtime.port}/health")
            self.assertEqual(response.json(), {"status": "ok"})
            self.assertEqual((await http.get(f"http://127.0.0.1:{self.runtime.port}/ui/")).status_code, 404)
        settings_text = Path(self.directory.name, "settings.json").read_text()
        self.assertNotIn(self.runtime.active_token, settings_text)

    async def test_second_desktop_joins_and_exchanges_messages(self):
        invite = await self.runtime.invite("second-device", self.runtime.active_url)
        other_directory = Path(self.directory.name) / "other"
        self.other = DesktopRuntime(Storage(other_directory, MemoryVault()))
        await self.other.start()
        await self.other.join(invite["url"], invite["token"])
        self.assertTrue(self.other.remote)
        result = await self.runtime.send("second-device", {"text": "Across desktops"})
        self.assertIn("Across desktops", result["text"])
        await self.other.use_local()
        self.assertFalse(self.other.remote)

    async def test_joined_devices_receive_visible_messages_in_both_directions(self):
        invite = await self.runtime.invite("visible-device", self.runtime.active_url)
        received = []
        self.other = DesktopRuntime(Storage(Path(self.directory.name) / "visible", MemoryVault()),
                                    lambda kind, data: received.append((kind, data)))
        await self.other.start()
        await self.other.join(invite["url"], invite["token"])
        await self.runtime.send("visible-device", {"text": "Hello from the host"})
        incoming = [data for kind, data in received if kind == "incoming"]
        self.assertEqual(incoming[-1], {"from": self.runtime.active_id, "to": "visible-device", "text": "Hello from the host"})
        self.assertTrue(any(kind == "incoming_reply" for kind, _ in received))
        await self.other.send(self.runtime.active_id, {"messages": [{"role": "user", "content": "Reply from the guest"}]})
        incoming = [data for kind, data in self.events if kind == "incoming"]
        self.assertEqual(incoming[-1]["from"], "visible-device")
        self.assertEqual(incoming[-1]["text"], "Reply from the guest")
        count = len(incoming)
        await self.runtime.send(self.runtime.active_id, {"text": "Self check"})
        self.assertEqual(len([data for kind, data in self.events if kind == "incoming"]), count)

    async def test_saved_remote_workspace_restores_on_launch(self):
        invite = await self.runtime.invite("returning-device", self.runtime.active_url)
        directory = Path(self.directory.name) / "returning"
        vault = MemoryVault()
        first = DesktopRuntime(Storage(directory, vault))
        await first.start()
        await first.join(invite["url"], invite["token"])
        await first.close()
        self.other = DesktopRuntime(Storage(directory, vault))
        await self.other.start()
        self.assertTrue(self.other.remote)
        self.assertEqual(self.other.active_id, "returning-device")
        self.assertIn("remote", self.other.storage.settings)

    async def test_provider_agent_runs_in_process_and_restores(self):
        old_http = self.runtime.http
        async def transport(request):
            if request.url.host == "model.example":
                body = json.loads(request.content)
                return httpx.Response(200, json={"choices": [{"message": {"content": "Native model reply"}, "finish_reason": "stop"}]})
            return await old_http.send(request)
        self.runtime.http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
        profile = {"id": "researcher", "provider": "bionic", "model": "test-model", "base_url": "https://model.example/v1", "autostart": True}
        await self.runtime.save_agent(profile, "private-provider-key")
        result = await self.runtime.send("researcher", {"text": "Hello"})
        self.assertEqual(result["text"], "Native model reply")
        invite = await self.runtime.invite("model-guest", self.runtime.active_url)
        self.other = DesktopRuntime(Storage(Path(self.directory.name) / "model-guest", MemoryVault()))
        await self.other.start()
        await self.other.join(invite["url"], invite["token"])
        result = await self.other.send("researcher", {"text": "Use the host's model from the joined device"})
        self.assertEqual(result["text"], "Native model reply")
        self.assertTrue(any(kind == "incoming" and data["from"] == "model-guest" and data["to"] == "researcher"
                            for kind, data in self.events))
        self.assertNotIn("private-provider-key", self.storage.path.read_text())
        await self.runtime.stop_agent("researcher")
        self.assertFalse(self.storage.settings["agents"][0]["autostart"])
        await old_http.aclose()

    async def test_invalid_join_preserves_local_workspace(self):
        identity = self.runtime.active_id
        with self.assertRaisesRegex(ValueError, "rejected"):
            await self.runtime.join(self.runtime.active_url, "incorrect-token-" * 3)
        self.assertEqual(self.runtime.active_id, identity)
        self.assertFalse(self.runtime.remote)
        self.assertIn("still connected", (await self.runtime.send(identity, {"text": "still connected"}))["text"])

    async def test_unreachable_join_has_visible_error_and_preserves_workspace(self):
        original_http = self.runtime.http
        identity = self.runtime.active_id
        try:
            for exception, expected in ((httpx.ReadTimeout(""), "did not respond"),
                                        (httpx.ConnectError(""), "Could not connect")):
                def fail(request):
                    raise exception
                self.runtime.http = httpx.AsyncClient(transport=httpx.MockTransport(fail))
                with self.assertRaisesRegex(ConnectionError, expected):
                    await self.runtime.join("wss://unreachable.example/connect", "x" * 32)
                self.assertEqual(self.runtime.active_id, identity)
                self.assertFalse(self.runtime.remote)
                await self.runtime.http.aclose()
        finally:
            self.runtime.http = original_http

    async def test_join_rejects_identity_already_in_use_without_disconnect(self):
        identity = self.runtime.active_id
        with self.assertRaisesRegex(ValueError, "already connected"):
            await self.runtime.join(self.runtime.active_url, self.runtime.active_token)
        self.assertTrue(self.runtime.runners[identity][0].ready.is_set())
        self.assertFalse(self.runtime.remote)

    async def test_shutdown_releases_sockets_and_preserves_identity(self):
        identity = self.runtime.active_id
        await self.runtime.close()
        self.assertTrue(self.runtime.server_task.done())
        self.runtime = DesktopRuntime(Storage(self.directory.name, self.vault))
        await self.runtime.start()
        self.assertEqual(self.runtime.active_id, identity)

    def test_internet_join_requires_tls(self):
        with self.assertRaisesRegex(ValueError, "require wss"):
            relay_http_url("ws://remote.example/connect")
        self.assertEqual(relay_http_url("wss://remote.example/connect"), "https://remote.example")
        with self.assertRaises(ValueError):
            relay_http_url("wss://remote.example/connect?token=secret")


class WorkspaceDurabilityTests(unittest.IsolatedAsyncioTestCase):
    """Paths where a failure could undo or hide a destructive action.

    Each of these wrote something the user asked for, then did a network
    or disk operation that can fail before the change reached disk.
    """

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.events = []
        self.storage = Storage(self.directory.name, self.vault)
        self.runtime = DesktopRuntime(self.storage, lambda kind, data: self.events.append((kind, data)))
        await self.runtime.start()

    async def asyncTearDown(self):
        await self.runtime.close()

    async def test_stopping_an_agent_survives_a_failed_reconnect(self):
        """Stopping an agent must be recorded even if the echo agent fails.

        The remote branch re-attaches the connectivity agent before it
        saved, and that attach waits on the network. When it raised, the
        stop was lost and the next launch started the agent again.
        """
        self.runtime.engine.storage.settings["remote_agent"] = {"id": self.runtime.active_id, "autostart": True}
        self.runtime.engine.remote = True
        original = self.runtime.engine._attach

        async def fail(*args, **kwargs):
            raise TimeoutError("Agent did not connect.")

        self.runtime.engine._attach = fail
        try:
            with self.assertRaises(TimeoutError):
                await self.runtime.engine.stop_agent(self.runtime.active_id)
        finally:
            self.runtime.engine._attach = original
            self.runtime.engine.remote = False

        on_disk = json.loads(self.storage.path.read_text(encoding="utf-8"))
        self.assertFalse(on_disk["remote_agent"]["autostart"])

    async def test_deleting_a_workspace_is_recorded_before_anything_is_destroyed(self):
        """A failure after the wipe must not resurrect the workspace.

        ensure_engine() starts a relay and touches the credential store.
        It previously ran after the credentials and history were gone but
        before workspaces.json was written, so a failure there left the
        workspace listed on disk with nothing behind it.
        """
        second = await self.runtime.create_workspace("Writing")
        await self.runtime.switch_workspace(second["id"])
        original = self.runtime.ensure_engine

        async def fail(entry):
            raise RuntimeError("Local relay could not start")

        self.runtime.ensure_engine = fail
        with self.assertRaisesRegex(RuntimeError, "relay could not start"):
            await self.runtime.delete_workspace()

        catalog = json.loads(self.runtime.catalog_path.read_text(encoding="utf-8"))
        remaining = {entry["id"] for entry in catalog["workspaces"]}
        self.assertNotIn(second["id"], remaining)
        self.assertTrue(remaining, "a replacement workspace must remain")
        self.runtime.ensure_engine = original

    async def test_reset_identity_rotates_every_local_workspace(self):
        """Each local workspace owns its own relay and credential set.

        The catalog manager delegates the rotation per workspace, and this
        loop had no coverage at all, which is how a method call chained
        onto an un-awaited coroutine survived review.
        """
        second = await self.runtime.create_workspace("Writing")
        await self.runtime.switch_workspace(second["id"])
        engines = [self.runtime.engines[entry["id"]]
                   for entry in self.runtime.catalog["workspaces"] if entry["kind"] == "local"]
        before = {engine.active_id: engine.credentials[engine.active_id]["token"]
                  for engine in engines}
        self.assertEqual(len(engines), 2, "expected two local workspaces")

        results = await self.runtime.reset_identity()

        self.assertEqual(len(results), 2)
        for engine in engines:
            after = engine.credentials[engine.active_id]["token"]
            self.assertNotEqual(after, before[engine.active_id],
                                "each workspace should have received a new token")
            # Read through the engine's own vault: only the workspace
            # called "local" shares the root one, the rest are namespaced.
            self.assertTrue(engine.storage.vault.get("relay:" + engine.active_id),
                            "the replacement token must be in the vault")


if __name__ == "__main__":
    unittest.main()
