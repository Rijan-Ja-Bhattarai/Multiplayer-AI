"""A minimal command-line client for the Connection Server.

Run two of these in separate terminals to see client-to-client
messaging working end to end::

    python scripts/demo_client.py client-A client-B "Hello Client B"
    python scripts/demo_client.py client-B client-A "Hi Client A"

Arguments:
    client_id      This client's own id, used in the WebSocket path.
    destination    Id of the client to send to. It must already be
                   connected in the other terminal.
    text           The message body.

Optional:
    --host HOST    Server host (default: localhost, or $CONNECTION_HOST)
    --port PORT    Server port (default: 8000, or $CONNECTION_PORT)

On a successful send nothing is printed about delivery: the server
returns no acknowledgement, so the destination's terminal is where a
delivered message shows up. Errors are printed as they arrive.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import websockets
from websockets.exceptions import ConnectionClosed

DEFAULT_HOST = os.environ.get("CONNECTION_HOST", "localhost")
DEFAULT_PORT = int(os.environ.get("CONNECTION_PORT", "8000"))


def parse_args(argv: list[str]) -> tuple[str, str, str, str, int]:
    """Parse the positional arguments and optional --host/--port flags."""
    host, port = DEFAULT_HOST, DEFAULT_PORT
    positional: list[str] = []

    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--host" and index + 1 < len(argv):
            host = argv[index + 1]
            index += 2
        elif arg == "--port" and index + 1 < len(argv):
            port = int(argv[index + 1])
            index += 2
        else:
            positional.append(arg)
            index += 1

    if len(positional) != 3:
        raise SystemExit(
            "usage: python scripts/demo_client.py "
            "<client_id> <destination_id> <text> [--host HOST] [--port PORT]"
        )

    return positional[0], positional[1], positional[2], host, port


def build_envelope(source: str, destination: str, text: str) -> str:
    """Build a client-to-client message envelope in the wire format."""
    return json.dumps(
        {
            "messageId": f"msg-{uuid.uuid4()}",
            "type": "message",
            "source": {"id": source, "type": "client"},
            "destination": {"id": destination, "type": "client"},
            "sessionId": "session-001",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "payload": {"text": text},
        }
    )


async def run(client_id: str, destination: str, text: str, host: str, port: int) -> None:
    uri = f"ws://{host}:{port}/ws/{client_id}"
    print(f"[{client_id}] connecting to {uri}")

    async with websockets.connect(uri) as socket:
        print(f"[{client_id}] connected. Listening for messages.")
        print(f"[{client_id}] sending to {destination}: {text!r}")
        await socket.send(build_envelope(client_id, destination, text))
        print(f"[{client_id}] sent (no acknowledgement is returned on success)")

        # Stay connected so replies arrive and later errors are shown.
        # A closed socket is an ordinary way to stop, not a crash.
        try:
            async for raw in socket:
                frame = json.loads(raw)
                if frame.get("type") == "error":
                    print(
                        f"[{client_id}] ERROR {frame['code']}: "
                        f"{frame['message']} (messageId={frame['messageId']})"
                    )
                else:
                    print(
                        f"[{client_id}] <- {frame['source']['id']}: "
                        f"{frame['payload'].get('text')}"
                    )
        except ConnectionClosed:
            print(f"[{client_id}] connection closed by server")


def main() -> None:
    client_id, destination, text, host, port = parse_args(sys.argv[1:])
    try:
        asyncio.run(run(client_id, destination, text, host, port))
    except KeyboardInterrupt:
        print(f"\n[{client_id}] disconnected")


if __name__ == "__main__":
    main()
