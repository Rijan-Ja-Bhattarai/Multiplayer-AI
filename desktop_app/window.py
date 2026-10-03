import json
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, Slot
from PySide6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import (QCheckBox, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QProgressBar, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget, QInputDialog, QMenu)

from network_a2a.persistence import HistoryStore, model_context

from .bridge import NetworkThread
from .dialogs import AgentDialog, InviteDialog, JoinDialog
from .markdown import MarkdownMessage
from .theme import PROVIDER_NAMES, THEME
from .widgets import Composer, OrbitArt, WorkspaceButton, action, label


def frame(name, layout_type=QVBoxLayout):
    widget = QFrame()
    widget.setObjectName(name)
    layout = layout_type(widget)
    return widget, layout


def clear_layout(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


def app_icon():
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#5865f2"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, 64, 64, 20, 20)
    painter.setPen(QColor("white"))
    painter.setFont(QFont("Segoe UI", 27, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "M")
    painter.end()
    return QIcon(pixmap)


class MainWindow(QMainWindow):
    def __init__(self, storage):
        super().__init__()
        self.storage = storage
        self.agents = []
        self.identity = ""
        self.remote = False
        self.port = 0
        self.selected = None
        self.chats = {}
        self.conversations = {}
        self.workspace_id = None
        self.workspace_list = []
        self.workspace_meta = {}
        self.history_store = None
        self.live_requests = set()
        self.workspace_buttons = {}
        self.callbacks = {}
        self.request_count = 0
        self.ready = False
        self.closing = False
        self.setWindowTitle("Multiplayer AI")
        self.setWindowIcon(app_icon())
        self.resize(1330, 910)
        self.setMinimumSize(1010, 690)
        self.setStyleSheet(THEME)
        self.network = NetworkThread(storage, self)
        self.network.event.connect(self.network_event)
        self.network.completed.connect(self.command_success)
        self.network.failed.connect(self.command_failure)
        self.network.finished.connect(self.finish_close)
        self.build_ui()
        self.history_timer = QTimer(self)
        self.history_timer.setSingleShot(True)
        self.history_timer.setInterval(350)
        self.history_timer.timeout.connect(self.persist_history)
        self.composer.textChanged.connect(lambda: self.history_timer.start())
        self.network.start()

    def build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        rail, rail_layout = frame("rail")
        rail.setFixedWidth(72)
        rail_layout.setContentsMargins(12, 20, 12, 18)
        rail_layout.setSpacing(16)
        home = WorkspaceButton("M")
        home.setToolTip("Your local workspace")
        home.clicked.connect(lambda: self.command("use_local"))
        rail_layout.addWidget(home)
        local = WorkspaceButton("⌘")
        local.setToolTip("Workspace overview")
        local.clicked.connect(lambda: self.navigate(0))
        rail_layout.addWidget(local)
        workspace_scroll = QScrollArea()
        workspace_scroll.setWidgetResizable(True)
        workspace_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        workspace_content = QWidget()
        self.workspace_rail = QVBoxLayout(workspace_content)
        self.workspace_rail.setContentsMargins(0, 0, 0, 0)
        self.workspace_rail.setSpacing(12)
        self.workspace_rail.setAlignment(Qt.AlignmentFlag.AlignTop)
        workspace_scroll.setWidget(workspace_content)
        rail_layout.addWidget(workspace_scroll, 1)
        join = WorkspaceButton("+")
        join.setStyleSheet("color:#3ba55d")
        join.setToolTip("Create or join a workspace")
        join.clicked.connect(self.add_workspace)
        self.add_workspace_button = join
        rail_layout.addWidget(join)
        help_button = WorkspaceButton("?")
        help_button.clicked.connect(lambda: self.notice("The app starts your local relay automatically. Connect a model in Providers, or join a team with an invitation."))
        help_button.setToolTip("Quick help")
        rail_layout.addWidget(help_button)
        shell.addWidget(rail)
        sidebar, side = frame("sidebar")
        sidebar.setFixedWidth(238)
        side.setContentsMargins(14, 22, 14, 0)
        side.setSpacing(8)
        self.workspace_label = label("My workspace", "heading", True)
        side.addWidget(self.workspace_label)
        side.addWidget(label("Your intelligence, connected.", "muted"))
        self.workspace_settings_button = action("Workspace settings", self.manage_workspace, name="ghost")
        self.workspace_settings_button.setEnabled(False)
        side.addWidget(self.workspace_settings_button)
        side.addSpacing(25)
        side.addWidget(label("WORKSPACE", "eyebrow"))
        self.nav_buttons = []
        for index, name in enumerate(("◫   Overview", "⌘   Agents", "▤   Conversations", "◇   Providers")):
            button = action(name, lambda checked=False, page=index: self.navigate(page), name="nav")
            button.setCheckable(True)
            self.nav_buttons.append(button)
            side.addWidget(button)
        side.addSpacing(23)
        side.addWidget(label("CHATS AND AGENTS", "eyebrow"))
        self.sidebar_agents = QListWidget()
        self.sidebar_agents.setMaximumHeight(245)
        self.sidebar_agents.itemClicked.connect(lambda item: self.select_agent(item.data(Qt.ItemDataRole.UserRole)))
        side.addWidget(self.sidebar_agents)
        side.addStretch()
        self.connection_status = label("●  Starting your network…", "online", True)
        side.addWidget(self.connection_status)
        self.identity_label = label("Creating a private device identity", "muted", True)
        self.identity_label.setStyleSheet("font-size:11px")
        side.addWidget(self.identity_label)
        side.addSpacing(14)
        profile, row = frame("profile", QHBoxLayout)
        row.setContentsMargins(10, 16, 10, 16)
        avatar = label(" M ")
        avatar.setFixedWidth(34)
        avatar.setStyleSheet("background:#5865f2; border-radius:14px; padding:6px; font-weight:700;")
        row.addWidget(avatar)
        copy = QVBoxLayout()
        copy.addWidget(label("This device"))
        copy.addWidget(label("Desktop app", "muted"))
        row.addLayout(copy)
        side.addWidget(profile)
        shell.addWidget(sidebar)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        top, top_layout = frame("topbar", QHBoxLayout)
        top.setFixedHeight(68)
        top_layout.setContentsMargins(28, 0, 25, 0)
        self.page_title = label("#  overview", "heading")
        top_layout.addWidget(self.page_title)
        top_layout.addStretch()
        self.invite_button = action("Invite a device", self.invite_device)
        self.add_button = action("+  Connect a model", self.add_agent, True)
        self.invite_button.setEnabled(False)
        self.add_button.setEnabled(False)
        top_layout.addWidget(self.invite_button)
        top_layout.addWidget(self.add_button)
        body_layout.addWidget(top)
        self.toast = label("", wrap=True)
        self.toast.setStyleSheet("background:#243c32; color:#b3e4c7; padding:12px 24px;")
        self.toast.hide()
        body_layout.addWidget(self.toast)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        body_layout.addWidget(self.progress)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.overview_page())
        self.stack.addWidget(self.agents_page())
        self.stack.addWidget(self.chat_page())
        self.stack.addWidget(self.providers_page())
        body_layout.addWidget(self.stack, 1)
        shell.addWidget(body, 1)
        self.navigate(0)

    def scroll_page(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 27, 30, 27)
        layout.setSpacing(22)
        scroll.setWidget(content)
        return scroll, layout

    def overview_page(self):
        page, layout = self.scroll_page()
        layout.addWidget(label("YOUR COLLABORATION HUB", "eyebrow"))
        layout.addWidget(label("A place for all your intelligence.", "title"))
        layout.addWidget(label("Your agents. Your devices. A team that works wherever you do.", "muted"))
        hero, row = frame("hero", QHBoxLayout)
        row.setContentsMargins(28, 20, 12, 20)
        copy = QVBoxLayout()
        copy.setSpacing(14)
        copy.addWidget(label("BETTER TOGETHER", "eyebrow"))
        copy.addWidget(label("Your agents.\nAll in one place.", "heroTitle"))
        copy.addWidget(label("The networking is already taken care of.\nConnect a model, invite a device, and get to work.", "muted", True))
        call = action("Connect your first model  →", self.add_agent, True)
        copy.addWidget(call, alignment=Qt.AlignmentFlag.AlignLeft)
        row.addLayout(copy, 1)
        self.orbit = OrbitArt()
        row.addWidget(self.orbit, 1)
        layout.addWidget(hero)
        stats = QHBoxLayout()
        stats.setSpacing(16)
        self.stat_values = []
        for title, subtitle in (("Agents in your group", "Ready for teamwork"), ("Online right now", "Connected devices"), ("Requests this visit", "Ideas on the move")):
            card, column = frame("stat")
            column.setContentsMargins(18, 15, 18, 15)
            column.addWidget(label(title, "muted"))
            value = label("0", "statValue")
            column.addWidget(value)
            column.addWidget(label(subtitle, "muted"))
            self.stat_values.append(value)
            stats.addWidget(card, 1)
        layout.addLayout(stats)
        layout.addWidget(label("Your agents", "heading"))
        self.overview_cards = QGridLayout()
        self.overview_cards.setSpacing(14)
        layout.addLayout(self.overview_cards)
        activity_card, column = frame("card")
        column.setContentsMargins(20, 18, 20, 18)
        column.addWidget(label("Workspace activity", "heading"))
        self.activity_list = QListWidget()
        self.activity_list.setMinimumHeight(140)
        self.activity_list.setMaximumHeight(210)
        column.addWidget(self.activity_list)
        layout.addWidget(activity_card)
        layout.addWidget(label("Private keys stay on your device. Possibilities go everywhere.", "muted"))
        layout.addStretch()
        return page

    def agents_page(self):
        page, layout = self.scroll_page()
        layout.addWidget(label("YOUR DISTRIBUTED TEAM", "eyebrow"))
        layout.addWidget(label("Agents", "title"))
        layout.addWidget(label("Every laptop and desktop has a place here.", "muted"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search agents or models…")
        self.search.textChanged.connect(self.render_agents)
        layout.addWidget(self.search)
        self.agent_cards = QGridLayout()
        self.agent_cards.setSpacing(16)
        layout.addLayout(self.agent_cards)
        layout.addStretch()
        return page

    def chat_page(self):
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.chat_agents = QListWidget()
        self.chat_agents.setFixedWidth(200)
        self.chat_agents.itemClicked.connect(lambda item: self.select_agent(item.data(Qt.ItemDataRole.UserRole)))
        layout.addWidget(self.chat_agents)
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(22, 22, 22, 20)
        self.chat_title = label("Choose an agent", "heading")
        column.addWidget(self.chat_title)
        self.chat_subtitle = label("Start a conversation with a connected device.", "muted")
        column.addWidget(self.chat_subtitle)
        self.share_conversation_button = action("Invite to conversation", self.invite_conversation)
        column.addWidget(self.share_conversation_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.messages_scroll = QScrollArea()
        self.messages_scroll.setWidgetResizable(True)
        self.messages_widget = QWidget()
        self.messages = QVBoxLayout(self.messages_widget)
        self.messages.setContentsMargins(0, 16, 5, 16)
        self.messages.setSpacing(16)
        self.messages.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.messages_scroll.setWidget(self.messages_widget)
        column.addWidget(self.messages_scroll, 1)
        self.composer = Composer()
        self.composer.setPlaceholderText("What would you like to work on?")
        self.composer.setMaximumHeight(105)
        self.composer.submitted.connect(self.send_message)
        column.addWidget(self.composer)
        footer = QHBoxLayout()
        footer.addWidget(label("Enter to send · Shift + Enter for a new line", "muted"))
        footer.addStretch()
        self.send_button = action("Send request  ↑", self.send_message, True)
        footer.addWidget(self.send_button)
        column.addLayout(footer)
        layout.addWidget(panel, 1)
        return page

    def providers_page(self):
        page, layout = self.scroll_page()
        layout.addWidget(label("CHOOSE YOUR INTELLIGENCE", "eyebrow"))
        layout.addWidget(label("Your next teammate starts here.", "title"))
        layout.addWidget(label("Configure a provider once. The app starts its agent for you, every time.", "muted"))
        grid = QGridLayout()
        grid.setSpacing(16)
        for index, (provider, info) in enumerate(PROVIDER_NAMES.items()):
            card, column = frame("card")
            column.setContentsMargins(21, 20, 21, 20)
            glyph = label(info[2])
            glyph.setStyleSheet(f"font-size:27px; color:{info[3]}; font-weight:650;")
            column.addWidget(glyph)
            column.addWidget(label(info[0], "heading"))
            column.addWidget(label(info[1], "muted", True))
            column.addSpacing(10)
            column.addWidget(action("Connect model  →", lambda checked=False, key=provider: self.add_agent(key), True))
            grid.addWidget(card, index // 2, index % 2)
        layout.addLayout(grid)
        self.reduce_motion = QCheckBox("Reduce animations")
        self.reduce_motion.setChecked(self.storage.settings.get("reduce_motion", False))
        self.reduce_motion.toggled.connect(self.motion_changed)
        layout.addWidget(self.reduce_motion)
        layout.addStretch()
        return page

    def motion_changed(self, reduced):
        self.storage.settings["reduce_motion"] = reduced
        self.storage.save()
        self.orbit.animation.stop() if reduced else self.orbit.animation.start()

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        for number, button in enumerate(self.nav_buttons):
            button.setChecked(number == index)
        self.page_title.setText(("#  overview", "#  agents", "#  conversations", "#  providers")[index])
        if not self.storage.settings.get("reduce_motion"):
            widget = self.stack.currentWidget()
            effect = QGraphicsOpacityEffect(widget)
            widget.setGraphicsEffect(effect)
            animation = QPropertyAnimation(effect, b"opacity", widget)
            animation.setDuration(200)
            animation.setStartValue(.25)
            animation.setEndValue(1.0)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self.page_animation = animation
            animation.start()

    def render_agents(self):
        clear_layout(self.overview_cards)
        clear_layout(self.agent_cards)
        for grid, agents in ((self.overview_cards, self.agents[:3]),
                             (self.agent_cards, [agent for agent in self.agents if self.search.text().lower() in f"{agent['id']} {agent.get('model') or ''}".lower()])):
            if not agents:
                card, column = frame("card")
                column.addWidget(label("Your team is getting ready.", "heading"))
                column.addWidget(label("Connect a model or invite a device to collaborate.", "muted", True))
                grid.addWidget(card, 0, 0)
            for index, agent in enumerate(agents):
                card, column = frame("card")
                column.setContentsMargins(18, 17, 18, 17)
                top = QHBoxLayout()
                info = PROVIDER_NAMES.get(agent.get("provider"), ("Connectivity agent", "", "⌘", "#a59af5"))
                glyph = label(info[2])
                glyph.setStyleSheet(f"color:{info[3]}; font-size:24px;")
                top.addWidget(glyph)
                top.addStretch()
                status = label("● Online" if agent["online"] else "● Offline", "online" if agent["online"] else "muted")
                top.addWidget(status)
                column.addLayout(top)
                column.addWidget(label(agent["id"], "heading", True))
                column.addWidget(label(agent.get("model") or info[0], "muted", True))
                column.addSpacing(7)
                talk = action("Open conversation  →", lambda checked=False, id=agent["id"]: self.select_agent(id))
                talk.setEnabled(agent["online"])
                column.addWidget(talk)
                if agent.get("profile"):
                    row = QHBoxLayout()
                    row.addWidget(action("Edit", lambda checked=False, data=agent: self.edit_agent(data), name="ghost"))
                    if agent.get("local"):
                        row.addWidget(action("Stop", lambda checked=False, id=agent["id"]: self.command("stop_agent", id), name="ghost"))
                    column.addLayout(row)
                grid.addWidget(card, index // 3 if grid is self.overview_cards else index // 2,
                               index % 3 if grid is self.overview_cards else index % 2)
        for listing in (self.sidebar_agents, self.chat_agents):
            listing.clear()
            for room in self.conversations.values():
                unread = self.chats.get(room["id"], {}).get("unread", 0)
                item = QListWidgetItem("▤  " + room["title"] + (f"  ({unread} new)" if unread else ""))
                item.setData(Qt.ItemDataRole.UserRole, room["id"])
                item.setToolTip("Shared conversation · " + ", ".join(room["members"]))
                listing.addItem(item)
                if room["id"] == self.selected:
                    listing.setCurrentItem(item)
            for agent in self.agents:
                unread = self.chats.get(agent["id"], {}).get("unread", 0)
                item = QListWidgetItem(("●  " if agent["online"] else "○  ") + agent["id"] + (f"  ({unread} new)" if unread else ""))
                item.setData(Qt.ItemDataRole.UserRole, agent["id"])
                item.setToolTip(agent.get("model") or "Connectivity agent")
                listing.addItem(item)
                if agent["id"] == self.selected:
                    listing.setCurrentItem(item)
            current_ids = {agent["id"] for agent in self.agents} | set(self.conversations)
            for target, chat in self.chats.items():
                if target in current_ids or not chat.get("messages"):
                    continue
                item = QListWidgetItem("○  " + target + "  (saved)")
                item.setData(Qt.ItemDataRole.UserRole, target)
                item.setToolTip("Saved conversation")
                listing.addItem(item)
                if target == self.selected:
                    listing.setCurrentItem(item)
        self.stat_values[0].setText(str(len(self.agents)))
        self.stat_values[1].setText(str(sum(agent["online"] for agent in self.agents)))
        self.stat_values[2].setText(str(self.request_count))
        self.update_chat_controls()

    def update_chat_controls(self):
        room = self.conversations.get(self.selected)
        target = room["target"] if room else self.selected
        agent = next((item for item in self.agents if item["id"] == target), None)
        chat = self.chats.get(self.selected, {})
        pending = chat.get("pending") or chat.get("local_pending")
        self.send_button.setEnabled(bool(agent and agent["online"] and not pending))
        self.send_button.setText("Working…" if pending else "Send request  ↑")
        self.chat_title.setText(room["title"] if room else self.selected or "Choose an agent")
        subtitle = "Your agent is working…" if pending else "Online · Ready to collaborate" if agent and agent["online"] else "Start this agent on its device to continue" if agent else "Choose a connected agent to begin"
        self.chat_subtitle.setText((f"Shared with {len(room['members'])} devices · " if room else "") + subtitle)
        self.share_conversation_button.setEnabled(bool(self.ready and not self.remote and agent and agent["online"] and not pending))

    def select_agent(self, agent_id):
        self.selected = agent_id
        self.chats.setdefault(agent_id, {"messages": [], "history": [], "pending": False})
        self.chats[agent_id]["unread"] = 0
        self.navigate(2)
        self.render_agents()
        self.render_messages()
        self.composer.setFocus()

    @staticmethod
    def saved_chat(chat):
        return {key: value for key, value in chat.items() if key != "message_ids"}

    def persist_history(self):
        if self.history_store:
            self.history_store.save("ui", "state", {"chats": {key: self.saved_chat(chat) for key, chat in self.chats.items()},
                "conversations": self.conversations, "selected": self.selected, "workspace_info": self.workspace_meta,
                "draft": self.composer.toPlainText()})

    def persist_reply(self, workspace_id, store, target, chat):
        if not any(entry["id"] == workspace_id for entry in self.workspace_list):
            return
        if workspace_id == self.workspace_id:
            self.chats[target] = chat
            self.persist_history()
            if self.selected == target:
                self.render_messages()
        else:
            state = store.load("ui").get("state", {})
            state.setdefault("chats", {})[target] = self.saved_chat(chat)
            store.save("ui", "state", state)

    def restore_history(self, directory):
        self.history_store = HistoryStore(directory)
        state = self.history_store.load("ui").get("state", {})
        self.chats = state.get("chats", {})
        self.conversations = state.get("conversations", {})
        self.workspace_meta = state.get("workspace_info", {})
        self.selected = state.get("selected")
        self.composer.setPlainText(state.get("draft", ""))
        for target, chat in self.chats.items():
            chat["messages"] = [tuple(message) for message in chat.get("messages", [])]
            if (self.workspace_id, target) not in self.live_requests:
                if chat.get("pending") and target not in self.conversations:
                    chat["messages"].append(("error", "The app closed during this request. It was not replayed."))
                chat["pending"] = False
                chat["local_pending"] = False
            if target in self.conversations:
                chat["message_ids"] = {message["id"] for message in self.conversations[target]["messages"]}

    def render_workspaces(self):
        identities = {entry["id"] for entry in self.workspace_list}
        for identity in list(self.workspace_buttons):
            if identity not in identities:
                button = self.workspace_buttons.pop(identity)
                button.animation.stop()
                self.workspace_rail.removeWidget(button)
                button.hide()
                button.deleteLater()
        for entry in self.workspace_list:
            button = self.workspace_buttons.get(entry["id"])
            if button is None:
                button = WorkspaceButton(entry["name"][:2].upper())
                button.setCheckable(True)
                button.clicked.connect(lambda checked=False, identity=entry["id"]: self.command("switch_workspace", identity))
                self.workspace_rail.addWidget(button)
                self.workspace_buttons[entry["id"]] = button
            button.setText(entry["name"][:2].upper())
            button.setToolTip(entry["name"])
            button.setChecked(entry["id"] == self.workspace_id)

    def render_messages(self):
        clear_layout(self.messages)
        chat = self.chats.get(self.selected, {})
        if not chat.get("messages"):
            self.messages.addWidget(label("#  This is the beginning of your collaboration.", "heading", True))
            self.messages.addWidget(label("Ask a question, explore an idea, or simply say hello.", "muted", True))
        for role, text in chat.get("messages", []):
            bubble, column = frame("card")
            column.setContentsMargins(17, 13, 17, 13)
            names = {"user": "You", "error": "Request unsuccessful", "local_agent": "Agent on this device"}
            room = self.conversations.get(self.selected)
            speaker = role.removeprefix("member:") if role.startswith("member:") else names.get(role, room["target"] if room else self.selected)
            title = label(speaker)
            title.setStyleSheet("font-weight:650; color:" + ("#f38a8e" if role == "error" else "#a8b0ff") + ";")
            column.addWidget(title)
            if role in ("assistant", "local_agent"):
                body = MarkdownMessage(text)
            else:
                body = label(text, wrap=True)
                body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            column.addWidget(body)
            self.messages.addWidget(bubble)
        if chat.get("pending") or chat.get("local_pending"):
            self.messages.addWidget(label("● ● ●   Waiting for your agent…", "muted"))
        self.update_chat_controls()
        self.persist_history()
        QTimer.singleShot(0, lambda: self.messages_scroll.verticalScrollBar().setValue(self.messages_scroll.verticalScrollBar().maximum()))

    def send_message(self):
        if not self.selected or not self.send_button.isEnabled():
            return
        text = self.composer.toPlainText().strip()
        if not text:
            return
        if len(json.dumps(text).encode()) > 180000:
            self.notice("This message is too large. Send a shorter request.")
            return
        target = self.selected
        chat = self.chats[target]
        workspace_id, store = self.workspace_id, self.history_store
        self.live_requests.add((workspace_id, target))
        if target in self.conversations:
            chat["local_pending"] = True
            self.composer.clear()
            self.request_count += 1
            self.stat_values[2].setText(str(self.request_count))
            self.render_messages()
            def finished(result):
                self.live_requests.discard((workspace_id, target))
                if workspace_id == self.workspace_id and target in self.chats:
                    self.chats[target]["local_pending"] = False
                    self.persist_history()
                    if target == self.selected:
                        self.render_messages()
                elif any(entry["id"] == workspace_id for entry in self.workspace_list):
                    state = store.load("ui").get("state", {})
                    if target in state.get("chats", {}):
                        state["chats"][target]["local_pending"] = False
                        store.save("ui", "state", state)
            def failed(message):
                finished(None)
                self.notice(message)
            self.command("send_conversation", target, text, success=finished, failure=failed)
            return
        payload = {"messages": model_context([*chat["history"], {"role": "user", "content": text}])} if chat["history"] else {"text": text}
        chat["pending"] = True
        chat["messages"].append(("user", text))
        self.composer.clear()
        self.request_count += 1
        self.stat_values[2].setText(str(self.request_count))
        self.render_messages()
        def success(result):
            self.live_requests.discard((workspace_id, target))
            response = result.get("text") if isinstance(result, dict) else result
            if not isinstance(response, str):
                response = json.dumps(result, indent=2)
            chat["messages"].append(("assistant", response))
            if isinstance(result, dict) and result.get("provider"):
                chat["history"] = [*chat["history"], {"role": "user", "content": text}, {"role": "assistant", "content": response}]
            chat["pending"] = False
            if workspace_id == self.workspace_id:
                self.add_activity(f"{target} replied", "Request completed")
            self.persist_reply(workspace_id, store, target, chat)
        def failure(message):
            self.live_requests.discard((workspace_id, target))
            chat["pending"] = False
            chat["messages"].append(("error", message + "\nThe app did not replay this request."))
            self.persist_reply(workspace_id, store, target, chat)
        self.command("send", target, payload, success=success, failure=failure)

    def add_activity(self, title, detail):
        stamp = datetime.now().strftime("%H:%M")
        self.activity_list.insertItem(0, f"{title}   ·   {stamp}\n{detail}")
        while self.activity_list.count() > 8:
            self.activity_list.takeItem(self.activity_list.count() - 1)

    def notice(self, message):
        self.toast.setText(message)
        self.toast.show()
        QTimer.singleShot(7500, self.toast.hide)

    def command(self, method, *args, success=None, failure=None):
        if not self.ready and method != "close":
            self.notice("Your network is still starting. Please wait a moment.")
            if failure:
                failure("Networking is still starting")
            return
        request_id = self.network.submit(method, *args)
        self.callbacks[request_id] = (success, failure)

    @Slot(str, object)
    def command_success(self, request_id, result):
        success, _ = self.callbacks.pop(request_id, (None, None))
        if success:
            success(result)

    @Slot(str, str)
    def command_failure(self, request_id, message):
        _, failure = self.callbacks.pop(request_id, (None, None))
        if failure:
            failure(message)
        else:
            self.notice(message)

    @Slot(str, object)
    def network_event(self, event, data):
        if event == "ready":
            self.ready = True
            self.port = data["port"]
            self.progress.hide()
            self.add_button.setEnabled(True)
            self.invite_button.setEnabled(not self.remote)
            self.workspace_settings_button.setEnabled(True)
            self.add_activity("Your desktop is connected", "Local relay and device identity started automatically")
            if self.storage.settings.get("reduce_motion"):
                self.orbit.animation.stop()
        elif event == "workspace":
            changed = self.workspace_id != data["id"]
            if changed:
                self.persist_history()
                self.workspace_id = data["id"]
                self.restore_history(data["history_directory"])
                self.agents = []
            self.remote = data["remote"]
            self.identity = data["self"]
            self.port = data["port"]
            self.workspace_label.setText(data["name"])
            self.identity_label.setText(data["self"])
            self.invite_button.setEnabled(self.ready and not self.remote)
            if changed:
                self.request_count = 0
            self.render_agents()
            self.render_messages()
        elif event == "workspaces":
            self.workspace_list = data["workspaces"]
            self.render_workspaces()
        elif event == "workspace_removed":
            self.workspace_list = [entry for entry in self.workspace_list if entry["id"] != data]
            if self.workspace_id == data:
                self.history_store = None
                self.chats.clear()
                self.conversations.clear()
                self.selected = None
        elif event == "workspace_info":
            changed = self.workspace_meta != data
            self.workspace_meta = data
            self.workspace_label.setText(data["name"])
            if changed:
                self.persist_history()
        elif event == "agents":
            self.agents = data["agents"]
            self.connection_status.setText("●  Relay connected" if data["connected"] else "●  Reconnecting…")
            self.render_agents()
        elif event == "conversations":
            changed = set(self.conversations) != {room["id"] for room in data}
            self.conversations = {room["id"]: room for room in data}
            selected_changed = False
            for room in data:
                chat = self.chats.setdefault(room["id"], {"messages": [], "history": [], "pending": False})
                if chat.get("revision") == room["revision"]:
                    continue
                changed = True
                selected_changed = selected_changed or self.selected == room["id"]
                previous_ids = chat.get("message_ids", set())
                new_ids = {message["id"] for message in room["messages"]}
                if self.selected != room["id"]:
                    chat["unread"] = chat.get("unread", 0) + len(new_ids - previous_ids)
                chat["message_ids"] = new_ids
                chat["revision"] = room["revision"]
                chat["messages"] = [("user" if message["role"] == "user" and message["from"] == self.identity
                    else "member:" + message["from"] if message["role"] == "user" else message["role"], message["content"])
                    for message in room["messages"]]
                chat["pending"] = room["pending"]
            self.render_agents()
            if selected_changed:
                self.render_messages()
            elif changed:
                self.persist_history()
        elif event == "conversation_joined":
            self.select_agent(data)
        elif event in ("incoming", "incoming_reply"):
            source = data["from"]
            chat = self.chats.setdefault(source, {"messages": [], "history": [], "pending": False})
            if event == "incoming":
                chat["messages"].append(("peer", data["text"]))
                if self.selected != source:
                    chat["unread"] = chat.get("unread", 0) + 1
                self.add_activity(f"Message from {source}", f"Received by {data['to']}")
                self.notice(f"Message from {source}. Open their conversation to view it.")
            else:
                chat["messages"].append(("error" if data.get("error") else "local_agent", data["to"] + ":\n" + data["text"]))
            self.render_agents()
            self.persist_history()
            if self.selected == source:
                self.render_messages()
        elif event == "activity":
            self.add_activity(data["title"], data["detail"])
        elif event in ("notice", "fatal"):
            self.notice(data)
            if event == "fatal":
                self.progress.hide()
                self.connection_status.setText("●  Startup needs attention")
        elif event == "offline":
            self.connection_status.setText("●  Relay unavailable")
            for agent in self.agents:
                agent["online"] = False
            for member in self.workspace_meta.get("members", []):
                member["online"] = False
            self.render_agents()

    def add_agent(self, provider="ollama"):
        if not self.ready:
            self.notice("Your network is still starting.")
            return
        if not isinstance(provider, str):
            provider = "ollama"
        AgentDialog(self, provider).exec()

    def edit_agent(self, agent):
        AgentDialog(self, agent["provider"], agent["profile"]).exec()

    def join_workspace(self):
        if self.ready:
            JoinDialog(self).exec()
        else:
            self.notice("Your network is still starting.")

    def add_workspace(self):
        menu = QMenu(self)
        create = menu.addAction("Create a workspace")
        join = menu.addAction("Join a workspace")
        selected = menu.exec(self.add_workspace_button.mapToGlobal(self.add_workspace_button.rect().bottomRight()))
        menu.deleteLater()
        # Open modal dialogs after the menu's event loop has returned.
        if selected == create:
            self.create_workspace()
        elif selected == join:
            self.join_workspace()

    def create_workspace(self):
        name, accepted = QInputDialog.getText(self, "Create a workspace", "Workspace name")
        if accepted:
            self.command("create_workspace", name)

    def manage_workspace(self):
        from .workspace_dialog import WorkspaceDialog
        if self.ready:
            WorkspaceDialog(self).exec()

    def invite_device(self):
        if self.ready and not self.remote:
            InviteDialog(self).exec()

    def invite_conversation(self):
        if not self.ready or self.remote or not self.selected:
            return
        if self.selected in self.conversations:
            InviteDialog(self, conversation_id=self.selected).exec()
        else:
            chat = self.chats.get(self.selected, {})
            history = [{"role": role, "content": text} for role, text in chat.get("messages", [])
                       if role in ("user", "assistant")]
            InviteDialog(self, target=self.selected, messages=history).exec()

    def closeEvent(self, event):
        if self.closing and not self.network.isRunning():
            event.accept()
            return
        event.ignore()
        if not self.closing:
            self.persist_history()
            self.closing = True
            self.setEnabled(False)
            self.toast.setText("Disconnecting agents and shutting down your local relay…")
            self.toast.show()
            self.network.shutdown()

    @Slot()
    def finish_close(self):
        if self.closing:
            self.close()
