import PySide6
import json
import os
import platform
import subprocess
import sys
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PySide6.QtGui import (QGuiApplication, QIcon, QKeySequence, QPixmap, QPainter, QColor,
                           QFont, QShortcut)
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QScrollArea,
    QStackedWidget, QVBoxLayout, QWidget)

from .bridge import NetworkThread
from .dialogs import AgentDialog, InviteDialog, JoinDialog
from .layout import minimum_size, sidebar_should_collapse, window_size
from .resources import ResourceSampler
from .theme import (DARK, LIGHT, THEME_CHOICES, color, provider_entry, provider_names,
                    resolve_theme, stylesheet, system_theme)
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


# Navigation is declarative because the nav buttons, the stacked pages and
# the page title all have to agree on order and length. Adding a page here
# and nowhere else used to mean fixing the title list as well, which is
# exactly the kind of duplication that goes stale.
PAGES = (
    ("◫   Overview", "overview"),
    ("⌘   Agents", "agents"),
    ("▤   Conversations", "conversations"),
    ("◇   Providers", "providers"),
    ("◴   Resources", "resources"),
    ("⚙   Settings", "settings"),
)
PAGE_TITLES = tuple(f"#  {key}" for _, key in PAGES)
RESOURCES_PAGE = 4
SETTINGS_PAGE = 5


