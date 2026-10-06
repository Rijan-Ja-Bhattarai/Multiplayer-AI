import PySide6
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, QUrl, Slot
from PySide6.QtGui import (QGuiApplication, QIcon, QKeySequence, QPixmap, QPainter,
                           QColor, QDesktopServices, QFont, QPalette, QShortcut)
from PySide6.QtWidgets import (QApplication, QCheckBox, QFileDialog,
    QFormLayout, QFrame, QGraphicsOpacityEffect, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QScrollArea, QSizePolicy, QStackedWidget,
    QVBoxLayout,
    QWidget)

from network_a2a.persistence import HistoryStore, model_context
from network_a2a.content import MAX_MESSAGE_BYTES, content_summary, validate_content
from network_a2a.adapters import PROVIDERS

from .bridge import NetworkThread
from .dialogs import InviteDialog, parse_invitation
from .icons import navigation_icon, provider_logo, provider_pixmap
from .lan import lan_addresses
from .layout import minimum_size, sidebar_should_collapse, window_size
from .markdown import MarkdownMessage
from .resources import ResourceSampler
from .theme import (DARK, LIGHT, PROVIDER_NAMES, THEME_CHOICES, color,
                   provider_entry, provider_names, resolve_theme, stylesheet,
                   system_theme)
from .widgets import (Composer, ErrorLine, HoverRow, OrbitArt, Select, WorkspaceButton,
                     action, app_mark, label)


def frame(name, layout_type=QVBoxLayout):
    widget = QFrame()
    widget.setObjectName(name)
    layout = layout_type(widget)
    return widget, layout


