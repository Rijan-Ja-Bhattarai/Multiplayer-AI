"""The Resources and Settings pages, against a real headless window.

Runs through the offscreen Qt platform plugin so no display is needed.
The window is given a MemoryVault rather than a real one: DesktopRuntime
calls Storage.save_credentials while starting up, so a real Vault would
leave a device token in the developer's OS credential store.
"""

from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 is needed for the desktop page tests")
pytest.importorskip("psutil", reason="psutil backs the Resources page")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import (QApplication, QFrame, QLabel, QMessageBox,
                             QPushButton)  # noqa: E402

from desktop_app import window as win  # noqa: E402
from desktop_app.storage import ABSENT, REMOVED, Storage  # noqa: E402
from desktop_app.theme import (DARK, LIGHT, MIKU, THEME_CHOICES, THEME_NAMES,
                               THEMES, color)  # noqa: E402


class MemoryVault:
    """Stands in for the OS credential store."""

    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        return REMOVED if self.values.pop(name, None) is not None else ABSENT



@pytest.fixture(scope="session")
def qt_app():
    """One QApplication for the session; Qt allows only one."""
    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path, MemoryVault())


@pytest.fixture
def window(qt_app, storage):
    """A real MainWindow, torn down after the test."""
    instance = win.MainWindow(storage)
    yield instance
    instance.network.shutdown()
    instance.network.wait(10000)


# --- first run -----------------------------------------------------------


def test_every_nav_button_explains_its_page(window) -> None:
    """A page whose name does not describe it needs saying out loud."""
    for button in window.nav_buttons:
        assert button.toolTip(), f"a nav button with no tooltip: {button.text()!r}"
    assert set(win.PAGE_HELP) == {key for _, key in win.PAGES}, (
        "every page needs an entry, or a tooltip is about to be missing")


def test_the_shell_controls_explain_themselves(window) -> None:
    """The top bar was the one part of the shell a hover could not teach you.

    Tooltips already covered the navigation buttons and the workspace
    settings, but not the two buttons a new user is most likely to reach
    for, nor the chat page's own controls. Each of these says what will
    actually happen rather than repeating the label it sits beside.
    """
    chrome = {
        "invite": window.invite_button,
        "connect a model": window.add_button,
        "welcome connect": window.welcome_connect,
        "welcome join": window.welcome_join,
        "share": window.share_conversation_button,
        "agent count": window.chat_agent_count,
        "attach": window.attach_button,
        "send": window.send_button,
    }
    for name, button in chrome.items():
        assert button.toolTip(), f"the {name} button has no tooltip"
        assert button.toolTip() != button.text().strip(), (
            f"the {name} tooltip only repeats its own label")


def test_the_rows_built_on_demand_explain_themselves(window) -> None:
    """Agent cards and attachments appear only once there is something to show.

    Both are built after the window opens, so they cannot be covered by a
    check of the fixed chrome. A row of identical "Remove" buttons is
    ambiguous without the filename, and an agent card cannot say who it
    opens without naming them.
    """
    window.agents = [{
        "id": "local-llama", "online": True, "provider": "ollama",
        "model": "llama3", "profile": {"vision": True},
    }]
    window.selected = "local-llama"
    window.chats = {"local-llama": {"draft_attachments": [
        {"name": "diagram.png", "note": "120 KB"}]}}
    window.render_agents()
    window.render_attachments()

    talk = [b for b in window.findChildren(QPushButton)
            if b.text().startswith("Open conversation")]
    assert talk, "the agent card did not render its conversation button"
    assert all(b.toolTip() for b in talk), "an agent card cannot say who it opens"
    assert all("local-llama" in b.toolTip() for b in talk), (
        "naming the agent is the point of the tooltip")

    remove = [b for b in window.findChildren(QPushButton) if b.text() == "Remove"]
    assert remove, "the attachment did not render its remove button"
    assert all("diagram.png" in b.toolTip() for b in remove), (
        "a column of identical Remove buttons is ambiguous")


def test_the_sidebar_says_when_there_is_nothing_to_chat_to(window) -> None:
    """An empty list read as a hole rather than as an absence of anything."""
    window.agents = []
    window.conversations = {}
    window.render_agents()

    assert not window.chats_hint.isHidden()
    assert "Nothing" in window.chats_hint.text()


def test_the_sidebar_hint_goes_once_there_is_something(window) -> None:
    window.agents = []
    window.render_agents()
    assert not window.chats_hint.isHidden()

    window.agents = [{"id": "model", "online": True, "provider": "ollama", "model": "m"}]
    window.render_agents()

    assert window.chats_hint.isHidden()


