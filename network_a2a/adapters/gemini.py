from urllib.parse import quote

from .base import HTTPAdapter
from ..content import images, text_content


class GeminiAdapter(HTTPAdapter):
    async def generate(self, messages, instructions=None):
        body = {"contents": [{"role": "model" if message["role"] == "assistant" else "user",
                              "parts": [{"text": text_content(message["content"])}] + [
                                  {"inlineData": {"mimeType": image["mime_type"], "data": image["data"]}}
                                  for image in images(message["content"])]} for message in messages],
                "generationConfig": {"maxOutputTokens": self.config.max_tokens}}
        system = self.system_instructions(instructions)
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
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
