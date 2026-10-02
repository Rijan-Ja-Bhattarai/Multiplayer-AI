import json
import socket

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QVBoxLayout

from network_a2a.adapters import PROVIDERS

from .theme import PROVIDER_NAMES
from .widgets import action, label


class AgentDialog(QDialog):
    def __init__(self, window, provider="ollama", profile=None):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Connect a model · Multiplayer AI")
        self.setMinimumWidth(490)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(28, 26, 28, 26)
        self.layout.setSpacing(14)
        self.layout.addWidget(label("MAKE ROOM FOR A NEW TEAMMATE", "eyebrow"))
        self.layout.addWidget(label("Connect a model", "title"))
        self.layout.addWidget(label("Your model runs on this device. Its key stays in your OS credential store.", "muted", True))
        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.name = QLineEdit()
        self.provider = QComboBox()
        for key, info in PROVIDER_NAMES.items():
            self.provider.addItem(info[0], key)
        self.provider.setCurrentIndex(self.provider.findData(provider))
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.lineEdit().setPlaceholderText("Model ID, e.g. llama3.2")
        self.models_button = action("Find local models", self.find_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model, 1)
        model_row.addWidget(self.models_button)
        self.base = QLineEdit()
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("API key · leave blank to keep a saved key")
        self.system = QPlainTextEdit()
        self.system.setMaximumHeight(90)
        self.system.setPlaceholderText("Optional instructions for this agent")
        self.autostart = QCheckBox("Start this agent automatically when the app opens")
        self.autostart.setChecked(True)
        self.insecure = QCheckBox("Allow a provider's HTTP endpoint on a trusted LAN")
        form.addRow("Agent name", self.name)
        form.addRow("Provider", self.provider)
        form.addRow("Model", model_row)
        form.addRow("API root", self.base)
        form.addRow("API key", self.key)
        form.addRow("Instructions", self.system)
        self.layout.addLayout(form)
        self.layout.addWidget(self.autostart)
        self.layout.addWidget(self.insecure)
        self.error = label("", "muted", True)
        self.error.setStyleSheet("color: #f38a8e;")
        self.layout.addWidget(self.error)
        row = QHBoxLayout()
        row.addWidget(action("Cancel", self.reject))
        self.save_button = action("Connect agent", self.save, True)
        row.addWidget(self.save_button)
        self.layout.addLayout(row)
        self.provider.currentIndexChanged.connect(self.change_provider)
        self.change_provider()
        if profile:
            self.name.setText(profile["id"])
            self.model.setCurrentText(profile["model"])
            self.base.setText(profile.get("base_url") or "")
            self.system.setPlainText(profile.get("system_prompt") or "")
            self.autostart.setChecked(profile.get("autostart", True))
            self.insecure.setChecked(profile.get("allow_insecure", False))
        if window.remote:
            self.name.setText(window.identity)
            self.name.setReadOnly(True)

    def change_provider(self):
        key = self.provider.currentData()
        spec = PROVIDERS[key]
        self.name.setText(f"{key}-agent")
        if self.window.remote:
            self.name.setText(self.window.identity)
        self.base.setText(spec.base_url or "")
        self.base.setPlaceholderText("https://your-deployment.example.com/v1")
        self.models_button.setVisible(key == "ollama")
        self.key.setPlaceholderText("Optional for local Ollama" if not spec.key_required else "API key · leave blank to keep a saved key")

    def find_models(self):
        self.models_button.setEnabled(False)
        def success(models):
            if self.isVisible():
                self.models_button.setEnabled(True)
                self.model.clear()
                self.model.addItems(models)
                if not models:
                    self.error.setText("No models found. Pull a model in Ollama first.")
        def fail(message):
            if self.isVisible():
                self.models_button.setEnabled(True)
                self.error.setText("Could not reach Ollama. Start Ollama on this device and try again.")
        self.window.command("ollama_models", self.base.text(), success=success, failure=fail)

    def save(self):
        self.error.setText("")
        profile = {"id": self.name.text().strip(), "provider": self.provider.currentData(),
                   "model": self.model.currentText().strip(), "base_url": self.base.text().strip(),
                   "system_prompt": self.system.toPlainText().strip(), "autostart": self.autostart.isChecked(),
                   "allow_insecure": self.insecure.isChecked()}
        self.save_button.setEnabled(False)
        def success(result):
            self.key.clear()
            self.accept()
        def failure(message):
            if self.isVisible():
                self.error.setText(message)
                self.save_button.setEnabled(True)
        self.window.command("save_agent", profile, self.key.text() or None, success=success, failure=failure)