def test_the_sidebar_hint_ignores_this_device(window) -> None:
    """The hint used to key off the row count, and this device is always a row.

    On a first run the sidebar holds one entry, this device, which is
    nothing a new user can talk to. Counting rows therefore hid the hint
    precisely when it was needed. It now agrees with the welcome panel.
    """
    window.agents = [{"id": "device-abc123", "kind": "device", "online": True}]
    window.conversations = {}
    window.render_agents()

    assert window.sidebar_agents.count() == 1, "this test needs the device listed"
    assert not window.chats_hint.isHidden(), (
        "the hint stayed hidden because this device is always a row in the list")


def test_the_welcome_appears_although_this_device_is_listed(window) -> None:
    """A first run always lists this device in the sidebar, and so it always did.

    The welcome used to test the agent list for emptiness, which can never
    hold because this device is one of its own entries. So the panel a new
    user is meant to land on was hidden on a genuine first run and stayed
    hidden afterwards. Only the model agents a user can actually talk to
    should count.
    """
    window.agents = [{"id": "device-abc123", "kind": "device", "online": True}]
    window.conversations = {}
    window.render_agents()

    assert not window.model_agents(), "a device is not a model agent"
    assert not window.welcome_card.isHidden(), (
        "the welcome stayed hidden on a first run, because this device is "
        "always in the agent list it was testing for emptiness")


def test_the_statistics_return_once_a_model_is_connected(window) -> None:
    """The welcome has to get out of the way once there is something to count."""
    window.agents = [
        {"id": "device-abc123", "kind": "device", "online": True},
        {"id": "local-llama", "kind": "model", "online": True,
         "provider": "ollama", "model": "llama3"},
    ]
    window.conversations = {}
    window.render_agents()

    assert window.welcome_card.isHidden(), "the welcome stayed up over real statistics"
    assert not window.overview_content.isHidden(), "the statistics were never restored"


def test_a_saved_conversation_also_puts_the_welcome_away(window) -> None:
    """Somebody with history to come back to is not a first run."""
    window.agents = [{"id": "device-abc123", "kind": "device", "online": True}]
    window.conversations = {"local-llama": {
        "id": "local-llama", "title": "Notes on the parser",
        "target": "local-llama", "members": ["device-abc123"]}}
    window.render_agents()

    assert window.welcome_card.isHidden(), "the welcome covered a real conversation"


# --- the welcome illustration -------------------------------------------


def test_the_welcome_actually_carries_the_orbit_illustration(window, qt_app) -> None:
    """The orbit was painted for this panel and never mounted.

    It has its own unit test, which passed throughout while the widget sat
    in the module unused, so nothing about it ever reached the screen. The
    assertion has to be against the built window, not against the widget.
    """
    window.resize(1180, 760)
    window.show()
    qt_app.processEvents()
    art = window.welcome_art

    assert art in window.welcome_card.findChildren(win.OrbitArt), (
        "the orbit illustration is not in the welcome panel, so the first "
        "thing a new user sees has no motion in it")
    assert not art.isHidden(), "the orbit illustration is on a hidden page"
    assert art.width() >= 200 and art.height() >= 180, (
        f"the orbit was given {art.width()}x{art.height()}, too small to read")
    assert art.width() <= window.welcome_card.width(), (
        "the orbit is wider than the card holding it")


