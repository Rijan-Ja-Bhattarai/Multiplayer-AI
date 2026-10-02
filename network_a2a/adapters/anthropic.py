from .base import HTTPAdapter


class AnthropicAdapter(HTTPAdapter):
    async def generate(self, messages):
        body = {"model": self.config.model, "messages": messages,
                "max_tokens": self.config.max_tokens, "stream": False}
        if self.config.system_prompt:
            body["system"] = self.config.system_prompt
        headers = {"x-api-key": self.config.api_key, "anthropic-version": "2023-06-01"}
        data = await self.post("messages", body, headers)
        text = "".join(part["text"] for part in data["content"] if part.get("type") == "text")
        return text, data.get("usage", {}), data.get("stop_reason")
