from .base import HTTPAdapter, ProviderError
from ..content import provider_parts


class OpenAIAdapter(HTTPAdapter):
    """OpenAI Responses API; history supplied per request, storage disabled."""

    async def generate(self, messages, instructions=None):
        converted = [{"role": message["role"], "content": provider_parts(message["content"], "openai")} for message in messages]
        body = {"model": self.config.model, "input": converted, "store": False,
                "stream": False, "max_output_tokens": self.config.max_tokens}
        system = self.system_instructions(instructions)
        if system:
            body["instructions"] = system
        data = await self.post("responses", body)
        if data.get("status") not in ("completed", "incomplete"):
            raise ProviderError("invalid_response", "OpenAI response did not complete")
        text = "".join(part["text"] for item in data["output"] if item.get("type") == "message"
                       for part in item.get("content", []) if part.get("type") == "output_text")
        reason = data.get("incomplete_details") or {}
        return text, data.get("usage", {}), reason.get("reason", data["status"])