def test_the_orbit_repaints_for_the_new_theme(window, qt_app) -> None:
    """It paints from the palette rather than being styled, so it has to be
    told when the theme changes or it keeps the colours it was built with.

    Asserted on the tile in the middle rather than by comparing whole
    images: the chips orbit, so two grabs taken a moment apart always
    differ and would make this pass whatever the theme did.
    """
    art = window.welcome_art
    art.resize(260, 240)
    art.set_moving(False)

    def tile() -> str:
        image = art.grab().toImage()
        return image.pixelColor(image.width() // 2, image.height() // 2).name()

    window.apply_theme(DARK)
    qt_app.processEvents()
    assert tile() == color(DARK, "accent"), "the orbit is not painted in the theme's accent"

    window.apply_theme(LIGHT)
    qt_app.processEvents()
    assert tile() == color(LIGHT, "accent"), (
        "the orbit kept the previous theme's colours")


def test_the_orbit_holds_still_when_motion_is_reduced(window) -> None:
    """The setting already covered the page fade; it has to reach this too."""
    window.motion_changed(True)
    assert not window.welcome_art.moving, (
        "the orbit keeps turning with reduce-motion on")
    window.motion_changed(False)
    assert window.welcome_art.moving, (
        "turning reduce-motion off did not restart the orbit")


def test_the_orbit_respects_the_stored_motion_setting(qt_app, storage) -> None:
    """The setting is read when the panel is built, not only when toggled.

    Somebody who turned animations off in a previous session should not
    get a moving illustration for the length of this one.
    """
    storage.settings["reduce_motion"] = True
    from desktop_app.window import MainWindow

    instance = MainWindow(storage)
    try:
        assert not instance.welcome_art.moving, (
            "the orbit started moving despite the stored reduce-motion setting")
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


# --- the conversation ----------------------------------------------------


def conversation_window(window):
    """A chat with two speakers, so grouping has something to group."""
    window.navigate(2)
    window.agents = [{"id": "local-llama", "kind": "model", "online": True,
                      "provider": "ollama", "model": "llama3"}]
    window.selected = "local-llama"
    window.chats = {"local-llama": {"messages": [
        ("user", "Explain this traceback"),
        ("user", "It only happens on the second run"),
        ("assistant", "Two runs means the port is still bound"),
        ("assistant", "Close it before starting again"),
    ]}}
    window.render_messages()
    return window


def row_lead(row):
    """The widget holding a row's leading slot: an avatar, or the indent."""
    return row.content.itemAt(0).widget()


def row_has_mark(row):
    """A mark is a label carrying a painted pixmap; the indent is a bare widget."""
    lead = row_lead(row)
    return isinstance(lead, QLabel) and not lead.pixmap().isNull()


def row_labels(row):
    return [child.text() for child in row.findChildren(QLabel) if child.text()]


def test_messages_are_rows_and_not_cards(window) -> None:
    """Each message used to be a full-width card, which reads as a stack of
    documents rather than as a conversation."""
    conversation_window(window)

    rows = window.messages_widget.findChildren(win.HoverRow)
    assert len(rows) == 4, f"expected one row per message, got {len(rows)}"
    assert not [c for c in window.messages_widget.findChildren(QFrame)
                if c.objectName() == "card"], (
        "a message is still drawn as a card")


def test_consecutive_messages_from_one_speaker_are_grouped(window) -> None:
    """Discord repeats the name and mark once per speaker, not once per message."""
    conversation_window(window)
    rows = window.messages_widget.findChildren(win.HoverRow)

    assert row_has_mark(rows[0]), (
        "the first message from a speaker should carry their mark")
    assert not row_has_mark(rows[1]), (
        "the second message from the same speaker should have no mark")
    assert "You" in row_labels(rows[0]), "the opening message should name its speaker"
    assert "You" not in row_labels(rows[1]), (
        "a follow-on message repeats the name above it")

    assert "local-llama" in row_labels(rows[2]), (
        "a new speaker should be named")
    assert "local-llama" not in row_labels(rows[3]), (
        "a follow-on message repeats the name above it")


def test_a_grouped_message_lines_up_under_the_one_above(window, qt_app) -> None:
    """Without the indent the text slides left under the absent mark."""
    conversation_window(window)
    window.show()
    qt_app.processEvents()
    rows = window.messages_widget.findChildren(win.HoverRow)

    def text_indent(row):
        # Any label with text is body copy; the speaker's name is a label
        # too, but it only appears on an ungrouped row and sits at the same
        # indent, so it does not disturb the comparison.
        bodies = [child for child in row.findChildren(QLabel) if child.text()]
        return min((b.mapTo(row, b.rect().topLeft()).x() for b in bodies),
                   default=None)

    assert text_indent(rows[0]) == text_indent(rows[1]), (
        f"a grouped message sits at {text_indent(rows[1])} while the one above "
        f"it sits at {text_indent(rows[0])}")


def test_the_action_slot_keeps_its_place_when_revealed(window, qt_app) -> None:
    """The actions stay in the layout whether or not they are shown.

    If they were added on hover instead, every row would jump sideways as
    the pointer crossed it. The invariant is checked on the layout rather
    than only on the resulting width, because a row is sized by the panel
    around it and a reflow can leave the width unchanged by luck.
    """
    conversation_window(window)
    window.show()
    qt_app.processEvents()
    rows = window.messages_widget.findChildren(win.HoverRow)

    def action_slot(row):
        return [row.outer.itemAt(index).widget()
                for index in range(row.outer.count())]

    assert rows and not any(r.actions_revealed for r in rows), (
        "the actions start visible, so there is nothing to reveal")
    for row in rows:
        assert any(w is not None and w.objectName() == "messageActions"
                   for w in action_slot(row)), (
            "the actions are not in the layout while hidden, so revealing "
            "them would reflow the row")

    before = [r.geometry().width() for r in rows]
    for row in rows:
        row.reveal_actions(True)
    qt_app.processEvents()

    assert all(r.actions_revealed for r in rows), "revealing did not take"
    assert [r.geometry().width() for r in rows] == before, (
        "revealing the actions changed the row width, so the row will shift "
        "as the pointer crosses it")

    for row in rows:
        row.reveal_actions(False)
    assert not any(r.actions_revealed for r in rows), "hiding did not take"


def test_the_composer_is_set_apart_from_the_messages(window, qt_app) -> None:
    """It already sat outside the scrolling area, so it did not move.

    What it lacked was any sign that it was a separate surface.
    """
    conversation_window(window)
    window.show()
    qt_app.processEvents()

    assert window.composer_bar.geometry().y() >= window.messages_scroll.geometry().y(), (
        "the composer overlaps the message area")
    assert window.composer_bar.objectName() == "composerBar"


@pytest.mark.parametrize("name", THEME_NAMES)
def test_the_composer_and_the_rows_are_divided_in_every_theme(name) -> None:
    for frame_name in ("composerBar", "messageRow", "messageActions"):
        rule = next((line for line in THEMES[name].splitlines()
                     if f"#{frame_name}" in line), "")
        assert rule, f"{name} has no rule for {frame_name}"
        assert "transparent" in rule or "border-top" in rule, (
            f"{name} gives {frame_name} no separation from the surface behind it")



# --- the user panel ------------------------------------------------------


def test_the_user_panel_reaches_the_edges_of_the_sidebar(window, qt_app) -> None:
    """It used to be a child of a layout carrying 14px side margins.

    So its background stopped that far short on both sides and read as a
    panel clipped by the window rather than as a footer of the sidebar. It
    now hangs off the sidebar frame itself, the width is the sidebar's
    width, and the inset is inside it where the text needs it.
    """
    window.resize(1180, 760)
    window.show()
    qt_app.processEvents()
    footer = window.avatar.parentWidget()

    assert footer.parentWidget() is window.sidebar, (
        "the panel is inside the padded body, so it inherits its margins "
        "and cannot reach the edge")
    assert footer.width() == window.sidebar.width(), (
        f"the panel is {footer.width()}px against a {window.sidebar.width()}px sidebar")
    left, right = footer.layout().contentsMargins().left(), footer.layout().contentsMargins().right()
    assert left and right, "the panel's text has no inset, so it will touch the window edge"


def test_the_avatar_is_a_painted_mark_and_not_a_letter(window) -> None:
    """It used to be the text " M " on a fixed width with no fixed height.

    With a 14px radius and a height the layout chose, that drew a rounded
    rectangle of arbitrary height rather than the circle it was aiming at.
    """
    assert not window.avatar.text(), (
        f"the avatar is still a text label, {window.avatar.text()!r}")
    assert not window.avatar.pixmap().isNull(), "the avatar carries no painted mark"
    assert window.avatar.width() == window.avatar.height(), (
        f"the avatar is {window.avatar.width()}x{window.avatar.height()}, not square")


def test_the_avatar_follows_the_identity_and_the_theme(window) -> None:
    """The identity arrives after the window is built, and the theme changes later."""
    window.identity = "device-77ac8845"
    window.refresh_avatar()
    before = window.avatar.pixmap().toImage()

    window.apply_theme(LIGHT)
    light = window.avatar.pixmap().toImage()
    assert light != before, "the avatar ignored the theme change"

    window.apply_theme(MIKU)
    miku = window.avatar.pixmap().toImage()
    assert miku != light, "the avatar kept the previous theme's accent"


@pytest.mark.parametrize("identity,expected", [
    ("device-77ac8845", "D"),
    ("relay", "R"),
    # The identity is only known once the runtime has started, and it can
    # arrive with whitespace in it. Neither may draw an empty tile, which
    # reads as a missing image rather than as a device not yet named.
    ("", "?"),
    ("   ", "?"),
    (None, "?"),
])
def test_the_avatar_initial_is_never_blank(identity, expected) -> None:
    """A fixed mark cannot tell two devices apart, so the initial comes
    from the identity. Asserted on the letter rather than on pixels: this
    platform installs no fonts, so Qt renders no text at all and two
    different initials paint identically."""
    assert win.device_initial(identity) == expected


@pytest.mark.parametrize("name", THEME_NAMES)
def test_the_user_panel_is_divided_by_a_border_in_every_theme(name) -> None:
    """All three themes put the panel on the sidebar's own colour.

    That is what makes it read as one surface, so the border is the only
    thing dividing the footer from the list above it and it has to exist
    wherever the colours match.
    """
    rule = next((line for line in THEMES[name].splitlines()
                 if line.startswith("QFrame#profile")), "")
    assert rule, f"{name} has no rule for the user panel at all"
    assert "border-top" in rule, (
        f"{name} puts the panel on the sidebar colour with nothing dividing it")

    body = next((line for line in THEMES[name].splitlines()
                 if "sidebarBody" in line), "")
    assert "transparent" in body, (
        f"{name} paints the padded body as a panel, so the footer is "
        f"floating on a second surface")



def test_the_device_identity_is_shown_on_the_profile_card(window, tmp_path) -> None:
    """It used to sit on its own line beside a card already saying 'This device'.

    Someone reading their identity out to join a workspace should find it in
    the place that names them, not on a line of its own above it. The
    history directory is a real path because the handler opens it, and a
    relative one lands in the repository.
    """
    window.network_event("workspace", {
        "id": "local", "name": "My workspace", "remote": False,
        "self": "device-abc123", "port": 5000,
        "history_directory": str(tmp_path / "history")})

    assert window.profile_identity.text() == "device-abc123"
    assert window.profile_identity.toolTip(), "it has to be explainable on hover"


def test_working_is_shown_while_a_command_is_in_flight(window) -> None:
    """Every command goes through one place, so all of them get feedback."""
    window.ready = True
    window.network.submit = lambda *args, **kwargs: "request-1"

    window.command("join_workspace")

    assert window.busy, "submitting work must show that the app is waiting"
    assert not window.progress.isHidden()

    window.command_success("request-1", None)

    assert not window.busy
    assert window.progress.isHidden()


def test_two_overlapping_commands_do_not_clear_the_indicator_early(window) -> None:
    """The first to finish must not report done while the second is running."""
    window.ready = True
    submitted = iter(["request-1", "request-2"])
    window.network.submit = lambda *args, **kwargs: next(submitted)

    window.command("join_workspace")
    window.command("create_workspace")
    window.command_success("request-1", None)

    assert window.busy, "one command is still in flight"

    window.command_failure("request-2", "nope")

    assert not window.busy


def test_a_command_refused_before_submitting_leaves_no_busy_state(window) -> None:
    """Refused while starting up is a notice, not something to wait for."""
    window.ready = False
    window.network.submit = lambda *args, **kwargs: "should-not-happen"
    # The bar is up because the app is still starting; a refused command
    # must leave that alone rather than clearing it.
    startup_bar = not window.progress.isHidden()

    window.command("join_workspace")

    assert not window.busy, "nothing was submitted, so nothing is in flight"
    assert not window.progress.isHidden() == startup_bar, (
        "the startup indicator must not be disturbed by a refused command")


def test_an_empty_workspace_shows_a_welcome_instead_of_three_zeroes(window) -> None:
    """A first-run user should be told what to do, not shown three zeroes.

    Three statistic cards all reading zero, and a large empty activity
    list, say nothing about what the app is or what to press. The welcome
    replaces all of it while the workspace holds nothing.
    """
    window.navigate(win.OVERVIEW_PAGE if hasattr(win, "OVERVIEW_PAGE") else 0)
    window.render_agents()

    assert not window.welcome_card.isHidden(), "an empty workspace needs the welcome"
    assert window.overview_content.isHidden(), "zeroes are not worth showing at zero"
    assert window.welcome_connect.text().strip().endswith("Connect a model")
    assert window.welcome_join.text().strip().endswith("Join a workspace")


def texts(layout):
    """Every label's text in a layout, flattened."""
    found = []
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is not None and hasattr(item.widget(), "text"):
            found.append(item.widget().text())
        elif item.layout() is not None:
            found.extend(texts(item.layout()))
    return found


def test_the_chat_does_not_claim_a_collaboration_that_has_not_started(window) -> None:
    """It said 'the beginning of your collaboration' with nothing chosen.

    The title directly above read 'Choose an agent', so the two told a
    first-run user opposite things about whether they had started.
    """
    window.selected = ""
    window.chats = {}
    window.render_messages()

    said = " ".join(texts(window.messages))
    assert "No conversation open" in said
    assert "beginning of your collaboration" not in said

    window.selected = "agent-1"
    window.render_messages()

    assert "beginning of your collaboration" in " ".join(texts(window.messages)), (
        "an opened conversation with no messages yet is still a beginning")


def test_the_chat_hides_its_own_agent_list_while_it_is_empty(window) -> None:
    """Two empty 200px columns either side of an empty conversation.

    The sidebar already carries the same list, so the copy on the chat page
    was width nobody could use until something was connected.
    """
    window.agents = []
    window.conversations = {}
    # Wide enough that the narrow-window rule is not what is hiding it, so
    # this tests the empty-list rule rather than the collapse rule.
    window.resize(1400, 900)
    window.render_agents()
    window.apply_sidebar_density()

    assert window.chat_agents.isHidden()

    window.agents = [{"id": "model", "online": True, "provider": "ollama", "model": "m"}]
    window.render_agents()
    window.apply_sidebar_density()

    assert not window.chat_agents.isHidden(), "a populated list has to be reachable"


def test_the_welcome_and_the_statistics_are_never_shown_together(window) -> None:
    """They are alternatives, so neither may be left showing by a stale event."""
    for agents in ([], [{"id": "model", "online": True, "provider": "ollama", "model": "m"}]):
        window.agents = agents
        window.render_agents()

        assert bool(window.welcome_card.isHidden()) != bool(window.overview_content.isHidden()), (
            f"exactly one of welcome and statistics must show with agents={agents}")


def test_the_welcome_hides_once_a_model_is_connected(window) -> None:
    window.agents = []
    window.render_agents()
    assert not window.welcome_card.isHidden()

    window.agents = [{"id": "model", "online": True, "provider": "ollama", "model": "m"}]
    window.render_agents()

    assert window.welcome_card.isHidden()
    assert not window.overview_content.isHidden()
    assert window.stat_values[0].text() == "1", "the statistics still have to be right"


# --- navigation ----------------------------------------------------------


def test_pages_and_titles_stay_in_step() -> None:
    """The nav, the stacked pages and the titles must agree in length.

    These were three separate lists before, so adding a page meant fixing
    the title tuple as well and forgetting to do so was an IndexError at
    startup.
    """
    assert len(win.PAGES) == len(win.PAGE_TITLES)
    assert 0 <= win.RESOURCES_PAGE < len(win.PAGES)
    assert 0 <= win.SETTINGS_PAGE < len(win.PAGES)


def test_window_builds_every_page(window) -> None:
    """All six pages are constructed, including the two new ones."""
    assert len(window.nav_buttons) == len(win.PAGES)
    assert window.stack.count() == len(win.PAGES)


@pytest.mark.parametrize("index", range(len(win.PAGES)))
def test_every_page_can_be_opened(window, index: int) -> None:
    """Navigating to each page does not raise."""
    window.navigate(index)

    assert window.stack.currentIndex() == index
    assert window.page_title.text() == win.PAGE_TITLES[index]


def test_controls_moved_off_the_providers_page(window) -> None:
    """Providers holds provider configuration and nothing else."""
    window.navigate(3)

    assert not hasattr(window.stack.currentWidget(), "_holds_settings")


def test_settings_controls_are_on_the_settings_page(window) -> None:
    """Reduce motion and the theme picker live in Settings."""
    window.navigate(win.SETTINGS_PAGE)

    assert window.theme_picker.count() == len(THEME_CHOICES)
    assert window.reduce_motion in window.findChildren(type(window.reduce_motion))


# --- theme picker ---------------------------------------------------------


def test_theme_picker_offers_all_themes(window) -> None:
    """Every theme is selectable, with follow-system first."""
    labels = [window.theme_picker.itemText(i) for i in range(window.theme_picker.count())]
    stored = [window.theme_picker.itemData(i) for i in range(window.theme_picker.count())]

    assert labels[0] == "Follow system"
    assert stored[0] is None
    assert set(stored[1:]) == {DARK, LIGHT, MIKU}


def test_theme_picker_defaults_to_follow_system(window) -> None:
    """With nothing stored the picker shows the system-following entry."""
    assert window.storage.settings.get("theme") is None
    assert window.theme_picker.currentIndex() == 0


def test_choosing_a_theme_applies_and_persists_it(window) -> None:
    """Picking Miku switches the window and remembers the choice."""
    index = [c[1] for c in THEME_CHOICES].index(MIKU)

    window.theme_picker.setCurrentIndex(index)

    assert window.theme == MIKU
    assert window.storage.settings["theme"] == MIKU
    assert window.styleSheet() == win.stylesheet(MIKU)


def test_returning_to_follow_system_removes_the_stored_choice(window) -> None:
    """Follow-system is represented by absence, not a stored value."""
    miku_index = [c[1] for c in THEME_CHOICES].index(MIKU)
    window.theme_picker.setCurrentIndex(miku_index)
    assert window.storage.settings["theme"] == MIKU

    window.theme_picker.setCurrentIndex(0)

    assert "theme" not in window.storage.settings
    assert window.theme in (DARK, LIGHT, MIKU)


def test_theme_hint_explains_the_current_choice(window) -> None:
    """The hint says what the picker is doing."""
    window.update_theme_hint()
    following = window.theme_hint.text()

    window.storage.settings["theme"] = LIGHT
    window.update_theme_hint()
    chosen = window.theme_hint.text()

    assert following != chosen
    assert "system" in following.lower()


def test_switching_theme_does_not_write_the_preference_back(window) -> None:
    """Applying a theme programmatically must not fight the picker.

    apply_theme resets the combo index, and if that emitted currentIndexChanged
    the stored preference would be rewritten from the resolved theme, which
    would silently convert follow-system into an explicit choice.
    """
    window.theme_picker.setCurrentIndex(0)
    window.apply_theme(MIKU)

    assert "theme" not in window.storage.settings
    assert window.theme_picker.currentIndex() == 0


# --- resources page -------------------------------------------------------


def test_resources_page_shows_real_readings(window) -> None:
    """Meters are filled from a live sample, not placeholders."""
    window.navigate(win.RESOURCES_PAGE)

    for key in ("cpu", "memory", "disk"):
        reading, bar = window.resource_rows[key]
        assert reading.text() not in ("", "—"), key
        assert bar.value() >= 0


def test_resources_page_builds_one_bar_per_core(window) -> None:
    """The per-core grid matches the machine."""
    window.navigate(win.RESOURCES_PAGE)

    assert len(window.core_bars) == window.sampler.sample()["core_count"]


def test_polling_runs_only_while_the_page_is_visible(window) -> None:
    """Sampling stops when the page is left.

    A panel that polls while hidden spends the app's own CPU to display
    nothing, on a page about CPU usage.
    """
    window.navigate(win.RESOURCES_PAGE)
    assert window.resource_timer.isActive()

    window.navigate(0)
    assert not window.resource_timer.isActive()

    window.navigate(win.RESOURCES_PAGE)
    assert window.resource_timer.isActive()


def test_manual_refresh_works_from_any_page(window) -> None:
    """The refresh shortcut updates the meters without navigating."""
    window.navigate(0)
    window.refresh_resources()

    assert window.resource_rows["cpu"][0].text() != "—"


# --- window sizing --------------------------------------------------------


def test_window_fits_the_screen_it_opens_on(qt_app, storage) -> None:
    """The opening size never exceeds the usable screen area."""
    instance = win.MainWindow(storage)
    try:
        available = instance.primary_screen_size()
        assert available is not None
        assert instance.width() <= available[0]
        assert instance.height() <= available[1]
        assert instance.minimumWidth() <= available[0]
        assert instance.minimumHeight() <= available[1]
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


def test_window_size_is_remembered(qt_app, storage) -> None:
    """Closing records the size so the next launch opens at the same one."""
    instance = win.MainWindow(storage)
    try:
        instance.resize(1150, 760)
        instance.remember_window_size()
        assert storage.settings["window_size"] == [1150, 760]
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


def test_sidebar_hides_when_the_window_is_narrow(window) -> None:
    """The agent sidebar is dropped when there is no room for it.

    isHidden is checked rather than isVisible because the window is never
    shown in a headless test, which makes every child report as not
    visible regardless of what was asked for.
    """
    window.resize(800, 700)
    window.apply_sidebar_density()
    assert window.sidebar.isHidden() is True

    window.resize(1500, 900)
    window.apply_sidebar_density()
    assert window.sidebar.isHidden() is False



def test_settings_page_offers_the_identity_reset(window) -> None:
    """Settings is where a user goes to replace an exposed token."""
    window.navigate(win.SETTINGS_PAGE)
    labels = [button.text() for button in window.findChildren(QPushButton)]

    assert any("Reset local identity" in text for text in labels)


def _reset_dialog_answer(window, answer):
    """Run confirm_reset_identity with the dialog auto-answered.

    QMessageBox.exec() blocks, so it is entered through a timer that
    clicks the chosen button. Without this the test would hang forever
    under the offscreen platform, where nobody can dismiss a dialog.
    """
    calls = []
    window.command = calls.append

    def answer_it():
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QMessageBox):
                widget.button(answer).click()
                return

    QTimer.singleShot(0, answer_it)
    window.confirm_reset_identity()
    return calls