class JoinDialog(QDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.setWindowTitle("Join a workspace")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(14)
        layout.addWidget(label("GOOD WORK TRAVELS", "eyebrow"))
        layout.addWidget(label("Join your team", "title"))
        layout.addWidget(label("Paste an invitation, or enter your relay address and private token.", "muted", True))
        self.invitation = QPlainTextEdit()
        self.invitation.setPlaceholderText("Paste your invitation JSON here")
        self.invitation.setMaximumHeight(100)
        layout.addWidget(self.invitation)
        self.url = QLineEdit()
        self.url.setPlaceholderText("wss://your-relay.example.com/connect")
        self.token = QLineEdit()
        self.token.setPlaceholderText("Your device's relay token")
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addWidget(label("Relay address", "muted"))
        layout.addWidget(self.url)
        layout.addWidget(label("Device token", "muted"))
        layout.addWidget(self.token)
        self.insecure = QCheckBox("This is a trusted LAN connection (allow ws://)")
        layout.addWidget(self.insecure)
        self.error = label("", "muted", True)
        self.error.setStyleSheet("color:#f38a8e")
        layout.addWidget(self.error)
        self.connect_button = action("Join workspace", self.join, True)
        layout.addWidget(self.connect_button)
        layout.addWidget(action("Cancel", self.reject))

    def join(self):
        self.error.setText("")
        try:
            conversation_id = None
            if self.invitation.toPlainText().strip():
                data = json.loads(self.invitation.toPlainText())
                if not isinstance(data, dict):
                    raise ValueError("Paste the invitation object beginning with { and ending with }")
                if data.get("version") != 1:
                    raise ValueError("Unsupported invitation version")
                self.url.setText(data["url"])
                self.token.setText(data["token"])
                self.insecure.setChecked(bool(data.get("allow_insecure")))
                conversation_id = data.get("conversation_id")
                if conversation_id is not None and (not isinstance(conversation_id, str)
                        or not conversation_id.startswith("conversation-")
                        or not conversation_id.removeprefix("conversation-").isalnum()):
                    raise ValueError("Use the complete shared conversation invitation")
            if len(self.token.text().strip()) < 32:
                raise ValueError("Enter the device token from your invitation")
            self.connect_button.setEnabled(False)
            self.connect_button.setText("Connecting…")
            self.error.setText("Contacting the relay. Keep the host app open; this can take up to 30 seconds.")
            def failure(message):
                if self.isVisible():
                    self.connect_button.setEnabled(True)
                    self.connect_button.setText("Join workspace")
                    self.error.setText(message or "The connection failed. Check the host address, network, and firewall.")
            def success(result):
                self.token.clear()
                self.invitation.clear()
                self.accept()
                if conversation_id:
                    self.window.select_agent(conversation_id)
                    return
                peer = next((agent for agent in self.window.agents if agent["online"] and agent["id"] != self.window.identity), None)
                if peer:
                    self.window.select_agent(peer["id"])
                else:
                    self.window.navigate(1)
                    self.window.notice("Workspace joined. No other devices are online yet. Keep the host app open and connect an agent to begin.")
            self.window.command("join", self.url.text().strip(), self.token.text().strip(), self.insecure.isChecked(), True, conversation_id,
                                success=success, failure=failure)
        except (ValueError, KeyError, TypeError) as exc:
            self.connect_button.setEnabled(True)
            self.connect_button.setText("Join workspace")
            self.error.setText(str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError)
                               else "Paste the complete invitation JSON, including its url, token, and version fields.")


class InviteDialog(QDialog):
    def __init__(self, window, conversation_id=None, target=None, messages=None):
        super().__init__(window)
        self.window = window
        self.conversation_id = conversation_id
        self.target = target
        self.messages = messages
        self.setWindowTitle("Invite a device")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(14)
        layout.addWidget(label("MAKE YOUR WORKSPACE MULTIPLAYER", "eyebrow"))
        layout.addWidget(label("Invite a device", "title"))
        layout.addWidget(label("Give each laptop or desktop its own identity. Share its invitation privately.", "muted", True))
        if conversation_id or target:
            self.setWindowTitle("Invite to conversation")
            layout.addWidget(label("This invitation shares the conversation's messages and AI replies with the other device.", "muted", True))
        self.name = QLineEdit()
        self.name.setPlaceholderText("e.g. alex-laptop")
        self.lan = QCheckBox("Share this relay on my local network")
        self.lan.setChecked(True)
        self.url = QLineEdit()
        try:
            addresses = socket.gethostbyname_ex(socket.gethostname())[2]
            address = next((ip for ip in addresses if not ip.startswith("127.")), "YOUR_LAN_IP")
        except OSError:
            address = "YOUR_LAN_IP"
        self.url.setText(f"ws://{address}:{window.port}/connect")
        layout.addWidget(label("New device identity", "muted"))
        layout.addWidget(self.name)
        layout.addWidget(self.lan)
        layout.addWidget(label("Address the other device can reach", "muted"))
        layout.addWidget(self.url)
        layout.addWidget(label("LAN sharing makes this relay listen for connections from other devices while the app is open. Your firewall must allow its port. For internet access, enter the WSS address of a TLS proxy pointing to this relay, or join a hosted relay.", "muted", True))
        self.error = label("", "muted", True)
        self.error.setStyleSheet("color:#f38a8e")
        layout.addWidget(self.error)
        self.create_button = action("Create invitation", self.create, True)
        layout.addWidget(self.create_button)
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setMaximumHeight(180)
        self.result.hide()
        layout.addWidget(self.result)
        self.copy_button = action("Copy private invitation", self.copy)
        self.copy_button.hide()
        layout.addWidget(self.copy_button)
        layout.addWidget(action("Done", self.accept))

    def create(self):
        self.create_button.setEnabled(False)
        def success(data):
            if self.isVisible():
                self.result.setPlainText(json.dumps(data, indent=2))
                self.result.show()
                self.copy_button.show()
                if data.get("conversation_id"):
                    self.conversation_id = data["conversation_id"]
                    self.window.select_agent(self.conversation_id)
        def failure(message):
            if self.isVisible():
                self.error.setText(message)
                self.create_button.setEnabled(True)
        self.window.command("invite", self.name.text().strip(), self.url.text().strip(), self.lan.isChecked(),
                            self.conversation_id, self.target, self.messages, success=success, failure=failure)

    def copy(self):
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.result.toPlainText())
        self.window.notice("Private invitation copied. Send it only to the intended device.")
