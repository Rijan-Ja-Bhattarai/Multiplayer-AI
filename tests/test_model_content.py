"""Verify actual provider payloads, search decisions, and attachment boundaries."""
import base64
import json
import unittest

import httpx

from network_a2a.adapters import ProviderConfig, ProviderError, create_adapter
from network_a2a.content import MAX_MESSAGE_BYTES, content_summary, validate_content
from network_a2a.persistence import model_context
from network_a2a.web_search import OllamaSearch, SearXNG, SearchError, validate_search_url


IMAGE = {"type": "image", "name": "diagram.png", "mime_type": "image/png",
         "data": base64.b64encode(b"\x89PNG\r\n\x1a\nfixture").decode()}
CONTENT = [{"type": "text", "text": "Explain these files"},
           {"type": "document", "name": "report.pdf", "text": "Page 1\nRevenue is 123."}, IMAGE]


class ContentTests(unittest.TestCase):
    def test_invalid_content_cannot_supply_urls_or_settings(self):
        for content in ([{**IMAGE, "data": "invalid"}], [{**IMAGE, "data": "aHR0cHM6Ly9leGFtcGxlLmNvbQ=="}],
                        [{**IMAGE, "url": "http://internal"}], [{"type": "document", "name": "r", "text": ""}],
                        [{"type": "text", "text": "Hi", "model": "other"}]):
            with self.subTest(content=content), self.assertRaises(ValueError):
                validate_content(content)
        with self.assertRaises(ValueError):
            validate_content(CONTENT, "assistant")

    def test_summary_omits_private_document_text_and_binary(self):
        summary = content_summary(CONTENT)
        self.assertIn("Explain these files", summary)
        self.assertIn("report.pdf", summary)
        self.assertNotIn("Revenue", summary)
        self.assertNotIn(IMAGE["data"], summary)

    def test_context_preserves_large_attachment_and_prunes_old_turns(self):
        content = [CONTENT[0], {**IMAGE, "data": base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"x" * 260000).decode()}]
        latest = {"role": "user", "content": content}
        self.assertEqual(model_context([latest]), [latest])
        old = {"role": "user", "content": "a" * 196000}
        context = model_context([old, {"role": "assistant", "content": "Done"},
                                 {"role": "user", "content": CONTENT}])
        self.assertEqual(context, [{"role": "user", "content": CONTENT}])
        with self.assertRaises(ValueError):
            validate_content([{**IMAGE, "data": "A" * (MAX_MESSAGE_BYTES + 1)}])