def test_choosing_reset_dispatches_the_command(window) -> None:
    """Confirming Reset asks the runtime to replace the identity."""
    calls = _reset_dialog_answer(window, QMessageBox.StandardButton.Reset)

    assert calls == ["reset_identity"]


def test_cancelling_does_nothing(window) -> None:
    """Cancelling must leave the identity alone."""
    calls = _reset_dialog_answer(window, QMessageBox.StandardButton.Cancel)

    assert calls == []


def test_the_destructive_dialog_defaults_to_cancel(window) -> None:
    """A stray Enter must not discard the token.

    Otherwise the default button would reset the identity without the
    user having chosen Reset.
"""
    seen = {}
    original = QMessageBox.exec

    def capture(self):
        seen["box"] = self
        return int(QMessageBox.StandardButton.Cancel)

    QMessageBox.exec = capture
    try:
        _reset_dialog_answer(window, QMessageBox.StandardButton.Reset)
    finally:
        QMessageBox.exec = original

    box = seen["box"]
    assert box.defaultButton() is box.button(QMessageBox.StandardButton.Cancel)


def test_follow_system_persists_immediately(window) -> None:
    """Removing the stored theme must reach the file, not just memory.

    Only the explicit-choice branch saved, so picking "Follow system"
    left the old theme in settings.json until some unrelated write
    happened to flush it, and the choice came back on the next launch.
    """
    miku_index = [c[1] for c in THEME_CHOICES].index(MIKU)
    window.theme_picker.setCurrentIndex(miku_index)
    assert json.loads(window.storage.path.read_text(encoding="utf-8"))["theme"] == MIKU

    window.theme_picker.setCurrentIndex(0)

    on_disk = json.loads(window.storage.path.read_text(encoding="utf-8"))
    assert "theme" not in on_disk


