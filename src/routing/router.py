"""The single routing decision point for client-facing messages.

agent.md section 20 asks for one clearly identifiable routing component
rather than routing decisions scattered across unrelated modules, so
every inbound message funnels through :func:`route_message`.

The shape of the decision (section 20)::

    Incoming Message
           |
        Validate
           |
       Read Destination
           |
           +-- destination.type == client --> Client Registry --> WebSocket
           |
           +-- destination.type == agent  --> Agent Gateway --> A2A

Only the client branch is implemented. The agent branch fails loudly with
``AGENT_NOT_FOUND`` because the Agent Gateway does not exist yet; that is
deliberate, so an agent destination is never silently accepted and dropped.
"""

from __future__ import annotations

from typing import Any, Dict

from src.errors import InvalidMessageError
from src.messages.envelope import MessageEnvelope


async def route_message(
    envelope: MessageEnvelope,
    client_id: str,
    connection_manager: Any,
) -> Dict[str, str]:
    """Deliver one validated envelope to its destination.

    The source recorded in the delivered frame is always rewritten to the
    id of the connection that sent it. A client-supplied ``source`` is
    not trusted: otherwise client-A could post a message claiming to come
    from client-B (section 9 requires the source be identified).

    Args:
        envelope: The parsed message envelope.
        client_id: Id of the connection that sent this frame.
        connection_manager: Registry used to reach the destination client.

    Returns:
        The destination as it was resolved, for logging.

    Raises:
        InvalidMessageError: Destination id or type is missing/unknown.
        ClientNotFoundError: Destination client is not connected.
        ClientDeliveryError: Destination client socket rejected the write.
        AgentNotFoundError: Destination is an agent; not yet supported.
    """
    destination_type = envelope.destination_type
    destination_id = envelope.destination_id

    if not destination_type or not destination_id:
        raise InvalidMessageError(
            "Message envelope requires destination.id and destination.type."
        )

    # The connection is the authority on who sent this, not the payload.
    envelope.source = {"id": client_id, "type": "client"}

    if destination_type == "client":
        await connection_manager.send_to_client(destination_id, envelope.to_wire())
        return {"type": destination_type, "id": destination_id}

    if destination_type == "agent":
        raise _agent_unsupported(destination_id)

    raise InvalidMessageError(
        f"Unknown destination type '{destination_type}'. "
        "Expected 'client' or 'agent'."
    )


def _agent_unsupported(agent_id: str) -> Exception:
    """Build the error for an agent destination.

    The Agent Gateway (agent.md sections 13 and 14) is not implemented,
    so there is no registry to resolve an agent id against and no A2A
    endpoint to call. Returning a constructed error keeps the branch
    explicit and documented at the point of failure.
    """
    from src.errors import AgentNotFoundError

    return AgentNotFoundError(
        f"Agent '{agent_id}' cannot be reached: the Agent Gateway is not "
        "implemented yet. Client-to-client messaging is the only "
        "supported destination."
    )
