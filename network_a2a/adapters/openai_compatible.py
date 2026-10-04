from .base import HTTPAdapter
from ..content import provider_parts


class OpenAICompatibleAdapter(HTTPAdapter):
    """Bionic GPT, Groq, DeepSeek, Mistral, OpenRouter and compatible servers."""

    async def generate(self, messages, instructions=None):
        converted = [{"role": message["role"], "content": provider_parts(message["content"], "compatible")}
                     for message in self.messages_with_system(messages, instructions)]
        data = await self.post("chat/completions", {
            "model": self.config.model, "messages": converted,
            "stream": False, "max_tokens": self.config.max_tokens,
        })
        choice = data["choices"][0]
        return choice["message"]["content"], data.get("usage", {}), choice.get("finish_reason")
