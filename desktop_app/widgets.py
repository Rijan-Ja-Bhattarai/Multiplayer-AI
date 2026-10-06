import math

from PySide6.QtCore import (Property, QAbstractAnimation, QEasingCurve, QRectF,
                            QPropertyAnimation, Qt, Signal)
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QPlainTextEdit,
                             QVBoxLayout, QWidget)

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

    The chips used to move 0.03 of a pixel per frame and had their
    coordinates rounded to whole pixels, so a movement smaller than a
    pixel could only appear as still, still, jump: roughly one visible
    one-pixel step every half second. It read as an image that had stopped
    loading rather than as slow motion. The coordinates are now floats,
    which the antialiasing render hint resolves to a soft edge, and the
    travel and cycle are set so the movement is perceptible without being
    hurried.
    """
    # (label, glyph, token) for the chips orbiting the centre mark.
    _CHIPS = (("Ollama", "O", "orbit_ollama"), ("Claude", "✳", "orbit_claude"),
              ("Gemini", "✦", "orbit_gemini"), ("OpenAI", "◎", "orbit_openai"))
    # The ellipse the chips travel, the sway either side of their resting
    # place in radians, and how long one full breath takes. At this sway a
    # chip drifts about 40px across eight seconds. The sway is also what
    # keeps the four chips from tracing near-identical paths: a chip that
    # barely moves cannot be told from its neighbours however its phase is
    # arranged.
    _CHIP_RX = 111.0
    _CHIP_RY = 98.0
    _SWAY = 0.26
    _CYCLE_MS = 16000

    def __init__(self, parent=None, theme=DARK, moving=True):
        super().__init__(parent)
        self.setMinimumSize(260, 240)
        self._phase = 0.0
        self._theme = theme
        self.animation = QPropertyAnimation(self, b"phase", self)
        self.animation.setStartValue(0.0)
        self.animation.setEndValue(math.tau)
        self.animation.setDuration(self._CYCLE_MS)
        self.animation.setLoopCount(-1)
        if moving:
            self.animation.start()

    def chip_angle(self, index, phase):
        """Where chip ``index`` sits, in radians, at ``phase``.

        Every chip shares one phase. That looks like it should make them
        move as one, and it was worth measuring: with the old 0.07 sway it
        did, the four paths landing within 7% of each other, because at
        that amplitude all four were nearly stationary rather than because
        of the phase. Raising the sway is what separated them, and it
        leaves the chips as far apart at 0.26 as a phase offset between
        neighbours did. So the offset is not here: one less term, and
        nothing measurable given up for it.
        """
        return index * math.pi / 2 - math.pi / 4 + math.sin(phase) * self._SWAY

    def chip_position(self, index, phase):
        """The centre of chip ``index`` at ``phase``, as floats.

        Left unrounded on purpose. Snapping these to whole pixels is what
        made the motion step, and it is kept in one place so a test can
        measure the travel that is actually painted rather than a copy of
        the formula.
        """
        angle = self.chip_angle(index, phase)
        return (self.width() / 2 + math.cos(angle) * self._CHIP_RX,
                self.height() / 2 + math.sin(angle) * self._CHIP_RY)

    def set_theme(self, name):
        """Adopt another theme and repaint with its colours."""
        self._theme = name
        self.update()

    def set_moving(self, moving):
        """Hold the orbit still, or let it turn again.

        Paused rather than stopped, which would snap the chips back to
        their start value. A held illustration is still a whole one, since
        the chips are placed by index rather than by where they ended up.
        """
        if moving:
            if self.animation.state() != QAbstractAnimation.State.Running:
                self.animation.start()
        else:
            self.animation.pause()

    @property
    def moving(self):
        return self.animation.state() == QAbstractAnimation.State.Running

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
        painter.drawRoundedRect(QRectF(cx - 72, cy - 72, 144, 144), 72, 72)
        painter.setPen(QPen(accent("orbit_ring_dashed"), 1, Qt.PenStyle.DashLine))
        painter.drawRoundedRect(QRectF(cx - 112, cy - 112, 224, 224), 112, 112)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent("orbit_tile"))
        painter.drawRoundedRect(QRectF(cx - 41, cy - 41, 82, 82), 23, 23)
        painter.setBrush(accent("accent"))
        painter.drawRoundedRect(QRectF(cx - 31, cy - 31, 62, 62), 18, 18)
        painter.setFont(QFont("Segoe UI", 25, QFont.Weight.Bold))
        painter.setPen(accent("on_accent"))
        painter.drawText(QRectF(cx - 31, cy - 31, 62, 62),
                         Qt.AlignmentFlag.AlignCenter, "M")
        for index, (name, glyph, token) in enumerate(self._CHIPS):
            x, y = self.chip_position(index, self._phase)
            painter.setPen(QPen(accent("orbit_chip_ring"), 1))
            painter.setBrush(accent("orbit_chip_bg"))
            painter.drawRoundedRect(QRectF(x - 37, y - 33, 74, 66), 12, 12)
            painter.setPen(accent(token))
            painter.setFont(QFont("Segoe UI", 21))
            painter.drawText(QRectF(x - 35, y - 31, 70, 36),
                             Qt.AlignmentFlag.AlignCenter, glyph)
            painter.setPen(accent("orbit_chip_text"))
            painter.setFont(QFont("Segoe UI", 9))
            painter.drawText(QRectF(x - 35, y + 6, 70, 20),
                             Qt.AlignmentFlag.AlignCenter, name)


class HoverRow(QWidget):
    """A row that keeps a slot for actions it only shows while hovered.

    The actions stay in the layout whether or not they are visible, so
    revealing them cannot reflow the row and a message cannot shift as the
    pointer passes over it. The row itself is one widget rather than a
    layout, because ``:hover`` in a stylesheet matches widgets, and a bare
    layout has nothing to match on.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("messageRow")
        self.outer = QHBoxLayout(self)
        self.outer.setContentsMargins(0, 0, 0, 0)
        self.outer.setSpacing(0)
        self.content = QHBoxLayout()
        self.content.setSpacing(12)
        self.outer.addLayout(self.content, 1)
        self._actions = QWidget()
        self._actions.setObjectName("messageActions")
        self._actions_layout = QHBoxLayout(self._actions)
        self._actions_layout.setContentsMargins(8, 0, 0, 0)
        self._actions_layout.setSpacing(4)
        self.outer.addWidget(self._actions, 0, Qt.AlignmentFlag.AlignTop)
        self._actions.setVisible(False)
        self._revealed = False

    def add_content(self, item, stretch=0, alignment=None):
        """Add a widget or a sub-layout to the part that is always shown.

        Qt keeps these in two methods, so a caller holding a layout rather
        than a widget has to know which to reach for. Here the type decides,
        and a caller can pass either without keeping track.
        """
        if isinstance(item, QWidget):
            if alignment is None:
                self.content.addWidget(item, stretch)
            else:
                self.content.addWidget(item, stretch, alignment)
        else:
            self.content.addLayout(item, stretch)
        return item

    def add_action(self, widget):
        self._actions_layout.addWidget(widget)
        return widget

    @property
    def actions_revealed(self):
        return self._revealed

    def reveal_actions(self, revealed):
        """Show or hide the action slot.

        A no-op when it is already in that state, so the enter and leave
        events of a pointer crossing a child widget cannot fight each other
        into a flicker.
        """
        if revealed == self._revealed:
            return
        self._revealed = revealed
        self._actions.setVisible(revealed)
        self.setProperty("actionsShown", revealed)
        self.style().unpolish(self)
        self.style().polish(self)

    def enterEvent(self, event):
        self.reveal_actions(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.reveal_actions(False)
        super().leaveEvent(event)


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
