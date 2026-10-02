import math

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QLabel, QPushButton, QPlainTextEdit, QWidget

from .theme import DARK, color


def label(text="", name=None, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def action(text, callback=None, primary=False, name=None):
    widget = QPushButton(text)
    widget.setObjectName(name or ("primary" if primary else ""))
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if callback:
        widget.clicked.connect(callback)
    return widget


class Composer(QPlainTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self.submitted.emit()
            event.accept()
        else:
            super().keyPressEvent(event)


class OrbitArt(QWidget):
    """Quiet orbital motion in the welcome panel; no synthetic network metrics.

    Painted directly rather than styled, so it cannot be reached by a Qt
    Style Sheet. Its colours come from the theme palette and it repaints
    itself when the theme changes.
    """
    # (label, glyph, token) for the chips orbiting the centre mark.
    _CHIPS = (("Ollama", "O", "orbit_ollama"), ("Claude", "✳", "orbit_claude"),
              ("Gemini", "✦", "orbit_gemini"), ("OpenAI", "◎", "orbit_openai"))

    def __init__(self, parent=None, theme=DARK):
        super().__init__(parent)
        self.setMinimumSize(260, 240)
        self._phase = 0.0
        self._theme = theme
        self.animation = QPropertyAnimation(self, b"phase", self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(math.tau)
        self.animation.setDuration(24000)
        self.animation.setLoopCount(-1)
        self.animation.start()

    def set_theme(self, name):
        """Adopt another theme and repaint with its colours."""
        self._theme = name
        self.update()

    def get_phase(self):
        return self._phase

    def set_phase(self, value):
        self._phase = value
        self.update()

    phase = Property(float, get_phase, set_phase)

    def paintEvent(self, event):
        accent = lambda token: QColor(color(self._theme, token))  # noqa: E731
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = self.width() / 2, self.height() / 2
        painter.setPen(QPen(accent("orbit_ring"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(int(cx - 72), int(cy - 72), 144, 144)
        painter.setPen(QPen(accent("orbit_ring_dashed"), 1, Qt.PenStyle.DashLine))
        painter.drawEllipse(int(cx - 112), int(cy - 112), 224, 224)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent("orbit_tile"))
        painter.drawRoundedRect(int(cx - 41), int(cy - 41), 82, 82, 23, 23)
        painter.setBrush(accent("accent"))
        painter.drawRoundedRect(int(cx - 31), int(cy - 31), 62, 62, 18, 18)
        painter.setFont(QFont("Segoe UI", 25, QFont.Weight.Bold))
        painter.setPen(accent("on_accent"))
        painter.drawText(int(cx - 31), int(cy - 31), 62, 62, Qt.AlignmentFlag.AlignCenter, "M")
        for index, (name, glyph, token) in enumerate(self._CHIPS):
            angle = index * math.pi / 2 - math.pi / 4 + math.sin(self._phase) * .07
            x, y = cx + math.cos(angle) * 111, cy + math.sin(angle) * 98
            painter.setPen(QPen(accent("orbit_chip_ring"), 1))
            painter.setBrush(accent("orbit_chip_bg"))
            painter.drawRoundedRect(int(x - 37), int(y - 33), 74, 66, 12, 12)
            painter.setPen(accent(token))
            painter.setFont(QFont("Segoe UI", 21))
            painter.drawText(int(x - 35), int(y - 31), 70, 36, Qt.AlignmentFlag.AlignCenter, glyph)
            painter.setPen(accent("orbit_chip_text"))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(int(x - 35), int(y + 6), 70, 20, Qt.AlignmentFlag.AlignCenter, name)


class WorkspaceButton(QPushButton):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setFixedSize(48, 48)
        self.setObjectName("workspace")
        self._radius = 23.0
        self.animation = QPropertyAnimation(self, b"cornerRadius", self)
        self.animation.setDuration(160)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def radius(self):
        return self._radius

    def set_radius(self, value):
        self._radius = value
        self.setStyleSheet(f"QPushButton {{ border-radius: {int(value)}px; }}")

    cornerRadius = Property(float, radius, set_radius)

    def enterEvent(self, event):
        self.animation.stop()
        self.animation.setStartValue(self._radius)
        self.animation.setEndValue(15.0)
        self.animation.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.animation.stop()
        self.animation.setStartValue(self._radius)
        self.animation.setEndValue(23.0)
        self.animation.start()
        super().leaveEvent(event)
