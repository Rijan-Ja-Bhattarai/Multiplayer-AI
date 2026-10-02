# Multiplayer AI

Multiplayer AI is a platform where authorized users by the session leader
can invite people to work on the same session without sharing accounts or
people from the same organization.

## Status: under development

The first milestone is the **client communication layer**: a Connection
Server that lets one connected client deliver a message to another.

Implemented and tested:

- Persistent client connections over WebSocket
- In-memory client registry
- Client-to-client message routing
- Structured error responses and event logging

Not implemented yet: the Agent Gateway, A2A agent-to-agent communication,
sessions, and authentication. See [Scope](#scope) for details.

## Architecture

Two layers, deliberately kept separate:

```
   CLIENT DOMAIN                      AGENT LAYER
   Client A ──┐                                  │
              │ WebSocket                        │
              ▼                                  │
      ┌──────────────────┐      A2A      ┌──────────────┐
      │ Connection Server│ ───────────▶  │   Agent A    │
      │  (this repo)     │               └──────┬───────┘
      └──────────────────┘                      │ A2A
              ▲                           ┌──────▼───────┐
              │                           │   Agent B    │
            Client B                      └──────────────┘
```

The Connection Server is the only component in this repository. The agent
layer is not built yet — see [Scope](#scope).

### Layout

| Path | Responsibility |
|------|----------------|
| `src/server/app.py` | HTTP/WebSocket entry point, frame handling, logging |
| `src/server/connection_manager.py` | In-memory client registry and delivery |
| `src/routing/router.py` | The single routing decision point |
| `src/messages/envelope.py` | The client-facing message format |
| `src/errors.py` | Error codes and the error frame format |
| `scripts/demo_client.py` | Command-line client for manual testing |

Routing decisions live only in `src/routing/router.py`. The WebSocket
handler does not decide where a message goes.

## Setup

The project uses an existing Conda environment. From the repository root:

```bash
conda env create -f environment.yml
conda activate Multiplayer-AI
```

If the environment already exists, install just the new test dependencies:

```bash
python -m pip install pytest-asyncio websockets
```

## Running

Start the Connection Server:

```bash
python -m uvicorn src.server.app:app --host localhost --port 8000
```

Check it is healthy:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"healthy"}
```

The health endpoint does not depend on any agent being reachable.

## Trying client-to-client messaging

Open three terminals.

**Terminal 1** — the server (from above).

**Terminal 2** — client-B, the receiver:

```bash
python scripts/demo_client.py client-B client-A "Hi from B"
```

**Terminal 3** — client-A, the sender:

```bash
python scripts/demo_client.py client-A client-B "Hello Client B"
```

Client-B prints the received message:

```
[client-B] <- client-A: Hello Client B
```

The demo client listens until you stop it with `Ctrl+C`. If the server is
not on `localhost:8000`, pass `--host` and `--port`, or set
`CONNECTION_HOST` and `CONNECTION_PORT`:

```bash
python scripts/demo_client.py client-A client-B "Hello" --port 8010
```

### Why the sender prints nothing

On a successful send the server returns **no acknowledgement** — the
destination is the only place a delivered message is observable. Client-A
stays silent after `sent (no acknowledgement is returned on success)`.
That silence *is* the success signal; nothing is missing.

A consequence worth knowing: a sender cannot distinguish "delivered" from
"the server died" by watching its own socket. Delivery receipts are not
implemented.

## Message format

Client-to-client messages use a single envelope. Field names are camelCase.

```json
{
  "messageId": "msg-123",
  "type": "message",
  "source": { "id": "client-A", "type": "client" },
  "destination": { "id": "client-B", "type": "client" },
  "sessionId": "session-001",
  "timestamp": "2026-10-02T10:00:00Z",
  "payload": { "text": "Hello" }
}
```

`source` is ignored on receipt. The server rewrites it to the id of the
connection that sent the frame, so one client cannot impersonate another.

`destination.type` selects the route:

- `client` — delivered to that client's WebSocket
- `agent` — **not supported yet**, returns `AGENT_NOT_FOUND`

## Errors

Failures are returned to the *sender* as a JSON frame:

```json
{
  "type": "error",
  "messageId": "msg-123",
  "code": "CLIENT_NOT_FOUND",
  "message": "Destination client 'ghost' is not connected."
}
```

| Code | Meaning |
|------|---------|
| `INVALID_MESSAGE` | Frame is not JSON, or the envelope is malformed |
| `CLIENT_NOT_FOUND` | Destination client is not connected |
| `AGENT_NOT_FOUND` | Agent destinations are not implemented yet |

`AGENT_UNAVAILABLE`, `SESSION_NOT_FOUND`, `A2A_ERROR`, `TIMEOUT`, and
`INTERNAL_ERROR` are defined but not yet reachable.

`messageId` is `null` when the frame could not be parsed far enough to
recover an id. Stack traces are never sent to clients.

A malformed frame does **not** close the connection: the server answers
with `INVALID_MESSAGE` and keeps reading.

## Tests

```bash
python -m pytest
```

61 tests covering agent.md Tests 1–6 (server starts, health responds,
client connects, two clients stay connected, A → B delivers, unknown
destination errors) plus the envelope and routing contracts.

Run a subset:

```bash
python -m pytest tests/test_client_messaging.py -v
```

Known warning: Starlette's `TestClient` reports that `httpx` is deprecated
in favour of `httpx2`. Cosmetic, and left alone for now.

## Scope

Not built, on purpose, until the client communication path is proven:

- Agent Gateway and A2A agent-to-agent messaging
- Response correlation between a client request and a later agent reply
- Session management
- Authentication — client ids are self-declared and unverified
- Persistence; the registry is in-memory and resets on restart

Two behaviours worth knowing about:

- **Reconnecting with an existing client id** replaces the registry entry
  but does not close the previous socket, which is left orphaned.
- **`connection_id`** is server-internal and never exposed to clients.

## Design notes

`agent.md` in the repository root holds the full specification these
decisions come from, including the phase plan, acceptance criteria, and
the reasoning behind each boundary.
