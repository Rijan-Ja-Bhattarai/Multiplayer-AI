"""Regression tests for the agent connection lifecycle.

The defect these guard is about what the *relay* observes, so they run
against a real relay on a real socket. A mock could not distinguish "the
socket was closed" from "the reference was dropped".

The setup deliberately mirrors ``desktop_app/runtime.py``: the agent runs
on its own event loop, its task is cancelled from outside, and the loop
keeps running afterwards. That is the condition that matters. Cancelling
a task and awaiting it on the test's own loop also ends up closing the
socket as a side effect of teardown, which hides the bug.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from network_a2a.client import AgentClient
from network_a2a.server import create_app


def _token(name: str) -> str:
    return f"tok-{name}-" + "x" * 40


CREDENTIALS = {"agent-A": {"token": _token("agent-A"), "group": "team"}}


@pytest.fixture(scope="module")
def relay() -> dict:
    """A real relay listening on an ephemeral port."""
    app = create_app(CREDENTIALS, timeout=5)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", ws_max_size=262144))

    async def serve():
        await server.serve(sockets=[sock])

    thread = threading.Thread(target=lambda: asyncio.run(serve()), daemon=True)
    thread.start()

    deadline = time.monotonic() + 10
    while not server.started:
        if not thread.is_alive() or time.monotonic() > deadline:
            raise RuntimeError("relay failed to start")
        time.sleep(0.02)

    yield {
        "http": f"http://127.0.0.1:{port}",
        "ws": f"ws://127.0.0.1:{port}/connect",
    }

    server.should_exit = True
    thread.join(10)
    sock.close()


def online_agents(relay) -> set:
    """Agent ids the relay currently considers connected.

    agent-A is the only identity in the credential file, so it queries the
    list as itself.
    """
    with httpx.Client(timeout=5) as http:
        result = http.get(
            relay["http"] + "/agents",
            headers={"Authorization": f"Bearer {CREDENTIALS['agent-A']['token']}"},
        )
    return {a["id"] for a in result.json()["agents"] if a["online"]}


def wait_until(predicate, timeout: float = 10.0, interval: float = 0.02) -> bool:
    """Poll until ``predicate`` holds or the timeout expires."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class RunningAgent:
    """An AgentClient on its own loop, cancelled the way runtime.py does.

    The loop is deliberately left open after cancelling: a still-running
    loop is what keeps the unclosed socket reachable, and therefore what
    keeps the relay holding a stale peer.
    """

    def __init__(self, relay, handler, timeout: float = 30.0) -> None:
        self.relay = relay
        self.loop = asyncio.new_event_loop()
        self.holder: dict = {}
        self._handler = handler
        self._timeout = timeout

        def run() -> None:
            asyncio.set_event_loop(self.loop)
            client = AgentClient(
                relay["ws"],
                CREDENTIALS["agent-A"]["token"],
                handler,
                timeout=timeout,
                allow_insecure=True,
            )

            async def body() -> None:
                self.holder["task"] = asyncio.current_task()
                self.holder["client"] = client
                await client.run()

            try:
                self.loop.run_until_complete(body())
            except BaseException:
                pass

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    @property
    def client(self):
        return self.holder["client"]

    def wait_online(self) -> None:
        assert wait_until(lambda: "agent-A" in online_agents(self.relay)), (
            "agent never registered with the relay"
        )

    def wait_offline(self) -> bool:
        return wait_until(lambda: "agent-A" not in online_agents(self.relay))

    def cancel(self) -> None:
        """Cancel the run task without stopping the loop, like runtime.py."""
        self.loop.call_soon_threadsafe(self.holder["task"].cancel)
        self.thread.join(5)

    def close(self) -> None:
        self.cancel()
        self.loop.close()


async def _echo(payload, source):
    return {"text": "ok"}


def test_cancelling_run_closes_the_connection(relay) -> None:
    """Cancelling run() makes the relay see the agent go offline.

    Cancelling raises CancelledError, a BaseException that the client's
    ``except Exception`` cannot catch, but the ``finally`` block still
    runs. Before the fix that block only cleared ``self.socket``: the
    connection was abandoned rather than closed, so the relay kept the
    agent registered as a peer.
    """
    agent = RunningAgent(relay, _echo)
    try:
        agent.wait_online()

        agent.cancel()

        assert agent.wait_offline(), (
            "the relay still lists the agent as online after its run task was "
            "cancelled; the connection was dropped without being closed"
        )
    finally:
        agent.close()


def test_agent_can_reconnect_with_the_same_id(relay) -> None:
    """The same id can reconnect immediately after a cancel.

    The relay refuses a duplicate id while the previous one is still
    registered, so this is the user-visible symptom: switch workspace in
    the desktop app and the restarted agent silently fails to connect.
    """
    first = RunningAgent(relay, _echo)
    try:
        first.wait_online()
        first.cancel()
        assert first.wait_offline(), "the first agent did not disconnect"

        second = RunningAgent(relay, _echo)
        try:
            second.wait_online()
        finally:
            second.close()
    finally:
        first.close()


def test_pending_requests_fail_when_the_connection_closes(relay) -> None:
    """A cancel still fails in-flight requests instead of hanging them.

    The close must not disturb the guarantee that a request is never
    replayed: pending futures fail and nothing is retried.
    """
    started = threading.Event()

    async def slow(payload, source):
        started.set()
        await asyncio.sleep(30)
        return {"text": "never"}

    agent = RunningAgent(relay, slow, timeout=30)
    try:
        agent.wait_online()

        loop = agent.loop
        failed: list = []

        async def issue():
            try:
                await agent.client.request("agent-A", {"text": "hi"})
            except Exception as exc:  # noqa: BLE001 - recorded for assertion
                failed.append(exc)

        runner = asyncio.run_coroutine_threadsafe(issue(), loop)
        assert started.wait(10), "the agent handler never started"

        agent.cancel()

        runner.result(timeout=10)
        assert failed, "the in-flight request neither returned nor failed"
        message = str(failed[0])
        assert "replayed" in message or "closed" in message, message
    finally:
        agent.close()
