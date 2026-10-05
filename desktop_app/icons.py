"""Matching navigation line icons and bundled provider brand marks."""
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel

from .theme import LIGHT, color


ASSETS = Path(__file__).resolve().parent / "assets" / "providers"
BRANDS = frozenset(("ollama", "bionic", "openai", "anthropic", "gemini", "groq", "deepseek", "mistral", "openrouter"))


def navigation_icon(name, ink):
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(2, 2)
    painter.setPen(QPen(QColor(ink), 1.7, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)

    def line(*points):
        path = QPainterPath(QPointF(*points[0]))
        for point in points[1:]:
            path.lineTo(*point)
        painter.drawPath(path)

    if name == "overview":
        for x, y in ((4, 4), (14, 4), (4, 14), (14, 14)):
            painter.drawRoundedRect(x, y, 6, 6, 1, 1)
    elif name == "agents":
        painter.drawEllipse(QPointF(10, 8), 3, 3)
        line((4, 20), (4, 17), (7, 14), (13, 14), (16, 17), (16, 20))
        line((17, 5), (20, 8), (17, 11))
        line((19, 14), (21, 17), (21, 20))
    elif name == "conversations":
        line((5, 4), (20, 4), (20, 15), (11, 15), (5, 20), (5, 4))
        line((9, 8), (16, 8))
        line((9, 11), (14, 11))
    elif name == "resources":
        line((4, 4), (4, 20), (21, 20))
        line((7, 15), (11, 10), (15, 13), (20, 6))
    elif name == "settings":
        for y, x in ((6, 9), (12, 16), (18, 7)):
            line((4, y), (x - 2, y))
            line((x + 2, y), (21, y))
            painter.drawEllipse(QPointF(x, y), 2, 2)
    else:
        line((7, 8), (12, 16), (17, 8))
        for x, y in ((6, 6), (18, 6), (12, 18)):
            painter.drawEllipse(QPointF(x, y), 2.5, 2.5)
    painter.end()
    return QIcon(pixmap)


def provider_pixmap(provider, theme, size=36):
    path = (ASSETS / "bionic.png" if provider == "bionic" else
            ASSETS / ("light" if theme == LIGHT else "dark") / f"{provider}.png") if provider in BRANDS else None
    # Custom deployments have no single brand; show a matching connection icon.
    pixmap = QPixmap(str(path)) if path and path.is_file() else navigation_icon("providers", color(theme, "text")).pixmap(size * 2, size * 2)
    pixmap = pixmap.scaled(size * 2, size * 2, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    pixmap.setDevicePixelRatio(2)
    return pixmap


def provider_logo(provider, theme, size=36):
    widget = QLabel()
    widget.setFixedSize(size, size)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    widget.setPixmap(provider_pixmap(provider, theme, size))
    widget.setAccessibleName(f"{provider or 'Custom endpoint'} logo")
    return widget
