"""Read selected files locally and prepare bounded text and vision inputs."""
import base64
import json
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QSize, Qt
from PySide6.QtGui import QImage, QImageReader
from PySide6.QtPdf import QPdfDocument

from network_a2a.content import MAX_MESSAGE_BYTES, MAX_TEXT_BYTES


MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_FILES = 8


def encode_image(image, name):
    if image.isNull():
        raise ValueError(f"Could not read image: {name}")
    if image.width() > 1600 or image.height() > 1600:
        image = image.scaled(1600, 1600, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    # Flatten transparent images onto white so the model can read dark diagrams.
    if image.hasAlphaChannel():
        from PySide6.QtGui import QPainter
        flattened = QImage(image.size(), QImage.Format.Format_RGB32)
        flattened.fill(Qt.GlobalColor.white)
        painter = QPainter(flattened)
        painter.drawImage(0, 0, image)
        painter.end()
        image = flattened
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "JPEG", 85):
        raise ValueError(f"Could not prepare image: {name}")
    buffer.close()
    return {"type": "image", "name": name[:256], "mime_type": "image/jpeg", "data": base64.b64encode(bytes(data)).decode("ascii")}


def prepare_pdf(path, vision):
    document = QPdfDocument()
    data = QByteArray(path.read_bytes())
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    try:
        document.load(buffer)
        if document.status() != QPdfDocument.Status.Ready:
            raise ValueError(f"Could not open {path.name}. Unlock password-protected PDFs before attaching them.")
        pages = document.pageCount()
        if not 1 <= pages <= 200:
            raise ValueError("Attach a PDF with 1–200 pages, or split it into smaller documents")
        text = []
        for page in range(pages):
            extracted = document.getAllText(page).text().strip()
            if extracted:
                text.append(f"Page {page + 1}\n{extracted}")
            if sum(len(part.encode()) for part in text) > MAX_TEXT_BYTES - 10000:
                raise ValueError(f"{path.name} has too much text for one request. Split it into smaller PDFs.")
        content = [{"type": "document", "name": path.name, "text": "\n\n".join(text)}] if text else []
        if not text and not vision:
            raise ValueError(f"{path.name} is a scanned PDF. Enable image support on a vision-capable model to read it.")
        note = f"{pages} pages · text extracted" if text else f"{pages} scanned pages"
        if vision:
            if pages > 16:
                if not text:
                    raise ValueError("Split scanned PDFs into documents with at most 16 pages")
                note += " · page images omitted (split into 16-page PDFs to include diagrams)"
            else:
                for page in range(pages):
                    size = document.pagePointSize(page)
                    if size.width() <= 0 or size.height() <= 0:
                        raise ValueError(f"Could not render page {page + 1} of {path.name}")
                    scale = min(1600 / size.width(), 1600 / size.height())
                    image = document.render(page, QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale))))
                    content.append(encode_image(image, f"{path.name} · page {page + 1}"))
                note += " · page images included"
        return content, note
    finally:
        document.close()
        buffer.close()


def prepare_attachments(paths, vision=False):
    if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_FILES:
        raise ValueError("Attach up to 8 images or PDFs at a time")
    prepared = []
    for filename in paths:
        path = Path(filename)
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_FILE_BYTES:
            raise ValueError("Choose readable images or PDFs no larger than 20 MiB each")
        if path.suffix.lower() == ".pdf":
            content, note = prepare_pdf(path, vision)
        else:
            if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
                raise ValueError("Choose PNG, JPEG, WebP, GIF, BMP images or PDF documents")
            if not vision:
                raise ValueError("Enable image support on a vision-capable model before attaching images")
            reader = QImageReader(str(path))
            reader.setAutoTransform(True)
            size = reader.size()
            if not size.isValid() or size.width() * size.height() > 80000000:
                raise ValueError(f"{path.name} is invalid or has too many pixels")
            if size.width() > 1600 or size.height() > 1600:
                reader.setScaledSize(size.scaled(1600, 1600, Qt.AspectRatioMode.KeepAspectRatio))
            content, note = [encode_image(reader.read(), path.name)], "Image ready"
        prepared.append({"name": path.name, "content": content, "note": note})
        if len(json.dumps(prepared).encode()) > MAX_MESSAGE_BYTES - 65536:
            raise ValueError("Attachments exceed the request limit. Send fewer files or split the PDF.")
    if sum(len(part.get("text", "").encode()) for item in prepared for part in item["content"]) > MAX_TEXT_BYTES - 10000:
        raise ValueError("These PDFs contain too much text for one request. Send them separately.")
    return prepared
