from .base import HTTPAdapter


class OllamaAdapter(HTTPAdapter):
    async def generate(self, messages):
        data = await self.post("api/chat", {
            "model": self.config.model, "messages": self.messages_with_system(messages),
            "stream": False, "options": {"num_predict": self.config.max_tokens},
        })
        usage = {"input_tokens": data.get("prompt_eval_count", 0), "output_tokens": data.get("eval_count", 0)}
        return data["message"]["content"], usage, data.get("done_reason")
