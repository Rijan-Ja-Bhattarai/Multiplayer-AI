"""Tests for the Agent Gateway: relay calls and error-code translation."""

from __future__ import annotations

import json

import httpx
import pytest

from src.agents.gateway import AgentGateway, AgentGatewayError
from src.errors import ErrorCode


@pytest.fixture
def gateway() -> AgentGateway:
    return AgentGateway("http://relay.invalid", "token" * 8)


def stub_transport(handler) -> httpx.AsyncClient:
    """An httpx client whose requests are answered by ``handler``."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- construction and configuration --------------------------------------


def test_requires_a_relay_url_and_token() -> None:
    """A gateway cannot be built without both, to avoid a silent no-op."""
    with pytest.raises(ValueError):
        AgentGateway("", "token")
    with pytest.raises(ValueError):
        AgentGateway("http://relay.invalid", "")


def test_from_env_returns_none_when_unconfigured(monkeypatch) -> None:
    """With no relay configured the gateway is absent, not broken.

    The client-to-client path must keep working without agent support,
    so this reports "unavailable" rather than raising at import time.
    """
    monkeypatch.delenv("A2A_RELAY_URL", raising=False)
    monkeypatch.delenv("A2A_RELAY_TOKEN", raising=False)

    assert AgentGateway.from_env() is None


def test_from_env_builds_when_configured(monkeypatch) -> None:
    """A relay URL and token are enough to enable agent messaging."""
    monkeypatch.setenv("A2A_RELAY_URL", "http://localhost:9100/")
    monkeypatch.setenv("A2A_RELAY_TOKEN", "t" * 40)

    gateway = AgentGateway.from_env()

    assert gateway is not None
    # The trailing slash is trimmed so joined paths never double up.
    assert gateway.relay_url == "http://localhost:9100"


def test_from_env_needs_both_variables(monkeypatch) -> None:
    """Half a configuration is treated as no configuration."""
    monkeypatch.setenv("A2A_RELAY_URL", "http://localhost:9100")
    monkeypatch.delenv("A2A_RELAY_TOKEN", raising=False)

    assert AgentGateway.from_env() is None


# --- invoking ------------------------------------------------------------


async def test_invoke_returns_the_agent_payload(gateway) -> None:
    """A 200 response body becomes the agent's result payload."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"text": "hello", "provider": "echo"})

    gateway._client = stub_transport(handler)
    result = await gateway.invoke("agent-A", {"text": "hi"})

    assert result == {"text": "hello", "provider": "echo"}
    assert seen["url"] == "http://relay.invalid/agents/agent-A/invoke"
    assert seen["auth"] == f"Bearer {'token' * 8}"
    assert seen["body"] == {"text": "hi"}


async def test_invoke_forwards_the_payload_unchanged(gateway) -> None:
    """The client payload is passed through without translation.

    The relay hands it to the agent handler, and the provider adapters
    already accept {"text": ...}, so rewriting it here would only couple
    the gateway to provider internals.
    """
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"text": "ok"})

    gateway._client = stub_transport(handler)
    await gateway.invoke("agent-A", {"text": "hi", "extra": [1, 2]})

    assert captured == {"text": "hi", "extra": [1, 2]}


async def test_invoke_closes_cleanly(gateway) -> None:
    """aclose() releases the pooled client and allows a fresh one later."""
    gateway._client = stub_transport(lambda r: httpx.Response(200, json={"text": "x"}))

    await gateway.aclose()

    assert gateway._client is None


@pytest.mark.parametrize(
    "agent_id",
    ["has space", "has/slash", "a" * 65, "", "emoji-\N{ROCKET}"],
)
async def test_rejects_agent_ids_the_relay_would_refuse(
    gateway, agent_id: str
) -> None:
    """A malformed agent id is a client error, not a wasted relay call.

    The relay would answer 403, which it also uses for group isolation,
    so checking the id here keeps that distinction meaningful.
    """
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={})

    gateway._client = stub_transport(handler)

    with pytest.raises(AgentGatewayError) as caught:
        await gateway.invoke(agent_id, {"text": "hi"})

    assert caught.value.code == ErrorCode.INVALID_MESSAGE
    assert not called


