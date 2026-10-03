"""Exercise native invitation, join, and send controls on two desktop windows."""
import tempfile
import time
import unittest
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from starlette.responses import JSONResponse
from starlette.routing import Route

from desktop_app.dialogs import AgentDialog, InviteDialog, JoinDialog
from desktop_app.markdown import MarkdownMessage
from desktop_app.storage import Storage
from desktop_app.window import MainWindow
from tests.test_desktop_runtime import MemoryVault


class SharedConversationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("Native UI condition timed out")
            time.sleep(.01)
        self.app.processEvents()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.host = MainWindow(Storage(Path(self.directory.name) / "host", MemoryVault()))
        self.guest = MainWindow(Storage(Path(self.directory.name) / "guest", MemoryVault()))
        self.host.show()
        self.guest.show()
        self.wait(lambda: self.host.ready and self.guest.ready)
        # Startup emits "ready" and then the poller emits "agents" right
        # away, so this window's first agent list arrives as a queued Qt
        # signal. Everything below runs in the same QApplication, which is
        # shared with every other UI test file, so that signal can land
        # after this test starts poking at state unless it is drained here.
        self.wait(lambda: self.host.chat_agents.count() > 0)

    def tearDown(self):
        self.host.close()
        self.guest.close()
        self.wait(lambda: not self.host.network.isRunning() and not self.guest.network.isRunning())
        self.directory.cleanup()

    def send(self, window, text):
        self.wait(lambda: window.send_button.isEnabled())
        window.composer.setPlainText(text)
        QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: any(role == "assistant" and value == "AI: " + text
                             for role, value in window.chats[window.selected]["messages"]))

    def test_invitation_adds_shared_chat_to_both_devices_and_syncs_ai_replies(self):
        async def model(request):
            body = await request.json()
            return JSONResponse({"choices": [{"message": {"content": "AI: " + body["messages"][-1]["content"]},
                                               "finish_reason": "stop"}]})
        self.host.network.runtime.app.router.routes.append(Route("/mock/v1/chat/completions", model, methods=["POST"]))
        provider = AgentDialog(self.host, "bionic")
        provider.show()
        provider.name.setText("shared-model")
        provider.base.setText(f"http://127.0.0.1:{self.host.port}/mock/v1")
        provider.model.setCurrentText("test-model")
        provider.key.setText("test-key")
        QTest.mouseClick(provider.save_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: not provider.isVisible())
        self.host.select_agent("shared-model")
        self.send(self.host, "Before the guest joined")
        history = [{"role": role, "content": text}
                   for role, text in self.host.chats["shared-model"]["messages"]]
        invite = InviteDialog(self.host, target="shared-model", messages=history)
        invite.show()
        invite.name.setText("guest-laptop")
        invite.url.setText(f"ws://127.0.0.1:{self.host.port}/connect")
        QTest.mouseClick(invite.create_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: bool(invite.result.toPlainText()))
        room_id = invite.conversation_id
        self.assertEqual(self.host.selected, room_id)
        join = JoinDialog(self.guest)
        join.show()
        join.invitation.setPlainText(invite.result.toPlainText())
        QTest.mouseClick(join.connect_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: not join.isVisible())
        self.assertEqual(self.guest.selected, room_id)
        for window in (self.host, self.guest):
            self.assertIn(room_id, window.conversations)
            self.assertTrue(any(window.chat_agents.item(i).data(Qt.ItemDataRole.UserRole) == room_id
                                for i in range(window.chat_agents.count())))
            self.assertTrue(any(text == "Before the guest joined" for _, text in window.chats[room_id]["messages"]))
        self.send(self.guest, "Guest message")
        self.wait(lambda: any(text == "AI: Guest message" for _, text in self.host.chats[room_id]["messages"]))
        self.send(self.host, "Host message")
        self.wait(lambda: any(text == "AI: Host message" for _, text in self.guest.chats[room_id]["messages"]))
        self.assertEqual([text for _, text in self.host.chats[room_id]["messages"]],
                         [text for _, text in self.guest.chats[room_id]["messages"]])
        self.assertIn(("member:guest-laptop", "Guest message"), self.host.chats[room_id]["messages"])
        self.assertIn(("user", "Guest message"), self.guest.chats[room_id]["messages"])
        invite.close()

    def test_model_replies_render_markdown_without_changing_conversation_history(self):
        text = "## Model heading\n\nHere is **bold text** and `inline code`."
        self.host.select_agent(self.host.identity)
        chat = self.host.chats[self.host.identity]
        chat["messages"] = [("user", text), ("assistant", text), ("local_agent", text)]
        self.host.render_messages()
        # render_messages() rebuilds the bubble layout from scratch, and
        # render_agents() is reachable from a queued poller signal, so the
        # reply widgets are settled on rather than read the instant they are
        # created. Every other test in this file waits for the UI; this one
        # used to assert a single synchronous snapshot.
        self.wait(lambda: len(self.host.messages_widget.findChildren(MarkdownMessage)) == 2)
        replies = self.host.messages_widget.findChildren(MarkdownMessage)
        self.assertEqual(len(replies), 2)
        for reply in replies:
            self.assertEqual(reply.document().begin().blockFormat().headingLevel(), 2)
            self.assertNotIn("**", reply.toPlainText())
            self.assertIn("bold text", reply.toPlainText())
        self.assertEqual(chat["messages"], [("user", text), ("assistant", text), ("local_agent", text)])
        self.wait(lambda: self.host.messages.count() > 0)
        user_bubble = self.host.messages.itemAt(0).widget()
        self.assertEqual(user_bubble.layout().itemAt(1).widget().text(), text)
