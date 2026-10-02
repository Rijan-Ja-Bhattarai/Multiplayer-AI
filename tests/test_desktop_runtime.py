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


if __name__ == "__main__":
    unittest.main()
