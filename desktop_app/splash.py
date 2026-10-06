"""The launch screen: the app's first impression, and its first chance to lie.

A splash screen is the one piece of UI whose failure is invisible. If it
never goes away the app looks hung; if it leaves early you glimpse an
unpainted window; and if it stays up over an error it hides that error
behind a smiling logo. So three things are handled rather than hoped for:

* It waits for the runtime to report ready, but never for less than a
  floor. Startup here takes about a quarter of a second, so waiting only
  for readiness would flash a splash for a fifth of a second and read as a
  glitch rather than as an intention.
* A fatal error tears it down at once. Nothing should be able to leave a
  logo on screen over a broken app.
* It is skipped when the stored preferences say so, and when the
  reduce-motion setting is on, since an accessibility switch that let an
  animation play anyway would not be doing its job.

The mark and the orbit are the ones the welcome panel already uses, so the
app introduces itself with what the user is about to see rather than with
a second drawing of it.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QGraphicsOpacityEffect, QLabel,
                               QVBoxLayout, QWidget)

from .theme import DARK, color
from .widgets import OrbitArt, app_mark

# Long enough to read as deliberate rather than as a flash, short enough
# that nobody is waiting for it.
LAUNCH_FLOOR_MS = 900
FADE_MS = 260


class LaunchScreen(QWidget):
    """A frameless card holding the mark, the name and the orbit."""

    def __init__(self, theme_name=DARK, moving=True, parent=None):
        super().__init__(parent)
        self._theme = theme_name
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                           | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAccessibleName("Multiplayer AI is starting")
        self.setFixedSize(400, 440)

        column = QVBoxLayout(self)
        column.setContentsMargins(24, 30, 24, 24)
        column.setSpacing(8)

        # The app's own mark, letter and all, because that is the mark the
        # taskbar is about to show and the one the window is built from.
        # Not this device's initial: the identity is not known until the
        # runtime answers, and a mark that shows a placeholder and then
        # changes is worse than one that shows the app throughout.
        self.mark = QLabel()
        self.mark.setFixedSize(88, 88)
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mark.setPixmap(app_mark(theme_name, "M", 88))
        self.mark.setAccessibleName("Multiplayer AI")
        column.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignHCenter)

        self.caption = Caption(theme_name)
        column.addWidget(self.caption, 0, Qt.AlignmentFlag.AlignHCenter)

        self.art = OrbitArt(theme=theme_name, moving=moving)
        column.addWidget(self.art, 1)

        self.shown_at = 0.0
        self.dismissed = False

    # -- lifecycle ------------------------------------------------------

    def begin(self):
        """Show it and start fading in.

        Readiness is not waited for here. It may already have happened by
        the time this is constructed, so the caller says so afterwards
        rather than this subscribing to an event that has been and gone.
        """
        self._centre()
        self.show()
        self.raise_()
        self.shown_at = time.monotonic()
        self.fade_to(1.0, FADE_MS, QEasingCurve.Type.OutCubic)

    def runtime_ready(self):
        """Leave as soon as the floor has passed."""
        self.dismiss_when_floored()

    def dismiss_when_floored(self):
        remaining = LAUNCH_FLOOR_MS / 1000 - (time.monotonic() - self.shown_at)
        if remaining <= 0:
            self.dismiss()
        else:
            QTimer.singleShot(int(remaining * 1000), self.dismiss)

    def dismiss(self, on_done=None):
        """Fade out and close, then hand back.

        The opacity effect is taken off once the fade is over. Left
        attached it would put every later paint of this widget through Qt's
        effect machinery for nothing, which is the fault the page fade has
        in the window and is not worth repeating here.
        """
        if self.dismissed or not self.isVisible():
            self.dismissed = True
            if on_done:
                on_done()
            return
        self.art.set_moving(False)
        self.dismissed = True
        self.fade_to(0.0, FADE_MS, QEasingCurve.Type.InCubic,
                     on_finished=lambda: self._close(on_done))

    def _close(self, on_done):
        if self.graphicsEffect() is not None:
            self.setGraphicsEffect(None)
        self.hide()
        if on_done:
            on_done()

    # -- appearance -----------------------------------------------------

    def set_theme(self, name):
        self._theme = name
        self.mark.setPixmap(app_mark(name, "M", 88))
        self.caption.set_theme(name)
        self.art.set_theme(name)
        self.update()

    def fade_to(self, opacity, duration, easing, on_finished=None):
        """Fade the whole card, creating the effect only while needed."""
        effect = self.graphicsEffect()
        if effect is None:
            effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(effect)
        effect.setOpacity(opacity)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setStartValue(opacity)
        animation.setEndValue(opacity)
        animation.setDuration(duration)
        animation.setEasingCurve(easing)
        animation.start()
        self._fade = animation
        if on_finished:
            animation.finished.connect(on_finished)

    def _centre(self):
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.center().x() - self.width() // 2,
                  area.center().y() - self.height() // 2)


class Caption(QWidget):
    """The product name, painted so it carries no layout of its own."""

    def __init__(self, theme_name):
        super().__init__()
        self._theme = theme_name
        self.setFixedHeight(28)

    def set_theme(self, name):
        self._theme = name
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(color(self._theme, "text_strong")), 1))
        painter.setFont(QFont("Segoe UI", 17, QFont.Weight.DemiBold))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Multiplayer AI")
        painter.end()