def clear_layout(layout):
    """Hide and schedule removal of layout widgets, recursively clearing child layouts."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


# Navigation is declarative because the nav buttons, the stacked pages and
# the page title all have to agree on order and length. Adding a page here
# and nowhere else used to mean fixing the title list as well, which is
# exactly the kind of duplication that goes stale.
PAGES = (
    ("Overview", "overview"),
    ("Agents", "agents"),
    ("Workspaces", "workspaces"),
    ("Conversations", "conversations"),
    ("Providers", "providers"),
    ("Resources", "resources"),
    ("Settings", "settings"),
)
PAGE_TITLES = tuple(f"#  {key}" for _, key in PAGES)
# Looked up by key rather than counted. Adding a page used to shift every
# index below it, which broke the Ctrl+5 and Ctrl+, shortcuts without
# anything failing: the numbers were still in range, just pointing at the
# wrong page.
PAGE_INDEX = {key: index for index, (_, key) in enumerate(PAGES)}

# Tooltips for the navigation buttons. Every one of them is reachable from
# the keyboard and from the rail, so a page whose name does not describe its
# contents needs saying out loud.
PAGE_HELP = {
    "overview": "What is in this workspace, and what to do next",
    "agents": "Every model agent on this workspace, online or not",
    "workspaces": "The workspace you are in, and how to add another",
    "conversations": "Shared conversations and the messages in them",
    "providers": "Where models come from: local, hosted, or your own endpoint",
    "resources": "Disk, memory and what this device is using",
    "settings": "Appearance, privacy and resetting this device's identity",
}
RESOURCES_PAGE = PAGE_INDEX["resources"]
SETTINGS_PAGE = PAGE_INDEX["settings"]
WORKSPACES_PAGE = PAGE_INDEX["workspaces"]
AGENTS_PAGE = PAGE_INDEX["agents"]
# The mark beside a message, and the width a grouped message is indented by
# when it follows its own speaker's earlier message.
SPEAKER_AVATAR = 34


def app_icon(theme_name=DARK):
    """The window and taskbar icon, drawn in the accent colour."""
    return QIcon(app_mark(theme_name))


def device_initial(identity):
    """The single letter this device's avatar carries.

    Taken from the identity so two devices on one screen can be told apart,
    which a fixed mark cannot do. The identity is only known once the
    runtime has started, so an unidentified device gets a question mark
    rather than a blank, and it is never allowed to be an empty string,
    which would draw an empty tile that looks like a missing image.
    """
    return (identity or "").strip()[:1].upper() or "?"


def refresh_join_icon(window, theme_name):
    """Redraw the rail's plus in this theme's success colour.

    Hand-painted, so like the orbit and the avatar it never sees a
    stylesheet and has to be told when the palette moves. Painted at twice
    the slot and marked as such, or it arrives soft on a dense display.
    The middle pixel of a cross is a stroke, so that is what a test reads
    to confirm the colour actually landed.
    """
    pixmap = navigation_icon("plus", color(theme_name, "success")).pixmap(48, 48)
    pixmap.setDevicePixelRatio(2)
    window.join_button.setIcon(QIcon(pixmap))


def device_avatar(theme_name, identity, size=34):
    """A circular avatar carrying this device's initial on the app's mark.

    The sidebar used to set its text to " M " with a fixed width but no
    height and a 14px radius, which drew a rounded rectangle of whatever
    height the layout gave it rather than the circle it was aiming at. It
    is painted at twice the size and scaled down instead, so the curve is
    the real one and it stays crisp on a dense display.
    """
    widget = QLabel()
    widget.setFixedSize(size, size)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    mark = app_mark(theme_name, device_initial(identity), size * 2)
    mark.setDevicePixelRatio(2)
    widget.setPixmap(mark)
    widget.setAccessibleName(f"This device, {identity or 'not identified yet'}")
    return widget


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
        self.busy = False
        # Every message a card shows, keyed by the card that owns it. Held
        # here rather than in the card's widgets because cards are rebuilt on
        # every poll: a message kept in a widget is destroyed by the next
        # render, which is exactly what happened to the workspace page's
        # single shared line. Only set_card_message, its dismiss handler and
        # the card's own action may touch these.
        self.card_messages = {}
        # The card message line currently on screen for each key, so a
        # rebuild of the page can find it again. Lines hold no message of
        # their own.
        self.card_lines = {}
        self.toast_error = False
        self.request_count = 0
        self.ready = False
        self.closing = False
        self.preparing_files = False
        self.setWindowTitle("Multiplayer AI")
        # Resolved before any widget is built so everything created below
        # picks up the right colours the first time.
        self.theme = resolve_theme(storage.settings)
        self.setWindowIcon(app_icon(self.theme))
        # The app always opens maximised, so no size is restored: a window
        # that reopens at whatever it happened to be last time is the reason
        # it read as an ordinary window rather than an app. A size is still
        # set here because Qt wants sane geometry before maximising, and the
        # minimum stays so restoring the window down cannot make it unusable.
        width, height = window_size(None, {}, self.primary_screen_size())
        self.resize(width, height)
        self.setMinimumSize(*minimum_size(self.primary_screen_size()))
        self.setStyleSheet(stylesheet(self.theme))
        self.style_placeholders()
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
        self.history_timer = QTimer(self)
        self.history_timer.setSingleShot(True)
        self.history_timer.setInterval(350)
        self.history_timer.timeout.connect(self.persist_history)
        self.composer.textChanged.connect(lambda: self.history_timer.start())
        self.network.start()

    def build_ui(self):
        """Build the workspace rail, navigation, pages, and application status controls."""
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
        join = WorkspaceButton("")
        self.join_button = join
        self.add_workspace_button = join
        join.setIconSize(QSize(24, 24))
        join.set_ink("success")
        join.setToolTip("Create or join a workspace")
        refresh_join_icon(self, self.theme)
        join.clicked.connect(self.add_workspace)
        rail_layout.addWidget(join)
        self.help_button = WorkspaceButton("?")
        self.help_button.clicked.connect(self.open_documentation)
        self.help_button.setToolTip("Project documentation")
        rail_layout.addWidget(self.help_button)
        shell.addWidget(rail)
        sidebar, outer = frame("sidebar")
        sidebar.setFixedWidth(238)
        self.sidebar = sidebar
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # The user panel has to run edge to edge, the way the sidebar it
        # belongs to does. It used to be a child of a layout carrying 14px
        # side margins, so its background stopped that far short on both
        # sides and read as a panel clipped by the window rather than as a
        # footer. Everything above it keeps those margins; the footer is
        # separated by a hairline instead, as in the dark theme.
        body = QWidget()
        body.setObjectName("sidebarBody")
        side = QVBoxLayout(body)
        side.setContentsMargins(14, 22, 14, 0)
        side.setSpacing(8)
        outer.addWidget(body, 1)
        self.workspace_label = label("My workspace", "heading", True)
        side.addWidget(self.workspace_label)
        self.workspace_settings_button = action("Workspace settings", self.manage_workspace, name="ghost")
        self.workspace_settings_button.setEnabled(False)
        self.workspace_settings_button.setToolTip(
            "Rename, invite a device to, or delete this workspace")
        side.addWidget(self.workspace_settings_button)
        side.addSpacing(18)
        side.addWidget(label("WORKSPACE", "eyebrow"))
        self.nav_buttons = []
        for index, (name, key) in enumerate(PAGES):
            button = action(name, lambda checked=False, page=index: self.navigate(page), name="nav")
            button.setIcon(navigation_icon(key, color(self.theme, "text")))
            button.setIconSize(QSize(20, 20))
            button.setCheckable(True)
            button.setToolTip(PAGE_HELP[key])
            self.nav_buttons.append(button)
            side.addWidget(button)
        side.addSpacing(16)
        chats = QHBoxLayout()
        chats.setContentsMargins(15, 0, 15, 0)
        self.chats_heading = label("CHATS AND AGENTS", "eyebrow")
        chats.addWidget(self.chats_heading)
        chats.addStretch()
        # The list below is blank until something is connected, which read as
        # a hole in the sidebar rather than as an absence of anything.
        self.chats_hint = label("Nothing yet", "muted", True)
        self.chats_hint.setStyleSheet("font-size:11px")
        # The sidebar is 238px wide; letting this wrap put it on a line of its
        # own under the heading, which read as a mistake rather than a note.
        self.chats_hint.setWordWrap(False)
        chats.addWidget(self.chats_hint)
        side.addLayout(chats)
        self.sidebar_agents = QListWidget()
        self.sidebar_agents.setMaximumHeight(245)
        # A long agent name used to grow a horizontal scrollbar under the
        # list, which reads as a control that has more to say sideways.
        # The names wrap instead, and there is nothing to scroll to.
        self.sidebar_agents.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.sidebar_agents.setWordWrap(True)
        self.sidebar_agents.setToolTip("Every conversation and agent you can open")
        self.sidebar_agents.itemClicked.connect(lambda item: self.select_agent(item.data(Qt.ItemDataRole.UserRole)))
        side.addWidget(self.sidebar_agents)
        side.addStretch()
        self.connection_status = label("●  Starting your network…", "online", True)
        self.connection_status.setToolTip(
            "Whether this device is talking to its own relay. Unlocked keys are needed to send.")
        side.addWidget(self.connection_status)
        profile, row = frame("profile", QHBoxLayout)
        row.setContentsMargins(12, 11, 12, 12)
        row.setSpacing(10)
        avatar = device_avatar(self.theme, self.identity)
        self.avatar = avatar
        row.addWidget(avatar)
        copy = QVBoxLayout()
        copy.setSpacing(1)
        # The device identity used to sit on its own line just above this
        # card, next to a card that already said "This device". One line
        # carrying the actual id is both shorter and more use to somebody
        # who has to read it out to join someone else's workspace.
        self.profile_identity = label("Starting…")
        self.profile_identity.setToolTip(
            "This device's identity. Someone inviting you to a workspace will "
            "need it.")
        copy.addWidget(self.profile_identity)
        copy.addWidget(label("This device", "muted"))
        row.addLayout(copy)
        outer.addWidget(profile)
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
        # The top bar carried no tooltips at all, so these two were the one
        # part of the shell a new user could hover with nothing to learn.
        self.invite_button.setToolTip(
            "Create an invitation that another device can use to join this workspace")
        self.add_button.setToolTip(
            "Connect a model agent to this workspace")
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
        self.progress.setToolTip("Waiting on the network")
        body_layout.addWidget(self.progress)
        # Beside the bar rather than replacing it, so the message and the
        # movement reinforce each other instead of competing.
        self.busy_label = label("", "muted", True)
        self.busy_label.hide()
        body_layout.addWidget(self.busy_label)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.overview_page())
        self.stack.addWidget(self.agents_page())
        self.stack.addWidget(self.workspaces_page())
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
        # Settle the welcome now rather than waiting for the first agents
        # event, so the first paint never shows the welcome and the
        # statistics at the same time.
        self.update_overview_state()

    def refresh_avatar(self):
        """Repaint the device avatar from the theme and the current identity.

        The avatar carries two things that both change under it: the accent
        it is filled with follows the theme, and the initial it carries
        follows the identity, which only arrives once the runtime starts.
        Called from both places rather than at construction, since the
        identity is empty until then.
        """
        if not hasattr(self, "avatar"):
            return
        mark = app_mark(self.theme, device_initial(self.identity),
                        self.avatar.width() * 2)
        mark.setDevicePixelRatio(2)
        self.avatar.setPixmap(mark)

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
        """Hide the agent sidebar on a window too narrow to carry it.

        The chat page carries its own agent list as well as the one in the
        sidebar, and with nothing connected both were empty 200px columns
        either side of the conversation. Each is hidden when it has nothing
        in it, so the room is left until there is something to pick.
        """
        collapse = sidebar_should_collapse(self.width(), self.primary_screen_size())
        self.sidebar.setVisible(not collapse)
        if getattr(self, "chat_agents", None) is not None:
            self.chat_agents.setVisible(not collapse and self.chat_agents.count() > 0)

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
        """Build the workspace overview, with a first-run welcome in place of it.

        A new user opening this page used to see three statistic cards all
        reading zero and a large empty activity list. None of that tells
        them anything, and the only action on offer was in the top bar.
        So when the workspace holds nothing at all, the statistics are
        replaced by a panel that says what the app is and offers the two
        ways to get started.
        """
        page, layout = self.scroll_page()
        layout.addWidget(label("Workspace overview", "title"))

        self.welcome_card, welcome = frame("hero")
        welcome.setContentsMargins(28, 26, 20, 26)
        welcome.setSpacing(12)
        # The orbit illustration was painted for this panel and never
        # mounted, so the first thing a new user saw had no motion in it at
        # all. Text and actions stay on the left; the art takes the right
        # and is allowed to shrink before the words are.
        hero_row = QHBoxLayout()
        hero_row.setSpacing(18)
        hero_words = QVBoxLayout()
        hero_words.setSpacing(10)
        hero_row.addLayout(hero_words, 1)
        self.welcome_art = OrbitArt(theme=self.theme)
        self.welcome_art.set_moving(not self.storage.settings.get("reduce_motion"))
        hero_row.addWidget(self.welcome_art, 0)
        welcome.addLayout(hero_row)
        hero_words.addWidget(label("Nothing here yet", "heroTitle"))
        hero_words.addWidget(label(
            "This workspace runs a private network on your own device. "
            "Connect a model to start talking to it, or join someone "
            "else's workspace with an invitation.", "muted", True))
        hero_words.addSpacing(6)
        # Held on the window so it can be disabled until the runtime is
        # ready, exactly as the top bar's own connect button is. Laid out in
        # a row so they keep their natural width instead of stretching the
        # full width of the card.
        self.welcome_connect = action("  +  Connect a model", self.add_agent, True)
        self.welcome_connect.setEnabled(False)
        self.welcome_join = action("  Join a workspace", self.add_workspace, name="ghost")
        self.welcome_connect.setToolTip(
            "Connect a model agent to this workspace")
        # This opens a menu offering both routes rather than going straight
        # to the join dialog, so the tooltip says so.
        self.welcome_join.setToolTip(
            "Create a new workspace, or join someone else's with an invitation")
        choices = QHBoxLayout()
        choices.setSpacing(10)
        choices.addWidget(self.welcome_connect)
        choices.addWidget(self.welcome_join)
        choices.addStretch()
        hero_words.addLayout(choices)
        layout.addWidget(self.welcome_card)

        # Everything the welcome replaces, kept as one widget so it can be
        # hidden as a unit rather than six pieces.
        self.overview_content = QWidget()
        body = QVBoxLayout(self.overview_content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(22)

        stats = QHBoxLayout()
        stats.setSpacing(16)
        self.stat_values = []
        for index, (title, subtitle) in enumerate((("Agents in this workspace", "Click to see your model agents"), ("Agents online", "Connected models"), ("Requests this visit", "Completed and pending requests"))):
            card, column = frame("stat")
            column.setContentsMargins(18, 15, 18, 15)
            column.addWidget(label(title, "muted"))
            value = action("0", self.show_workspace_agents, name="statValue") if index == 0 else label("0", "statValue")
            if index == 0:
                value.setFlat(True)
            column.addWidget(value)
            column.addWidget(label(subtitle, "muted"))
            self.stat_values.append(value)
            stats.addWidget(card, 1)
        body.addLayout(stats)
        body.addWidget(label("Your agents", "heading"))
        self.overview_cards = QGridLayout()
        self.overview_cards.setSpacing(14)
        body.addLayout(self.overview_cards)
        activity_card, column = frame("card")
        column.setContentsMargins(20, 18, 20, 18)
        column.addWidget(label("Workspace activity", "heading"))
        self.activity_list = QListWidget()
        self.activity_list.setMinimumHeight(140)
        self.activity_list.setMaximumHeight(210)
        column.addWidget(self.activity_list)
        body.addWidget(activity_card)
        body.addWidget(label("Private keys stay on your device. Possibilities go everywhere.", "muted"))
        body.addStretch()
        layout.addWidget(self.overview_content)
        layout.addStretch()
        return page

    def update_overview_state(self):
        """Show the welcome only while there is genuinely nothing to show.

        Deliberately narrow: an online count of zero with agents present is
        a real statistic, so the statistics only go away when there are no
        agents, no conversations and nothing has happened yet.

        The test is ``self.agents``, which always carries this device, and
        so could never make this true. The welcome has to key off the
        model agents a user can actually talk to, which is why it appeared
        on a first run and then never again.
        """
        empty = not self.model_agents() and not self.conversations
        self.welcome_card.setVisible(empty)
        self.overview_content.setVisible(not empty)

    def agents_page(self):
        page, layout = self.scroll_page()
        layout.addWidget(label("YOUR DISTRIBUTED TEAM", "eyebrow"))
        layout.addWidget(label("Agents", "title"))
        layout.addWidget(label("Models connected to this workspace.", "muted"))
        self.model_card, self.model_column = self.model_connection_card()
        layout.addWidget(self.model_card)
        layout.addWidget(label("CONNECTED", "eyebrow"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search agents or models…")
        self.search.textChanged.connect(self.render_agents)
        layout.addWidget(self.search)
        self.agent_cards = QGridLayout()
        self.agent_cards.setSpacing(16)
        layout.addLayout(self.agent_cards)
        layout.addStretch()
        return page

    def model_connection_card(self):
        """The whole model form, on the page, above the connected models.

        This is AgentDialog's 272 lines moved into the content area. It was a
        separate window for a form with no reason to be one: the fields were
        somewhere else from the list of models they add to, and the result of
        a save was a dialog closing rather than a new card appearing above.

        Built once and reused. Edit loads a profile into these same fields,
        so there is one place that knows what a model is, rather than a card
        and a dialog between them.
        """
        card, column = frame("createCard")
        column.setContentsMargins(22, 20, 22, 20)
        self.model_form_title = label("Connect a model", "heading")
        column.addWidget(self.model_form_title)
        column.addWidget(label(
            "Your model runs on this device. Its key stays in your OS "
            "credential store.", "muted", True))
        column.addSpacing(12)

        form = QFormLayout()
        form.setVerticalSpacing(12)
        self.model_name = QLineEdit()
        form.addRow("Agent name", self.model_name)
        self.model_provider = Select()
        for key, info in PROVIDER_NAMES.items():
            self.model_provider.addItem(info[0], key)
        form.addRow("Provider", self.model_provider)
        self.model_id = Select()
        self.model_id.setEditable(True)
        self.model_id.lineEdit().setPlaceholderText("Model ID, e.g. llama3.2")
        self.model_models_button = action("Find models", self.find_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_id, 1)
        model_row.addWidget(self.model_models_button)
        form.addRow("Model", model_row)
        self.model_base = QLineEdit()
        form.addRow("API root", self.model_base)
        self.model_key = QLineEdit()
        self.model_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model_key.setPlaceholderText("API key · leave blank to keep a saved key")
        form.addRow("API key", self.model_key)
        self.model_system = QPlainTextEdit()
        self.model_system.setMaximumHeight(90)
        self.model_system.setPlaceholderText("Optional instructions for this agent")
        form.addRow("Instructions", self.model_system)
        column.addLayout(form)

        self.model_autostart = QCheckBox("Start this agent automatically when the app opens")
        self.model_autostart.setChecked(True)
        self.model_autostart.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        column.addWidget(self.model_autostart, 0, Qt.AlignmentFlag.AlignLeft)
        self.model_insecure = QCheckBox("Allow a provider's HTTP endpoint on a trusted LAN")
        self.model_insecure.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        column.addWidget(self.model_insecure, 0, Qt.AlignmentFlag.AlignLeft)
        self.model_vision = QCheckBox("Enable image support for this model")
        self.model_vision.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        column.addWidget(self.model_vision, 0, Qt.AlignmentFlag.AlignLeft)
        column.addWidget(label(
            "Select a vision-capable model to understand images and scanned "
            "PDFs. Text PDFs work with any model.", "muted", True))

        column.addWidget(label("Internet access", "heading"))
        self.model_internet = QCheckBox("Allow this model to search the web")
        self.model_internet.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        column.addWidget(self.model_internet, 0, Qt.AlignmentFlag.AlignLeft)
        self.model_search_settings = QWidget()
        search_layout = QVBoxLayout(self.model_search_settings)
        search_layout.setContentsMargins(0, 0, 0, 0)
        self.model_search_form = QFormLayout()
        self.model_search_provider = Select()
        self.model_search_provider.addItem("Ollama web search · no server setup", "ollama")
        self.model_search_provider.addItem("SearXNG · use your own server", "searxng")
        self.model_search_mode = Select()
        self.model_search_mode.addItem(
            "Automatic · when the model needs outside information", "auto")
        self.model_search_mode.addItem("Always · search for every question", "always")
        self.model_search_url = QLineEdit()
        self.model_search_url.setPlaceholderText(
            "https://your-server.example.com or http://localhost:8888")
        self.model_search_key = QLineEdit()
        self.model_search_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model_search_key.setPlaceholderText(
            "Ollama account key · leave blank to keep a saved search key")
        self.model_search_form.addRow("Search provider", self.model_search_provider)
        self.model_search_form.addRow("Search mode", self.model_search_mode)
        self.model_search_form.addRow("Search API key", self.model_search_key)
        self.model_search_form.addRow("SearXNG server", self.model_search_url)
        search_layout.addLayout(self.model_search_form)
        self.model_search_insecure = QCheckBox("Allow SearXNG over HTTP on a trusted LAN")
        self.model_search_insecure.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        search_layout.addWidget(self.model_search_insecure, 0, Qt.AlignmentFlag.AlignLeft)
        self.model_search_help = label("", "muted", True)
        self.model_search_help.setTextFormat(Qt.TextFormat.RichText)
        self.model_search_help.setOpenExternalLinks(True)
        search_layout.addWidget(self.model_search_help)
        self.model_search_test = action("Test web search", self.test_web_search)
        self.model_search_testing = False
        search_layout.addWidget(self.model_search_test)
        self.model_search_status = label("", "muted", True)
        self.model_search_status.setWordWrap(True)
        search_layout.addWidget(self.model_search_status)
        search_layout.addWidget(label(
            "Automatic mode lets the model request a search for current or "
            "uncertain facts. Some models may miss when a search is needed; "
            "choose Always to search before every answer. Queries go to the "
            "search provider you select, and source links are included in the "
            "answer.", "muted", True))
        column.addWidget(self.model_search_settings)

        # Keyed model:connect, so a save failure belongs to this card alone
        # and survives the poll that rebuilds the grid below it.
        self.model_error_line = self.message_line("model:connect")
        column.addWidget(self.model_error_line)
        self.model_save_button = action("Connect agent", self.save_model, True)
        column.addWidget(self.model_save_button)

        # None means connecting a new model rather than editing one, which
        # decides whether the provider change may rename the field.
        self.model_profile_id = None
        self.model_provider.currentIndexChanged.connect(self.change_model_provider)
        self.model_internet.toggled.connect(self.change_model_internet)
        self.model_search_provider.currentIndexChanged.connect(
            self.change_model_internet)
        for signal in (self.model_search_url.textChanged,
                       self.model_search_key.textChanged,
                       self.model_search_insecure.toggled,
                       self.model_search_provider.currentIndexChanged,
                       self.model_internet.toggled):
            signal.connect(lambda *args: self.model_search_status.setText(""))
        self.change_model_provider()
        self.change_model_internet()
        return card, column

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
        self.share_conversation_button.setToolTip(
            "Invite another device into this conversation")
        chat_actions = QHBoxLayout()
        self.chat_agent_count = action("0 agents", self.show_chat_agents, name="ghost")
        self.chat_agent_count.setToolTip(
            "Show the agents taking part in this conversation")
        chat_actions.addWidget(self.chat_agent_count)
        chat_actions.addWidget(self.share_conversation_button)
        chat_actions.addStretch()
        column.addLayout(chat_actions)
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
        self.attachment_rows = QVBoxLayout()
        self.attachment_rows.setSpacing(6)
        # The composer already sat outside the scrolling area, so it did not
        # move; what it lacked was any sign that it was a different surface
        # from the messages above it. A hairline is what says that.
        self.composer_bar, composer_column = frame("composerBar")
        composer_column.setContentsMargins(0, 12, 0, 14)
        composer_column.setSpacing(8)
        composer_column.addLayout(self.attachment_rows)
        composer_column.addWidget(self.composer)
        footer = QHBoxLayout()
        self.attach_button = action("Attach images / PDFs", self.attach_files)
        # The hint beside this button states the keys but not what the button
        # itself accepts, and only agents with vision can read an image.
        self.attach_button.setToolTip(
            "Attach images or PDFs. An image is only read by an agent with vision enabled.")
        footer.addWidget(self.attach_button)
        footer.addWidget(label("Enter to send · Shift + Enter for a new line", "muted"))
        footer.addStretch()
        self.send_button = action("Send request  ↑", self.send_message, True)
        self.send_button.setToolTip("Send this message to the selected agent")
        footer.addWidget(self.send_button)
        composer_column.addLayout(footer)
        column.addWidget(self.composer_bar)
        layout.addWidget(panel, 1)
        return page

    def providers_page(self):
        """Build imported model controls and provider connection cards with brand logos."""
        page, layout = self.scroll_page()
        layout.addWidget(label("CHOOSE YOUR INTELLIGENCE", "eyebrow"))
        layout.addWidget(label("Models", "title"))
        layout.addWidget(label("Configure a provider once. The app starts its agent for you, every time.", "muted"))
        layout.addWidget(label("Imported models", "heading"))
        self.imported_model_rows = QVBoxLayout()
        self.imported_model_rows.setSpacing(10)
        layout.addLayout(self.imported_model_rows)
        layout.addWidget(label("Connect another model", "heading"))
        grid = QGridLayout()
        grid.setSpacing(16)
        self.provider_logos = {}
        providers = provider_names(self.theme)
        for index, provider in enumerate(providers):
            card, column = frame("card")
            column.setContentsMargins(21, 20, 21, 20)
            info = providers[provider]
            logo = provider_logo(provider, self.theme)
            self.provider_logos[provider] = logo
            column.addWidget(logo)
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
        layout.addWidget(label("Device totals include all programs and local model runtimes such as Ollama. Multiplayer AI's own usage is shown separately below. Models hosted by a provider use that provider's hardware.", "muted", True))

        self.resource_timer = QTimer(self)
        self.resource_timer.setInterval(2000)
        self.resource_timer.timeout.connect(self.refresh_resources)

        def meter(key, title, subtitle):
            """Add a labeled usage card and return its reading label and progress bar."""
            card, column = frame("stat")
            column.setContentsMargins(18, 15, 18, 15)
            column.addWidget(label(title, "muted"))
            reading = label("—", "statValue")
            column.addWidget(reading)
            detail = label(subtitle, "muted", True)
            self.resource_details[key] = detail
            column.addWidget(detail)
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            bar.setAccessibleName(title)
            column.addSpacing(6)
            column.addWidget(bar)
            layout.addWidget(card)
            return reading, bar

        self.resource_details = {}
        self.resource_rows = {
            "cpu": meter("cpu", "Processor · entire device", "Combined CPU load from all programs"),
            "memory": meter("memory", "Memory (RAM) · entire device", "RAM used by all programs and the operating system"),
            "disk": meter("disk", "Storage · app data drive", "Disk space used by all files on this drive; not disk activity"),
        }

        layout.addWidget(label("CPU load per logical processor · all programs", "heading", True))
        self.core_bars = []
        self.core_labels = []
        self.core_grid = QGridLayout()
        self.core_grid.setSpacing(10)
        layout.addLayout(self.core_grid)

        detail, column = frame("card")
        column.setContentsMargins(20, 18, 20, 18)
        column.addWidget(label("Multiplayer AI · this app process", "heading"))
        column.addWidget(label("Includes the desktop interface and its relay. Separate model processes, such as Ollama, are included in the device totals above.", "muted", True))
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
        self.theme_picker = Select()
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
        self.launch_screen_toggle = QCheckBox("Show the launch screen")
        self.launch_screen_toggle.setChecked(
            self.storage.settings.get("launch_screen", True) is not False)
        self.launch_screen_toggle.toggled.connect(self.launch_screen_changed)
        column.addWidget(self.launch_screen_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        column.addWidget(label(
            "A brief branded screen while the network starts. Reduce "
            "animations below overrides it either way.", "muted", True))
        column.addSpacing(10)
        self.reduce_motion = QCheckBox("Reduce animations")
        self.reduce_motion.setChecked(self.storage.settings.get("reduce_motion", False))
        self.reduce_motion.toggled.connect(self.motion_changed)
        column.addWidget(self.reduce_motion, 0, Qt.AlignmentFlag.AlignLeft)
        column.addWidget(label(
            "Turns off page fades and the moving illustration, and the "
            "launch screen with them.", "muted", True))
        layout.addWidget(motion)

        cleanup, column = frame("settings")
        column.setContentsMargins(22, 18, 22, 20)
        column.addWidget(label("Stored credentials", "heading"))
        self.pending_cleanup = label("", "muted", True)
        self.pending_cleanup.setWordWrap(True)
        self.pending_cleanup.setVisible(False)
        column.addWidget(self.pending_cleanup)
        layout.addWidget(cleanup)

        storage, column = frame("settings")
        column.setContentsMargins(22, 18, 22, 20)
        column.addWidget(label("Data on this device", "heading"))
        self.storage_path = label(str(self.storage.directory), "muted", True)
        self.storage_path.setWordWrap(True)
        self.storage_path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.storage_path)
        column.addWidget(action("Show in file manager", self.open_storage_folder))
        layout.addWidget(storage)
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
            self.resource_details["memory"].setText(
                f"{memory['used_human']} used of {memory['total_human']} RAM · {memory['available_human']} available · all programs and the operating system")
        else:
            self.resource_rows["memory"][0].setText("Unavailable")
            self.resource_rows["memory"][1].setValue(0)
            self.resource_details["memory"].setText("System RAM usage is unavailable.")

        disk = reading["disk"]
        if disk:
            self.resource_rows["disk"][0].setText(f"{disk['percent']:.0f}%")
            self.resource_rows["disk"][1].setValue(int(disk["percent"]))
            self.resource_details["disk"].setText(
                f"{disk['used_human']} used of {disk['total_human']} · {disk['free_human']} free\n"
                f"Drive containing {disk['path']} · all files on that drive, not disk activity")
        else:
            self.resource_rows["disk"][0].setText("Unavailable")
            self.resource_rows["disk"][1].setValue(0)
            self.resource_details["disk"].setText("Storage usage is unavailable.")

        cores = reading["per_core"] or []
        while len(self.core_bars) < len(cores):
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            index = len(self.core_bars)
            caption = label(f"Logical CPU {index + 1}", "muted")
            bar.setAccessibleName(f"Logical CPU {index + 1} load from all programs")
            core = QWidget()
            column = QVBoxLayout(core)
            column.setContentsMargins(0, 0, 0, 0)
            column.addWidget(caption)
            column.addWidget(bar)
            self.core_labels.append(caption)
            self.core_bars.append(bar)
            self.core_grid.addWidget(core, index // 2, index % 2)
        for index, (caption, bar, value) in enumerate(zip(self.core_labels, self.core_bars, cores)):
            caption.setText(f"Logical CPU {index + 1} · {value:.0f}%")
            bar.setValue(int(value))

        process = reading["process"]
        if process:
            self.app_detail.setText(
                f"App RAM: {process['memory_human']} resident memory\n"
                f"App CPU: {process['cpu']:.0f}% of one logical CPU (can exceed 100% across several CPUs)\n"
                f"App threads: {process['threads']}"
            )
        else:
            self.app_detail.setText("Unavailable")

        self.system_detail.setText(
            f"Uptime {reading['uptime']} · {reading['core_count']} logical cores · "
            f"preferences at {self.storage.directory}"
        )

    def open_documentation(self):
        """Open the bundled offline guide in the browser, reporting unavailable files."""
        path = Path(__file__).resolve().parents[1] / "docs.html"
        if not path.is_file() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.notice("Could not open the documentation. Open docs.html from the app folder in your browser.", error=True)

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
            # Saved here too, not just in the branch below: removing the
            # preference must persist immediately, or the theme reappears
            # on next launch because nothing else wrote the file yet.
            self.storage.save()
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

    def style_toast(self, error=None):
        """Repaint the toast in the current theme's colours.

        ``error`` selects the error foreground so a failure is not
        reported in the success colour. Left as None it keeps the current
        state, because apply_theme() restyles on a theme switch and an
        error toast still on screen must not turn green.
        """
        if error is not None:
            self.toast_error = error
        self.toast.setStyleSheet("background:" + color(self.theme, "toast_bg")
                                 + "; color:" + color(self.theme, "error" if self.toast_error else "toast_fg")
                                 + "; border:1px solid " + color(self.theme, "text")
                                 + "; padding:12px 24px;")

    def style_placeholders(self):
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(color(self.theme, "text_muted")))
        self.setPalette(palette)

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
        self.style_placeholders()
        self.setWindowIcon(app_icon(name))
        self.style_toast()
        # A card message carries its own colour, so it does not follow the
        # application sheet and would keep the old theme's error colour.
        for line in self.card_lines.values():
            line.set_theme(name)
        self.join_button.set_ink("success")
        refresh_join_icon(self, name)
        self.refresh_avatar()
        if hasattr(self, "welcome_art"):
            self.welcome_art.set_theme(name)
        for button, (_, key) in zip(self.nav_buttons, PAGES):
            button.setIcon(navigation_icon(key, color(name, "text")))
        for provider, logo in self.provider_logos.items():
            logo.setPixmap(provider_pixmap(provider, name))
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

    def launch_screen_changed(self, wanted):
        """Store whether to show the launch screen next time.

        A preference rather than a motion setting, so it is stored on its
        own. It only takes effect on the next start; the screen is
        already up or already gone by the time anybody can reach this.
        """
        self.storage.settings["launch_screen"] = bool(wanted)
        self.storage.save()

    def motion_changed(self, reduced):
        self.storage.settings["reduce_motion"] = reduced
        self.storage.save()
        if reduced and hasattr(self, "page_animation"):
            self.page_animation.stop()
            effect = self.stack.currentWidget().graphicsEffect()
            if effect:
                effect.setOpacity(1.0)
        if hasattr(self, "welcome_art"):
            self.welcome_art.set_moving(not reduced)

    def is_model_agent(self, agent):
        return bool(agent.get("kind") == "model" or agent.get("provider") or agent.get("model") or any(
            member["id"] == agent["id"] and member.get("role") == "Model" for member in self.workspace_meta.get("members", [])))

    def model_agents(self, chat_only=False):
        agents = [agent for agent in self.agents if self.is_model_agent(agent)]
        if chat_only:
            room = self.conversations.get(self.selected)
            identities = {room["target"], *room["members"]} if room else {self.selected}
            agents = [agent for agent in agents if agent["id"] in identities]
        return agents

    def show_workspace_agents(self):
        self.show_agent_list(False)

    def show_chat_agents(self):
        self.show_agent_list(True)

    def show_agent_list(self, chat_only):
        from .agent_list_dialog import AgentListDialog
        dialog = AgentListDialog(self, chat_only)
        dialog.exec()
        dialog.deleteLater()

    def render_imported_models(self):
        models = self.model_agents()
        signature = (self.workspace_id, json.dumps(models, sort_keys=True))
        if getattr(self, "_model_rows_signature", None) == signature:
            return
        self._model_rows_signature = signature
        clear_layout(self.imported_model_rows)
        if not models:
            self.imported_model_rows.addWidget(label("No models connected yet. Choose a provider below.", "muted", True))
        for agent in models:
            card, row = frame("card", QHBoxLayout)
            copy = QVBoxLayout()
            copy.addWidget(label(agent["id"], "heading"))
            copy.addWidget(label(f"{agent.get('provider') or 'Model'} · {agent.get('model') or 'Configured model'}", "muted", True))
            profile = agent.get("profile", {})
            search = profile.get("web_search", "off")
            if profile:
                copy.addWidget(label("Web search: " + {"off": "Off", "auto": "Automatic", "always": "Always"}[search] +
                    (" · Images enabled" if profile.get("vision") else " · Text and PDFs"), "muted"))
            row.addLayout(copy, 1)
            if profile:
                row.addWidget(action("Edit model", lambda checked=False, current=agent: self.edit_agent(current)))
            else:
                row.addWidget(label("Managed on its device", "muted"))
            self.imported_model_rows.addWidget(card)

    def navigate(self, index):
        """Select a page, update navigation, and sample resources only while visible."""
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
        """Refresh model listings while retaining unchanged cards and scroll positions."""
        models = self.model_agents()
        for grid, agents in ((self.overview_cards, models[:3]),
                             (self.agent_cards, [agent for agent in models if self.search.text().lower() in f"{agent['id']} {agent.get('model') or ''}".lower()])):
            signature = (self.workspace_id, self.theme, json.dumps(agents, sort_keys=True))
            if getattr(grid, "render_signature", None) == signature:
                continue
            grid.render_signature = signature
            clear_layout(grid)
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
                top.addWidget(provider_logo(agent.get("provider"), self.theme, 28))
                top.addStretch()
                status = label("● Online" if agent["online"] else "● Offline", "online" if agent["online"] else "muted")
                top.addWidget(status)
                column.addLayout(top)
                column.addWidget(label(agent["id"], "heading", True))
                column.addWidget(label(agent.get("model") or info[0], "muted", True))
                column.addSpacing(7)
                talk = action("Open conversation  →", lambda checked=False, id=agent["id"]: self.select_agent(id))
                talk.setEnabled(agent["online"])
                talk.setToolTip(f"Start a conversation with {agent['id']}")
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
        # The list is blank until something is connected, which read as a
        # hole in the sidebar rather than as an absence of anything. It was
        # also never blank in practice, because this device is always one of
        # its own entries, so the hint keyed off the count of rows never
        # appeared. It now agrees with the welcome panel instead.
        self.chats_hint.setVisible(not self.model_agents() and not self.conversations)
        self.stat_values[0].setText(str(len(models)))
        self.stat_values[1].setText(str(sum(agent["online"] for agent in models)))
        self.stat_values[2].setText(str(self.request_count))
        self.update_overview_state()
        self.update_chat_controls()
        self.render_imported_models()

    def update_chat_controls(self):
        room = self.conversations.get(self.selected)
        target = room["target"] if room else self.selected
        agent = next((item for item in self.agents if item["id"] == target), None)
        chat = self.chats.get(self.selected, {})
        pending = chat.get("pending") or chat.get("local_pending")
        self.send_button.setEnabled(bool(agent and agent["online"] and not pending and not self.preparing_files))
        self.attach_button.setEnabled(bool(self.ready and agent and self.is_model_agent(agent) and not pending and not self.preparing_files))
        count = len(self.model_agents(chat_only=True))
        self.chat_agent_count.setText(f"{count} " + ("agent" if count == 1 else "agents"))
        self.chat_agent_count.setEnabled(bool(self.selected))
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
        self.render_attachments()
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
        self.render_attachments()
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

    def speaker_avatar(self, speaker, role):
        """The mark beside a message.

        The provider's own logo when the speaker is a known model agent, so
        a conversation between providers can be read at a glance, and the
        shared device mark otherwise. Both are painted rather than styled,
        so they follow the theme on the same terms as the orbit art.
        """
        if role == "user":
            return device_avatar(self.theme, self.identity, SPEAKER_AVATAR)
        agent = next((entry for entry in self.agents if entry["id"] == speaker), None)
        if agent and agent.get("provider"):
            return provider_logo(agent["provider"], self.theme, SPEAKER_AVATAR)
        return device_avatar(self.theme, speaker, SPEAKER_AVATAR)

    def copy_message(self, text):
        """Put a message on the clipboard.

        Says so afterwards, because nothing on screen changes when a copy
        succeeds and silence would leave the button looking inert.
        """
        QApplication.clipboard().setText(text)
        self.notice("Message copied.")

    def render_messages(self):
        clear_layout(self.messages)
        chat = self.chats.get(self.selected, {})
        if not chat.get("messages"):
            # Two different empty states. Saying "the beginning of your
            # collaboration" while the title above still reads "Choose an
            # agent" told a first-run user they had already started one.
            if self.selected:
                self.messages.addWidget(
                    label("#  This is the beginning of your collaboration.", "heading", True))
                self.messages.addWidget(
                    label("Ask a question, explore an idea, or simply say hello.", "muted", True))
            else:
                self.messages.addWidget(
                    label("#  No conversation open", "heading", True))
                self.messages.addWidget(
                    label("Pick a conversation or an agent on the left, or connect a "
                          "model from Providers to start one.", "muted", True))
        names = {"user": "You", "error": "Request unsuccessful", "local_agent": "Agent on this device"}
        room = self.conversations.get(self.selected)
        previous = None
        for role, text in chat.get("messages", []):
            speaker = role.removeprefix("member:") if role.startswith("member:") else names.get(role, room["target"] if room else self.selected)
            # Discord groups consecutive messages from one speaker under a
            # single header rather than boxing each one. A row per message
            # with a card around it reads as a stack of documents, which is
            # what this used to look like.
            grouped = speaker == previous
            row = HoverRow()
            if grouped:
                # Keep the text aligned under the first message's text
                # rather than sliding it left under the absent avatar.
                spacer = QWidget()
                spacer.setFixedWidth(SPEAKER_AVATAR)
                row.add_content(spacer, 0, Qt.AlignmentFlag.AlignTop)
            else:
                row.add_content(self.speaker_avatar(speaker, role), 0,
                                Qt.AlignmentFlag.AlignTop)
            column = QVBoxLayout()
            column.setSpacing(4)
            row.add_content(column, 1)
            if not grouped:
                title = label(speaker)
                title.setStyleSheet("font-weight:650; color:"
                                    + color(self.theme, "error" if role == "error" else "agent_title") + ";")
                column.addWidget(title)
            if role in ("assistant", "local_agent"):
                body = MarkdownMessage(text, theme_name=self.theme)
            else:
                body = label(text, wrap=True)
                body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            column.addWidget(body)
            copy = action("Copy", lambda checked=False, value=text: self.copy_message(value), name="ghost")
            copy.setToolTip("Copy this message")
            row.add_action(copy)
            self.messages.addWidget(row)
            previous = speaker
        if chat.get("pending") or chat.get("local_pending"):
            self.messages.addWidget(label("● ● ●   Waiting for your agent…", "muted"))
        self.update_chat_controls()
        self.persist_history()
        QTimer.singleShot(0, lambda: self.messages_scroll.verticalScrollBar().setValue(self.messages_scroll.verticalScrollBar().maximum()))

    def render_attachments(self):
        clear_layout(self.attachment_rows)
        files = self.chats.get(self.selected, {}).get("draft_attachments", [])
        for index, item in enumerate(files):
            row = QHBoxLayout()
            row.addWidget(label(item["name"] + " · " + item["note"], "muted", True), 1)
            remove = action("Remove", lambda checked=False, position=index: self.remove_attachment(position), name="ghost")
            remove.setEnabled(not self.preparing_files)
            # A column of identical "Remove" buttons is ambiguous, so each
            # one names the file it drops.
            remove.setToolTip(f"Remove {item['name']} from this message")
            row.addWidget(remove)
            self.attachment_rows.addLayout(row)

    def remove_attachment(self, position):
        files = self.chats.get(self.selected, {}).get("draft_attachments", [])
        if 0 <= position < len(files):
            files.pop(position)
            self.render_attachments()
            self.persist_history()

    def attach_files(self):
        if not self.selected or not self.attach_button.isEnabled():
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "Attach images or PDFs", "",
            "Images and PDFs (*.png *.jpg *.jpeg *.webp *.gif *.bmp *.pdf);;PDF documents (*.pdf);;Images (*.png *.jpg *.jpeg *.webp *.gif *.bmp)")
        if paths:
            self.prepare_files(paths)

    def prepare_files(self, paths):
        room = self.conversations.get(self.selected)
        identity = room["target"] if room else self.selected
        agent = next((agent for agent in self.agents if agent["id"] == identity), None)
        if not agent or not self.is_model_agent(agent) or self.preparing_files:
            return
        workspace_id, target, store = self.workspace_id, self.selected, self.history_store
        self.preparing_files = True
        self.attach_button.setText("Reading files…")
        self.update_chat_controls()
        def finished():
            self.preparing_files = False
            self.attach_button.setText("Attach images / PDFs")
            self.render_attachments()
            self.update_chat_controls()
        def success(prepared):
            if not any(entry["id"] == workspace_id for entry in self.workspace_list):
                finished()
                return
            state = None
            if workspace_id == self.workspace_id:
                chat = self.chats.setdefault(target, {"messages": [], "history": [], "pending": False})
            else:
                state = store.load("ui").get("state", {})
                chat = state.setdefault("chats", {}).setdefault(target, {"messages": [], "history": [], "pending": False})
            combined = [*chat.get("draft_attachments", []), *prepared]
            if len(combined) > 8 or len(json.dumps(combined).encode()) > MAX_MESSAGE_BYTES - 65536:
                self.notice("Attach up to 8 files within the request limit. Send the existing files first.")
            else:
                chat["draft_attachments"] = combined
                try:
                    validate_content([{"type": "text", "text": "Analyze attached files"},
                                      *(part for item in combined for part in item["content"])])
                except ValueError as exc:
                    chat["draft_attachments"] = combined[:-len(prepared)]
                    self.notice(str(exc))
                if state is None:
                    self.persist_history()
                else:
                    store.save("ui", "state", state)
            finished()
        def failed(message):
            finished()
            self.notice(message)
        self.command("prepare_attachments", paths, agent.get("vision", False), success=success, failure=failed)

    def send_message(self):
        if not self.selected or not self.send_button.isEnabled():
            return
        text = self.composer.toPlainText().strip()
        attached = self.chats[self.selected].get("draft_attachments", [])
        if not text and not attached:
            return
        if not text:
            text = "Please analyze the attached files."
        if len(json.dumps(text).encode()) > 180000:
            self.notice("This message is too large. Send a shorter request.")
            return
        target = self.selected
        chat = self.chats[target]
        content = [{"type": "text", "text": text}, *(part for item in attached for part in item["content"])] if attached else text
        try:
            content = validate_content(content)
        except ValueError as exc:
            self.notice(str(exc))
            return
        display_text = content_summary(content)
        workspace_id, store = self.workspace_id, self.history_store
        self.live_requests.add((workspace_id, target))
        if target in self.conversations:
            chat["local_pending"] = True
            chat["draft_attachments"] = []
            self.composer.clear()
            self.render_attachments()
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
            self.command("send_conversation", target, content, success=finished, failure=failed)
            return
        payload = {"messages": model_context([*chat["history"], {"role": "user", "content": content}])} if chat["history"] or attached else {"text": text}
        chat["pending"] = True
        chat["messages"].append(("user", display_text))
        chat["draft_attachments"] = []
        self.composer.clear()
        self.render_attachments()
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
                chat["history"] = [*chat["history"], {"role": "user", "content": content}, {"role": "assistant", "content": response}]
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

    def notice(self, message, error=False):
        self.style_toast(error)
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
        self.busy_changed(len(self.callbacks) + 1)

    def busy_changed(self, in_flight):
        """Show that the app is waiting on the network, and what for.

        Every command goes through here, so joining a workspace, switching
        to one and connecting a model all get the same feedback rather than
        only the ones that happened to remember it. Counted rather than
        toggled, because two commands can overlap and the first to finish
        must not clear the indicator while the second is still running.
        """
        if in_flight and not self.busy:
            self.busy = True
            self.progress.show()
            self.busy_label.setText("  Working…")
        elif not in_flight and self.busy:
            self.busy = False
            self.progress.hide()
            self.busy_label.setText("")
        if in_flight:
            self.busy_label.setVisible(True)
        else:
            self.busy_label.setVisible(False)

    @Slot(str, object)
    def command_success(self, request_id, result):
        success, _ = self.callbacks.pop(request_id, (None, None))
        self.busy_changed(len(self.callbacks))
        if success:
            success(result)

    @Slot(str, str)
    def command_failure(self, request_id, message):
        _, failure = self.callbacks.pop(request_id, (None, None))
        self.busy_changed(len(self.callbacks))
        if failure:
            failure(message)
        else:
            self.notice(message, error=True)

    def show_pending_cleanup(self, count, files=0):
        """State, rather than a passing notice, for a cleanup still owed.

        The toast is gone in a few seconds, and this can be true for as
        long as the credential store stays locked or something keeps hold of
        a deleted workspace's folder, so Settings keeps it visible until the
        cleanup actually completes.

        The two are reported separately because they need opposite things:
        an undeleted credential needs the keyring unlocked, a stranded file
        needs whatever is holding the folder closed.
        """
        count, files = int(count or 0), int(files or 0)
        if not hasattr(self, "pending_cleanup"):
            return
        self.pending_cleanup.setVisible(count > 0 or files > 0)
        sentences = []
        if count:
            sentences.append(
                f"{count} saved {'credential' if count == 1 else 'credentials'} from a "
                "deleted workspace could not be removed. Unlock your credential store "
                "and restart; the app will try again.")
        if files:
            sentences.append(
                f"{files} deleted {'workspace' if files == 1 else 'workspaces'}"
                f"{' has' if files == 1 else ' have'} files that could not be removed. "
                "Close anything still using that folder; the app will try again.")
        if sentences:
            self.pending_cleanup.setText(" ".join(sentences))

    @Slot(str, object)
    def network_event(self, event, data):
        if event == "ready":
            self.ready = True
            self.port = data["port"]
            self.progress.hide()
            self.add_button.setEnabled(True)
            self.welcome_connect.setEnabled(True)
            # The Workspaces page holds its buttons disabled until the
            # runtime is up. It refreshed on the agents poll, so opening it
            # in the first second would have shown controls that were not
            # yet usable and gave no sign that they were coming.
            self.render_workspace_page()
            self.invite_button.setEnabled(not self.remote)
            self.workspace_settings_button.setEnabled(True)
            self.update_chat_controls()
            self.add_activity("Your desktop is connected", "Local relay and device identity started automatically")
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
            self.profile_identity.setText(data["self"])
            self.invite_button.setEnabled(self.ready and not self.remote)
            if changed:
                self.request_count = 0
            self.refresh_avatar()
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
            self.render_workspace_page()
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
                    else "member:" + message["from"] if message["role"] == "user" else message["role"], content_summary(message["content"]))
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
        elif event == "pending_cleanup":
            self.show_pending_cleanup(data["credentials"], data.get("files", 0))
        elif event == "offline":
            self.connection_status.setText("●  Relay unavailable")
            for agent in self.agents:
                agent["online"] = False
            for member in self.workspace_meta.get("members", []):
                member["online"] = False
            self.render_agents()

    def change_model_provider(self):
        """Point the form at whichever provider is selected.

        Same rule the dialog used, so the model it can reach and the key it
        needs are decided in one place.
        """
        key = self.model_provider.currentData()
        spec = PROVIDERS[key]
        if self.model_profile_id is None:
            self.model_name.setText(f"{key}-agent")
        if self.remote:
            self.model_name.setText(self.identity)
            self.model_name.setReadOnly(True)
        self.model_base.setText(spec.base_url or "")
        self.model_base.setPlaceholderText("https://your-deployment.example.com/v1")
        self.model_models_button.setVisible(key not in ("anthropic", "gemini"))
        self.model_key.setPlaceholderText(
            "Optional for local Ollama" if not spec.key_required
            else "API key · leave blank to keep a saved key")

    def change_model_internet(self):
        """Show the chosen search provider's fields and allow a test."""
        enabled = self.model_internet.isChecked()
        hosted = self.model_search_provider.currentData() == "ollama"
        self.model_search_settings.setVisible(enabled)
        self.model_search_form.setRowVisible(self.model_search_key, hosted)
        self.model_search_form.setRowVisible(self.model_search_url, not hosted)
        self.model_search_insecure.setVisible(not hosted)
        self.model_search_test.setEnabled(enabled and not self.model_search_testing)
        self.model_search_help.setText(
            f'Get a key from <a href="https://ollama.com/settings/keys" '
            f'style="color: {color(self.theme, "accent")}">your Ollama account</a> '
            'and paste it into Search API key. It works with any connected model, '
            'including local Ollama. The key stays in your OS credential store.'
            if hosted else
            "SearXNG needs a running search server; it is separate from your "
            "model's API root. Enter its address above. The server owner must "
            "enable JSON search results. Choose Ollama web search if you do not "
            "have a server.")

    def model_search_state(self):
        """Everything that would make a test result out of date."""
        return (self.model_search_provider.currentData(), self.model_search_mode.currentData(),
                self.model_search_url.text().strip(), self.model_search_key.text().strip(),
                self.model_search_insecure.isChecked())

    def validate_model_search(self):
        """Refuse an incomplete search setup, saying what is missing."""
        if not self.model_internet.isChecked():
            return True
        if self.model_search_provider.currentData() == "ollama":
            if not self.model_search_key.text().strip() and self.model_search_mode.currentData() != "off":
                self.focus_model_search()
                self.set_card_message(
                    self.model_error_line,
                    "Ollama web search needs an account key in Search API key.")
                return False
            return True
        url = self.model_search_url.text().strip()
        if not url:
            self.focus_model_search()
            self.set_card_message(
                self.model_error_line,
                "SearXNG needs the address of your search server.")
            return False
        if not url.startswith(("https://", "http://")):
            self.focus_model_search()
            self.set_card_message(
                self.model_error_line, "The SearXNG address must start with http:// or https://")
            return False
        if url.startswith("http://") and not self.model_search_insecure.isChecked():
            self.focus_model_search()
            self.set_card_message(
                self.model_error_line,
                "That SearXNG address is plain HTTP. Tick the trusted-LAN box "
                "to allow it, or use https://")
            return False
        return True

    def focus_model_search(self):
        """Put the caret in the search field the problem is about."""
        hosted = self.model_search_provider.currentData() == "ollama"
        (self.model_search_key if hosted else self.model_search_url).setFocus()

    def find_models(self):
        """List the provider's models without blocking the page."""
        self.model_models_button.setEnabled(False)

        def success(models):
            self.model_models_button.setEnabled(True)
            selected = self.model_id.currentText()
            self.model_id.clear()
            self.model_id.addItems(models)
            if selected:
                self.model_id.setCurrentText(selected)
            if not models:
                self.set_card_message(
                    self.model_error_line,
                    "No models found. Pull a model in Ollama or enter your "
                    "provider's model ID directly.")

        def failure(message):
            self.model_models_button.setEnabled(True)
            self.set_card_message(
                self.model_error_line,
                "Could not list models. Check the API root and key, or enter "
                "the model ID directly.")

        self.command("provider_models", self.model_provider.currentData(),
                     self.model_base.text().strip(), self.model_key.text() or None,
                     self.model_insecure.isChecked(), self.model_profile_id,
                     success=success, failure=failure)

    def test_web_search(self):
        """Test the configured search service, and report it where it is set."""
        self.clear_card_message(self.model_error_line)
        if not self.validate_model_search():
            return
        self.model_search_testing = True
        self.change_model_internet()
        state = self.model_search_state()
        self.model_search_status.setText("Testing web search…")

        def success(count):
            self.model_search_testing = False
            self.change_model_internet()
            if self.model_search_state() == state:
                self.model_search_status.setText(
                    f"Web search is reachable · {count} results returned" if count else
                    "Connected to the search service, but no results were returned. "
                    "Try again before relying on web search.")

        def failure(message):
            self.model_search_testing = False
            self.change_model_internet()
            if self.model_search_state() == state:
                self.model_search_status.setText(message)
                self.focus_model_search()

        self.command("test_web_search", self.model_search_url.text().strip(),
                     self.model_search_insecure.isChecked(),
                     self.model_search_provider.currentData(),
                     self.model_search_key.text().strip() or None,
                     self.model_profile_id, success=success, failure=failure)

    def save_model(self):
        """Validate and save the profile, with separate model and search keys."""
        self.clear_card_message(self.model_error_line)
        if not self.validate_model_search():
            return
        profile = {"id": self.model_name.text().strip(),
                   "provider": self.model_provider.currentData(),
                   "model": self.model_id.currentText().strip(),
                   "base_url": self.model_base.text().strip(),
                   "system_prompt": self.model_system.toPlainText().strip(),
                   "autostart": self.model_autostart.isChecked(),
                   "allow_insecure": self.model_insecure.isChecked(),
                   "vision": self.model_vision.isChecked(),
                   "web_search": self.model_search_mode.currentData()
                   if self.model_internet.isChecked() else "off",
                   "search_provider": self.model_search_provider.currentData(),
                   "searxng_url": self.model_search_url.text().strip(),
                   "searxng_allow_insecure": self.model_search_insecure.isChecked()}
        self.model_save_button.setEnabled(False)

        def success(result):
            self.model_key.clear()
            self.model_search_key.clear()
            self.model_profile_id = None
            self.model_save_button.setEnabled(True)
            self.model_form_title.setText("Connect a model")
            self.model_save_button.setText("Connect agent")
            self.clear_model_form()
            self.navigate(AGENTS_PAGE)
            self.notice(f"{profile['id']} is connected.")

        def failure(message):
            self.model_save_button.setEnabled(True)
            self.set_card_message(self.model_error_line, message)
            if self.model_internet.isChecked() and (
                    "search" in message.lower() or "searxng" in message.lower()):
                self.focus_model_search()

        search_key = (self.model_search_key.text().strip() or None) \
            if self.model_search_provider.currentData() == "ollama" else None
        self.command("save_agent", profile, self.model_key.text() or None,
                     search_key, success=success, failure=failure)

    def clear_model_form(self):
        """Back to a blank connect form after a save."""
        self.model_key.clear()
        self.model_search_key.clear()
        self.model_system.clear()
        self.model_search_url.clear()
        self.model_search_status.setText("")
        self.model_name.setReadOnly(False)
        self.model_provider.setCurrentIndex(
            self.model_provider.findData("ollama"))
        self.change_model_provider()
        self.model_autostart.setChecked(True)
        self.model_insecure.setChecked(False)
        self.model_vision.setChecked(False)
        self.model_internet.setChecked(False)
        self.change_model_internet()

    def add_agent(self, provider="ollama"):
        """Open the page's own card, set to this provider.

        Every one of the five entry points lands here now, so connecting a
        model happens in one place. A dialog was a separate window for a form
        that has no reason to be one, and it meant the model you connected
        appeared in a different part of the app from the card that made it.
        """
        if not self.ready:
            self.notice("Your network is still starting. Please wait a moment.")
            return
        if not isinstance(provider, str):
            provider = "ollama"
        self.model_profile_id = None
        self.model_form_title.setText("Connect a model")
        self.model_save_button.setText("Connect agent")
        self.model_provider.setCurrentIndex(self.model_provider.findData(provider))
        self.navigate(AGENTS_PAGE)
        self.model_name.setFocus()

    def edit_agent(self, agent):
        """Load this model into the same card, so there is one form."""
        if not self.ready:
            self.notice("Your network is still starting. Please wait a moment.")
            return
        profile = agent.get("profile") or {}
        self.model_profile_id = profile.get("id") or agent["id"]
        self.model_form_title.setText(f"Edit {self.model_profile_id}")
        self.model_save_button.setText("Save changes")
        self.model_provider.setCurrentIndex(
            self.model_provider.findData(agent.get("provider") or "ollama"))
        self.change_model_provider()
        self.model_name.setText(self.model_profile_id)
        self.model_name.setReadOnly(True)
        self.model_id.setCurrentText(profile.get("model") or "")
        self.model_base.setText(profile.get("base_url") or "")
        self.model_system.setPlainText(profile.get("system_prompt") or "")
        self.model_autostart.setChecked(profile.get("autostart", True))
        self.model_insecure.setChecked(profile.get("allow_insecure", False))
        self.model_vision.setChecked(profile.get("vision", False))
        self.model_search_provider.setCurrentIndex(max(0, self.model_search_provider.findData(
            profile.get("search_provider", "searxng"))))
        self.model_internet.setChecked(profile.get("web_search", "off") != "off")
        self.model_search_mode.setCurrentIndex(max(0, self.model_search_mode.findData(
            profile.get("web_search", "auto"))))
        self.model_search_url.setText(profile.get("searxng_url", ""))
        self.model_search_insecure.setChecked(profile.get("searxng_allow_insecure", False))
        self.change_model_internet()
        self.navigate(AGENTS_PAGE)
        self.model_id.setFocus()

    def name_rule(self):
        """The one wording for the workspace-name limit, used in both places."""
        return "Use a workspace name with 1-80 characters"

    def set_card_message(self, line, message, tone="error"):
        """Put a message on a card's own line, in the window's state.

        The line widget is only the view. Holding the message here is what
        lets it outlive the rebuild of the card it sits on, and keying it by
        the line means no card can overwrite another's message.
        """
        self.card_messages[line.key] = (message, tone)
        line.set_tone(tone)
        line.show_message(message)

    def clear_card_message(self, line):
        """Remove one card's message. Only its own action may do this for it."""
        self.card_messages.pop(line.key, None)
        line.clear()

    def refresh_card_messages(self):
        """Re-apply every stored message to the lines now on screen.

        Called after a page rebuild, so a message survives the render that
        would otherwise destroy the widget holding it. Render methods must
        not write messages themselves: a background poll that clears them is
        exactly how the workspace page's errors used to vanish three seconds
        after they appeared.
        """
        for key, line in self.card_lines.items():
            entry = self.card_messages.get(key)
            if entry is not None:
                message, tone = entry
                line.set_tone(tone)
                line.show_message(message)

    def message_line(self, key):
        """A card's own message line, registered so a rebuild can find it.

        A rebuilt page replaces the line for its key rather than adding a
        second one, so the old widget is released with the old page instead
        of being held for the life of the window.
        """
        line = ErrorLine(self.dismiss_card_message, theme=self.theme)
        line.key = key
        self.card_lines[key] = line
        entry = self.card_messages.get(key)
        if entry is not None:
            message, tone = entry
            line.set_tone(tone)
            line.show_message(message)
        return line

    def dismiss_card_message(self, line):
        self.clear_card_message(line)

    def workspaces_page(self):
        """The workspace you are in, and the two ways of adding another.

        This used to be a pop-up menu off the rail followed by a modal:
        creating was a bare prompt with one field, and joining was a
        dialog that had been built properly but did not belong in a modal
        for a choice this size. Both now sit in the content area beside
        the other pages, and the rail's plus comes straight here.
        """
        page, layout = self.scroll_page()
        layout.addWidget(label("WHERE YOU ARE", "eyebrow"))
        layout.addWidget(label("Workspaces", "title"))
        layout.addWidget(label(
            "A workspace is a private network. This device belongs to one at a "
            "time, and you can move between them or bring in another device.",
            "muted", True))

        current, column = frame("card")
        column.setContentsMargins(22, 20, 22, 20)
        column.addWidget(label("This workspace", "heading"))
        self.workspace_name_label = label(self.workspace_label.text(), "statValue")
        column.addWidget(self.workspace_name_label)
        self.workspace_members_label = label("", "muted", True)
        self.workspace_members_label.setWordWrap(True)
        column.addWidget(self.workspace_members_label)
        column.addSpacing(10)
        self.workspace_invite_button = action("Invite a device", self.invite_device)
        column.addWidget(self.workspace_invite_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(current)

        # The invitation generator, on the page rather than in a dialog.
        # This used to open InviteDialog, which is a separate window for a
        # short form: the fields were somewhere else from the description of
        # what an invitation is for, and the JSON it produces appeared in the
        # dialog while the card it belongs to stayed empty.
        layout.addWidget(label("BRING IN ANOTHER DEVICE", "eyebrow"))
        invite, column = frame("inviteCard")
        column.setContentsMargins(22, 20, 22, 20)
        column.addWidget(label("Invite a device", "heading"))
        column.addWidget(label(
            "Creates an invitation to share with another laptop or desktop. "
            "Each device gets its own identity, and the invitation is private: "
            "send it only to the machine you mean.", "muted", True))
        column.addSpacing(12)
        self.workspace_invite_name = QLineEdit()
        self.workspace_invite_name.setPlaceholderText("Device name · e.g. alex-laptop")
        self.workspace_invite_name.textChanged.connect(
            self.workspace_invite_typed)
        self.workspace_invite_name.returnPressed.connect(self.create_invitation)
        column.addWidget(self.workspace_invite_name)
        self.workspace_invite_lan = QCheckBox("Share this relay on my local network")
        self.workspace_invite_lan.setChecked(True)
        self.workspace_invite_lan.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        column.addWidget(self.workspace_invite_lan, 0, Qt.AlignmentFlag.AlignLeft)
        column.addWidget(label(
            "Host network · choose the Wi-Fi or hotspot the other device is on",
            "muted", True))
        self.workspace_invite_network = Select()
        for name, ip in lan_addresses():
            self.workspace_invite_network.addItem(f"{name} · {ip}", ip)
        self.workspace_invite_network.currentIndexChanged.connect(
            self.workspace_invite_address)
        column.addWidget(self.workspace_invite_network)
        column.addWidget(label("Address the other device can reach", "muted", True))
        self.workspace_invite_url = QLineEdit()
        column.addWidget(self.workspace_invite_url)
        column.addWidget(label(
            "Keep the host app open and allow Multiplayer AI through its "
            "firewall. After changing Wi-Fi or hotspot, create a new "
            "invitation with the current address. Campus and guest Wi-Fi may "
            "block devices from reaching each other even on the same name; for "
            "those, use a reachable WSS relay.", "muted", True))
        self.workspace_invite_error = self.message_line("workspace:invite")
        column.addWidget(self.workspace_invite_error)
        self.workspace_invite_create_button = action(
            "Create invitation", self.create_invitation, True)
        self.workspace_invite_create_button.setEnabled(False)
        column.addWidget(self.workspace_invite_create_button)
        self.workspace_invite_result = QPlainTextEdit()
        self.workspace_invite_result.setReadOnly(True)
        self.workspace_invite_result.setMaximumHeight(180)
        self.workspace_invite_result.hide()
        column.addWidget(self.workspace_invite_result)
        self.workspace_invite_copy_button = action(
            "Copy private invitation", self.copy_invitation)
        self.workspace_invite_copy_button.hide()
        column.addWidget(self.workspace_invite_copy_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(invite)
        self.workspace_invite_address()

        # One heading per section with the name of the job in it, because two
        # cards under a shared "Add another" heading read as one form split
        # in half: both had a text box, a line of help text and a button, and
        # nothing said that create makes a network here while join connects
        # to one somewhere else.
        layout.addWidget(label("MAKE A NEW ONE HERE", "eyebrow"))
        # Stacked rather than side by side. Two cards with fields in them
        # need about 950px together, and the window may be as narrow as
        # 720px, so a row either clipped the second card or gave the page
        # a horizontal scrollbar. One above the other fits every width, and
        # the page already scrolls. Each has its own accent edge so they
        # cannot be read as halves of a single form.
        create, column = frame("createCard")
        column.setContentsMargins(22, 20, 22, 20)
        column.addWidget(label("Create a workspace", "heading"))
        column.addWidget(label(
            "Starts a new private network on this device. Nothing to connect "
            "to, and no invitation needed — you can bring in other devices "
            "once it exists.", "muted", True))
        column.addSpacing(12)
        self.workspace_name_input = QLineEdit()
        self.workspace_name_input.setPlaceholderText("Workspace name")
        self.workspace_name_input.textChanged.connect(self.workspace_name_typed)
        self.workspace_name_input.returnPressed.connect(self.create_workspace)
        column.addWidget(self.workspace_name_input)
        # The rail shows a workspace as its first two letters, so the name
        # is previewed as it is typed rather than after the fact.
        preview_row = QHBoxLayout()
        preview_row.setSpacing(10)
        self.workspace_preview = WorkspaceButton("")
        self.workspace_preview.setEnabled(False)
        preview_row.addWidget(self.workspace_preview)
        self.workspace_preview_label = label("", "muted", True)
        preview_row.addWidget(self.workspace_preview_label)
        preview_row.addStretch()
        column.addLayout(preview_row)
        # Two separate lines rather than one. The name rule is a live hint
        # under the field it constrains and changes as you type; the create
        # outcome is what the runtime said about the attempt. They were one
        # line, so typing over a failure replaced it with the name rule, and
        # a failure arrived where a hint was expected.
        self.workspace_name_line = self.message_line("workspace:name")
        column.addWidget(self.workspace_name_line)
        column.addSpacing(8)
        self.workspace_create_line = self.message_line("workspace:create")
        column.addWidget(self.workspace_create_line)
        self.workspace_create_button = action(
            "Create workspace", self.create_workspace, True)
        self.workspace_create_button.setEnabled(False)
        column.addWidget(self.workspace_create_button)
        layout.addWidget(create)

        layout.addWidget(label("OR CONNECT TO ONE SOMEWHERE ELSE", "eyebrow"))
        join, column = frame("joinCard")
        column.setContentsMargins(22, 20, 22, 20)
        column.addWidget(label("Join a workspace", "heading"))
        column.addWidget(label(
            "Connects this device to a network hosted on another machine. "
            "Someone has to invite this device first — paste their invitation, "
            "or enter their relay address and your device token.", "muted", True))
        column.addSpacing(12)
        self.workspace_invitation = QPlainTextEdit()
        self.workspace_invitation.setPlaceholderText("Paste an invitation")
        self.workspace_invitation.setMaximumHeight(96)
        column.addWidget(self.workspace_invitation)
        self.workspace_relay = QLineEdit()
        self.workspace_relay.setPlaceholderText("wss://your-relay.example.com/connect")
        column.addWidget(self.workspace_relay)
        self.workspace_token = QLineEdit()
        self.workspace_token.setPlaceholderText("Your device's relay token")
        self.workspace_token.setEchoMode(QLineEdit.EchoMode.Password)
        column.addWidget(self.workspace_token)
        self.workspace_lan = QCheckBox("Trusted LAN, so ws:// is allowed")
        # A checkbox's minimum is the width of its whole label and it has no
        # word wrap to fall back on, so a long one sets the width of its
        # card and of the page, and the page gains a horizontal scrollbar.
        # The label is kept short for the reader's sake and the horizontal
        # policy is Ignored so that a longer one in future cannot do it
        # again; the checkbox then takes the width it is given and draws
        # from the left like every other control here.
        self.workspace_lan.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        column.addWidget(self.workspace_lan, 0, Qt.AlignmentFlag.AlignLeft)
        column.addSpacing(8)
        self.workspace_join_line = self.message_line("workspace:join")
        column.addWidget(self.workspace_join_line)
        self.workspace_join_button = action("Join workspace", self.join_workspace, True)
        column.addWidget(self.workspace_join_button)
        layout.addWidget(join)
        layout.addStretch()
        self.render_workspace_page()
        self.refresh_card_messages()
        return page

    def render_workspace_page(self):
        """Refresh the parts of the page that follow the runtime.

        Called on every agents event, because joining or creating changes
        what the workspace is, and the page has to stop claiming to be
        creating something you already created.

        Deliberately writes no messages. This runs on the three-second
        agents poll, and it used to end in workspace_name_typed(), which
        owned the card's single shared line, so any error on this page was
        erased within three seconds of appearing. State refresh belongs
        here; messages belong to the card's own action.
        """
        if not hasattr(self, "workspace_name_label"):
            return
        self.workspace_name_label.setText(self.workspace_label.text())
        members = self.workspace_meta.get("members", [])
        people = [m for m in members if m.get("role") != "Model"]
        models = [m for m in members if m.get("role") == "Model"]
        summary = f"{len(people)} device{'s' if len(people) != 1 else ''} here"
        if models:
            summary += f", {len(models)} model agent{'s' if len(models) != 1 else ''}"
        if self.remote:
            summary += " · joined from another device"
        self.workspace_members_label.setText(summary)
        self.workspace_invite_button.setEnabled(self.ready and not self.remote)
        self.workspace_invite_create_button.setEnabled(
            self.ready and bool(self.workspace_invite_name.text().strip()))
        self.workspace_invite_error.setVisible(bool(
            self.workspace_invite_error.message.text()))
        self.workspace_name_input.setEnabled(self.ready)
        self.workspace_create_button.setEnabled(
            self.ready and bool(self.workspace_name_input.text().strip()))
        self.workspace_join_button.setEnabled(self.ready)
        self.workspace_preview.setEnabled(False)
        # The button and preview follow the runtime here, but the name hint
        # belongs to typing, so only that one line is re-derived.
        name = self.workspace_name_input.text().strip()
        self.workspace_preview.setText(name[:2].upper())
        self.workspace_preview_label.setText(
            f"appears in the rail as {name[:2].upper()!r}" if name else "")
        self.workspace_create_button.setEnabled(self.ready and 1 <= len(name) <= 80)

    def workspace_name_typed(self, text):
        """Keep the create button honest, preview the rail button, hint the rule.

        The name is checked here rather than on submit, so the constraint is
        visible before anything is attempted, using the same rule and the
        same wording the runtime applies, so the two cannot disagree.

        Writes only the name hint, never the create outcome. It used to own
        the card's single shared line, so a poll reaching here three seconds
        after a failure erased it, and typing replaced a real failure with
        the name rule.
        """
        name = text.strip()
        allowed = 1 <= len(name) <= 80
        self.workspace_preview.setText(name[:2].upper())
        self.workspace_preview_label.setText(
            f"appears in the rail as {name[:2].upper()!r}" if name else "")
        if allowed:
            self.clear_card_message(self.workspace_name_line)
        else:
            self.set_card_message(self.workspace_name_line, self.name_rule())
        self.workspace_create_button.setEnabled(bool(self.ready and allowed))

    def create_workspace(self):
        """Create from the page, and report the outcome where it happened.

        The command is asynchronous, so the failure arrives after this
        returns; routing it to the page's own line keeps the message on
        screen beside the field that caused it, rather than in a toast that
        has gone by the time it is read.
        """
        name = self.workspace_name_input.text().strip()
        if not 1 <= len(name) <= 80:
            self.set_card_message(self.workspace_name_line, self.name_rule())
            return
        self.clear_card_message(self.workspace_name_line)
        # Retrying is the one thing that clears the previous outcome, so the
        # message never lingers over a button the reader has just pressed.
        self.clear_card_message(self.workspace_create_line)
        self.workspace_create_button.setEnabled(False)
        self.command("create_workspace", name,
                     success=self.workspace_created,
                     failure=self.workspace_failed)

    def workspace_created(self, entry):
        self.workspace_name_input.clear()
        self.clear_card_message(self.workspace_create_line)
        self.notice(f"Now in {entry.get('name', 'the new workspace')}.")

    def workspace_failed(self, message):
        self.set_card_message(self.workspace_create_line, message)
        self.workspace_create_button.setEnabled(
            bool(self.ready and self.workspace_name_input.text().strip()))

    def join_workspace(self):
        """Join from the page, with the whole connection done here.

        This used to open the old modal, which made the page's own fields
        decoration: paste an invitation, press the button, and the page was
        thrown away in favour of being asked for the same information again.
        The rules about what counts as an invitation are not restated here;
        they live in ``parse_invitation`` so this route and the dialog's
        cannot disagree.

        The command is asynchronous, so a failure arrives after this returns
        and is reported on the page's own line, beside the fields that
        caused it, rather than in a toast that has gone by the time it is
        read.
        """
        if not self.ready:
            self.set_card_message(self.workspace_join_line,
                                  "Your network is still starting.")
            return
        self.clear_card_message(self.workspace_join_line)
        try:
            conversation_id = None
            invitation = self.workspace_invitation.toPlainText().strip()
            if invitation:
                url, token, allow_insecure, conversation_id = parse_invitation(invitation)
                self.workspace_relay.setText(url)
                self.workspace_token.setText(token)
                self.workspace_lan.setChecked(allow_insecure)
            if len(self.workspace_token.text().strip()) < 32:
                raise ValueError("Enter the device token from your invitation")
            self.workspace_join_button.setEnabled(False)
            self.workspace_join_button.setText("Connecting…")
            # Progress belongs to this card and stays until the attempt
            # resolves, so a slow relay says why it is slow rather than
            # looking hung.
            self.set_card_message(
                self.workspace_join_line,
                "Contacting the relay. Keep the host app open; this can take up "
                "to 30 seconds.", "progress")

            def failure(message):
                self.workspace_join_button.setEnabled(True)
                self.workspace_join_button.setText("Join workspace")
                self.set_card_message(
                    self.workspace_join_line,
                    message or "The connection failed. Check the host address, network, "
                               "and firewall.")

            def success(result):
                self.workspace_token.clear()
                self.workspace_invitation.clear()
                self.clear_card_message(self.workspace_join_line)
                self.render_workspace_page()
                if conversation_id:
                    self.select_agent(conversation_id)
                    return
                peer = next((agent for agent in self.agents
                             if agent["online"] and agent["id"] != self.identity), None)
                if peer:
                    self.select_agent(peer["id"])
                else:
                    self.navigate(1)
                    self.notice("Workspace joined. No other devices are online yet. "
                                "Keep the host app open and connect an agent to begin.")

            self.command("join", self.workspace_relay.text().strip(),
                         self.workspace_token.text().strip(),
                         self.workspace_lan.isChecked(), True, conversation_id,
                         success=success, failure=failure)
        except (ValueError, KeyError, TypeError) as exc:
            self.workspace_join_button.setEnabled(True)
            self.workspace_join_button.setText("Join workspace")
            self.set_card_message(self.workspace_join_line, str(exc))

    def workspace_invite_typed(self, text):
        """Keep the button honest, and say what the network address will be.

        The address is shown as soon as the card exists rather than after the
        invitation is made, because the wrong Wi-Fi is the usual reason a
        device cannot reach this one.
        """
        self.workspace_invite_create_button.setEnabled(
            bool(self.ready and text.strip()))
        self.workspace_invite_address()

    def workspace_invite_address(self):
        """Point the URL at whichever network is selected, while LAN is on."""
        if not self.workspace_invite_lan.isChecked():
            return
        address = self.workspace_invite_network.currentData() or "YOUR_LAN_IP"
        self.workspace_invite_url.setText(f"ws://{address}:{self.port}/connect")

    def create_invitation(self):
        """Make the invitation from the page, and show it here.

        The command is asynchronous, so the result arrives after this returns;
        it is put back on the card that asked for it rather than in a dialog
        that has already closed.
        """
        if not self.ready:
            self.set_card_message(self.workspace_invite_error,
                                  "Your network is still starting.")
            return
        self.clear_card_message(self.workspace_invite_error)
        self.workspace_invite_create_button.setEnabled(False)
        self.workspace_invite_create_button.setText("Creating…")

        def failure(message):
            self.workspace_invite_create_button.setEnabled(True)
            self.workspace_invite_create_button.setText("Create invitation")
            self.set_card_message(self.workspace_invite_error, message)

        def success(data):
            self.workspace_invite_create_button.setText("Create invitation")
            self.workspace_invite_create_button.setEnabled(
                bool(self.ready and self.workspace_invite_name.text().strip()))
            self.workspace_invite_name.clear()
            self.workspace_invite_result.setPlainText(json.dumps(data, indent=2))
            self.workspace_invite_result.show()
            self.workspace_invite_copy_button.show()

        self.command("invite", self.workspace_invite_name.text().strip(),
                     self.workspace_invite_url.text().strip(),
                     self.workspace_invite_lan.isChecked(),
                     None, None, None, success=success, failure=failure)

    def copy_invitation(self):
        QApplication.clipboard().setText(self.workspace_invite_result.toPlainText())
        self.notice("Private invitation copied. Send it only to the intended device.")

    def add_workspace(self):
        """The rail's plus goes to the page, where both routes are offered."""
        self.navigate(WORKSPACES_PAGE)

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
