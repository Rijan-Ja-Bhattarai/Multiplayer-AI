"""Selectable Markdown replies that fit into the conversation's scroll area."""
import math

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QPalette, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import QFrame, QSizePolicy, QTextBrowser

from .theme import DARK, color


class MarkdownMessage(QTextBrowser):
    def __init__(self, text, parent=None, theme_name=DARK):
        super().__init__(parent)
        self._fitting = False
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        foreground = color(theme_name, "text")
        link = color(theme_name, "agent_title")
        # Transparent, so the bubble behind shows through. It used to paint an
        # opaque surface of its own, which is what made a reply read as a
        # full-width slab of slightly different grey with square corners and
        # no padding, while a plain label beside it had no fill at all.
        #
        # Padding is deliberately not set here: fit_height measures
        # contentsMargins(), which a stylesheet's padding does not reach, so
        # text would be clipped. The bubble's layout margins do the padding.
        self.setStyleSheet("QTextBrowser { background: transparent; color: " + foreground
                          + "; border: none; padding: 0; selection-background-color: "
                          + color(theme_name, "accent") + "; selection-color: "
                          + color(theme_name, "on_accent") + "; }")
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Link, QColor(link))
        palette.setColor(QPalette.ColorRole.Text, QColor(foreground))
        palette.setColor(QPalette.ColorRole.Base, QColor(Qt.GlobalColor.transparent))
        palette.setColor(QPalette.ColorRole.Window, QColor(Qt.GlobalColor.transparent))
        self.setPalette(palette)
        document = self.document()
        document.setDocumentMargin(0)
        document.setDefaultStyleSheet("a { color: " + link + "; } pre, code { font-family: Consolas, monospace; }")
        document.setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub
                             | QTextDocument.MarkdownFeature.MarkdownNoHTML)
        # The Markdown importer assigns its own link colour; keep links readable
        # on the dark chat surface without changing their other formatting.
        links = []
        block = document.begin()
        while block.isValid():
            fragment_iterator = block.begin()
            while not fragment_iterator.atEnd():
                fragment = fragment_iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isAnchor():
                    links.append((fragment.position(), fragment.length()))
                fragment_iterator += 1
            block = block.next()
        link_format = QTextCharFormat()
        link_format.setForeground(QColor(link))
        for position, length in links:
            cursor = QTextCursor(document)
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            cursor.mergeCharFormat(link_format)
        document.documentLayout().documentSizeChanged.connect(self.fit_height)
        self.anchorClicked.connect(self.open_link)
        QTimer.singleShot(0, self.fit_height)

    def loadResource(self, resource_type, url):
        # Replies cannot load images or other files from the device or network.
        return QByteArray()

    def open_link(self, url):
        if url.scheme().lower() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)

    def natural_width(self):
        """How wide this reply would be if it were not wrapping.

        ``idealWidth`` is clamped by the text width ``resizeEvent`` sets to
        make the reply wrap in the first place, so it reports the current
        width rather than the width of the text. The wrap is lifted for the
        measurement and put back immediately, because a reply is not a fixed
        width and it has to keep wrapping at whatever the panel gives it.
        """
        document = self.document()
        current = document.textWidth()
        try:
            document.setTextWidth(-1)
            width = document.idealWidth()
        finally:
            # Put back what was there, not a stand-in of one pixel. A reply
            # that has not been laid out yet has no width of its own, and
            # wrapping it at a single pixel makes a paragraph 24000px tall.
            document.setTextWidth(current if current > 0 else -1)
        return width

    def fit_height(self, *args):
        """Size this reply to the text at whatever width it currently has.

        The wrap is set here rather than only in ``resizeEvent`` because the
        viewport is not always resized along with the widget: a reply that has
        just been moved into a bubble can be handed a new width while its
        viewport is still the 640px one it started with, and measuring against
        that reports one enormous line. Setting the width from whichever of
        the two is real makes the height correct whenever it is asked for,
        which is what the bubble sizing relies on.
        """
        if self._fitting:
            return
        margins = self.contentsMargins()
        width = self.viewport().width() or self.width()
        self._fitting = True
        try:
            self.document().setTextWidth(max(1, width))
            height = math.ceil(self.document().size().height()) + margins.top() + margins.bottom()
        finally:
            self._fitting = False
        if self.horizontalScrollBar().isVisible():
            height += self.horizontalScrollBar().height()
        if self.height() != max(1, height):
            self.setFixedHeight(max(1, height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_height()