def test_a_failed_command_is_reported_in_the_error_colour(window) -> None:
    """A failure must not be painted in the success colours."""
    window.apply_theme(LIGHT)
    window.command_failure("no-such-request", "Could not save credentials.")

    assert window.toast_error is True
    assert win.color(window.theme, "error") in window.toast.styleSheet()
    assert win.color(window.theme, "toast_fg") not in window.toast.styleSheet()


def test_a_notice_is_reported_in_the_success_colour(window) -> None:
    """The default stays success-coloured."""
    window.notice("Replaced 2 saved identities.")

    assert window.toast_error is False
    assert win.color(window.theme, "toast_fg") in window.toast.styleSheet()


def test_switching_themes_does_not_turn_an_error_toast_green(window) -> None:
    """apply_theme restyles the toast, so it must keep the error state.

    Otherwise a failure would silently change colour mid-display while
    the user is still reading it.
    """
    window.notice("Could not save credentials.", error=True)

    window.apply_theme(LIGHT)

    assert win.color(LIGHT, "error") in window.toast.styleSheet()
    assert win.color(LIGHT, "toast_fg") not in window.toast.styleSheet()


def test_settings_shows_owed_credentials_until_the_cleanup_completes(window) -> None:
    """State, not a toast, for a condition that outlives the message.

    isHidden is checked rather than isVisible because the window is never
    shown in a headless test, so every child reports as not visible.
    """
    window.navigate(win.SETTINGS_PAGE)

    assert window.pending_cleanup.isHidden(), "nothing is owed after a clean start"

    window.show_pending_cleanup(2)
    assert not window.pending_cleanup.isHidden()
    assert "2 saved credentials" in window.pending_cleanup.text()
    assert "Unlock" in window.pending_cleanup.text()

    window.show_pending_cleanup(1)
    assert "1 saved credential " in window.pending_cleanup.text()

    window.show_pending_cleanup(0)
    assert window.pending_cleanup.isHidden(), "cleared once nothing is owed"


