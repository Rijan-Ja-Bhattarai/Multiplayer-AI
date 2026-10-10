from .base import HTTPAdapter
from ..content import images, text_content


class OllamaAdapter(HTTPAdapter):
    async def generate_structured(self, messages, schema):
        return await self.generate(messages, schema=schema)

    async def generate(self, messages, instructions=None, schema=None):
        converted = []
        for message in self.messages_with_system(messages, instructions):
            item = {"role": message["role"], "content": text_content(message["content"])}
            attached = images(message["content"])
            if attached:
                item["images"] = [image["data"] for image in attached]
            converted.append(item)
        body = {
            "model": self.config.model, "messages": converted,
            "stream": False, "options": {"num_predict": self.config.max_tokens},
        }
        if schema is not None:
            body["format"] = schema
            body["options"]["temperature"] = 0
        data = await self.post("api/chat", body)
        usage = {"input_tokens": data.get("prompt_eval_count", 0), "output_tokens": data.get("eval_count", 0)}
        return data["message"]["content"], usage, data.get("done_reason")