class ModelContentTests(unittest.IsolatedAsyncioTestCase):
    def config(self, provider="bionic", **settings):
        return ProviderConfig(provider, "test-model", "https://provider.example/v1", api_key="private-key", **settings)

    async def test_multimodal_formats_for_all_provider_apis(self):
        responses = {
            "ollama": {"message": {"content": "Read files"}},
            "openai": {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "Read files"}]}]},
            "anthropic": {"content": [{"type": "text", "text": "Read files"}]},
            "gemini": {"candidates": [{"content": {"parts": [{"text": "Read files"}]}}]},
            "bionic": {"choices": [{"message": {"content": "Read files"}}]},
        }
        for provider, response in responses.items():
            with self.subTest(provider=provider):
                seen = []
                def transport(request):
                    seen.append(json.loads(request.content))
                    return httpx.Response(200, json=response)
                async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
                    result = await create_adapter(self.config(provider, vision=True), http)(
                        {"messages": [{"role": "user", "content": CONTENT}]}, "peer")
                body = seen[0]
                self.assertEqual(result["text"], "Read files")
                self.assertIn("Revenue is 123.", json.dumps(body))
                if provider == "ollama":
                    self.assertEqual(body["messages"][0]["images"], [IMAGE["data"]])
                elif provider == "openai":
                    self.assertEqual(body["input"][0]["content"][-1],
                        {"type": "input_image", "image_url": "data:image/png;base64," + IMAGE["data"]})
                elif provider == "anthropic":
                    self.assertEqual(body["messages"][0]["content"][-1]["source"],
                        {"type": "base64", "media_type": "image/png", "data": IMAGE["data"]})
                elif provider == "gemini":
                    self.assertEqual(body["contents"][0]["parts"][-1]["inlineData"],
                        {"mimeType": "image/png", "data": IMAGE["data"]})
                else:
                    self.assertEqual(body["messages"][0]["content"][-1]["image_url"]["url"],
                        "data:image/png;base64," + IMAGE["data"])

    async def test_vision_rejection_happens_before_provider_call(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: self.fail("Provider should not be called"))) as http:
            with self.assertRaisesRegex(ProviderError, "vision-capable"):
                await create_adapter(self.config(), http)({"messages": [{"role": "user", "content": CONTENT}]}, "peer")

    async def search_answer(self, mode, initial, status=200):
        calls = []
        def transport(request):
            calls.append(request)
            if request.url.host == "search.example":
                self.assertNotIn("authorization", request.headers)
                return httpx.Response(status, json={"results": [
                    {"title": "Official facts", "url": "https://source.example/facts", "content": "New findings"},
                    {"title": "bad", "url": "file:///private", "content": "ignore"}]})
            reply = initial if len(calls) == 1 and mode == "auto" else "Answer from the evidence"
            return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            adapter = create_adapter(self.config(web_search=mode, searxng_url="https://search.example", system_prompt="Use plain English"), http)
            result = await adapter({"messages": [{"role": "user", "content": CONTENT[:2]}]}, "peer")
        return result, calls

    async def test_automatic_search_answers_known_facts_without_search(self):
        result, calls = await self.search_answer("auto", "Known answer")
        self.assertEqual(len(calls), 1)
        self.assertEqual(result["text"], "Known answer")
        self.assertNotIn("sources", result)

    async def test_automatic_search_requests_results_then_answers_with_sources(self):
        result, calls = await self.search_answer("auto", '{"web_search_query":"latest public findings"}')
        self.assertEqual([request.url.host for request in calls], ["provider.example", "search.example", "provider.example"])
        self.assertEqual(calls[1].url.params["q"], "latest public findings")
        self.assertEqual(calls[1].url.params["format"], "json")
        final = json.loads(calls[-1].content)
        self.assertIn("New findings", final["messages"][0]["content"])
        self.assertIn("Use plain English", final["messages"][0]["content"])
        self.assertIn("Revenue is 123.", json.dumps(final["messages"][-1]))
        self.assertEqual(len(result["sources"]), 1)
        self.assertIn("[Official facts](https://source.example/facts)", result["text"])

    async def test_always_search_excludes_private_document_content_from_query(self):
        result, calls = await self.search_answer("always", "unused")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0].url.params["q"], "Explain these files")
        self.assertNotIn("Revenue", str(calls[0].url))
        self.assertIn("sources", result)

    async def test_search_failure_does_not_claim_an_answer(self):
        with self.assertRaisesRegex(ProviderError, "Enable the JSON format") as error:
            await self.search_answer("auto", '{"web_search_query":"fresh facts"}', status=403)
        self.assertEqual(error.exception.code, "web_search")

    async def test_search_failure_bodies_are_not_exposed(self):
        """Verify search failures never expose the remote response body."""
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500, text="SECRET"))) as http:
            with self.assertRaises(SearchError) as error:
                await SearXNG(http, "https://search.example").search("test")
        self.assertNotIn("SECRET", str(error.exception))

    async def test_hosted_search_works_with_other_model_providers_and_separate_keys(self):
        """Verify hosted search works across providers without mixing credentials or attachments."""
        calls = []
        def transport(request):
            """Simulate model and search endpoints while checking their separate requests and keys."""
            calls.append(request)
            if request.url.host == "ollama.com":
                self.assertEqual(str(request.url), "https://ollama.com/api/web_search")
                self.assertEqual(request.method, "POST")
                self.assertEqual(request.headers["authorization"], "Bearer search-only-key")
                self.assertEqual(json.loads(request.content), {"query": "latest public facts", "max_results": 5})
                return httpx.Response(200, json={"results": [
                    {"title": "Official facts", "url": "https://source.example/facts", "content": "Fresh evidence"}]})
            self.assertEqual(request.headers["authorization"], "Bearer private-key")
            self.assertNotIn("search-only-key", request.content.decode())
            reply = '{"web_search_query":"latest public facts"}' if len(calls) == 1 else "Answer from search"
            return httpx.Response(200, json={"choices": [{"message": {"content": reply}}]})
        config = self.config(web_search="auto", search_provider="ollama", search_api_key="search-only-key")
        self.assertNotIn("search-only-key", repr(config))
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            result = await create_adapter(config, http)({"messages": [{"role": "user", "content": CONTENT[:2]}]}, "peer")
        self.assertEqual([r.url.host for r in calls], ["provider.example", "ollama.com", "provider.example"])
        self.assertIn("https://source.example/facts", result["text"])
        self.assertNotIn("Revenue", calls[1].content.decode())

    async def test_hosted_search_errors_are_actionable_and_do_not_expose_bodies(self):
        """Verify safe actionable errors for authentication, rate limits, outages, and redirects."""
        for status, code, message in ((401, "web_search_authentication", "API key"),
                                      (403, "web_search_authentication", "API key"),
                                      (429, "web_search_rate_limit", "request limit"),
                                      (503, "web_search", "unavailable"),
                                      (302, "web_search", "unavailable")):
            calls = []
            def transport(request):
                """Return a failing search response containing a secret and an untrusted redirect."""
                calls.append(request)
                return httpx.Response(status, text="SECRET", headers={"location": "https://other.example/"})
            with self.subTest(status=status):
                async with httpx.AsyncClient(transport=httpx.MockTransport(transport), follow_redirects=True) as http:
                    with self.assertRaises(SearchError) as error:
                        await OllamaSearch(http, "search-key").search("test")
                self.assertEqual(error.exception.code, code)
                self.assertIn(message, str(error.exception))
                self.assertNotIn("SECRET", str(error.exception))
                self.assertEqual(len(calls), 1)

    async def test_search_results_are_bounded_and_reject_malformed_links(self):
        """Verify result limits, safe URLs, text sanitation, and response size limits."""
        results = [{"url": url} for url in ("file:///private", "https://key:secret@source.example", "https://[", "https://source.example:bad")]
        results += [{"title": "<b>Source &amp; facts</b>", "url": f"https://source.example/{i}", "content": "x" * 3000} for i in range(8)]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": results}))) as http:
            sources = await OllamaSearch(http, "search-key").search("test")
        self.assertEqual(len(sources), 5)
        self.assertEqual(sources[0]["title"], "Source & facts")
        self.assertEqual(len(sources[0]["snippet"]), 1600)
        for content in (b"<html>SECRET</html>", b"x" * 1048577):
            with self.subTest(size=len(content)):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=content))) as http:
                    with self.assertRaises(SearchError) as error:
                        await OllamaSearch(http, "search-key").search("test")
                self.assertNotIn("SECRET", str(error.exception))

    def test_hosted_search_requires_a_separate_search_key_only_when_enabled(self):
        """Verify a hosted search key is required only when web search is enabled."""
        with self.assertRaisesRegex(ValueError, "Search API key"):
            self.config(web_search="auto", search_provider="ollama")
        self.config(search_provider="ollama")
        with self.assertRaisesRegex(ValueError, "search provider"):
            self.config(web_search="always", search_provider="unknown")

    def test_search_endpoint_validation(self):
        """Verify malformed and unsafe search URLs fail while permitted local URLs normalize."""
        for url in ("http://remote.example", "file:///private", "https://key:secret@search.example", "https://search.example?q=secret", "https://[", "https://search.example:bad", ""):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_search_url(url)
        self.assertEqual(validate_search_url("http://localhost:8888/search/"), "http://localhost:8888/search")
        self.assertEqual(validate_search_url("http://192.168.1.2", True), "http://192.168.1.2/search")
        with self.assertRaises(ValueError):
            self.config(web_search="auto")