def app_icon(theme_name=DARK):
    """The window and taskbar icon, drawn in the accent colour.

    Painted rather than themed, so the accent is read from the palette
    instead of hardcoded.
    """
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color(theme_name, "accent")))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(0, 0, 64, 64, 20, 20)
    painter.setPen(QColor(color(theme_name, "on_accent")))
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
        self.callbacks = {}
        self.request_count = 0
        self.ready = False
        self.closing = False
        self.setWindowTitle("Multiplayer AI")
        # Resolved before any widget is built so everything created below
        # picks up the right colours the first time.
        self.theme = resolve_theme(storage.settings)
        self.setWindowIcon(app_icon(self.theme))
        width, height = window_size(self.stored_window_size(), storage.settings,
                                    self.primary_screen_size())
        self.resize(width, height)
        self.setMinimumSize(*minimum_size(self.primary_screen_size()))
        self.setStyleSheet(stylesheet(self.theme))
        # Measuring the disk the preferences live on is what the Resources
        # page reports, so it is resolved once here.
        try:
            self.sampler = ResourceSampler(storage.directory)
        except Exception:
            self.sampler = None
        self.network = NetworkThread(storage, self)
        self.network.event.connect(self.network_event)
        self.network.completed.connect(self.command_success)
        self.network.failed.connect(self.command_failure)
        self.network.finished.connect(self.finish_close)
        self.build_ui()
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
        join = WorkspaceButton("+")
        self.join_button = join
        join.setStyleSheet("color:" + color(self.theme, "success"))
        join.setToolTip("Join a workspace")
        join.clicked.connect(self.join_workspace)
        rail_layout.addWidget(join)
        rail_layout.addStretch()
        help_button = WorkspaceButton("?")
        help_button.clicked.connect(lambda: self.notice("The app starts your local relay automatically. Connect a model in Providers, or join a team with an invitation."))
        help_button.setToolTip("Quick help")
        rail_layout.addWidget(help_button)
        shell.addWidget(rail)
        sidebar, side = frame("sidebar")
        sidebar.setFixedWidth(238)
        self.sidebar = sidebar
        side.setContentsMargins(14, 22, 14, 0)
        side.setSpacing(8)
        self.workspace_label = label("My workspace", "heading")
        side.addWidget(self.workspace_label)
        side.addWidget(label("Your intelligence, connected.", "muted"))
        side.addSpacing(25)
        side.addWidget(label("WORKSPACE", "eyebrow"))
        self.nav_buttons = []
        for index, (name, _key) in enumerate(PAGES):
            button = action(name, lambda checked=False, page=index: self.navigate(page), name="nav")
            button.setCheckable(True)
            self.nav_buttons.append(button)
            side.addWidget(button)
        side.addSpacing(23)
        side.addWidget(label("CONNECTED AGENTS", "eyebrow"))
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
        self.avatar = avatar
        avatar.setFixedWidth(34)
        avatar.setStyleSheet("background:" + color(self.theme, "accent")
                             + "; border-radius:14px; padding:6px; font-weight:700;"
                             + " color:" + color(self.theme, "on_accent") + ";")
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
        self.style_toast()
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
        self.stack.addWidget(self.resources_page())
        self.stack.addWidget(self.settings_page())
        body_layout.addWidget(self.stack, 1)
        shell.addWidget(body, 1)
        self.install_shortcuts()
        self.apply_sidebar_density()
        self.about_label.setText(
            f"Multiplayer AI desktop · Python {platform.python_version()} · "
            f"Qt {PySide6.__version__}"
        )
        self.update_theme_hint()
        self.navigate(0)

    def install_shortcuts(self):
        """Keyboard navigation, so the app is usable without a mouse."""
        for index in range(len(PAGES)):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self)
            shortcut.activated.connect(lambda page=index: self.navigate(page))
        QShortcut(QKeySequence("Ctrl+,"), self).activated.connect(
            lambda: self.navigate(SETTINGS_PAGE)
        )
        QShortcut(QKeySequence("F5"), self).activated.connect(self.refresh_resources)
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(self.refresh_resources)

    def apply_sidebar_density(self):
        """Hide the agent sidebar on a window too narrow to carry it."""
        collapse = sidebar_should_collapse(self.width(), self.primary_screen_size())
        self.sidebar.setVisible(not collapse)
        if getattr(self, "chat_agents", None) is not None:
            self.chat_agents.setVisible(not collapse)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.apply_sidebar_density()

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
        providers = provider_names(self.theme)
        for index, provider in enumerate(providers):
            card, column = frame("card")
            column.setContentsMargins(21, 20, 21, 20)
            info = providers[provider]
            glyph = label(info[2])
            glyph.setStyleSheet(f"font-size:27px; color:{info[3]}; font-weight:650;")
            column.addWidget(glyph)
            column.addWidget(label(info[0], "heading"))
            column.addWidget(label(info[1], "muted", True))
            column.addSpacing(10)
            column.addWidget(action("Connect model  →", lambda checked=False, key=provider: self.add_agent(key), True))
            grid.addWidget(card, index // 2, index % 2)
        layout.addLayout(grid)
        layout.addStretch()
        return page

    def resources_page(self):
        """Live CPU, memory, disk and process usage.

        Sampling stops while this page is off screen. Polling a panel
        nobody is looking at would spend the app's own CPU to display
        nothing, which is self-defeating on a page about CPU usage.
        """
        page, layout = self.scroll_page()
        layout.addWidget(label("WHAT YOUR MACHINE IS DOING", "eyebrow"))
        layout.addWidget(label("Resources", "title"))
        layout.addWidget(label("Live readings from this device. Nothing is sent anywhere.", "muted", True))

        self.resource_timer = QTimer(self)
        self.resource_timer.setInterval(2000)
        self.resource_timer.timeout.connect(self.refresh_resources)

        def meter(title, subtitle):
            card, column = frame("stat")
            column.setContentsMargins(18, 15, 18, 15)
            column.addWidget(label(title, "muted"))
            reading = label("—", "statValue")
            column.addWidget(reading)
            column.addWidget(label(subtitle, "muted"))
            bar = QProgressBar()
            bar.setRange(0, 100)
            column.addSpacing(6)
            column.addWidget(bar)
            layout.addWidget(card)
            return reading, bar

        self.resource_rows = {
            "cpu": meter("Processor", "Total load across all cores"),
            "memory": meter("Memory", "In use across the whole system"),
            "disk": meter("Disk", "Used on the drive holding this app's data"),
        }

        layout.addWidget(label("Per-core load", "heading"))
        self.core_bars = []
        self.core_grid = QGridLayout()
        self.core_grid.setSpacing(10)
        layout.addLayout(self.core_grid)

        detail, column = frame("card")
        column.setContentsMargins(20, 18, 20, 18)
        column.addWidget(label("This application", "heading"))
        self.app_detail = label("", "muted", True)
        self.app_detail.setWordWrap(True)
        column.addWidget(self.app_detail)
        layout.addWidget(detail)

        self.system_detail = label("", "muted", True)
        self.system_detail.setWordWrap(True)
        layout.addWidget(self.system_detail)
        layout.addWidget(action("Refresh now", self.refresh_resources))
        layout.addStretch()
        return page

    def settings_page(self):
        """Appearance, motion, and where preferences live."""
        page, layout = self.scroll_page()
        layout.addWidget(label("MAKE IT YOURS", "eyebrow"))
        layout.addWidget(label("Settings", "title"))

        appearance, column = frame("settings")
        column.setContentsMargins(22, 18, 22, 20)
        column.addWidget(label("Appearance", "heading"))
        column.addWidget(label("Theme", "muted"))
        self.theme_picker = QComboBox()
        for choice in THEME_CHOICES:
            self.theme_picker.addItem(choice[0], choice[1])
        self.theme_picker.setCurrentIndex(self._theme_choice_index())
        self.theme_picker.currentIndexChanged.connect(self.theme_choice_changed)
        column.addWidget(self.theme_picker)
        self.theme_hint = label("", "muted", True)
        self.theme_hint.setWordWrap(True)
        column.addWidget(self.theme_hint)
        layout.addWidget(appearance)

        motion, column = frame("settings")
        column.setContentsMargins(22, 18, 22, 20)
        column.addWidget(label("Motion", "heading"))
        self.reduce_motion = QCheckBox("Reduce animations")
        self.reduce_motion.setChecked(self.storage.settings.get("reduce_motion", False))
        self.reduce_motion.toggled.connect(self.motion_changed)
        column.addWidget(self.reduce_motion)
        column.addWidget(label("Turns off the welcome animation and page fades.", "muted", True))
        layout.addWidget(motion)

        storage, column = frame("settings")
        column.setContentsMargins(22, 18, 22, 20)
        column.addWidget(label("Data on this device", "heading"))
        self.storage_path = label(str(self.storage.directory), "muted", True)
        self.storage_path.setWordWrap(True)
        self.storage_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.storage_path)
        column.addWidget(action("Show in file manager", self.open_storage_folder))
        identity, column = frame("settings")
        column.setContentsMargins(22, 18, 22, 20)
        column.addWidget(label("Device identity", "heading"))
        column.addWidget(label(
            "Your device holds a private token in the operating system's credential "
            "store. Reset it if you think the token has been exposed. Workspaces "
            "joined with the old identity will need new invitations.",
            "muted", True))
        column.addWidget(action("Reset local identity", self.confirm_reset_identity,
                                name="danger"))
        layout.addWidget(identity)
        layout.addWidget(storage)

        about, column = frame("settings")
        column.setContentsMargins(22, 18, 22, 20)
        column.addWidget(label("About", "heading"))
        self.about_label = label("", "muted", True)
        self.about_label.setWordWrap(True)
        column.addWidget(self.about_label)
        layout.addWidget(about)
        layout.addStretch()
        return page

    def confirm_reset_identity(self):
        """Ask before discarding the device token.

        The action cannot be undone and any joined workspace stops
        working, so it is confirmed rather than fired on a single click.
        """
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Reset local identity?")
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText("Your device will get a new private identity.")
        confirm.setInformativeText(
            "Workspaces joined with the current identity will stop connecting and "
            "will need a new invitation. Connected agents are stopped and restarted.")
        confirm.setStandardButtons(QMessageBox.StandardButton.Cancel |
                                   QMessageBox.StandardButton.Reset)
        confirm.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if confirm.exec() == QMessageBox.StandardButton.Reset:
            self.command("reset_identity")

    def open_storage_folder(self):
        """Reveal the preferences directory in the platform's file manager."""
        path = str(self.storage.directory)
        try:
            if os.name == "nt":
                os.startfile(path)  # noqa: S606 - Windows shell open is intended
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            self.notice(f"Could not open {path}: {exc}")

    def refresh_resources(self):
        """Take one reading and update every meter."""
        if getattr(self, "sampler", None) is None:
            return
        reading = self.sampler.sample()

        self.resource_rows["cpu"][0].setText(f"{reading['cpu']:.0f}%")
        self.resource_rows["cpu"][1].setValue(int(reading["cpu"]))

        memory = reading["memory"]
        if memory:
            self.resource_rows["memory"][0].setText(f"{memory['percent']:.0f}%")
            self.resource_rows["memory"][1].setValue(int(memory["percent"]))
        else:
            self.resource_rows["memory"][0].setText("Unavailable")
            self.resource_rows["memory"][1].setValue(0)

        disk = reading["disk"]
        if disk:
            self.resource_rows["disk"][0].setText(f"{disk['percent']:.0f}%")
            self.resource_rows["disk"][1].setValue(int(disk["percent"]))
        else:
            self.resource_rows["disk"][0].setText("Unavailable")
            self.resource_rows["disk"][1].setValue(0)

        cores = reading["per_core"] or []
        while len(self.core_bars) < len(cores):
            bar = QProgressBar()
            bar.setRange(0, 100)
            self.core_bars.append(bar)
            self.core_grid.addWidget(bar, len(self.core_bars) // 2,
                                     len(self.core_bars) % 2)
        for bar, value in zip(self.core_bars, cores):
            bar.setValue(int(value))

        process = reading["process"]
        if process:
            self.app_detail.setText(
                f"{process['memory_human']} resident · {process['cpu']:.0f}% of one "
                f"core · {process['threads']} threads"
            )
        else:
            self.app_detail.setText("Unavailable")

        self.system_detail.setText(
            f"Uptime {reading['uptime']} · {reading['core_count']} logical cores · "
            f"preferences at {self.storage.directory}"
        )

    def _theme_choice_index(self):
        """Where the stored preference sits in the picker.

        An absent key means follow the system, which is the default entry.
        """
        stored = self.storage.settings.get("theme")
        for index, choice in enumerate(THEME_CHOICES):
            if choice[1] == stored:
                return index
        return 0

    def update_theme_hint(self):
        """Explain what the picker is currently doing."""
        stored = self.storage.settings.get("theme")
        if stored is None:
            detected = system_theme() or "unknown"
            self.theme_hint.setText(
                f"Following your system, which reports {detected}. "
                "Pick a theme to override it."
            )
        else:
            self.theme_hint.setText("Your choice is remembered and overrides the system.")

    def theme_choice_changed(self, index):
        """Persist the picked theme and apply it.

        Signals are blocked while the index is set programmatically so
        applying a theme does not write the preference back and fight the
        user's click.
        """
        if index < 0 or index >= len(THEME_CHOICES):
            return
        name = THEME_CHOICES[index][1]
        if name is None:
            self.storage.settings.pop("theme", None)
            self.apply_theme(resolve_theme({}))
        else:
            self.storage.settings["theme"] = name
            self.storage.save()
            self.apply_theme(name)
        self.update_theme_hint()

    def primary_screen_size(self):
        """Usable area of the screen the window is opening on.

        Read from the screen the window actually lands on rather than the
        primary one, so a window dragged to a second monitor is sized for
        that monitor.
        """
        try:
            screen = self.screen() or QGuiApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                return area.width(), area.height()
        except Exception:
            pass
        return None

    def stored_window_size(self):
        """The size saved from the previous session, if any."""
        stored = self.storage.settings.get("window_size")
        if not stored:
            return None
        try:
            return int(stored[0]), int(stored[1])
        except (TypeError, ValueError, IndexError):
            return None

    def remember_window_size(self):
        """Save the current size so the next launch opens at the same one."""
        self.storage.settings["window_size"] = [self.width(), self.height()]
        self.storage.save()

    def style_toast(self):
        """Repaint the toast in the current theme's success colours."""
        self.toast.setStyleSheet("background:" + color(self.theme, "toast_bg")
                                 + "; color:" + color(self.theme, "toast_fg")
                                 + "; padding:12px 24px;")

    def apply_theme(self, name):
        """Switch themes live.

        Re-applying the stylesheet is not enough on its own. A widget that
        carries its own stylesheet outranks the application sheet and
        keeps whatever colour it was given, so the hand-styled widgets are
        refreshed here and the visible page is re-rendered. Without the
        re-render, an open conversation would keep the accent colours from
        the theme it was drawn in.
        """
        self.theme = name
        self.setStyleSheet(stylesheet(name))
        self.setWindowIcon(app_icon(name))
        self.style_toast()
        self.join_button.setStyleSheet("color:" + color(name, "success"))
        self.avatar.setStyleSheet("background:" + color(name, "accent")
                                  + "; border-radius:14px; padding:6px; font-weight:700;"
                                  + " color:" + color(name, "on_accent") + ";")
        if hasattr(self, "orbit"):
            self.orbit.set_theme(name)
        if hasattr(self, "theme_picker"):
            self.theme_picker.blockSignals(True)
            self.theme_picker.setCurrentIndex(self._theme_choice_index())
            self.theme_picker.blockSignals(False)
        if hasattr(self, "selected"):
            self.render_agents()
            self.render_messages()

    def theme_changed(self, light):
        """Persist the theme chosen in the UI and apply it."""
        name = LIGHT if light else DARK
        self.storage.settings["theme"] = name
        self.storage.save()
        self.apply_theme(name)

    def motion_changed(self, reduced):
        self.storage.settings["reduce_motion"] = reduced
        self.storage.save()
        self.orbit.animation.stop() if reduced else self.orbit.animation.start()

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        for number, button in enumerate(self.nav_buttons):
            button.setChecked(number == index)
        self.page_title.setText(PAGE_TITLES[index])
        # Sampling runs only while the Resources page is on screen.
        if getattr(self, "resource_timer", None) is not None:
            if index == RESOURCES_PAGE:
                self.refresh_resources()
                self.resource_timer.start()
            else:
                self.resource_timer.stop()
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
                info = provider_entry(self.theme, agent.get("provider"))
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
            for agent in self.agents:
                unread = self.chats.get(agent["id"], {}).get("unread", 0)
                item = QListWidgetItem(("●  " if agent["online"] else "○  ") + agent["id"] + (f"  ({unread} new)" if unread else ""))
                item.setData(Qt.ItemDataRole.UserRole, agent["id"])
                item.setToolTip(agent.get("model") or "Connectivity agent")
                listing.addItem(item)
                if agent["id"] == self.selected:
                    listing.setCurrentItem(item)
        self.stat_values[0].setText(str(len(self.agents)))
        self.stat_values[1].setText(str(sum(agent["online"] for agent in self.agents)))
        self.stat_values[2].setText(str(self.request_count))
        self.update_chat_controls()

    def update_chat_controls(self):
        agent = next((item for item in self.agents if item["id"] == self.selected), None)
        chat = self.chats.get(self.selected, {})
        self.send_button.setEnabled(bool(agent and agent["online"] and not chat.get("pending")))
        self.send_button.setText("Working…" if chat.get("pending") else "Send request  ↑")
        self.chat_title.setText(self.selected or "Choose an agent")
        self.chat_subtitle.setText("Your agent is working…" if chat.get("pending") else "Online · Ready to collaborate" if agent and agent["online"] else "Start this agent on its device to continue" if agent else "Choose a connected agent to begin")

    def select_agent(self, agent_id):
        self.selected = agent_id
        self.chats.setdefault(agent_id, {"messages": [], "history": [], "pending": False})
        self.chats[agent_id]["unread"] = 0
        self.navigate(2)
        self.render_agents()
        self.render_messages()
        self.composer.setFocus()

    def render_messages(self):
        clear_layout(self.messages)
        chat = self.chats.get(self.selected, {})
        if not chat.get("messages"):
            self.messages.addWidget(label("#  This is the beginning of your collaboration.", "heading", True))
            self.messages.addWidget(label("Ask a question, explore an idea, or simply say hello.", "muted", True))
        for role, text in chat.get("messages", []):
            bubble, column = frame("card")
            column.setContentsMargins(17, 13, 17, 13)
            title = label("You" if role == "user" else "Request unsuccessful" if role == "error" else "Agent on this device" if role == "local_agent" else self.selected)
            title.setStyleSheet("font-weight:650; color:"
                                + color(self.theme, "error" if role == "error" else "agent_title") + ";")
            column.addWidget(title)
            body = label(text, wrap=True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            column.addWidget(body)
            self.messages.addWidget(bubble)
        if chat.get("pending"):
            self.messages.addWidget(label("● ● ●   Waiting for your agent…", "muted"))
        self.update_chat_controls()
        QTimer.singleShot(0, lambda: self.messages_scroll.verticalScrollBar().setValue(self.messages_scroll.verticalScrollBar().maximum()))

    def send_message(self):
        if not self.selected or not self.send_button.isEnabled():
            return
        text = self.composer.toPlainText().strip()
        if not text:
            return
        if len(text.encode()) > 180000:
            self.notice("This message is too large. Send a shorter request.")
            return
        target = self.selected
        chat = self.chats[target]
        payload = {"messages": [*chat["history"], {"role": "user", "content": text}]} if chat["history"] else {"text": text}
        chat["pending"] = True
        chat["messages"].append(("user", text))
        self.composer.clear()
        self.request_count += 1
        self.stat_values[2].setText(str(self.request_count))
        self.render_messages()
        def success(result):
            if self.chats.get(target) is not chat:
                return
            response = result.get("text") if isinstance(result, dict) else result
            if not isinstance(response, str):
                response = json.dumps(result, indent=2)
            chat["messages"].append(("assistant", response))
            if isinstance(result, dict) and result.get("provider"):
                chat["history"] = [*chat["history"], {"role": "user", "content": text}, {"role": "assistant", "content": response}][-98:]
            chat["pending"] = False
            self.add_activity(f"{target} replied", "Request completed")
            if target == self.selected:
                self.render_messages()
        def failure(message):
            if self.chats.get(target) is not chat:
                return
            chat["pending"] = False
            chat["messages"].append(("error", message + "\nThe app did not replay this request."))
            if target == self.selected:
                self.render_messages()
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

    def command_success(self, request_id, result):
        success, _ = self.callbacks.pop(request_id, (None, None))
        if success:
            success(result)

    def command_failure(self, request_id, message):
        _, failure = self.callbacks.pop(request_id, (None, None))
        if failure:
            failure(message)
        else:
            self.notice(message)

    def network_event(self, event, data):
        if event == "ready":
            self.ready = True
            self.port = data["port"]
            self.progress.hide()
            self.add_button.setEnabled(True)
            self.invite_button.setEnabled(not self.remote)
            self.add_activity("Your desktop is connected", "Local relay and device identity started automatically")
            if self.storage.settings.get("reduce_motion"):
                self.orbit.animation.stop()
        elif event == "workspace":
            self.remote = data["remote"]
            self.identity = data["self"]
            self.workspace_label.setText(data["name"])
            self.identity_label.setText(data["self"])
            self.invite_button.setEnabled(self.ready and not self.remote)
            self.chats.clear()
            self.selected = None
            self.request_count = 0
            self.render_messages()
        elif event == "agents":
            self.agents = data["agents"]
            self.connection_status.setText("●  Relay connected" if data["connected"] else "●  Reconnecting…")
            self.render_agents()
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

    def invite_device(self):
        if self.ready and not self.remote:
            InviteDialog(self).exec()

    def closeEvent(self, event):
        if self.closing and not self.network.isRunning():
            event.accept()
            return
        event.ignore()
        if not self.closing:
            self.closing = True
            # Remembered before the shutdown begins, because that path
            # ends in a second close that must not overwrite it.
            try:
                self.remember_window_size()
            except Exception:
                pass
            self.setEnabled(False)
            self.toast.setText("Disconnecting agents and shutting down your local relay…")
            self.toast.show()
            self.network.shutdown()

    def finish_close(self):
        if self.closing:
            self.close()
