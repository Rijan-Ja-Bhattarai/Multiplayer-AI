# Multiplayer AI

Multiplayer AI is a platform where authorized users by the session leader
can invite people to work on the same session without sharing accounts or
people from the same organization.

## Status: under development

The first milestone is the **client communication layer**: a Connection
Server that lets one connected client deliver a message to another client
or to an agent.

Implemented and tested:

- Persistent client connections over WebSocket
- In-memory client registry
- Client-to-client message routing
- Client-to-agent routing through the Agent Gateway
- Structured error responses and event logging

Sessions and authentication are still outstanding for the client Connection
Server. The authenticated agent relay and provider adapters live in
`network_a2a/`; the Connection Server reaches them through the Agent Gateway
in `src/agents/gateway.py`. See below and [Scope](#scope).

## Multi-device agent networking

The native Multiplayer AI desktop app starts its local relay, creates a private
device identity, and connects automatically. Configure model providers, invite
devices, join shared relays, and chat with agents from its desktop workspace.
See the [desktop guide](DESKTOP_GUIDE.md) to get started.

The desktop app runs in a dark, a light, or a Miku theme. With nothing stored
it follows your operating system's light or dark preference; the **Theme**
setting on the Settings page switches immediately and the choice is remembered.
Selecting "Follow system" again returns control to the OS.

Text contrast is enforced by the test suite rather than by eye: the pairings
that carry text are declared in `desktop_app/theme.py` and checked, so a theme
that measured badly could not be added. The Miku palette supplied for this work
had every text pairing between 1.09:1 and 2.00:1, so its colours are used as
accents over a neutral scale rather than as given.

The desktop app has a **Resources** page showing live Processor, Memory and Disk
use for this machine, plus per-core load. It resamples every two seconds, and
pauses while the page is off screen. **Settings** holds the theme, an animation
preference, the path to this device's data, and a way to reveal it in the file
manager.

Identity handling is written to recover rather than to strand you. Each device
holds a private token in the OS credential store, while the list of identities
lives in `settings.json`. If a token goes missing — a cleared keyring, a locked
store, settings restored on another machine — the app replaces that identity and
tells you at startup instead of refusing to open. Workspaces joined with the old
identity need a fresh invitation. To discard an identity deliberately, use
**Settings → Reset local identity**; it asks first, and stops and restarts
connected agents. An *unavailable* credential store is still reported as an
error, because a replacement token could not be written either. See
[recover a lost device identity](DESKTOP_GUIDE.md#recover-a-lost-device-identity).

The authenticated relay and reconnecting device client in `network_a2a/`
connect multiple laptops/desktops over LAN or the internet. They route requests
between local agents and can forward requests to a local A2A server.

See [setup and deployment instructions](NETWORK_SETUP.md) for credentials,
client commands, HTTPS/WSS deployment, and current limits. This relay uses a
custom transport; standard A2A discovery and streaming are not implemented.

Provider adapters support Ollama, Bionic GPT, OpenAI, Claude, Gemini, Groq,
DeepSeek, Mistral, OpenRouter, and custom OpenAI-compatible endpoints. See
[provider setup and examples](PROVIDER_ADAPTERS.md).

The Connection Server and the relay are separate applications with different
WebSocket message formats. The Agent Gateway translates between them, but they
still run on separate ports.

## Architecture

The client Connection Server follows this architecture:

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

The Connection Server reaches the agent layer through the Agent Gateway,
which authenticates to the relay in `network_a2a/` and calls
`POST /agents/{id}/invoke`. Agent-to-agent hops stay inside the relay, so
they never pass through the client layer.

### Layout

| Path | Responsibility |
|------|----------------|
| `src/server/app.py` | HTTP/WebSocket entry point, frame handling, logging |
| `src/server/connection_manager.py` | In-memory client registry and delivery |
| `src/routing/router.py` | The single routing decision point |
| `src/agents/gateway.py` | Agent Gateway: relay calls and error translation |
| `src/messages/envelope.py` | The client-facing message format |
| `src/errors.py` | Error codes and the error frame format |
| `scripts/demo_client.py` | Command-line client for manual testing |
| `network_a2a/server.py` | Authenticated multi-device agent relay |
| `network_a2a/client.py` | Reconnecting agent client and request correlation |
| `network_a2a/adapters/` | Model provider handlers |
| `network_a2a/__main__.py` | Agent client CLI and local A2A bridge |

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
- `agent` — sent to the agent through the relay

### Client to agent

Ask an agent by naming it as the destination. The reply comes back on the
same socket as a `response` envelope carrying the request's `messageId`
and `sessionId`:

```json
{
  "type": "response",
  "messageId": "msg-123",
  "source": { "id": "agent-A", "type": "agent" },
  "destination": { "id": "client-A", "type": "client" },
  "sessionId": "session-001",
  "timestamp": "2026-10-02T10:00:00Z",
  "payload": { "text": "Hello! How can I help?", "provider": "openai" }
}
```

Unlike client-to-client, an agent destination always answers — either a
`response` or an `error`.

The payload is forwarded to the agent unchanged. The provider adapters
already accept `{"text": ...}`, so no translation happens in the client
layer.

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
| `INVALID_MESSAGE` | Frame is not JSON, the envelope is malformed, or the agent id is invalid |
| `CLIENT_NOT_FOUND` | Destination client is not connected |
| `AGENT_NOT_FOUND` | No relay is configured on this server |
| `AGENT_UNAVAILABLE` | Agent is offline, unknown, outside the caller's group, or the relay is at capacity |
| `TIMEOUT` | The agent or relay did not answer in time |
| `A2A_ERROR` | The relay rejected this server's credentials or answered unusably |

`SESSION_NOT_FOUND` and `INTERNAL_ERROR` are defined; `INTERNAL_ERROR` is
the fallback for anything unexpected.

An unknown agent and an agent in another group both report
`AGENT_UNAVAILABLE` with the same wording. The relay returns one status for
both, and repeating it would let a client probe which agent ids exist in
other groups.

`messageId` is `null` when the frame could not be parsed far enough to
recover an id. Stack traces are never sent to clients.

A malformed frame does **not** close the connection: the server answers
with `INVALID_MESSAGE` and keeps reading.

## Trying client-to-agent messaging

The Connection Server needs to know where the relay is. Both variables are
optional: without them the server still runs and client-to-client messaging
still works, but agent destinations return `AGENT_NOT_FOUND`.

| Variable | Meaning |
|----------|---------|
| `A2A_RELAY_URL` | Base URL of the relay, e.g. `http://localhost:9100` |
| `A2A_RELAY_TOKEN` | This server's relay token, in the same group as the agents |
| `A2A_GATEWAY_TIMEOUT` | Seconds to wait for the relay (default `65`) |

Start the relay, then an agent, then the Connection Server. Each needs its own
token, so use a separate terminal for each so the variables do not leak
between processes:

**Terminal 1 — the relay**
```bash
$env:A2A_CREDENTIALS_FILE = "$PWD\credentials.json"
python -m uvicorn network_a2a.server:app_from_env --factory --port 9100 --workers 1
```

**Terminal 2 — the agent**
```bash
$env:A2A_TOKEN = "<agent-A's token>"
python -m network_a2a --server ws://localhost:9100/connect --provider openai --model gpt-4o
```

**Terminal 3 — the Connection Server**
```bash
$env:A2A_RELAY_URL = "http://localhost:9100"
$env:A2A_RELAY_TOKEN = "<the connection server's own token>"
python -m uvicorn src.server.app:app --host localhost --port 8000
```

Note the relay defaults to port 8000 in its own guide, which the Connection
Server also uses. Change one of them, as above, or the two will collide.

Credentials come from `python -m network_a2a.provision <agent-id> ...`,
which writes a `credentials.json` the relay reads. Give the Connection Server
its own identity in the same group as the agents, because the relay enforces
group isolation.

`A2A_TOKEN` and `A2A_RELAY_TOKEN` are deliberately different variables.
`A2A_TOKEN` is the identity a process presents *as an agent*; the Connection
Server is not an agent, so it uses a separate name.

## Tests

```bash
python -m pytest
```

Client Connection Server tests cover agent.md Tests 1–6 (server starts, health responds,
client connects, two clients stay connected, A → B delivers, unknown
destination errors), plus the envelope and routing contracts. The Agent
Gateway has unit tests for relay status translation, and end-to-end tests
that run a real relay, a real agent, and a real client together with nothing
mocked. Additional relay and adapter tests cover multi-device messaging,
reconnection, provider request formats, and safe failures.

Theme tests come in three layers. The palette tests need nothing extra: they
check that every theme styles the same set of widgets and that every colour
token exists in all of them. The contrast tests check each declared text
pairing against the ratio its class requires. The widget tests need `PySide6`
and build a real window headlessly, skipping themselves if it is absent.

Window sizing is tested as plain arithmetic against common screen shapes, so
a request that does not fit a 1366x768 laptop fails the build rather than
opening clipped.

Credential handling is tested for the failure that matters: a token that has
disappeared is replaced, an unavailable store is still an error, and one bad
identity does not discard the good ones.

Note that `--data-dir` redirects `settings.json` but **not** the OS credential
store, so hand-running `--check-startup` leaves real entries in the system
keyring. The tests use an in-memory vault to avoid exactly that; clean up
afterwards if you run the app directly.

Run a subset:

```bash
python -m pytest tests/test_desktop_pages.py -v
```

### Desktop dependencies

The Connection Server and the relay need only what `environment.yml` declares.
The **desktop app** additionally needs Qt and an OS credential store, which are
not in that file:

```bash
python -m pip install "PySide6>=6.8,<7" "keyring>=25,<27"
```

Prefer that to `pip install -r requirements-desktop.txt`, which pins
`starlette`, `uvicorn` and `websockets` to versions older than the ones in
`environment.yml` and would downgrade them in the shared environment.

Known warnings: Starlette's `TestClient` reports that `httpx` is deprecated
in favour of `httpx2`; `websockets` reports an un-awaited `aclose` when a
client loop is torn down mid-iteration. Both cosmetic.

## Scope

The following remain unimplemented in the client Connection Server (`src/`):

- Asynchronous agent responses — an agent reply is currently awaited inline
- Sessions
- Authentication — client ids are self-declared and unverified
- Persistence; the registry is in-memory and resets on restart

An agent request is relayed synchronously, so a client waits for the answer
before its socket produces the next frame. agent.md section 15 asks for the
design to tolerate asynchronous replies; `AgentGateway.invoke` is async and
returns the payload, so a push-based path can be added beside it, but it is
not built.

The separate `network_a2a/` relay already provides per-agent authentication,
group isolation, request/response correlation, and reconnecting clients. It
keeps connections in memory and requires a single worker and replica; it has
no session management or durable offline queue.

Two client Connection Server behaviours worth knowing about:

- **Reconnecting with an existing client id** replaces the registry entry
  but does not close the previous socket, which is left orphaned.
- **`connection_id`** is server-internal and never exposed to clients.

## Design notes

`agent.md` in the repository root holds the full specification these
decisions come from, including the phase plan, acceptance criteria, and
the reasoning behind each boundary.
