import asyncio
import json
import os
import socket
import subprocess
import sys
import threading
import time
import unittest

import httpx
import uvicorn
from starlette.responses import JSONResponse
from starlette.routing import Route
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus, ConnectionClosed

from network_a2a.client import AgentClient
from network_a2a.server import create_app


TOKENS = {name: name * 40 for name in ("a", "b", "c", "d")}


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_app({name: {"token": token, "group": "other" if name == "d" else "team"}
                              for name, token in TOKENS.items()}, timeout=1)
        async def local_agent(request):
            body = await request.json()
            return JSONResponse({"jsonrpc": "2.0", "id": body["id"], "result": {"message": body["params"]}})
        cls.app.router.routes.append(Route("/local-agent", local_agent, methods=["POST"]))
        async def ollama(request):
            body = await request.json()
            if body["model"] != "test-local-model" or body["stream"] is not False:
                return JSONResponse({"error": "Wrong model or stream setting"}, 400)
            return JSONResponse({"message": {"content": "Model received: " + body["messages"][-1]["content"]},
                                 "done_reason": "stop", "eval_count": 7})
        cls.app.router.routes.append(Route("/api/chat", ollama, methods=["POST"]))
        cls.socket = socket.socket()
        cls.socket.bind(("127.0.0.1", 0))
        cls.port = cls.socket.getsockname()[1]
        cls.server = uvicorn.Server(uvicorn.Config(cls.app, log_level="error", ws_max_size=262144))
        async def serve():
            cls.loop = asyncio.get_running_loop()
            await cls.server.serve(sockets=[cls.socket])
        cls.thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
        cls.thread.start()
        deadline = time.monotonic() + 10
        while not cls.server.started:
            if not cls.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("Test server failed to start")
            time.sleep(.02)
        cls.ws_url = f"ws://127.0.0.1:{cls.port}/connect"
        cls.http_url = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.should_exit = True
        cls.thread.join(10)
        cls.socket.close()

    async def asyncSetUp(self):
        self.runners = []

    async def asyncTearDown(self):
        for task in self.runners:
            task.cancel()
        await asyncio.gather(*self.runners, return_exceptions=True)
        # Wait for server-side cleanup before reusing identities.
        async with httpx.AsyncClient() as http:
            for _ in range(50):
                result = await http.get(self.http_url + "/agents", headers=self.headers("a"))
                if not any(agent["online"] for agent in result.json()["agents"]):
                    break
                await asyncio.sleep(.02)

    def headers(self, agent):
        return {"Authorization": "Bearer " + TOKENS[agent]}

    async def start(self, agent, handler=None):
        async def echo(payload, source):
            return {"device": agent, "source": source, "payload": payload}
        client = AgentClient(self.ws_url, TOKENS[agent], handler or echo, timeout=3)
        self.runners.append(asyncio.create_task(client.run()))
        await asyncio.wait_for(client.ready.wait(), 3)
        return client

    async def test_three_devices_concurrent_bidirectional_and_http(self):
        a, b, c = await self.start("a"), await self.start("b"), await self.start("c")
        results = await asyncio.gather(a.request("b", {"text": "a to b"}),
                                       b.request("c", {"text": "b to c"}),
                                       c.request("a", {"text": "c to a"}))
        self.assertEqual([r["device"] for r in results], ["b", "c", "a"])
        self.assertEqual([r["source"] for r in results], ["a", "b", "c"])
        async with httpx.AsyncClient() as http:
            response = await http.post(self.http_url + "/agents/c/invoke", headers=self.headers("a"),
                                       json={"jsonrpc": "2.0", "id": "original-id", "method": "SendMessage"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["payload"]["id"], "original-id")
            listed = await http.get(self.http_url + "/agents", headers=self.headers("a"))
            self.assertEqual({v["id"] for v in listed.json()["agents"]}, {"a", "b", "c"})

    async def test_authentication_and_group_isolation(self):
        with self.assertRaises(InvalidStatus):
            async with connect(self.ws_url, additional_headers={"Authorization": "Bearer bad"}):
                pass
        async with httpx.AsyncClient() as http:
            self.assertEqual((await http.get(self.http_url + "/agents")).status_code, 401)
            response = await http.post(self.http_url + "/agents/d/invoke", headers=self.headers("a"), json={})
            self.assertEqual(response.status_code, 403)
        a = await self.start("a")
        await self.start("d")
        with self.assertRaisesRegex(RuntimeError, "outside your group"):
            await a.request("d", {})

    async def test_offline_timeout_and_disconnect(self):
        a = await self.start("a")
        with self.assertRaisesRegex(RuntimeError, "offline"):
            await a.request("b", {})
        async def slow(payload, source):
            await asyncio.sleep(30)
        b = await self.start("b", slow)
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            await a.request("b", {})
        request = asyncio.create_task(a.request("b", {}))
        await asyncio.sleep(.1)
        self.runners[1].cancel()
        await asyncio.gather(self.runners[1], return_exceptions=True)
        with self.assertRaisesRegex(RuntimeError, "disconnected"):
            await request

    async def test_duplicate_identity_and_malformed_frame(self):
        await self.start("a")
        async with connect(self.ws_url, additional_headers=self.headers("a")) as duplicate:
            with self.assertRaises(ConnectionClosed):
                await duplicate.recv()
        async with connect(self.ws_url, additional_headers=self.headers("b")) as b:
            await b.recv()
            await b.send("not json")
            self.assertEqual(json.loads(await b.recv())["type"], "error")
            await b.send(json.dumps({"type": "request", "id": [], "to": "a"}))
            self.assertEqual(json.loads(await b.recv())["type"], "error")

    async def test_forged_reply_cannot_complete_another_agents_request(self):
        a = await self.start("a")
        async with connect(self.ws_url, additional_headers=self.headers("b")) as b:
            await b.recv()
            async with connect(self.ws_url, additional_headers=self.headers("c")) as c:
                await c.recv()
                request = asyncio.create_task(a.request("b", {"secret": "work"}))
                frame = json.loads(await b.recv())
                await c.send(json.dumps({"type": "response", "id": frame["id"], "payload": "forged"}))
                await asyncio.sleep(.05)
                self.assertFalse(request.done())
                await b.send(json.dumps({"type": "response", "id": frame["id"], "payload": "real"}))
                self.assertEqual(await request, "real")

    async def test_payload_limit_and_http_validation(self):
        async with httpx.AsyncClient() as http:
            url = self.http_url + "/agents/b/invoke"
            invalid = await http.post(url, headers=self.headers("a"), content="not JSON")
            self.assertEqual(invalid.status_code, 400)
            huge = await http.post(url, headers=self.headers("a"), content="x" * 262145)
            self.assertEqual(huge.status_code, 413)

    def test_remote_plaintext_rejected(self):
        with self.assertRaisesRegex(ValueError, "require wss"):
            AgentClient("ws://example.com/connect", TOKENS["a"])

    async def test_client_reconnects_after_connection_loss(self):
        a = await self.start("a")
        b = await self.start("b")
        old_peer = self.app.state.relay.peers["b"]
        close = asyncio.run_coroutine_threadsafe(old_peer.socket.close(code=1012), self.loop)
        await asyncio.wrap_future(close)
        for _ in range(200):
            if self.app.state.relay.peers.get("b") is not old_peer and "b" in self.app.state.relay.peers and b.ready.is_set():
                break
            await asyncio.sleep(.02)
        else:
            self.fail("Client did not reconnect")
        self.assertEqual((await a.request("b", "after reconnect"))["payload"], "after reconnect")

    async def test_cli_bridges_local_jsonrpc_endpoint(self):
        process = subprocess.Popen([sys.executable, "-m", "network_a2a", "--server", self.ws_url,
                                    "--local-a2a-url", self.http_url + "/local-agent"],
                                   env={**os.environ, "A2A_TOKEN": TOKENS["b"]},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            async with httpx.AsyncClient() as http:
                for _ in range(150):
                    listing = await http.get(self.http_url + "/agents", headers=self.headers("a"))
                    if any(agent["id"] == "b" and agent["online"] for agent in listing.json()["agents"]):
                        break
                    if process.poll() is not None:
                        self.fail("CLI bridge exited before connecting")
                    await asyncio.sleep(.02)
                response = await http.post(self.http_url + "/agents/b/invoke", headers=self.headers("a"),
                                           json={"jsonrpc": "2.0", "id": "inner-id", "method": "SendMessage",
                                                 "params": {"text": "real local HTTP bridge"}})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["id"], "inner-id")
                self.assertEqual(response.json()["result"]["message"]["text"], "real local HTTP bridge")
        finally:
            process.terminate()
            await asyncio.to_thread(process.wait, 5)

    async def test_cli_provider_agent_over_relay_and_safe_error(self):
        a = await self.start("a")
        process = subprocess.Popen([sys.executable, "-m", "network_a2a", "--server", self.ws_url,
                                    "--provider", "ollama", "--model", "test-local-model",
                                    "--provider-base-url", self.http_url],
                                   env={**os.environ, "A2A_TOKEN": TOKENS["b"]},
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            async with httpx.AsyncClient() as http:
                for _ in range(150):
                    listing = await http.get(self.http_url + "/agents", headers=self.headers("a"))
                    if any(agent["id"] == "b" and agent["online"] for agent in listing.json()["agents"]):
                        break
                    if process.poll() is not None:
                        self.fail("Provider CLI exited before connecting")
                    await asyncio.sleep(.02)
            result = await a.request("b", {"text": "from Alice"})
            self.assertEqual(result["text"], "Model received: from Alice")
            self.assertEqual(result["provider"], "ollama")
            self.assertEqual(result["usage"]["output_tokens"], 7)
            with self.assertRaisesRegex(RuntimeError, "invalid_input"):
                await a.request("b", {"text": "Hello", "model": "unauthorized-model"})
            # A failed input must not take the provider agent offline.
            self.assertEqual((await a.request("b", "still alive"))["text"], "Model received: still alive")
        finally:
            process.terminate()
            await asyncio.to_thread(process.wait, 5)


if __name__ == "__main__":
    unittest.main()
