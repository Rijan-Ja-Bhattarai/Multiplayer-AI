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
        self._natural = None
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

        Measured on a throwaway document rather than by lifting the wrap on
        this one. Lifting it looks like the obvious way and does not work: the
        change emits ``documentSizeChanged``, which re-enters ``fit_height``,
        and that puts the wrap straight back before the width is read. Worse,
        the layout is cached, so after a reply has been wrapped even once the
        reading comes back as the width it already had -- which made every
        measurement depend on the current layout, so a reply only ever matched
        the width it was already at.

        A new document has no cached layout, so its size is the real one. The
        result is cached because a reply's text never changes after it is built,
        and this runs on every render and on every window resize.
        """
        if self._natural is None:
            probe = QTextDocument()
            probe.setDocumentMargin(self.document().documentMargin())
            probe.setPlainText(self.document().toPlainText())
            self._natural = probe.documentLayout().documentSize().width()
        return self._natural

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
