import os
import unittest

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtGui import QFont, QTextTable
from PySide6.QtWidgets import QApplication

from desktop_app.markdown import MarkdownMessage


class MarkdownMessageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_markdown_formats_text_lists_tables_and_literal_code(self):
        text = ('## Heading\n\nSome **bold text** and *emphasis* with `inline_code`.\n\n'
                '- First\n- Second\n\n```python\nprint("hello")\n## literal code\n```\n\n'
                '| Name | Value |\n| --- | --- |\n| Example | 42 |\n\n'
                '[Website](https://example.test) and [another link](https://other.test)')
        widget = MarkdownMessage(text)
        self.addCleanup(widget.deleteLater)
        document = widget.document()
        self.assertEqual(document.begin().blockFormat().headingLevel(), 2)
        self.assertEqual(document.find("bold text").charFormat().fontWeight(), QFont.Weight.Bold.value)
        self.assertTrue(document.find("emphasis").charFormat().fontItalic())
        self.assertTrue(document.find("inline_code").charFormat().fontFixedPitch())
        self.assertIn("monospace", document.find('print("hello")').charFormat().fontFamilies())
        self.assertIsNotNone(document.find("First").block().textList())
        self.assertEqual(document.find("## literal code").block().blockFormat().headingLevel(), 0)
        self.assertNotIn("**", widget.toPlainText())
        self.assertNotIn("```", widget.toPlainText())
        table = next(frame for frame in document.rootFrame().childFrames() if isinstance(frame, QTextTable))
        self.assertEqual((table.rows(), table.columns()), (2, 2))
        for text, url in (("Website", "https://example.test"), ("another link", "https://other.test")):
            link_format = document.find(text).charFormat()
            self.assertEqual(link_format.anchorHref(), url)
            self.assertEqual(link_format.foreground().color().name(), "#ffffff")

    def test_long_replies_resize_without_clipping_and_remain_selectable(self):
        widget = MarkdownMessage("## Long reply\n\n" + "A sentence that needs to wrap. " * 100)
        self.addCleanup(widget.deleteLater)
        widget.resize(640, 100)
        widget.show()
        for _ in range(3):
            self.app.processEvents()
        wide_height = widget.height()
        widget.resize(320, wide_height)
        for _ in range(3):
            self.app.processEvents()
        self.assertGreater(widget.height(), wide_height)
        self.assertGreaterEqual(widget.viewport().height(), widget.document().size().height())
        self.assertEqual(widget.verticalScrollBar().maximum(), 0)
        self.assertTrue(widget.isReadOnly())
        widget.selectAll()
        self.assertIn("Long reply", widget.textCursor().selectedText())
