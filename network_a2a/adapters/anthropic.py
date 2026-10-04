from .base import HTTPAdapter
from ..content import provider_parts


class AnthropicAdapter(HTTPAdapter):
    async def generate(self, messages, instructions=None):
        converted = [{"role": message["role"], "content": provider_parts(message["content"], "anthropic")} for message in messages]
        body = {"model": self.config.model, "messages": converted,
                "max_tokens": self.config.max_tokens, "stream": False}
        system = self.system_instructions(instructions)
        if system:
            body["system"] = system
        headers = {"x-api-key": self.config.api_key, "anthropic-version": "2023-06-01"}
        data = await self.post("messages", body, headers)
        text = "".join(part["text"] for part in data["content"] if part.get("type") == "text")
        return text, data.get("usage", {}), data.get("stop_reason")
