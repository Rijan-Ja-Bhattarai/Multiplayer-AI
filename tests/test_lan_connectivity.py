import os
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

pytest.importorskip("psutil")

from desktop_app.lan import lan_addresses
from desktop_app.runtime import DesktopRuntime
from desktop_app.storage import Storage
from network_a2a.client import direct_relay_connection
from tests.test_desktop_runtime import MemoryVault


class LANAddressTests(unittest.TestCase):
    def addresses(self, interfaces, route=None):
        probe = unittest.mock.MagicMock()
        probe.__enter__.return_value = probe
        if route:
            probe.getsockname.return_value = (route, 0)
        else:
            probe.connect.side_effect = OSError("No default route")
        adapters = {name: [SimpleNamespace(family=socket.AF_INET, address=ip) for ip in ips]
                    for name, ips in interfaces.items()}
        with patch("desktop_app.lan.socket.socket", return_value=probe), \
                patch("desktop_app.lan.psutil.net_if_addrs", return_value=adapters), \
                patch("desktop_app.lan.psutil.net_if_stats", return_value={}):
            return lan_addresses()

    def test_wifi_is_preferred_over_virtual_adapter_even_with_vpn_default_route(self):
        result = self.addresses({"vEthernet (WSL)": ["172.28.0.1"], "Wi-Fi": ["192.168.43.12"],
                                 "VPN": ["10.8.0.2"], "Loopback": ["127.0.0.1"]}, "10.8.0.2")
        self.assertEqual(result[0], ("Wi-Fi", "192.168.43.12"))
        self.assertEqual(len(result), 3)

    def test_default_route_breaks_tie_between_physical_adapters(self):
        result = self.addresses({"Ethernet": ["192.168.1.2"], "Wi-Fi": ["192.168.43.12"]}, "192.168.43.12")
        self.assertEqual(result[0][0], "Wi-Fi")

    def test_hotspot_addresses_are_available_without_internet_or_hostname_dns(self):
        result = self.addresses({"Wi-Fi": ["192.168.43.12", "0.0.0.0"], "Ethernet": ["169.254.1.2"]})
        self.assertEqual(result[0], ("Wi-Fi", "192.168.43.12"))
        self.assertEqual(self.addresses({"Loopback": ["127.0.0.1"]}), [])

    def test_disconnected_and_ipv6_only_adapters_are_not_advertised(self):
        addresses = {"Ethernet": [SimpleNamespace(family=socket.AF_INET, address="192.168.1.2")],
                     "Wi-Fi": [SimpleNamespace(family=socket.AF_INET6, address="fe80::1")]}
        with patch("desktop_app.lan.socket.socket", side_effect=OSError), \
                patch("desktop_app.lan.psutil.net_if_addrs", return_value=addresses), \
                patch("desktop_app.lan.psutil.net_if_stats", return_value={"Ethernet": SimpleNamespace(isup=False)}):
            self.assertEqual(lan_addresses(), [])

    def test_only_local_or_explicit_insecure_relays_bypass_proxies(self):
        for url in ("ws://192.168.43.12:1234/connect", "ws://laptop.local/connect",
                    "wss://192.168.1.2/connect", "wss://localhost/connect", "wss://[::1]/connect"):
            self.assertTrue(direct_relay_connection(url), url)
        self.assertFalse(direct_relay_connection("wss://relay.example.com/connect"))
        self.assertFalse(direct_relay_connection("wss://8.8.8.8/connect"))


class LANConnectivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_lan_http_and_websocket_work_with_unreachable_system_proxies(self):
        addresses = lan_addresses()
        if not addresses:
            self.skipTest("No active IPv4 LAN adapter")
        # Exercise both discovery HTTP and WebSocket messaging on the host's
        # real LAN listener, with no proxy bypass environment configured.
        environment = {name: "http://127.0.0.1:1" for name in
                       ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy",
                        "all_proxy", "WS_PROXY", "WSS_PROXY", "ws_proxy", "wss_proxy")}
        environment.update(NO_PROXY="", no_proxy="")
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, environment):
            host = DesktopRuntime(Storage(Path(directory) / "host", MemoryVault()))
            guest = DesktopRuntime(Storage(Path(directory) / "guest", MemoryVault()))
            try:
                await host.start()
                await guest.start()
                url = f"ws://{addresses[0][1]}:{host.port}/connect"
                invitation = await host.invite("lan-guest", url, lan=True, target=host.active_id)
                self.assertEqual(host.server_socket.getsockname()[0], "0.0.0.0")
                await guest.join(url, invitation["token"], allow_insecure=True,
                                 conversation_id=invitation["conversation_id"])
                self.assertTrue(guest.remote)
                reply = await guest.send_conversation(invitation["conversation_id"], "Across the LAN")
                self.assertIn("Across the LAN", str(reply))
                reply = await host.send("lan-guest", {"text": "Back to the guest"})
                self.assertIn("Back to the guest", reply["text"])
                await guest.refresh()
                await guest.delete_workspace()
                self.assertNotIn("lan-guest", host.credentials)
                self.assertIs(host.relay_http("wss://relay.example.com/connect"), host.http)
            finally:
                await guest.close()
                await host.close()

    async def test_lan_timeout_explains_isolation_and_preserves_existing_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = DesktopRuntime(Storage(directory, MemoryVault()))
            original = None
            try:
                await runtime.start()
                original = runtime.engine.direct_http
                identity = runtime.active_workspace_id
                for exception in (httpx.ConnectTimeout(""), httpx.ReadTimeout("")):
                    def fail(request):
                        raise exception
                    async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as http:
                        runtime.engine.direct_http = http
                        with self.assertRaisesRegex(ConnectionError, "192.168.43.12:4321.*Campus or guest Wi-Fi"):
                            await runtime.join("ws://192.168.43.12:4321/connect", "x" * 32, allow_insecure=True)
                    self.assertEqual(runtime.active_workspace_id, identity)
                    self.assertEqual(len(runtime.catalog["workspaces"]), 1)
                runtime.engine.direct_http = original
                self.assertIn("Still running", (await runtime.send(runtime.active_id, {"text": "Still running"}))["text"])
            finally:
                if original is not None:
                    runtime.engine.direct_http = original
                await runtime.close()
