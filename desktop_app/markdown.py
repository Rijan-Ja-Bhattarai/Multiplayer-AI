"""Selectable Markdown replies that fit into the conversation's scroll area."""
import math

from PySide6.QtCore import QByteArray, Qt, QTimer
from PySide6.QtGui import QColor, QDesktopServices, QPalette, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import QFrame, QSizePolicy, QTextBrowser


class MarkdownMessage(QTextBrowser):
    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)
        self.setStyleSheet("QTextBrowser { background: transparent; border: none; padding: 0; selection-background-color: #5865f2; }")
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Link, QColor("#a8b0ff"))
        self.setPalette(palette)
        document = self.document()
        document.setDocumentMargin(0)
        document.setDefaultStyleSheet("a { color: #a8b0ff; } pre, code { font-family: Consolas, monospace; }")
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
        link_format.setForeground(QColor("#a8b0ff"))
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

    def fit_height(self, *args):
        margins = self.contentsMargins()
        height = math.ceil(self.document().size().height()) + margins.top() + margins.bottom()
        if self.horizontalScrollBar().isVisible():
            height += self.horizontalScrollBar().height()
        if self.height() != max(1, height):
            self.setFixedHeight(max(1, height))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.document().setTextWidth(max(1, self.viewport().width()))
        self.fit_height()