# --- error translation ---------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        (403, ErrorCode.AGENT_UNAVAILABLE),
        (429, ErrorCode.AGENT_UNAVAILABLE),
        (503, ErrorCode.AGENT_UNAVAILABLE),
        (504, ErrorCode.TIMEOUT),
        (502, ErrorCode.TIMEOUT),
        (401, ErrorCode.A2A_ERROR),
        (500, ErrorCode.A2A_ERROR),
    ],
)
async def test_relay_status_maps_to_client_code(gateway, status, expected) -> None:
    """Each relay status becomes the code the client protocol defines."""
    gateway._client = stub_transport(
        lambda request: httpx.Response(status, json={"error": "nope"})
    )

    with pytest.raises(AgentGatewayError) as caught:
        await gateway.invoke("agent-A", {"text": "hi"})

    assert caught.value.code == expected


async def test_group_isolation_is_not_disclosed(gateway) -> None:
    """A 403 is reported without saying whether the agent exists.

    The relay returns 403 both for an unknown agent and for one outside
    the caller's group. Echoing that detail would let a client probe for
    agent ids in other groups.
    """
    gateway._client = stub_transport(
        lambda request: httpx.Response(
            403, json={"error": "Target is unavailable or outside your group"}
        )
    )

    with pytest.raises(AgentGatewayError) as caught:
        await gateway.invoke("secret-agent", {"text": "hi"})

    assert caught.value.code == ErrorCode.AGENT_UNAVAILABLE
    assert "outside your group" not in str(caught.value)


async def test_unreadable_success_body_is_an_a2a_error(gateway) -> None:
    """A 200 that is not JSON is a protocol fault, reported as A2A_ERROR."""
    gateway._client = stub_transport(
        lambda request: httpx.Response(200, content=b"not json")
    )

    with pytest.raises(AgentGatewayError) as caught:
        await gateway.invoke("agent-A", {"text": "hi"})

    assert caught.value.code == ErrorCode.A2A_ERROR


async def test_non_object_success_body_is_an_a2a_error(gateway) -> None:
    """A JSON array or scalar success body is not a valid payload."""
    gateway._client = stub_transport(lambda request: httpx.Response(200, json=[1, 2]))

    with pytest.raises(AgentGatewayError) as caught:
        await gateway.invoke("agent-A", {"text": "hi"})

    assert caught.value.code == ErrorCode.A2A_ERROR


async def test_unreachable_relay_is_agent_unavailable(gateway) -> None:
    """A relay that cannot be reached reports the agent unavailable.

    Deliberately not INTERNAL_ERROR: from a client's point of view the
    agent could not be reached, which is the actionable description.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    gateway._client = stub_transport(handler)

    with pytest.raises(AgentGatewayError) as caught:
        await gateway.invoke("agent-A", {"text": "hi"})

    assert caught.value.code == ErrorCode.AGENT_UNAVAILABLE


async def test_slow_relay_reports_timeout(gateway) -> None:
    """A relay that never answers in time reports TIMEOUT."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    gateway._client = stub_transport(handler)

    with pytest.raises(AgentGatewayError) as caught:
        await gateway.invoke("agent-A", {"text": "hi"})

    assert caught.value.code == ErrorCode.TIMEOUT


async def test_error_detail_is_optional(gateway) -> None:
    """A relay error body without an error string still maps cleanly."""
    gateway._client = stub_transport(lambda request: httpx.Response(503, json={}))

    with pytest.raises(AgentGatewayError) as caught:
        await gateway.invoke("agent-A", {"text": "hi"})

    assert caught.value.code == ErrorCode.AGENT_UNAVAILABLE
    assert "agent-A" in str(caught.value)


async def test_rate_limit_mentions_retrying(gateway) -> None:
    """A 429 tells the client the relay is at capacity, not that it broke."""
    gateway._client = stub_transport(
        lambda request: httpx.Response(429, json={"error": "Relay capacity exceeded"})
    )

    with pytest.raises(AgentGatewayError) as caught:
        await gateway.invoke("agent-A", {"text": "hi"})

    assert "retry" in str(caught.value).lower()
