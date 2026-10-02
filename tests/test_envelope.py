"""Unit tests for the message envelope and its wire format."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from src.messages.envelope import MessageEnvelope


def test_parses_camel_case_wire_format() -> None:
    """The documented client format parses (messageId, sessionId)."""
    envelope = MessageEnvelope.from_wire(
        {
            "messageId": "msg-123",
            "type": "message",
            "source": {"id": "client-A", "type": "client"},
            "destination": {"id": "client-B", "type": "client"},
            "sessionId": "session-001",
            "timestamp": "2026-10-02T10:00:00Z",
            "payload": {"text": "Hello"},
        }
    )

    assert envelope.message_id == "msg-123"
    assert envelope.session_id == "session-001"


def test_parses_snake_case_field_names() -> None:
    """Internal construction by field name also works."""
    envelope = MessageEnvelope(
        message_id="msg-1",
        type="message",
        source={"id": "a", "type": "client"},
        destination={"id": "b", "type": "client"},
        session_id="s-1",
        timestamp="t",
        payload={},
    )

    assert envelope.message_id == "msg-1"


def test_serialises_back_to_camel_case() -> None:
    """to_wire() emits the field names the client protocol specifies."""
    envelope = MessageEnvelope.from_wire(
        {
            "messageId": "msg-123",
            "type": "message",
            "source": {"id": "client-A", "type": "client"},
            "destination": {"id": "client-B", "type": "client"},
            "sessionId": "session-001",
            "timestamp": "2026-10-02T10:00:00Z",
            "payload": {"text": "Hello"},
        }
    )

    serialised = json.loads(envelope.to_wire())

    assert serialised["messageId"] == "msg-123"
    assert serialised["sessionId"] == "session-001"
    assert "message_id" not in serialised


def test_round_trip_preserves_content() -> None:
    """Parsing then serialising is lossless for the wire format."""
    original = {
        "messageId": "msg-round",
        "type": "message",
        "source": {"id": "client-A", "type": "client"},
        "destination": {"id": "client-B", "type": "client"},
        "sessionId": "session-001",
        "timestamp": "2026-10-02T10:00:00Z",
        "payload": {"text": "Hello", "nested": {"n": 1}},
    }

    result = json.loads(MessageEnvelope.from_wire(original).to_wire())

    assert result == original


def test_missing_required_field_is_rejected() -> None:
    """An envelope missing messageId fails validation."""
    with pytest.raises(ValidationError):
        MessageEnvelope.from_wire(
            {
                "type": "message",
                "source": {"id": "a", "type": "client"},
                "destination": {"id": "b", "type": "client"},
                "sessionId": "s",
                "timestamp": "t",
                "payload": {},
            }
        )


def test_payload_accepts_arbitrary_content() -> None:
    """The payload is not restricted to a fixed shape."""
    envelope = MessageEnvelope.from_wire(
        {
            "messageId": "m",
            "type": "message",
            "source": {"id": "a", "type": "client"},
            "destination": {"id": "b", "type": "client"},
            "sessionId": "s",
            "timestamp": "t",
            "payload": {"anything": [1, 2, {"deep": True}]},
        }
    )

    assert envelope.payload["anything"][2]["deep"] is True


def test_destination_helpers_expose_type_and_id() -> None:
    """destination_type and destination_id read from the destination."""
    envelope = MessageEnvelope.from_wire(
        {
            "messageId": "m",
            "type": "message",
            "source": {"id": "a", "type": "client"},
            "destination": {"id": "agent-A", "type": "agent"},
            "sessionId": "s",
            "timestamp": "t",
            "payload": {},
        }
    )

    assert envelope.destination_type == "agent"
    assert envelope.destination_id == "agent-A"


def test_create_generates_id_and_timestamp() -> None:
    """create() fills in an id and an ISO timestamp."""
    envelope = MessageEnvelope.create(
        message_type="message",
        source_id="client-A",
        destination_id="client-B",
        source_type="client",
        destination_type="client",
        session_id="session-001",
        payload={"text": "hi"},
    )

    assert envelope.message_id
    assert envelope.timestamp
    assert envelope.destination == {"id": "client-B", "type": "client"}