def test_settings_reports_stranded_files_separately_from_credentials(window) -> None:
    """A folder that will not go is a different problem from a locked keyring.

    Folding it into the credential count would either blame the keyring or
    leave the line hidden, and this can last as long as whatever is holding
    the folder does.
    """
    window.navigate(win.SETTINGS_PAGE)

    window.show_pending_cleanup(0, 1)
    assert not window.pending_cleanup.isHidden()
    assert "1 deleted workspace has files" in window.pending_cleanup.text()
    assert "credential" not in window.pending_cleanup.text()

    window.show_pending_cleanup(2, 1)
    assert "2 saved credentials" in window.pending_cleanup.text()
    assert "1 deleted workspace has files" in window.pending_cleanup.text()

    window.show_pending_cleanup(0, 2)
    assert "2 deleted workspaces have files" in window.pending_cleanup.text()

    window.show_pending_cleanup(0, 0)
    assert window.pending_cleanup.isHidden(), "cleared once nothing is owed"


def test_the_pending_line_receives_the_runtime_event(window) -> None:
    """The runtime's pending_cleanup event is what drives the line."""
    window.network_event("pending_cleanup", {"credentials": 3, "files": 0})

    assert not window.pending_cleanup.isHidden()
    assert "3 saved credentials" in window.pending_cleanup.text()

    window.network_event("pending_cleanup", {"credentials": 0, "files": 1})

    assert "1 deleted workspace has files" in window.pending_cleanup.text()
