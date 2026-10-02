import argparse
import asyncio
import contextlib
import json
import os

import httpx

from .client import AgentClient
from .adapters import PROVIDERS, create_adapter, load_config


async def main(args):
    token = os.environ.get("A2A_TOKEN")
    if not token:
        raise ValueError("Set A2A_TOKEN to this agent's token")
    config = None
    if args.provider:
        config = load_config(args.provider, model=args.model, base_url=args.provider_base_url,
                             api_key_env=args.api_key_env, system_prompt=args.system_prompt,
                             max_tokens=args.max_tokens, timeout=args.provider_timeout,
                             concurrency=args.provider_concurrency, allow_insecure=args.allow_insecure_provider)
    async with httpx.AsyncClient(timeout=55) as http:
        adapter = create_adapter(config, http) if config else None
        async def handler(payload, source):
            if adapter:
                return await adapter(payload, source)
            if args.local_a2a_url:
                # Fixed operator-provided endpoint; peers cannot choose a URL.
                headers = {}
                if os.getenv("A2A_LOCAL_TOKEN"):
                    headers["Authorization"] = f"Bearer {os.environ['A2A_LOCAL_TOKEN']}"
                response = await http.post(args.local_a2a_url, json=payload, headers=headers)
                response.raise_for_status()
                if len(response.content) > 262144:
                    raise ValueError("Local response too large")
                return response.json()
            return {"agent": client.agent_id, "from": source, "echo": payload}

        client = AgentClient(args.server, token, handler, args.allow_insecure)
        runner = asyncio.create_task(client.run())
        try:
            if args.to:
                request = asyncio.create_task(client.request(args.to, json.loads(args.payload)))
                done, _ = await asyncio.wait([request, runner], return_when=asyncio.FIRST_COMPLETED)
                if runner in done:
                    request.cancel()
                    await asyncio.gather(request, return_exceptions=True)
                    await runner
                print(json.dumps(await request, indent=2))
            else:
                mode = f"{config.provider} / {config.model}" if config else "local A2A bridge" if args.local_a2a_url else "echo"
                print(f"Connecting agent ({mode}); Ctrl+C stops.", flush=True)
                await runner
        finally:
            runner.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runner


def build_parser():
    parser = argparse.ArgumentParser(description="Connect a local agent to the multi-device relay")
    parser.add_argument("--server", help="wss://your-domain/connect")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--local-a2a-url", help="Forward payloads to an existing local A2A JSON-RPC endpoint")
    mode.add_argument("--provider", choices=PROVIDERS, help="Use a model provider as this device's agent")
    parser.add_argument("--list-providers", action="store_true", help="List supported providers without connecting")
    parser.add_argument("--model", help="Provider model ID; defaults to A2A_MODEL")
    parser.add_argument("--provider-base-url", help="Override provider API root (include /v1 where appropriate)")
    parser.add_argument("--api-key-env", help="Read provider key from this environment variable")
    parser.add_argument("--system-prompt", help="Local system instructions; defaults to A2A_SYSTEM_PROMPT")
    parser.add_argument("--max-tokens", type=int, default=1024, help="Maximum output token budget")
    parser.add_argument("--provider-timeout", type=float, default=55, help="Seconds including provider queue wait")
    parser.add_argument("--provider-concurrency", type=int, default=4, help="Parallel provider calls on this device")
    parser.add_argument("--allow-insecure-provider", action="store_true", help="Allow plaintext provider HTTP on a trusted LAN")
    parser.add_argument("--to", help="Send one request to this agent ID")
    parser.add_argument("--payload", default='{"text":"Hello from another device"}', help="JSON payload")
    parser.add_argument("--allow-insecure", action="store_true", help="Allow plaintext ws:// on a trusted LAN")
    return parser

if __name__ == "__main__":
    parser = build_parser()
    args = parser.parse_args()
    if args.list_providers:
        for name, spec in PROVIDERS.items():
            print(f"{name}: key={spec.key_env}, base_url={spec.base_env}, default={spec.base_url or 'configure your endpoint'}")
        parser.exit()
    if not args.server:
        parser.error("--server is required unless using --list-providers")
    try:
        asyncio.run(main(args))
    except KeyboardInterrupt:
        pass
    except ValueError as exc:
        parser.error(str(exc))
