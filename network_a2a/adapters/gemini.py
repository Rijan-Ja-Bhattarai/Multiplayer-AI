from urllib.parse import quote

from .base import HTTPAdapter


class GeminiAdapter(HTTPAdapter):
    async def generate(self, messages):
        body = {"contents": [{"role": "model" if message["role"] == "assistant" else "user",
                              "parts": [{"text": message["content"]}]} for message in messages],
                "generationConfig": {"maxOutputTokens": self.config.max_tokens}}
        if self.config.system_prompt:
            body["systemInstruction"] = {"parts": [{"text": self.config.system_prompt}]}
        model = self.config.model.removeprefix("models/")
        data = await self.post(f"models/{quote(model, safe='')}:generateContent", body,
                               {"x-goog-api-key": self.config.api_key})
        candidates = data.get("candidates") or []
        if not candidates:
            return "", data.get("usageMetadata", {}), "blocked"
        candidate = candidates[0]
        text = "".join(part["text"] for part in candidate.get("content", {}).get("parts", [])
                       if "text" in part and not part.get("thought"))
        return text, data.get("usageMetadata", {}), candidate.get("finishReason")
