"""Connect a model, define its purpose, choose Jev, and send through the native UI."""
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication, QLabel
from starlette.responses import JSONResponse
from starlette.routing import Route

from desktop_app.storage import Storage
from desktop_app.window import CONVERSATIONS_PAGE, MainWindow
from network_a2a.orchestration import GENERAL_TARGET
from tests.test_desktop_runtime import MemoryVault


class OrchestrationUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait(self, predicate):
        deadline = time.monotonic() + 15
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("Native orchestration UI condition timed out")
            time.sleep(.01)
        self.app.processEvents()

    def test_default_ollama_import_with_saved_empty_coordinator_can_send_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            storage = Storage(directory, MemoryVault())
            storage.settings["coordinator"] = None
            storage.save()
            window = MainWindow(storage)
            window.show()
            try:
                self.wait(lambda: window.ready and window.agents)
                seen = []
                answer = 'print("Hello, world!")'

                async def provider(request):
                    seen.append(await request.json())
                    return JSONResponse({"message": {"content": answer}, "done": True, "done_reason": "stop"})

                window.network.runtime.app.router.routes.append(Route("/mock/api/chat", provider, methods=["POST"]))
                for model in ("first-model-id", "second-model-id"):
                    window.add_agent("ollama")
                    name = window.model_name.text()
                    self.assertEqual(name, "ollama-agent" if model == "first-model-id" else "ollama-agent-2")
                    window.model_id.setCurrentText(model)
                    window.model_base.setText(f"http://127.0.0.1:{window.port}/mock")
                    self.assertEqual(window.model_purpose.values(), {"purpose": "", "tasks": [], "delegation_enabled": False})
                    QTest.mouseClick(window.model_save_button, Qt.MouseButton.LeftButton)
                    self.wait(lambda: window.selected == GENERAL_TARGET and window.stack.currentIndex() == CONVERSATIONS_PAGE
                              and window.send_button.isEnabled() and any(agent["id"] == name for agent in window.model_agents()))
                    self.assertEqual(window.coordinator_picker.currentData(), "ollama-agent")
                    self.assertEqual(window.chat_title.text(), "General chat")
                    self.assertEqual(window.chat_agent_count.text(), "1 agent")
                    self.assertIn("Ready to chat", window.chat_subtitle.text())
                    prompt = "Make a hello world program" if model == "first-model-id" else "Now use a function"
                    window.composer.setPlainText(prompt)
                    QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
                    expected = 1 if model == "first-model-id" else 2
                    self.wait(lambda: len(seen) == expected and not window.chats[GENERAL_TARGET]["pending"])
                    self.assertEqual(seen[-1]["model"], "first-model-id")
                    self.assertEqual(seen[-1]["messages"][-1]["content"], prompt)
                    self.assertEqual(window.chats[GENERAL_TARGET]["messages"][-1], ("assistant", answer))
                self.assertEqual(len(window.model_agents()), 2)
                self.assertEqual(len(seen[-1]["messages"]), 3)
                self.assertEqual(seen[-1]["messages"][0]["content"], "Make a hello world program")
                self.assertEqual(window.chats[GENERAL_TARGET]["routing"]["mode"], "direct")
            finally:
                window.close()
                self.wait(lambda: not window.network.isRunning())

    def test_model_purpose_coordinator_general_chat_and_edit_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            window = MainWindow(Storage(directory, MemoryVault()))
            window.show()
            try:
                self.wait(lambda: window.ready and window.agents)
                seen = []

                async def provider(request):
                    body = await request.json()
                    seen.append(body)
                    if body["messages"][-1]["content"].startswith("You are Jev, the task coordinator"):
                        answer = json.dumps({"tasks": [{"agent_id": "python-model", "task_type": "debugging",
                                                         "instruction": "Debug the original code"}], "reason": "Python specialist", "reply": ""})
                    else:
                        answer = "The bug is fixed."
                    return JSONResponse({"choices": [{"message": {"content": answer}, "finish_reason": "stop"}]})

                window.network.runtime.app.router.routes.append(Route("/mock/v1/chat/completions", provider, methods=["POST"]))
                window.add_agent("bionic")
                window.model_name.setText("python-model")
                window.model_id.setCurrentText("any-model-id")
                window.model_base.setText(f"http://127.0.0.1:{window.port}/mock/v1")
                window.model_key.setText("mock-key")
                window.model_vision.setChecked(True)
                window.model_purpose.purpose.setPlainText("Python coding and debugging; avoid writing")
                window.model_purpose.tasks["debugging"].setChecked(True)
                window.model_purpose.enabled.setChecked(True)
                QTest.mouseClick(window.model_save_button, Qt.MouseButton.LeftButton)
                self.wait(lambda: window.selected == GENERAL_TARGET and window.workspace_meta.get("coordinator") == "python-model"
                          and window.send_button.isEnabled() and window.model_profile_id is None)
                self.assertEqual(window.stack.currentIndex(), CONVERSATIONS_PAGE)
                self.assertEqual(window.chat_title.text(), "General chat")
                self.assertEqual(window.coordinator_picker.currentData(), "python-model")
                image = QImage(20, 20, QImage.Format.Format_RGB32)
                image.fill(0xff000000)
                image_path = Path(directory) / "bug.png"
                self.assertTrue(image.save(str(image_path)))
                window.prepare_files([str(image_path)])
                self.wait(lambda: not window.preparing_files)
                self.assertEqual(len(window.chats[GENERAL_TARGET]["draft_attachments"]), 1)
                code = "Debug y\n```python\ndef f(): return 1 / 0\n```"
                window.composer.setPlainText(code)
                QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)
                self.wait(lambda: ("assistant", "The bug is fixed.") in window.chats[GENERAL_TARGET]["messages"])
                worker_content = seen[1]["messages"][0]["content"]
                self.assertEqual(worker_content[0]["text"], code)
                self.assertEqual(worker_content[1]["type"], "image_url")
                self.assertEqual(window.chats[GENERAL_TARGET]["history"][0]["content"][0]["text"], code)
                self.assertEqual(window.chat_title.text(), "General chat")
                self.assertTrue(any("Python specialist" in child.text() for child in window.messages_widget.findChildren(QLabel)))

                chat = window.chats[GENERAL_TARGET]
                saved_history = list(chat["history"])
                window.add_agent("bionic")
                window.model_name.setText("second-model")
                window.model_id.setCurrentText("second-model-id")
                window.model_base.setText(f"http://127.0.0.1:{window.port}/mock/v1")
                window.model_key.setText("second-mock-key")
                window.model_purpose.purpose.setPlainText("Debugging specialist")
                window.model_purpose.tasks["debugging"].setChecked(True)
                window.model_purpose.enabled.setChecked(True)
                QTest.mouseClick(window.model_save_button, Qt.MouseButton.LeftButton)
                self.wait(lambda: len(window.model_agents()) == 2 and window.selected == GENERAL_TARGET
                          and window.stack.currentIndex() == CONVERSATIONS_PAGE)
                self.assertEqual(window.workspace_meta["coordinator"], "python-model")
                self.assertIs(window.chats[GENERAL_TARGET], chat)
                self.assertEqual(chat["history"], saved_history)
                for listing in (window.chat_agents, window.sidebar_agents):
                    general_rows = [listing.item(index) for index in range(listing.count())
                                    if listing.item(index).data(Qt.ItemDataRole.UserRole) == GENERAL_TARGET]
                    self.assertEqual(len(general_rows), 1)
                    self.assertEqual(general_rows[0].text(), "General chat")

                agent = next(agent for agent in window.agents if agent["id"] == "python-model")
                window.edit_agent(agent)
                self.assertEqual(window.model_purpose.purpose.toPlainText(), "Python coding and debugging; avoid writing")
                self.assertTrue(window.model_purpose.tasks["debugging"].isChecked())
                self.assertFalse(window.model_purpose.tasks["coding"].isChecked())
                self.assertTrue(window.model_purpose.enabled.isChecked())
                window.add_agent("ollama")
                self.assertFalse(window.model_purpose.enabled.isChecked())
                self.assertEqual(window.model_purpose.purpose.toPlainText(), "")
                self.assertFalse(any(checkbox.isChecked() for checkbox in window.model_purpose.tasks.values()))
            finally:
                window.close()
                self.wait(lambda: not window.network.isRunning())
