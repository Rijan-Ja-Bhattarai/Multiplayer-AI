import asyncio
import contextlib
import io
import json
import unittest

import httpx

from network_a2a.__main__ import build_parser
from network_a2a.adapters import PROVIDERS, ProviderConfig, ProviderError, create_adapter, load_config


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    def config(self, provider, **kwargs):
        return load_config(provider, model="test-model", environ={
            PROVIDERS[provider].key_env: "private-provider-key",
            PROVIDERS[provider].base_env: "https://provider.example/v1",
        }, **kwargs)

    async def run_adapter(self, provider, response, payload=None, config=None, status=200):
        seen = []
        def transport(request):
            seen.append(request)
            return httpx.Response(status, json=response)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            adapter = create_adapter(config or self.config(provider), http)
            result = await adapter(payload if payload is not None else {"text": "Hello"}, "remote-agent")
        return result, seen[0]

    async def test_ollama_native_nonstreaming_and_system(self):
        result, request = await self.run_adapter("ollama", {"message": {"content": "Local reply"},
                                                               "prompt_eval_count": 5, "eval_count": 2, "done_reason": "stop"},
                                                 config=self.config("ollama", system_prompt="Be concise", max_tokens=40))
        body = json.loads(request.content)
        self.assertEqual(request.url.path, "/v1/api/chat")
        self.assertFalse(body["stream"])
        self.assertEqual(body["options"]["num_predict"], 40)
        self.assertEqual(body["messages"][0], {"role": "system", "content": "Be concise"})
        self.assertEqual(result["text"], "Local reply")
        self.assertEqual(result["usage"]["output_tokens"], 2)

    async def test_all_openai_compatible_presets(self):
        for provider in ("bionic", "groq", "deepseek", "mistral", "openrouter", "openai-compatible"):
            with self.subTest(provider=provider):
                result, request = await self.run_adapter(provider, {
                    "choices": [{"message": {"content": "Reply"}, "finish_reason": "stop"}],
                    "usage": {"completion_tokens": 4},
                })
                self.assertEqual(request.url.path, "/v1/chat/completions")
                self.assertEqual(request.headers["authorization"], "Bearer private-provider-key")
                self.assertEqual(json.loads(request.content)["model"], "test-model")
                self.assertEqual(result["provider"], provider)
                self.assertEqual(result["text"], "Reply")

    async def test_openai_responses_history_and_refusal_not_returned_as_text(self):
        history = {"messages": [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"},
                                {"role": "user", "content": "Continue"}]}
        result, request = await self.run_adapter("openai", {"status": "completed", "output": [
            {"type": "reasoning", "summary": []}, {"type": "message", "content": [
                {"type": "output_text", "text": "one"}, {"type": "output_text", "text": "two"}]}]},
            payload=history, config=self.config("openai", system_prompt="System", max_tokens=500))
        body = json.loads(request.content)
        self.assertEqual(request.url.path, "/v1/responses")
        self.assertEqual(body["input"], history["messages"])
        self.assertEqual(body["instructions"], "System")
        self.assertFalse(body["store"])
        self.assertEqual(body["max_output_tokens"], 500)
        self.assertEqual(result["text"], "onetwo")
        with self.assertRaises(ProviderError) as error:
            await self.run_adapter("openai", {"status": "completed", "output": [{"type": "message", "content": [
                {"type": "refusal", "refusal": "Refused"}]}]})
        self.assertEqual(error.exception.code, "no_text")

    async def test_anthropic_system_headers_and_text_blocks(self):
        result, request = await self.run_adapter("anthropic", {"content": [
            {"type": "thinking", "thinking": "private"}, {"type": "text", "text": "Claude reply"}],
            "stop_reason": "end_turn"}, config=self.config("anthropic", system_prompt="System"))
        body = json.loads(request.content)
        self.assertEqual(request.headers["x-api-key"], "private-provider-key")
        self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
        self.assertNotIn("authorization", request.headers)
        self.assertEqual(body["system"], "System")
        self.assertEqual(body["messages"], [{"role": "user", "content": "Hello"}])
        self.assertEqual(result["text"], "Claude reply")

    async def test_gemini_headers_history_system_and_thought_filter(self):
        result, request = await self.run_adapter("gemini", {"candidates": [{"content": {"parts": [
            {"text": "hidden", "thought": True}, {"text": "Gemini reply"}]}, "finishReason": "STOP"}],
            "usageMetadata": {"candidatesTokenCount": 3}}, config=self.config("gemini", system_prompt="System"),
            payload={"messages": [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"},
                                   {"role": "user", "content": "Continue"}]})
        self.assertEqual(request.url.path, "/v1/models/test-model:generateContent")
        self.assertEqual(request.headers["x-goog-api-key"], "private-provider-key")
        self.assertNotIn("key=", str(request.url))
        body = json.loads(request.content)
        self.assertEqual(body["contents"][1]["role"], "model")
        self.assertEqual(body["systemInstruction"]["parts"][0]["text"], "System")
        self.assertEqual(result["text"], "Gemini reply")

    async def test_status_errors_are_safe_and_not_retried(self):
        for status, code in ((401, "authentication"), (403, "authentication"), (429, "rate_limit"), (500, "http_error"), (302, "http_error")):
            with self.subTest(status=status):
                calls = []
                def transport(request):
                    calls.append(request)
                    return httpx.Response(status, json={"error": "PRIVATE SECRET FROM PROVIDER"})
                async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
                    with self.assertRaises(ProviderError) as error:
                        await create_adapter(self.config("bionic"), http)({"text": "Hi"}, "a")
                self.assertEqual(error.exception.code, code)
                self.assertNotIn("SECRET", str(error.exception))
                self.assertEqual(len(calls), 1)

    async def test_malformed_blocked_and_empty_responses(self):
        for provider, response in (("ollama", {}), ("bionic", {"choices": []}),
                                   ("gemini", {"promptFeedback": {"blockReason": "SAFETY"}}),
                                   ("anthropic", {"content": [{"type": "tool_use"}]}),
                                   ("openai", {"status": "failed", "output": []})):
            with self.subTest(provider=provider):
                with self.assertRaises(ProviderError):
                    await self.run_adapter(provider, response)

    async def test_remote_cannot_override_credentials_model_or_system(self):
        for payload in ({"text": "Hi", "model": "expensive"}, {"text": "Hi", "base_url": "http://malicious"},
                        {"messages": [{"role": "system", "content": "Override"}]},
                        {"text": ""}, {"messages": []}, {"messages": [{"role": "user", "content": ["image"]}]}):
            with self.subTest(payload=payload):
                with self.assertRaises(ProviderError) as error:
                    await self.run_adapter("ollama", {}, payload)
                self.assertEqual(error.exception.code, "invalid_input")

    async def test_timeout_connection_error_and_invalid_json(self):
        for issue, code in ((httpx.ReadTimeout("SECRET"), "timeout"), (httpx.ConnectError("SECRET"), "connection"), (None, "invalid_response")):
            def transport(request):
                if issue:
                    raise issue
                return httpx.Response(200, content=b"not json")
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
                with self.assertRaises(ProviderError) as error:
                    await create_adapter(self.config("ollama"), http)("Hi", "a")
            self.assertEqual(error.exception.code, code)
            self.assertNotIn("SECRET", str(error.exception))

    async def test_concurrency_limit_and_queue_timeout(self):
        active = peak = 0
        async def transport(request):
            nonlocal active, peak
            active += 1
            peak = max(active, peak)
            try:
                await asyncio.sleep(.02)
                return httpx.Response(200, json={"message": {"content": "Reply"}})
            finally:
                active -= 1
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            adapter = create_adapter(self.config("ollama", concurrency=2), http)
            await asyncio.gather(*(adapter("Hi", "a") for _ in range(5)))
            self.assertEqual(peak, 2)
            adapter = create_adapter(self.config("ollama", timeout=.005), http)
            with self.assertRaises(ProviderError) as error:
                await adapter("Hi", "a")
            self.assertEqual(error.exception.code, "timeout")

    def test_configuration_requires_model_key_and_explicit_custom_endpoints(self):
        with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
            load_config("openai", model="test", environ={})
        with self.assertRaisesRegex(ValueError, "Choose a model"):
            load_config("ollama", environ={})
        with self.assertRaisesRegex(ValueError, "BIONIC_BASE_URL"):
            load_config("bionic", model="test", environ={"BIONIC_API_KEY": "key"})
        config = load_config("ollama", environ={"A2A_MODEL": "local", "OLLAMA_HOST": "http://localhost:1234"})
        self.assertEqual(config.base_url, "http://localhost:1234")
        self.assertIsNone(config.api_key)
        self.assertNotIn("private-provider-key", repr(self.config("openai")))
        for url in ("http://remote.example/v1", "https://key:secret@example.com", "https://example.com/?key=secret"):
            with self.assertRaises(ValueError):
                ProviderConfig("ollama", "test", url)
        with self.assertRaises(ValueError):
            self.config("ollama", timeout=float("nan"))

    def test_cli_modes_are_exclusive(self):
        parser = build_parser()
        args = parser.parse_args(["--provider", "ollama", "--model", "local", "--server", "ws://localhost/connect"])
        self.assertEqual(args.provider, "ollama")
        self.assertEqual(args.model, "local")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["--provider", "ollama", "--local-a2a-url", "http://localhost/"])

    async def test_provider_and_relay_output_limits(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
                200, content=b"x" * 2097153))) as http:
            with self.assertRaises(ProviderError) as error:
                await create_adapter(self.config("ollama"), http)("Hi", "a")
        self.assertEqual(error.exception.code, "invalid_response")
        with self.assertRaises(ProviderError) as error:
            await self.run_adapter("ollama", {"message": {"content": "x" * 240000}})
        self.assertEqual(error.exception.code, "invalid_response")


if __name__ == "__main__":
    unittest.main()
