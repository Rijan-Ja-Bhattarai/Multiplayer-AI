"""Exercise saved context and workspace controls through native desktop windows."""
import asyncio
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QInputDialog, QMenu, QMessageBox
from starlette.responses import JSONResponse
from starlette.routing import Route

from network_a2a.persistence import HistoryStore

from desktop_app.dialogs import AgentDialog, InviteDialog, JoinDialog
from desktop_app.agent_list_dialog import AgentListDialog
from desktop_app.storage import Storage
from desktop_app.window import MainWindow
from desktop_app.workspace_dialog import WorkspaceDialog
from tests.test_desktop_runtime import MemoryVault
from tests.test_attachments import make_files


class WorkspaceUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)

    def wait(self, predicate, timeout=15):
        deadline = time.monotonic() + timeout
        while not predicate():
            self.app.processEvents()
            if time.monotonic() > deadline:
                self.fail("Workspace UI condition timed out")
            time.sleep(.01)
        self.app.processEvents()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.vault = MemoryVault()
        self.host = self.open_window("host", self.vault)
        self.guest = None

    def open_window(self, name, vault):
        window = MainWindow(Storage(Path(self.directory.name) / name, vault))
        window.show()
        self.wait(lambda: window.ready)
        return window

    def stop(self, window):
        window.close()
        self.wait(lambda: not window.network.isRunning())

    def tearDown(self):
        if self.guest:
            self.stop(self.guest)
        self.stop(self.host)
        self.directory.cleanup()

    def configure_model(self, handler, vision=False):
        self.host.network.runtime.app.router.routes.append(Route("/mock/v1/chat/completions", handler, methods=["POST"]))
        dialog = AgentDialog(self.host, "bionic")
        dialog.show()
        dialog.name.setText("saved-model")
        dialog.base.setText(f"http://127.0.0.1:{self.host.port}/mock/v1")
        dialog.model.setCurrentText("test-model")
        dialog.key.setText("test-key")
        dialog.vision.setChecked(vision)
        QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: not dialog.isVisible())
        self.host.select_agent("saved-model")

    @staticmethod
    async def echo_model(request):
        body = await request.json()
        return JSONResponse({"choices": [{"message": {"content": "AI: " + body["messages"][-1]["content"]}, "finish_reason": "stop"}]})

    def send(self, window, text):
        self.wait(lambda: window.send_button.isEnabled())
        window.composer.setPlainText(text)
        QTest.mouseClick(window.send_button, Qt.MouseButton.LeftButton)

    def reply_received(self, window, target, text):
        return ("assistant", "AI: " + text) in window.chats.get(target, {}).get("messages", [])

    def switch(self, window, workspace_id):
        QTest.mouseClick(window.workspace_buttons[workspace_id], Qt.MouseButton.LeftButton)
        self.wait(lambda: window.workspace_id == workspace_id and window.network.runtime.active_workspace_id == workspace_id)

    def test_ai_context_draft_and_selected_chat_survive_a_full_app_restart(self):
        calls = []
        async def model(request):
            calls.append((await request.json())["messages"])
            return await self.echo_model(request)
        self.configure_model(model)
        self.send(self.host, "My project is named Aurora")
        self.wait(lambda: self.reply_received(self.host, "saved-model", "My project is named Aurora"))
        self.host.composer.setPlainText("Unsaved draft survives too")
        self.stop(self.host)
        self.host = self.open_window("host", self.vault)
        self.host.network.runtime.app.router.routes.append(Route("/mock/v1/chat/completions", model, methods=["POST"]))
        self.assertEqual(self.host.selected, "saved-model")
        self.assertEqual(self.host.composer.toPlainText(), "Unsaved draft survives too")
        self.assertTrue(self.reply_received(self.host, "saved-model", "My project is named Aurora"))
        self.assertEqual(len(calls), 1)
        self.send(self.host, "What was my project name?")
        self.wait(lambda: len(calls) == 2 and self.reply_received(self.host, "saved-model", "What was my project name?"))
        self.assertEqual(calls[-1][-3:], [{"role": "user", "content": "My project is named Aurora"},
            {"role": "assistant", "content": "AI: My project is named Aurora"}, {"role": "user", "content": "What was my project name?"}])

    def test_create_rename_switch_and_delete_controls_preserve_other_workspace_history(self):
        original = self.host.workspace_id
        self.host.select_agent(self.host.identity)
        target = self.host.identity
        self.send(self.host, "Keep the original chat")
        self.wait(lambda: not self.host.chats[target]["pending"])
        original_messages = list(self.host.chats[target]["messages"])
        with patch("desktop_app.window.QInputDialog.getText", return_value=("Research", True)):
            self.host.create_workspace()
        self.wait(lambda: self.host.workspace_id != original)
        second = self.host.workspace_id
        self.assertEqual(self.host.workspace_label.text(), "Research")
        self.assertNotIn(target, self.host.chats)
        dialog = WorkspaceDialog(self.host)
        dialog.show()
        dialog.name.setText("Research lab")
        QTest.mouseClick(dialog.rename_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: self.host.workspace_label.text() == "Research lab")
        self.assertEqual(self.host.workspace_buttons[second].toolTip(), "Research lab")
        dialog.close()
        self.switch(self.host, original)
        self.assertEqual(self.host.chats[target]["messages"], original_messages)
        self.switch(self.host, second)
        deleted_directory = Path(self.directory.name) / "host" / "workspaces" / second
        deleted_directory.mkdir(parents=True, exist_ok=True)
        (deleted_directory / "settings.json").write_text("{}", encoding="utf-8")
        deleted_archive = deleted_directory / "history.sqlite3"
        HistoryStore(deleted_directory).save("ui", "state", {"chats": {"gone": {}}})
        dialog = WorkspaceDialog(self.host)
        dialog.show()
        with patch("desktop_app.workspace_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            QTest.mouseClick(dialog.delete_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: self.host.workspace_id == original and not dialog.isVisible())
        self.assertNotIn(second, self.host.workspace_buttons)
        self.assertEqual(self.host.chats[target]["messages"], original_messages)
        # The workspace's directory is removed outright, so its settings and
        # chat archive go with it rather than being emptied in place.
        self.assertFalse(deleted_directory.exists())

    def test_repeated_last_workspace_deletion_keeps_window_open_and_stops_closed_dialogs(self):
        dialogs = []
        for _ in range(3):
            original = self.host.workspace_id
            dialog = WorkspaceDialog(self.host)
            dialogs.append(dialog)
            QTimer.singleShot(20, dialog.delete_button.click)
            with patch("desktop_app.workspace_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                dialog.exec()
            self.assertFalse(dialog.timer.isActive())
            self.assertNotEqual(self.host.workspace_id, original)
            self.assertTrue(self.host.isVisible())
            self.assertTrue(self.host.network.isRunning())
            self.assertEqual(len(self.host.workspace_list), 1)
        # A closed dialog used to fire its table-refresh timer here and crash Qt.
        QTest.qWait(1200)
        self.assertTrue(self.host.isVisible())
        self.assertTrue(self.host.network.isRunning())
        self.host.command("create_workspace", "After deletion")
        self.wait(lambda: len(self.host.workspace_list) == 2)

    def test_invitation_network_selector_updates_address_and_preserves_manual_wss(self):
        with patch("desktop_app.dialogs.lan_addresses", return_value=[
                ("Wi-Fi", "192.168.43.12"), ("Ethernet", "192.168.1.2")]):
            dialog = InviteDialog(self.host)
        self.assertEqual(dialog.url.text(), f"ws://192.168.43.12:{self.host.port}/connect")
        dialog.network_address.setCurrentIndex(1)
        self.assertEqual(dialog.url.text(), f"ws://192.168.1.2:{self.host.port}/connect")
        dialog.lan.setChecked(False)
        dialog.url.setText("wss://relay.example.com/connect")
        dialog.network_address.setCurrentIndex(0)
        self.assertEqual(dialog.url.text(), "wss://relay.example.com/connect")
        dialog.close()

    def test_create_from_real_menu_and_dialog_preserves_existing_animated_rail_button(self):
        original = self.host.workspace_id
        button = self.host.workspace_buttons[original]
        button.animation.setDuration(10000)
        button.animation.setStartValue(23.0)
        button.animation.setEndValue(15.0)
        button.animation.start()
        stages = []
        deadline = time.monotonic() + 15

        def drive_dialogs():
            popup = self.app.activePopupWidget()
            dialog = self.app.activeModalWidget()
            if time.monotonic() > deadline:
                if isinstance(dialog, QInputDialog):
                    dialog.reject()
                if isinstance(popup, QMenu):
                    popup.close()
                driver.stop()
            elif isinstance(popup, QMenu) and not stages:
                stages.append("menu")
                QTest.mouseClick(popup, Qt.MouseButton.LeftButton, pos=popup.actionGeometry(popup.actions()[0]).center())
            elif isinstance(dialog, QInputDialog) and stages == ["menu"]:
                stages.append("dialog")
                dialog.setTextValue("Created through the menu")
                buttons = dialog.findChild(QDialogButtonBox)
                QTest.mouseClick(buttons.button(QDialogButtonBox.StandardButton.Ok), Qt.MouseButton.LeftButton)

        driver = QTimer(self.host)
        driver.setInterval(10)
        driver.timeout.connect(drive_dialogs)
        watchdog = QTimer(self.host)
        watchdog.setSingleShot(True)
        def dismiss_dialogs():
            dialog = self.app.activeModalWidget()
            popup = self.app.activePopupWidget()
            if isinstance(dialog, QInputDialog):
                dialog.reject()
            if isinstance(popup, QMenu):
                popup.close()
        watchdog.timeout.connect(dismiss_dialogs)
        watchdog.start(15000)
        driver.start()
        try:
            QTest.mouseClick(self.host.add_workspace_button, Qt.MouseButton.LeftButton)
        finally:
            driver.stop()
            watchdog.stop()
        self.assertEqual(stages, ["menu", "dialog"])
        self.wait(lambda: self.host.workspace_id != original)
        self.assertTrue(self.host.isVisible())
        self.assertTrue(self.host.network.isRunning())
        self.assertEqual(self.host.workspace_label.text(), "Created through the menu")
        self.assertIs(self.host.workspace_buttons[original], button)
        self.assertEqual(self.host.workspace_rail.count(), 2)
        self.switch(self.host, original)
        self.assertIs(self.host.workspace_buttons[original], button)

    def test_shared_chat_is_readable_offline_and_members_can_be_managed_after_restart(self):
        self.configure_model(self.echo_model)
        self.send(self.host, "Remember this shared project")
        self.wait(lambda: self.reply_received(self.host, "saved-model", "Remember this shared project"))
        invite = InviteDialog(self.host, target="saved-model", messages=list(self.host.chats["saved-model"]["history"]))
        invite.show()
        invite.name.setText("guest-laptop")
        invite.url.setText(f"ws://127.0.0.1:{self.host.port}/connect")
        QTest.mouseClick(invite.create_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: bool(invite.result.toPlainText()))
        invitation, room_id = invite.result.toPlainText(), invite.conversation_id
        invite.close()
        guest_vault = MemoryVault()
        self.guest = self.open_window("guest", guest_vault)
        join = JoinDialog(self.guest)
        join.show()
        join.invitation.setPlainText(invitation)
        QTest.mouseClick(join.connect_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: not join.isVisible() and self.guest.selected == room_id)
        guest_settings = WorkspaceDialog(self.guest)
        self.assertTrue(guest_settings.name.isReadOnly())
        self.assertFalse(guest_settings.rename_button.isEnabled())
        self.assertFalse(guest_settings.invite_button.isEnabled())
        guest_settings.close()
        self.stop(self.guest)
        self.stop(self.host)
        self.guest = self.open_window("guest", guest_vault)
        self.assertEqual(self.guest.selected, room_id)
        self.assertTrue(self.reply_received(self.guest, room_id, "Remember this shared project"))
        self.assertFalse(self.guest.send_button.isEnabled())
        self.host = self.open_window("host", self.vault)
        self.host.network.runtime.app.router.routes.append(Route("/mock/v1/chat/completions", self.echo_model, methods=["POST"]))
        join = JoinDialog(self.guest)
        join.show()
        join.invitation.setPlainText(invitation)
        QTest.mouseClick(join.connect_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: not join.isVisible())
        self.send(self.guest, "Continue after reopening")
        self.wait(lambda: self.reply_received(self.guest, room_id, "Continue after reopening"))
        self.wait(lambda: self.reply_received(self.host, room_id, "Continue after reopening"))
        settings = WorkspaceDialog(self.host)
        settings.show()
        row = next(row for row in range(settings.members.rowCount()) if settings.members.item(row, 0).text() == "guest-laptop")
        with patch("desktop_app.workspace_dialog.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            QTest.mouseClick(settings.members.cellWidget(row, 3), Qt.MouseButton.LeftButton)
        self.wait(lambda: "guest-laptop" not in self.host.network.runtime.credentials)
        self.wait(lambda: all(member["id"] != "guest-laptop" for member in self.host.workspace_meta["members"]))
        settings.close()

    def test_reply_survives_switching_away_and_back_while_request_runs(self):
        started, release = threading.Event(), threading.Event()
        async def delayed_model(request):
            started.set()
            while not release.is_set():
                await asyncio.sleep(.01)
            return await self.echo_model(request)
        self.configure_model(delayed_model)
        first = self.host.workspace_id
        with patch("desktop_app.window.QInputDialog.getText", return_value=("Other workspace", True)):
            self.host.create_workspace()
        self.wait(lambda: self.host.workspace_id != first)
        second = self.host.workspace_id
        self.switch(self.host, first)
        self.send(self.host, "Reply after switching back")
        self.wait(started.is_set)
        self.switch(self.host, second)
        self.switch(self.host, first)
        self.assertTrue(self.host.chats["saved-model"]["pending"])
        release.set()
        self.wait(lambda: self.reply_received(self.host, "saved-model", "Reply after switching back"))
        self.assertFalse(self.host.chats["saved-model"]["pending"])
        started.clear()
        release.clear()
        self.send(self.host, "Reply while away")
        self.wait(started.is_set)
        self.switch(self.host, second)
        release.set()
        self.wait(lambda: not self.host.live_requests)
        self.assertNotIn("saved-model", self.host.chats)
        self.switch(self.host, first)
        self.assertTrue(self.reply_received(self.host, "saved-model", "Reply while away"))
        self.assertFalse(self.host.chats["saved-model"]["pending"])

    def test_counts_and_clickable_lists_include_models_and_exclude_devices(self):
        self.configure_model(self.echo_model)
        result = []
        self.host.command("invite", "another-user", f"ws://127.0.0.1:{self.host.port}/connect", success=result.append)
        self.wait(lambda: bool(result) and len(self.host.agents) == 3)
        self.assertEqual(self.host.stat_values[0].text(), "1")
        self.assertEqual(self.host.stat_values[1].text(), "1")
        self.assertEqual(self.host.chat_agent_count.text(), "1 agent")
        for button in (self.host.chat_agent_count, self.host.stat_values[0]):
            captured = []
            def inspect_dialog():
                dialog = self.app.activeModalWidget()
                if isinstance(dialog, AgentListDialog):
                    captured.append([dialog.table.item(row, 0).text() for row in range(dialog.table.rowCount())])
                    dialog.accept()
            driver = QTimer(self.host)
            driver.setInterval(10)
            driver.timeout.connect(inspect_dialog)
            driver.start()
            try:
                button.click()
            finally:
                driver.stop()
            self.assertEqual(captured, [["saved-model"]])

    def test_edit_model_saves_and_uses_searxng_settings(self):
        calls, searches = [], []
        async def model(request):
            body = await request.json()
            calls.append(body)
            text = '{"web_search_query":"current public facts"}' if "optional web search tool" in body["messages"][0]["content"] else "A verified answer"
            return JSONResponse({"choices": [{"message": {"content": text}}]})
        async def search(request):
            searches.append(dict(request.query_params))
            return JSONResponse({"results": [{"title": "Public facts", "url": "https://source.example/current", "content": "Evidence today"}]})
        self.configure_model(model)
        self.host.network.runtime.app.router.routes.append(Route("/search", search))
        profile = next(agent["profile"] for agent in self.host.agents if agent["id"] == "saved-model")
        dialog = AgentDialog(self.host, "bionic", profile)
        dialog.show()
        self.assertTrue(dialog.name.isReadOnly())
        dialog.internet.setChecked(True)
        QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        self.wait(lambda: dialog.save_button.isEnabled())
        self.assertTrue(dialog.isVisible())
        self.assertIn("SearXNG", dialog.error.text())
        dialog.search_url.setText(f"http://127.0.0.1:{self.host.port}")
        dialog.search_test.click()
        self.wait(lambda: "reachable" in dialog.search_status.text())
        dialog.vision.setChecked(True)
        dialog.save_button.click()
        self.wait(lambda: not dialog.isVisible())
        saved = self.host.network.runtime.storage.settings["agents"][0]
        self.assertEqual(saved["web_search"], "auto")
        self.assertTrue(saved["vision"])
        restored = AgentDialog(self.host, "bionic", saved)
        self.assertTrue(restored.internet.isChecked())
        self.assertEqual(restored.search_mode.currentData(), "auto")
        self.assertEqual(restored.search_url.text(), dialog.search_url.text())
        restored.close()
        self.send(self.host, "Tell me the latest facts")
        self.wait(lambda: not self.host.chats["saved-model"]["pending"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(searches[-1]["q"], "current public facts")
        self.assertIn("Evidence today", calls[-1]["messages"][0]["content"])
        self.assertIn("https://source.example/current", self.host.chats["saved-model"]["messages"][-1][1])

    def test_uploaded_image_and_pdf_reach_model_and_survive_restart(self):
        calls = []
        async def model(request):
            calls.append(await request.json())
            return JSONResponse({"choices": [{"message": {"content": "Files understood"}}]})
        self.configure_model(model, vision=True)
        files = make_files(Path(self.directory.name))
        with patch("desktop_app.window.QFileDialog.getOpenFileNames", return_value=([str(path) for path in files[:2]], "")):
            self.host.attach_button.click()
        self.wait(lambda: not self.host.preparing_files)
        attached = self.host.chats["saved-model"]["draft_attachments"]
        self.assertEqual(len(attached), 2)
        self.host.composer.setPlainText("Please understand these files")
        self.host.send_button.click()
        self.wait(lambda: ("assistant", "Files understood") in self.host.chats["saved-model"]["messages"])
        content = calls[0]["messages"][-1]["content"]
        self.assertTrue(any(part.get("type") == "image_url" for part in content))
        self.assertTrue(any("Aurora" in part.get("text", "") for part in content))
        self.assertIn("report.pdf", self.host.chats["saved-model"]["messages"][0][1])
        self.assertNotIn("base64", self.host.chats["saved-model"]["messages"][0][1])
        self.stop(self.host)
        self.host = self.open_window("host", self.vault)
        self.host.network.runtime.app.router.routes.append(Route("/mock/v1/chat/completions", model, methods=["POST"]))
        self.send(self.host, "What did the PDF say?")
        self.wait(lambda: len(calls) == 2 and not self.host.chats["saved-model"]["pending"])
        self.assertEqual(calls[-1]["messages"][0]["content"], content)
        self.assertEqual(calls[-1]["messages"][-1]["content"], "What did the PDF say?")

    def test_guest_can_upload_files_to_shared_model_chat(self):
        calls = []
        async def model(request):
            calls.append(await request.json())
            return JSONResponse({"choices": [{"message": {"content": "Shared files understood"}}]})
        self.configure_model(model, vision=True)
        invite = InviteDialog(self.host, target="saved-model")
        invite.show()
        invite.name.setText("guest-laptop")
        invite.url.setText(f"ws://127.0.0.1:{self.host.port}/connect")
        invite.create_button.click()
        self.wait(lambda: bool(invite.result.toPlainText()))
        room_id = invite.conversation_id
        self.guest = self.open_window("guest", MemoryVault())
        join = JoinDialog(self.guest)
        join.show()
        join.invitation.setPlainText(invite.result.toPlainText())
        join.connect_button.click()
        self.wait(lambda: not join.isVisible() and self.guest.selected == room_id)
        self.assertEqual(self.guest.stat_values[0].text(), "1")
        self.assertEqual(self.guest.chat_agent_count.text(), "1 agent")
        files = make_files(Path(self.directory.name))
        self.guest.prepare_files([str(path) for path in files[:2]])
        self.wait(lambda: not self.guest.preparing_files)
        self.assertEqual(len(self.guest.chats[room_id]["draft_attachments"]), 2)
        self.send(self.guest, "Understand these shared files")
        self.wait(lambda: ("assistant", "Shared files understood") in self.guest.chats[room_id]["messages"])
        self.wait(lambda: ("assistant", "Shared files understood") in self.host.chats[room_id]["messages"])
        self.assertTrue(any("Aurora" in part.get("text", "") for part in calls[0]["messages"][-1]["content"]))
        self.assertIn("report.pdf", self.host.chats[room_id]["messages"][0][1])
        self.assertIsInstance(self.host.chats[room_id]["messages"][0][1], str)
        self.assertIsInstance(self.host.conversations[room_id]["messages"][0]["content"], list)
        invite.close()
