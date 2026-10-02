from .base import HTTPAdapter


class OpenAICompatibleAdapter(HTTPAdapter):
    """Bionic GPT, Groq, DeepSeek, Mistral, OpenRouter and compatible servers."""

    async def generate(self, messages):
        data = await self.post("chat/completions", {
            "model": self.config.model, "messages": self.messages_with_system(messages),
            "stream": False, "max_tokens": self.config.max_tokens,
        })
        choice = data["choices"][0]
        return choice["message"]["content"], data.get("usage", {}), choice.get("finish_reason")
