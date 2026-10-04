"""Provider-independent message content and bounded attachment transport."""
import base64
import binascii
import json


MAX_MESSAGE_BYTES = 8 * 1024 * 1024
MAX_FRAME_BYTES = MAX_MESSAGE_BYTES + 65536
MAX_TEXT_BYTES = 200000


def validate_content(content, role="user"):
    if isinstance(content, str):
        if not content.strip() or len(content.encode()) > MAX_TEXT_BYTES:
            raise ValueError("Enter text within the message size limit")
        return content
    if role != "user" or not isinstance(content, list) or not 1 <= len(content) <= 80:
        raise ValueError("Attachments require a user message with text and content parts")
    clean = []
    text_bytes = 0
    for part in content:
        if not isinstance(part, dict):
            raise ValueError("Invalid attachment content")
        kind = part.get("type")
        if kind == "text" and set(part) == {"type", "text"}:
            if not isinstance(part["text"], str) or not part["text"].strip():
                raise ValueError("Enter a message for your attachments")
            text_bytes += len(part["text"].encode())
        elif kind == "document" and set(part) == {"type", "name", "text"}:
            if not isinstance(part["text"], str) or not part["text"].strip():
                raise ValueError("Document contains no readable text")
            text_bytes += len(part["text"].encode())
        elif kind == "image" and set(part) == {"type", "name", "mime_type", "data"}:
            if part["mime_type"] not in ("image/png", "image/jpeg") or not isinstance(part["data"], str):
                raise ValueError("Images must contain PNG or JPEG data")
            if len(part["data"]) > MAX_MESSAGE_BYTES:
                raise ValueError("Image is too large")
            try:
                decoded = base64.b64decode(part["data"], validate=True)
            except (ValueError, binascii.Error):
                raise ValueError("Invalid image data") from None
            signature = b"\x89PNG\r\n\x1a\n" if part["mime_type"] == "image/png" else b"\xff\xd8\xff"
            if not decoded.startswith(signature):
                raise ValueError("Image data does not match its file format")
        else:
            raise ValueError("Unsupported attachment content")
        if kind in ("document", "image"):
            if not isinstance(part["name"], str) or not 1 <= len(part["name"]) <= 256:
                raise ValueError("Attachments need a valid filename")
        clean.append(dict(part))
    if text_bytes > MAX_TEXT_BYTES or len(json.dumps(clean).encode()) > MAX_MESSAGE_BYTES:
        raise ValueError("Attachments are too large; send fewer files or split the PDF")
    return clean


def text_content(content, include_documents=True):
    if isinstance(content, str):
        return content
    parts = []
    for part in content:
        if part["type"] == "text":
            parts.append(part["text"])
        elif part["type"] == "document" and include_documents:
            parts.append(f"Document: {part['name']}\n{part['text']}")
    return "\n\n".join(parts)


def content_summary(content):
    if isinstance(content, str):
        return content
    names = list(dict.fromkeys(part["name"] for part in content if part["type"] in ("image", "document")))
    text = text_content(content, include_documents=False)
    return text + ("\n\nAttached: " + ", ".join(names) if names else "")


def images(content):
    return [part for part in content if part["type"] == "image"] if isinstance(content, list) else []


def provider_parts(content, provider):
    if isinstance(content, str):
        return content
    result = []
    for part in content:
        if part["type"] in ("text", "document"):
            text = part["text"] if part["type"] == "text" else f"Document: {part['name']}\n{part['text']}"
            result.append({"type": "input_text" if provider == "openai" else "text", "text": text})
        elif provider == "anthropic":
            result.append({"type": "image", "source": {"type": "base64", "media_type": part["mime_type"], "data": part["data"]}})
        else:
            url = f"data:{part['mime_type']};base64,{part['data']}"
            result.append({"type": "input_image", "image_url": url} if provider == "openai" else
                          {"type": "image_url", "image_url": {"url": url}})
    return result
