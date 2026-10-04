"""Shared message contract, configuration, and bounded provider HTTP calls."""
import asyncio
import json
import math
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx

from ..content import MAX_MESSAGE_BYTES, MAX_TEXT_BYTES, images, text_content, validate_content
from ..web_search import AUTO_SEARCH, SEARCH_EVIDENCE, SearchError, SearXNG, requested_query, validate_search_url


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
    vision: bool = False
    web_search: str = "off"
    searxng_url: str = ""
    searxng_allow_insecure: bool = False

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
        if type(self.vision) is not bool or type(self.searxng_allow_insecure) is not bool:
            raise ValueError("Image support and SearXNG HTTP access must be boolean settings")
        if self.web_search not in ("off", "auto", "always"):
            raise ValueError("Choose Off, Automatic, or Always for web search")
        if self.web_search != "off":
            validate_search_url(self.searxng_url, self.searxng_allow_insecure)


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
        raise ProviderError("invalid_input", "Provide 1..100 messages")
    clean = []
    for message in messages:
        if (not isinstance(message, dict) or set(message) != {"role", "content"}
                or message["role"] not in ("user", "assistant")):
            raise ProviderError("invalid_input", "Messages require user/assistant role and valid content")
        try:
            clean.append({"role": message["role"], "content": validate_content(message["content"], message["role"])})
        except ValueError as exc:
            raise ProviderError("invalid_input", str(exc)) from None
    if clean[0]["role"] != "user" or clean[-1]["role"] != "user":
        raise ProviderError("invalid_input", "History must start and end with a user message")
    text_bytes = sum(len(text_content(message["content"]).encode()) for message in clean)
    if text_bytes > MAX_TEXT_BYTES or len(json.dumps(clean).encode()) > MAX_MESSAGE_BYTES:
        raise ProviderError("invalid_input", "Message history is too large")
    return clean


class HTTPAdapter:
    def __init__(self, config: ProviderConfig, http: httpx.AsyncClient):
        self.config = config
        self.http = http
        self.slots = asyncio.Semaphore(config.concurrency)

    def system_instructions(self, instructions=None):
        return "\n\n".join(text for text in (self.config.system_prompt, instructions) if text)

    def messages_with_system(self, messages, instructions=None):
        system = self.system_instructions(instructions)
        if system:
            return [{"role": "system", "content": system}, *messages]
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
        if not self.config.vision and any(images(message["content"]) for message in messages):
            raise ProviderError("invalid_input", "Choose a vision-capable model and enable image support in its settings")
        # Bound queueing plus HTTP time; this also protects against slow streams.
        try:
            async with asyncio.timeout(self.config.timeout):
                async with self.slots:
                    text, usage, finish_reason, sources = await self.generate_with_search(messages)
        except TimeoutError:
            raise ProviderError("timeout", f"{self.config.provider} request timed out") from None
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("invalid_response", "Provider response does not match its text API") from None
        if not isinstance(text, str) or not text.strip():
            raise ProviderError("no_text", "Provider returned no text (blocked, tool-only, or token budget exhausted)")
        result = {"text": text, "provider": self.config.provider, "model": self.config.model,
                  "usage": usage if isinstance(usage, dict) else {}, "finish_reason": finish_reason}
        if sources:
            result["sources"] = sources
            missing = [source for source in sources if source["url"] not in text]
            if missing:
                result["text"] += "\n\nSources:\n" + "\n".join(f"- [{source['title'].replace('[', '').replace(']', '')}]({source['url']})" for source in missing)
        if len(json.dumps(result).encode()) > 240000:
            raise ProviderError("invalid_response", "Provider output is too large for the relay")
        return result

    async def generate_with_search(self, messages):
        mode = self.config.web_search
        if mode == "off":
            return (*await self.generate(messages), [])
        query = text_content(messages[-1]["content"], include_documents=False)[:500].strip()
        if mode == "auto":
            initial = await self.generate(messages, AUTO_SEARCH)
            query = requested_query(initial[0])
            if query is None:
                return (*initial, [])
        try:
            sources = await SearXNG(self.http, self.config.searxng_url, self.config.searxng_allow_insecure).search(query)
        except SearchError as exc:
            raise ProviderError("web_search", str(exc)) from None
        evidence = SEARCH_EVIDENCE + "\n\n" + (json.dumps(sources, ensure_ascii=False) if sources else "No useful results were found. State this limitation.")
        text, usage, reason = await self.generate(messages, evidence)
        return text, usage, reason, sources

    async def generate(self, messages, instructions=None):
        raise NotImplementedError
