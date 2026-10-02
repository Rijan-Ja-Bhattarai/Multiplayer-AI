"""Shared text contract, configuration, and bounded provider HTTP calls."""
import asyncio
import json
import math
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx


class ProviderError(Exception):
    """Safe to report to peers: never contains response bodies or credentials."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    base_url: str
    api_key: str | None = field(default=None, repr=False)
    system_prompt: str | None = None
    max_tokens: int = 1024
    timeout: float = 55
    concurrency: int = 4
    allow_insecure: bool = False

    def __post_init__(self):
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("Choose a model with --model or A2A_MODEL")
        if not isinstance(self.base_url, str):
            raise ValueError("Provider base URL is required")
        url = urlsplit(self.base_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("Provider base URL must be http(s) without credentials, query, or fragment")
        if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1") and not self.allow_insecure:
            raise ValueError("Remote provider URLs require HTTPS; allow_insecure is for trusted LANs only")
        if type(self.max_tokens) is not int or not 1 <= self.max_tokens <= 131072:
            raise ValueError("max_tokens must be an integer in 1..131072")
        if not math.isfinite(self.timeout) or not 0 < self.timeout <= 300:
            raise ValueError("Provider timeout must be in 0..300 seconds")
        if type(self.concurrency) is not int or not 1 <= self.concurrency <= 32:
            raise ValueError("Provider concurrency must be in 1..32")
        if self.system_prompt is not None and not isinstance(self.system_prompt, str):
            raise ValueError("System prompt must be text")


def parse_messages(payload):
    """Accept text or caller-supplied history, never remote provider settings."""
    if isinstance(payload, str):
        messages = [{"role": "user", "content": payload}]
    elif isinstance(payload, dict) and set(payload) == {"text"}:
        messages = [{"role": "user", "content": payload["text"]}]
    elif isinstance(payload, dict) and set(payload) == {"messages"}:
        messages = payload["messages"]
    else:
        raise ProviderError("invalid_input", "Send text, {text: ...}, or {messages: [...]} only")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 100:
        raise ProviderError("invalid_input", "Provide 1..100 text messages")
    clean = []
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] not in ("user", "assistant")
                or not isinstance(message["content"], str) or not message["content"].strip()):
            raise ProviderError("invalid_input", "Messages require user/assistant role and nonempty text content")
        clean.append(dict(message))
    if clean[0]["role"] != "user" or clean[-1]["role"] != "user":
        raise ProviderError("invalid_input", "History must start and end with a user message")
    if len(json.dumps(clean).encode()) > 200000:
        raise ProviderError("invalid_input", "Message history is too large")
    return clean


class HTTPAdapter:
    def __init__(self, config: ProviderConfig, http: httpx.AsyncClient):
        self.config = config
        self.http = http
        self.slots = asyncio.Semaphore(config.concurrency)

    def messages_with_system(self, messages):
        if self.config.system_prompt:
            return [{"role": "system", "content": self.config.system_prompt}, *messages]
        return messages

    def headers(self):
        return {"Authorization": f"Bearer {self.config.api_key}"} if self.config.api_key else {}

    async def post(self, path, body, headers=None):
        url = self.config.base_url.rstrip("/") + "/" + path.lstrip("/")
        try:
            async with self.http.stream("POST", url, json=body, headers=headers if headers is not None else self.headers(),
                                        timeout=self.config.timeout, follow_redirects=False) as response:
                status = response.status_code
                if status >= 300:
                    code = "authentication" if status in (401, 403) else "rate_limit" if status == 429 else "http_error"
                    raise ProviderError(code, f"{self.config.provider} returned HTTP {status}")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 2097152:
                        raise ProviderError("invalid_response", "Provider response exceeds 2 MiB")
                result = json.loads(data)
                if not isinstance(result, dict) or result.get("error"):
                    raise ProviderError("invalid_response", "Provider returned an invalid or error response")
                return result
        except httpx.TimeoutException:
            raise ProviderError("timeout", f"{self.config.provider} request timed out") from None
        except httpx.HTTPError:
            raise ProviderError("connection", f"Could not reach {self.config.provider}") from None
        except (ValueError, UnicodeError):
            raise ProviderError("invalid_response", "Provider returned invalid JSON") from None

    async def __call__(self, payload, sender):
        messages = parse_messages(payload)
        # Bound queueing plus HTTP time; this also protects against slow streams.
        try:
            async with asyncio.timeout(self.config.timeout):
                async with self.slots:
                    text, usage, finish_reason = await self.generate(messages)
        except TimeoutError:
            raise ProviderError("timeout", f"{self.config.provider} request timed out") from None
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("invalid_response", "Provider response does not match its text API") from None
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("no_text", "Provider returned no text (blocked, tool-only, or token budget exhausted)")
        result = {"text": text, "provider": self.config.provider, "model": self.config.model,
                  "usage": usage if isinstance(usage, dict) else {}, "finish_reason": finish_reason}
        if len(json.dumps(result).encode()) > 240000:
            raise ProviderError("invalid_response", "Provider output is too large for the relay")
        return result

    async def generate(self, messages):
        raise NotImplementedError
