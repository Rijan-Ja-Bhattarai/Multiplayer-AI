"""The Resources and Settings pages, against a real headless window.

Runs through the offscreen Qt platform plugin so no display is needed.
The window is given a MemoryVault rather than a real one: DesktopRuntime
calls Storage.save_credentials while starting up, so a real Vault would
leave a device token in the developer's OS credential store.
"""

from __future__ import annotations

import json
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6", reason="PySide6 is needed for the desktop page tests")
pytest.importorskip("psutil", reason="psutil backs the Resources page")

from PySide6.QtCore import QPoint, QPointF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QShortcut, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QLabel,
                             QMessageBox, QPushButton)  # noqa: E402

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



# --- the rail's plus -----------------------------------------------------


def test_the_join_button_is_a_drawn_plus_not_a_character(window) -> None:
    """It was the text "+", which is placed by the font and not by us.

    Whether that lands where it looks right is a question about the font,
    so the mark is drawn instead, on the same 24-unit grid as the
    navigation icons and with the same stroke.
    """
    join = window.join_button
    assert not join.text(), f"the button is still a text glyph, {join.text()!r}"
    assert not join.icon().isNull(), "the button has no drawn mark"
    assert not join.icon().isMask(), "the mark should be coloured, not a mask"


def test_the_drawn_plus_is_centred_in_its_own_mark(window) -> None:
    """A round-capped stroke runs past the end point it was given.

    Drawn to its nominal bounds the cross sits low and right, so the ends
    are pulled back by half a stroke. Checked on the mark's own ink, which
    unlike a glyph's does not depend on a font being installed.

    The centroid rather than the bounding box: a cross's box is set by its
    longest arm, so nudging the other arm sideways still fits inside it and
    a box check would call that centred.
    """
    image = window.join_button.icon().pixmap(48, 48).toImage()
    lit = [(x, y) for x in range(image.width()) for y in range(image.height())
           if image.pixelColor(x, y).alpha() > 32]
    assert lit, "the mark is empty"

    centre_x = sum(x for x, _ in lit) / len(lit)
    centre_y = sum(y for _, y in lit) / len(lit)
    middle = (image.width() - 1) / 2

    assert abs(centre_x - middle) <= 0.5, (
        f"the ink sits {abs(centre_x - middle):.1f}px right of centre")
    assert abs(centre_y - middle) <= 0.5, (
        f"the ink sits {abs(centre_y - middle):.1f}px below centre")


def test_the_plus_follows_the_theme(window, qt_app) -> None:
    """Hand-painted, so nothing reaches it but an explicit repaint."""
    for name in THEME_NAMES:
        window.apply_theme(name)
        qt_app.processEvents()
        middle = window.join_button.icon().pixmap(48, 48).toImage().pixelColor(24, 24)
        assert middle.name().lower() == color(name, "success").lower(), (
            f"{name}: the middle of a cross is a stroke, so it should read "
            f"as {color(name, 'success')}, found {middle.name()}")


def test_hovering_the_rail_does_not_strip_a_workspace_button(qt_app) -> None:
    """The hover animation writes its own stylesheet, and it used to win.

    The ink was set by a widget stylesheet, which the animation overwrote,
    so hovering took the colour and everything else that sheet was
    carrying for the rest of the hover with it. The ink is a property now,
    read by the application stylesheet, so the two cannot collide.

    Checked on a button carrying text rather than the rail's plus, which
    is drawn and so never depended on the button's own colour.
    """
    from desktop_app.widgets import WorkspaceButton

    button = WorkspaceButton("AB")
    button.set_ink("success")
    before = button.styleSheet()

    button.set_radius(15.0)              # where the hover animation lands
    assert button.property("ink") == "success", (
        f"hovering took the ink, leaving {button.styleSheet()!r} instead of "
        f"the {before!r} that was there before")
    assert button.styleSheet() == "QPushButton { border-radius: 15px; }", (
        f"the hover sheet should carry the corner and nothing else, "
        f"found {button.styleSheet()!r}")


# --- the workspaces page ------------------------------------------------


def test_a_page_is_reachable_and_explains_itself(window) -> None:
    """Adding a page used to shift every index below it silently."""
    assert set(win.PAGE_HELP) == {key for _, key in win.PAGES}, (
        "every page needs a tooltip entry, or one is about to be missing")
    assert win.PAGE_INDEX["workspaces"] == win.WORKSPACES_PAGE
    assert len(win.PAGES) == window.stack.count(), (
        "a page is declared but never added to the stack, so its button "
        "would go somewhere else")


@pytest.mark.parametrize("key", ["resources", "settings", "workspaces"])
def test_the_named_pages_point_where_they_say(window, key) -> None:
    """RESOURCES_PAGE and SETTINGS_PAGE were counted, not looked up.

    Inserting a page above them moved both, which broke Ctrl+5 and Ctrl+,
    and nothing failed: the numbers were still in range, just addressing
    the wrong page.
    """
    position = [name for _, name in win.PAGES].index(key)
    assert getattr(win, f"{key.upper()}_PAGE") == position, (
        f"{key} is page {position} but the constant says "
        f"{getattr(win, f'{key.upper()}_PAGE')}")


@pytest.mark.parametrize("index,key", list(enumerate(k for _, k in win.PAGES)))
def test_every_shortcut_lands_on_its_own_page(window, index, key) -> None:
    """Ctrl+1..Ctrl+n have to follow the pages, not the order they were added."""
    shortcuts = {s.key().toString(): s for s in window.findChildren(QShortcut)}
    shortcut = shortcuts.get(f"Ctrl+{index + 1}")
    assert shortcut is not None, f"Ctrl+{index + 1} is not bound to anything"
    shortcut.activated.emit()
    assert window.stack.currentIndex() == index, (
        f"Ctrl+{index + 1} landed on {window.stack.currentIndex()} "
        f"instead of {key}")


def test_the_rail_plus_goes_to_the_page_and_not_a_menu(window) -> None:
    """It used to pop a two-item menu, and creating was a bare prompt."""
    before = window.stack.currentIndex()
    window.add_workspace_button.click()

    assert window.stack.currentIndex() == win.WORKSPACES_PAGE, (
        f"the plus left the user on page {window.stack.currentIndex()}")
    assert window.stack.currentIndex() != before
    assert QApplication.activePopupWidget() is None, (
        "the plus still opens a pop-up instead of going to the page")


def test_the_create_panel_refuses_a_name_it_would_be_rejected_for(window) -> None:
    """The rule is the runtime's, so the two cannot disagree about it."""
    window.ready = True
    for text, ok in (("", False), ("   ", False), ("x" * 81, False), ("Team", True)):
        window.workspace_name_input.setText(text)
        assert window.workspace_create_button.isEnabled() is ok, (
            f"{len(text.strip())} characters should {'allow' if ok else 'refuse'} "
            f"the submit button")

    window.workspace_name_input.setText("x" * 81)
    assert window.workspace_name_line.message.text(), "the refusal was not explained"


def test_a_refused_create_reports_on_the_page_not_in_a_toast(window, qt_app) -> None:
    """The command is asynchronous, so the failure lands after it returns.

    A toast has usually gone by the time anyone reads it; the field that
    caused the problem is still on screen.
    """
    window.ready = True
    window.workspace_name_input.setText("")
    shown: list[str] = []
    window.notice = lambda message, error=False: shown.append(message)

    window.create_workspace()

    assert window.workspace_name_line.message.text(), "nothing was reported anywhere"
    assert not shown, f"the message went to a toast instead: {shown}"


def test_joining_from_the_page_does_not_open_a_dialog(window, monkeypatch) -> None:
    """The page's own fields are the ones that get used.

    This was the bug: the page had an invitation box, a relay box, a token
    box and a checkbox, and pressing Join threw all of them away and opened
    the old modal to ask for the same information again. So the fields were
    decoration and whatever you had typed was silently discarded.
    """
    opened = []
    monkeypatch.setattr(QDialog, "exec", lambda self, *a, **k: opened.append(self))
    window.ready = True

    sent = []
    window.command = lambda name, *args, **kwargs: sent.append((name, args))

    invitation = json.dumps({
        "version": 1,
        "url": "wss://relay.example.com/connect",
        "token": "t" * 40,
        "allow_insecure": True,
    })
    window.workspace_invitation.setPlainText(invitation)

    window.join_workspace()

    assert not opened, f"a dialog was opened instead of joining: {opened}"
    assert sent, "no join was attempted"
    name, args = sent[0]
    assert name == "join"
    # The invitation filled the page's fields, and those were submitted.
    assert window.workspace_relay.text() == "wss://relay.example.com/connect"
    assert window.workspace_token.text() == "t" * 40
    assert window.workspace_lan.isChecked() is True
    assert args[0] == "wss://relay.example.com/connect"
    assert args[1] == "t" * 40
    assert args[2] is True


def test_a_shared_conversation_invitation_joins_that_conversation(window) -> None:
    """The conversation id in an invitation is carried into the join."""
    window.ready = True
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append((name, args))
    window.workspace_invitation.setPlainText(json.dumps({
        "version": 1,
        "url": "wss://relay.example.com/connect",
        "token": "t" * 40,
        "conversation_id": "conversation-abc123",
    }))

    window.join_workspace()

    assert sent[0][0] == "join"
    # join(relay, token, allow_insecure, announce, conversation_id)
    assert sent[0][1][4] == "conversation-abc123"


@pytest.mark.parametrize("paste, expected", [
    ("not json at all", "Paste the complete invitation JSON"),
    ('{"url": "wss://x", "token": "y"}', "Unsupported invitation version"),
    ('["a"]', "beginning with { and ending with }"),
    ('{"version": 1, "url": "wss://x", "token": "y", "conversation_id": "nope"}',
     "Use the complete shared conversation invitation"),
])
def test_a_bad_invitation_is_reported_on_the_page(window, paste, expected) -> None:
    """Every reason a paste is unusable is shown where the paste is."""
    window.ready = True
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append((name, args))
    window.workspace_invitation.setPlainText(paste)

    window.join_workspace()

    assert not sent, "a join was attempted with an unusable invitation"
    assert expected in window.workspace_join_line.message.text(), (
        f"the page says {window.workspace_join_line.message.text()!r}")
    assert window.workspace_join_button.text() == "Join workspace"


def test_a_too_short_token_is_refused_on_the_page(window) -> None:
    window.ready = True
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append((name, args))
    window.workspace_invitation.setPlainText("")

    window.join_workspace()

    assert not sent
    assert "device token" in window.workspace_join_line.message.text()


def test_a_failed_join_leaves_the_page_usable(window) -> None:
    """A failure puts the button back and says why, on the page."""
    window.ready = True
    window.workspace_invitation.setPlainText("")
    window.workspace_token.setText("t" * 40)
    window.workspace_relay.setText("wss://relay.example.com/connect")

    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)

    window.join_workspace()
    assert window.workspace_join_button.text() == "Connecting…"
    assert window.workspace_join_button.isEnabled() is False

    # The failure arrives asynchronously, after join_workspace returned.
    callbacks["failure"]("The relay refused the token")

    assert window.workspace_join_button.text() == "Join workspace"
    assert window.workspace_join_button.isEnabled() is True
    assert "refused" in window.workspace_join_line.message.text()


def test_joining_before_the_network_is_ready_is_refused(window) -> None:
    window.ready = False
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append((name, args))

    window.join_workspace()

    assert not sent, "a join was attempted before the network was ready"
    assert "still starting" in window.workspace_join_line.message.text()


def test_each_card_keeps_its_own_message(window) -> None:
    """A failure in one card never appears in, or clears, another.

    There was one line shared by the create and join forms, so a join error
    was painted inside the create card, and creating a workspace wrote its
    message to the same place a join was reading.
    """
    window.ready = True
    window.command = lambda name, *args, **kwargs: None

    window.set_card_message(window.workspace_join_line, "the relay refused the token")
    assert window.workspace_join_line.message.text() == "the relay refused the token"
    assert not window.workspace_create_line.message.text(), (
        "a join failure showed in the create card")
    assert not window.workspace_name_line.message.text()

    window.set_card_message(window.workspace_create_line, "that name is taken")
    assert window.workspace_create_line.message.text() == "that name is taken"
    assert window.workspace_join_line.message.text() == "the relay refused the token"


def test_a_new_page_starts_with_the_stored_message(window, qt_app) -> None:
    """A card built after a failure shows that failure, not an empty line.

    Anything held only by the widget that displayed it is lost when the page
    is torn down and built again, so the message is kept in the window's
    state and each new line is given it as it is constructed.
    """
    window.ready = True
    window.set_card_message(window.workspace_join_line, "the relay refused the token")

    # The page is held here because a page built and dropped takes its
    # widgets with it, which would delete the lines being asserted on.
    rebuilt = window.workspaces_page()
    qt_app.processEvents()

    line = window.card_lines["workspace:join"]
    assert line.message.text() == "the relay refused the token"
    # isHidden rather than isVisible: this page was never added to the
    # stack, so nothing in it is visible yet, and that is not what is
    # being checked here.
    assert line.isHidden() is False, "the line is still collapsed"
    assert window.card_messages["workspace:join"] == (
        "the relay refused the token", "error")
    assert rebuilt is not None


def test_a_card_rebuild_keeps_the_message(window, qt_app) -> None:
    """The live rebuild path: a grid cleared and filled again.

    This is what the agents page does on every poll, and it is why the
    message cannot live in the card.
    """
    window.ready = True
    window.set_card_message(window.workspace_join_line, "the relay refused the token")

    window.refresh_card_messages()
    qt_app.processEvents()

    line = window.card_lines["workspace:join"]
    assert line.message.text() == "the relay refused the token"


def test_the_agents_poll_does_not_clear_a_message(window, qt_app) -> None:
    """The reported bug: a message lasted about three seconds.

    The runtime polls agents every three seconds, each poll re-ran
    render_workspace_page, which ended in the name handler, and that
    handler owned the single shared line. So a failure was erased by the
    next poll whether or not anything had been retried.
    """
    window.ready = True
    window.set_card_message(window.workspace_join_line, "the relay refused the token")
    window.set_card_message(window.workspace_create_line, "that name is taken")

    for _ in range(3):
        window.network_event("agents", {"agents": [], "self": window.identity,
                                        "connected": False})
        qt_app.processEvents()

    assert window.workspace_join_line.message.text() == "the relay refused the token"
    assert window.workspace_create_line.message.text() == "that name is taken"


def test_no_runtime_event_clears_a_message(window, qt_app) -> None:
    """Every event that redraws the page must leave messages alone.

    The workspace event does not reach render_workspace_page today, so this
    drives render_workspace_page itself and also the events that do. The
    requirement is about the render, not about which event happens to call
    it, because a poll is free to start calling it tomorrow.
    """
    window.ready = True
    window.set_card_message(window.workspace_join_line, "the relay refused the token")
    window.set_card_message(window.workspace_create_line, "that name is taken")

    window.render_workspace_page()
    window.render_agents()
    window.network_event("agents", {"agents": [], "self": window.identity,
                                    "connected": False})
    window.network_event("workspace", {
        "id": window.workspace_id, "name": "Lab", "remote": False,
        "self": window.identity, "port": 1234,
        "history_directory": str(window.storage.directory)})
    qt_app.processEvents()

    assert window.workspace_join_line.message.text() == "the relay refused the token"
    assert window.workspace_create_line.message.text() == "that name is taken"


def test_typing_a_name_does_not_clear_a_create_failure(window) -> None:
    """The name rule and the create outcome are different messages.

    They shared one line, so typing after a failure replaced what the
    runtime said with the name-length hint.
    """
    window.ready = True
    window.set_card_message(window.workspace_create_line, "that name is taken")

    window.workspace_name_input.setText("Research lab")
    assert window.workspace_create_line.message.text() == "that name is taken"

    window.workspace_name_input.setText("x" * 81)
    assert window.workspace_create_line.message.text() == "that name is taken", (
        "typing a name replaced a real failure with the name rule")
    assert window.workspace_name_line.message.text(), (
        "but the name rule itself was not shown either")


def test_dismissing_clears_one_message_and_leaves_the_others(window, qt_app) -> None:
    window.ready = True
    window.set_card_message(window.workspace_join_line, "the relay refused the token")
    window.set_card_message(window.workspace_create_line, "that name is taken")

    window.workspace_join_line.dismiss.click()
    qt_app.processEvents()

    assert not window.workspace_join_line.message.text()
    assert not window.workspace_join_line.isVisible()
    assert window.workspace_create_line.message.text() == "that name is taken", (
        "dismissing the join message also cleared the create message")


def test_trying_again_replaces_the_message_it_is_replacing(window) -> None:
    """The outcome line shows the newest outcome, never a stale one.

    A failure left on screen while a second attempt is already under way
    tells the reader the attempt failed when it has not been tried yet.
    """
    window.ready = True
    window.workspace_invitation.setPlainText("")
    window.workspace_token.setText("t" * 40)
    window.workspace_relay.setText("wss://relay.example.com/connect")

    callbacks = {}
    window.command = lambda name, *args, **kwargs: callbacks.update(kwargs)
    window.join_workspace()
    callbacks["failure"]("The relay refused the token")
    assert window.workspace_join_line.message.text() == "The relay refused the token"

    window.join_workspace()

    assert window.workspace_join_line.message.text() != "The relay refused the token", (
        "the previous failure was still showing over a fresh attempt")
    assert "Contacting the relay" in window.workspace_join_line.message.text()


def test_trying_again_clears_a_failure_even_when_it_needs_no_message(window) -> None:
    """The clear happens because the button was pressed, not as a side effect.

    A retry that resolves without a message of its own must still remove the
    last failure, or it hangs over the form forever.
    """
    window.ready = True
    window.workspace_name_input.setText("Research lab")
    window.set_card_message(window.workspace_create_line, "that name is taken")
    sent = []
    window.command = lambda name, *args, **kwargs: sent.append(name)

    window.create_workspace()

    assert not window.workspace_create_line.message.text(), (
        "a previous failure stayed on screen after the button was pressed")
    assert not window.card_messages.get("workspace:create")


def test_a_message_line_costs_nothing_when_empty(window, qt_app) -> None:
    """An empty line must not leave a band of space on every card."""
    for line in (window.workspace_name_line, window.workspace_create_line,
                 window.workspace_join_line):
        assert line.isVisible() is False
        assert not line.message.text()


def test_a_message_line_is_out_of_the_layout_when_empty(window) -> None:
    """An empty line must not leave a band of space on every card.

    Checked through the layout rather than isVisible, because a page that
    was never shown has nothing visible in it; what matters is that the line
    contributes no height while it has nothing to say.
    """
    for line in (window.workspace_name_line, window.workspace_create_line,
                 window.workspace_join_line):
        assert not line.message.text()

    window.set_card_message(window.workspace_join_line, "refused")
    assert window.workspace_join_line.isHidden() is False

    window.clear_card_message(window.workspace_join_line)
    assert window.workspace_join_line.isHidden() is True, (
        "the cleared line is still taking up space on the card")


def test_a_card_message_follows_a_theme_change(window) -> None:
    """It carries its own colour, so the application sheet will not do it."""
    window.set_card_message(window.workspace_join_line, "refused")
    line = window.workspace_join_line
    assert color(LIGHT, "error") not in line.message.styleSheet()

    window.apply_theme(LIGHT)

    assert color(LIGHT, "error") in line.message.styleSheet(), (
        f"the message kept the old theme's colour: {line.message.styleSheet()!r}")
    assert color(DARK, "error") not in line.message.styleSheet()


def test_the_create_panel_previews_the_rail_button(window) -> None:
    """The rail shows a workspace as its first two letters.

    Nothing showed that before, so the name you typed and the button you
    would then be clicking were unconnected until you clicked it.
    """
    window.ready = True
    window.workspace_name_input.setText("Research lab")
    assert window.workspace_preview.text() == "RE", (
        f"the preview reads {window.workspace_preview.text()!r}")
    assert "RE" in window.workspace_preview_label.text(), (
        "the preview is not named in words beside the field")

    window.workspace_name_input.setText("")
    assert not window.workspace_preview.text(), (
        "an empty name still leaves letters in the preview")


def test_the_page_says_which_workspace_you_are_in(window) -> None:
    """The sidebar names it, but only in one line and only the name."""
    window.workspace_label.setText("Research lab")
    window.workspace_meta = {"members": [
        {"id": "device-1", "role": "Device"},
        {"id": "device-2", "role": "Device"},
        {"id": "local-llama", "role": "Model"},
    ]}
    window.render_workspace_page()

    assert window.workspace_name_label.text() == "Research lab"
    summary = window.workspace_members_label.text()
    assert "2 devices" in summary, f"the summary does not count the devices: {summary!r}"
    assert "1 model agent" in summary, f"the summary does not count the agents: {summary!r}"


def test_the_page_opens_usable_once_the_runtime_is_up(window) -> None:
    """It refreshed on the agents poll, so the first second looked broken.

    The runtime reports ready long before it lists any agents, so a user
    opening this page in that gap was shown controls that were not yet
    usable and nothing to say they were coming. Driven through the real
    event rather than by flipping the flag, because the flag is not what
    decides whether the page refreshes.
    """
    window.ready = False
    window.render_workspace_page()
    assert not window.workspace_invite_button.isEnabled()

    window.network_event("ready", {"port": 0, "device_id": "device-test"})

    assert window.ready, "the runtime was not marked ready"
    assert window.workspace_invite_button.isEnabled(), (
        "the page did not refresh when the runtime came up")
    assert window.workspace_join_button.isEnabled()


from desktop_app.splash import LAUNCH_CAP_MS, LAUNCH_FLOOR_MS  # noqa: E402

# --- the launch screen --------------------------------------------------


@pytest.mark.parametrize("events,expected,warnings", [
    # Nothing wrong: one plain line and no complaint.
    ([("ready", {"port": 1}), ("workspace", {"name": "Mine"}),
      ("agents", {"agents": [1, 2], "connected": True})],
     "Getting things ready", []),
    # The relay being down is worth saying, and it is not a reason to hold.
    ([("ready", {"port": 1}), ("agents", {"agents": [], "connected": False})],
     "Working without the relay", ["the relay is not connected"]),
    # A cleanup still owed outranks however many agents turned up, so the
    # later signal must not overwrite the more important line.
    ([("ready", {"port": 1}), ("pending_cleanup", {"credentials": 2, "files": 1}),
      ("agents", {"agents": [1], "connected": True})],
     "Finishing an earlier cleanup", ["2 credential(s) still owed"]),
    ([("ready", {"port": 1}), ("fatal", "the relay refused the connection")],
     "Could not start", ["the relay refused the connection"]),
])
def test_the_status_line_says_what_actually_happened(events, expected, warnings) -> None:
    from desktop_app.splash import HOLD_TEXT, StartupStatus

    status = StartupStatus()
    for event, data in events:
        status.observe(event, data)

    assert status.settled, "the startup never settled, so the card waits out its cap"
    assert status.line == expected
    assert status.warnings == warnings
    if not warnings:
        assert status.line == HOLD_TEXT


def test_cleanup_with_nothing_owed_is_not_a_warning() -> None:
    """Reported on every single start, including when there is nothing to do.

    Treating it as a warning made every launch open with a line about a
    cleanup that did not exist.
    """
    from desktop_app.splash import StartupStatus

    status = StartupStatus()
    status.observe("pending_cleanup", {"credentials": 0, "files": 0})
    status.observe("agents", {"agents": [], "connected": True})

    assert status.warnings == []
    assert status.tone == "normal"


def test_startup_facts_do_not_flicker_past_on_the_card() -> None:
    """Each fact arrives about 200ms after the last one.

    Showing them meant three lines changed in the time it takes to read a
    single one, which looks like a fault rather than like progress. They
    are recorded, but only the held line is displayed.
    """
    from desktop_app.splash import StartupStatus

    status = StartupStatus()
    seen = []
    for event, data in (("ready", {"port": 1}), ("workspace", {"name": "Mine"}),
                        ("agents", {"agents": [1], "connected": True})):
        if status.observe(event, data):
            seen.append(status.line)

    assert len(seen) == 1, f"the displayed line changed {len(seen)} times: {seen}"
    assert any("port 1" in f for f in status.facts), status.facts
    assert any("Mine" in f for f in status.facts), status.facts
    assert any("1 agent(s)" in f for f in status.facts), (
        "the facts are still recorded even though they are not displayed")


def test_the_window_is_hidden_until_the_card_has_gone(qt_app, storage) -> None:
    """It used to be shown first and covered, so it peeked out from behind.

    Now the window is built and painted off screen underneath the card, and
    only shown when the card has faded out.
    """
    from desktop_app.splash import LaunchScreen, StartupStatus

    window = win.MainWindow(storage)
    try:
        status = StartupStatus()
        screen = LaunchScreen(DARK)
        screen.set_status(status)
        revealed = []
        screen.begin(on_done=lambda: (revealed.append(True), window.show()))
        window.grab()

        assert not window.isVisible(), (
            "the window is on screen behind the card, so the card is "
            "overlaying it rather than replacing it")

        status.observe("ready", {"port": 1})
        status.observe("agents", {"agents": [], "connected": True})
        screen.runtime_ready()
        assert not revealed, "the window was revealed before the hold was over"

        pump(qt_app, (LAUNCH_FLOOR_MS + 1200) / 1000)
        assert revealed, "the window was never revealed"
        assert window.isVisible()
    finally:
        window.network.shutdown()
        window.network.wait(10000)
        window.hide()


def test_a_hidden_window_can_be_painted_before_it_is_shown(qt_app, storage) -> None:
    """The reason the reveal can be instant.

    Shown for the first time, a window paints its first frame after it
    appears, which reads as a flash of something unfinished. Rendering it
    off screen first means the frame the user sees is already complete.
    """
    window = win.MainWindow(storage)
    try:
        assert not window.isVisible()
        image = window.grab().toImage()
        assert not image.isNull()
        colours = {image.pixelColor(x, y).name()
                   for x in range(0, image.width(), 40)
                   for y in range(0, image.height(), 40)}
        assert len(colours) > 3, (
            "the hidden window rendered as a blank, so showing it would flash")
    finally:
        window.network.shutdown()
        window.network.wait(10000)


def test_the_card_releases_even_if_the_runtime_goes_quiet(qt_app, storage) -> None:
    """The one failure this screen is not allowed to have.

    A runtime that reported ready and then never listed its agents would
    otherwise hold a card over an app that is entirely usable. The status
    is left unsettled deliberately: a startup with nothing owed settles at
    once and would release at the floor whether or not the cap existed.
    """
    from desktop_app.splash import LAUNCH_CAP_MS, LaunchScreen, StartupStatus

    window = win.MainWindow(storage)
    try:
        status = StartupStatus()
        status.observe("ready", {"port": 1})   # ready, and then nothing
        assert not status.settled, "the status settled on its own"

        screen = LaunchScreen(DARK)
        screen.set_status(status)
        screen.begin(on_done=window.show)
        screen.runtime_ready()

        pump(qt_app, (LAUNCH_FLOOR_MS + LAUNCH_CAP_MS) / 1000 + 0.6)
        assert screen.dismissed, (
            "the card is still up although the runtime never reported again")
    finally:
        window.network.shutdown()
        window.network.wait(10000)


def test_closing_the_card_cancels_the_launch(qt_app, storage) -> None:
    """It has no title bar, so Alt+F4 on it is the only way to dismiss it.

    Showing a window nobody asked to see because they dismissed its card
    would be worse than quitting.
    """
    from desktop_app.splash import LaunchScreen

    window = win.MainWindow(storage)
    try:
        revealed = []
        screen = LaunchScreen(DARK)
        screen.begin(on_done=lambda: revealed.append(True))
        screen.close()
        qt_app.processEvents()
        assert screen.cancelled
        assert not revealed, "closing the card revealed the window anyway"
    finally:
        window.network.shutdown()
        window.network.wait(10000)


def pump(qt_app, seconds):
    """Turn the event loop for a while, which is what drives a timer."""
    end = time.time() + seconds
    while time.time() < end:
        qt_app.processEvents()
        time.sleep(0.005)


def test_the_launch_screen_waits_for_a_floor(qt_app) -> None:
    """Startup takes about a quarter of a second here.

    Waiting only for readiness would flash the screen for a fifth of a
    second, which reads as a glitch rather than as an intention, so it
    holds for a floor even though it is ready well before that.
    """
    from desktop_app.splash import LAUNCH_FLOOR_MS, LaunchScreen

    screen = LaunchScreen("dark")
    screen.begin()
    qt_app.processEvents()
    assert screen.isVisible()

    screen.runtime_ready()
    pump(qt_app, 0.2)
    assert not screen.dismissed, (
        f"it left after 200ms; the floor is {LAUNCH_FLOOR_MS}ms")

    pump(qt_app, LAUNCH_FLOOR_MS / 1000 + 0.5)
    assert screen.dismissed and not screen.isVisible()


def test_the_launch_screen_leaves_at_once_when_ready(qt_app) -> None:
    """Once the hold has passed, readiness releases it with nothing left to wait for."""
    from desktop_app.splash import LaunchScreen

    screen = LaunchScreen("dark")
    screen.begin()
    # Wait out the hold first, so this is asking whether readiness releases
    # it rather than whether the hold does.
    pump(qt_app, LAUNCH_FLOOR_MS / 1000 + 0.4)
    assert not screen.dismissed, "the hold let go on its own, which it should not"

    screen.runtime_ready()
    pump(qt_app, 0.5)
    assert screen.dismissed


def test_the_launch_screen_has_a_background_of_its_own(qt_app) -> None:
    """Left translucent with nothing behind it, it showed the window through.

    The splash comes up over a main window that is already painted, so a
    transparent one reads as an error dialog rather than as progress. It is
    a card in the theme's own colour: opaque in the middle, rounded and so
    transparent at the corners.
    """
    from desktop_app.splash import LaunchScreen

    for name in THEME_NAMES:
        screen = LaunchScreen(name)
        screen.begin()
        qt_app.processEvents()
        image = screen.grab().toImage()
        width, height = image.width(), image.height()

        middle = image.pixelColor(width // 2, 8)
        assert middle.alpha() == 255, (
            f"{name}: the background is see-through, so the window shows "
            f"through the loading screen")
        assert middle.name().lower() == color(name, "surface").lower(), (
            f"{name}: the background is {middle.name()}, not the theme's "
            f"card colour {color(name, 'surface')}")

        corner = image.pixelColor(1, 1)
        assert corner.alpha() < 255, (
            f"{name}: the card has square corners, so it reads as a plain "
            f"rectangle rather than as a card")

        edge = image.pixelColor(width // 2, 0)
        assert edge.name().lower() == color(name, "border").lower(), (
            f"{name}: the card has no hairline, so it has no edge against a "
            f"busy window behind it")
        screen.dismiss()
        pump(qt_app, 0.3)


def test_the_launch_screen_background_follows_a_theme_change(qt_app) -> None:
    from desktop_app.splash import LaunchScreen

    screen = LaunchScreen(DARK)
    screen.begin()
    screen.set_theme(LIGHT)
    qt_app.processEvents()
    image = screen.grab().toImage()
    middle = image.pixelColor(image.width() // 2, 8)
    assert middle.name().lower() == color(LIGHT, "surface").lower(), (
        "the card kept the dark theme's colour after a theme change")
    screen.dismiss()
    pump(qt_app, 0.3)


def test_the_launch_screen_takes_its_effect_off_when_it_goes(qt_app) -> None:
    """Left attached, every later paint goes through Qt's effect machinery.

    That is the fault the page fade has in the window, and a widget with
    nothing left to fade has no business keeping one.
    """
    from desktop_app.splash import LaunchScreen

    screen = LaunchScreen("dark")
    screen.begin()
    qt_app.processEvents()
    assert screen.graphicsEffect() is not None, "the fade-in needs an effect"

    screen.dismiss()
    pump(qt_app, 0.6)
    assert screen.graphicsEffect() is None, (
        "the effect outlived the animation it was there for")
    assert screen.art.moving is False, "the orbit keeps turning behind a closed splash"


def test_the_launch_screen_carries_the_apps_own_mark(qt_app) -> None:
    """The same mark the taskbar is about to show.

    The letter on it cannot be checked here: with no fonts installed Qt
    draws no glyph at all, so a marked tile and a blank one paint
    identically. What is checked is that the fill is the theme's accent,
    which is what would otherwise make the splash a coloured square.
    """
    from desktop_app.splash import LaunchScreen

    screen = LaunchScreen("miku")
    assert screen.art._theme == MIKU
    assert not screen.mark.pixmap().isNull()
    centre = screen.mark.pixmap().toImage().pixelColor(44, 44).name()
    assert centre == color(MIKU, "accent").lower(), (
        f"the mark is painted {centre}, not the theme's accent")


def test_the_launch_screen_actually_writes_its_text(qt_app) -> None:
    """The caption and the status line are on the card, not just in the code.

    Both of these lines were invisible for the whole life of the card. They
    are hand-painted widgets that a layout centres, and a bare QWidget has no
    valid size hint, so the layout gave them zero width and a zero-width
    widget never paints. Nothing failed, nothing warned, and the card looked
    finished because it had a mark and an orbit on it.

    So the check is on width and on painted pixels, which is the thing that
    was actually wrong, rather than on the presence of the widgets.
    """
    from desktop_app.splash import CARD_TEXT_WIDTH, LaunchScreen

    screen = LaunchScreen("dark")
    screen.show()
    qt_app.processEvents()

    for widget in (screen.caption, screen.status):
        assert widget.width() > 0, (
            f"a line of text has no width, so it cannot paint: {widget._text!r}")
        assert widget.width() <= CARD_TEXT_WIDTH, (
            f"a line wider than the card: {widget.width()} > {CARD_TEXT_WIDTH}")

    # Width is not proof of paint, so check glyphs were laid down. With no
    # fonts installed the shapes are wrong but the pixels are still inked.
    image = screen.grab().toImage()
    background = color(DARK, "surface")
    for widget in (screen.caption, screen.status):
        box = widget.geometry()
        region = image.copy(box.x(), box.y(), box.width(), box.height())
        inked = {region.pixelColor(x, y).name()
                 for y in range(region.height())
                 for x in range(region.width())} - {background}
        assert inked, f"nothing was painted for {widget._text!r}"


def test_a_status_line_too_long_for_the_card_is_elided(qt_app) -> None:
    """A long warning cannot push the layout about or run off both edges."""
    from desktop_app.splash import CARD_TEXT_WIDTH, LaunchScreen

    screen = LaunchScreen("dark")
    screen.show()
    screen.status.set("Could not start: the relay is unreachable and its vault "
                      "could not be read, so nothing can be sent", "error")
    qt_app.processEvents()

    assert screen.status.sizeHint().width() <= CARD_TEXT_WIDTH
    assert screen.status.width() <= CARD_TEXT_WIDTH
    assert screen.status.width() > 0


def test_the_launch_screen_follows_the_theme(qt_app) -> None:
    from desktop_app.splash import LaunchScreen

    screen = LaunchScreen("dark")
    screen.set_theme(LIGHT)
    assert screen.art._theme == LIGHT
    centre = screen.mark.pixmap().toImage().pixelColor(44, 44).name()
    assert centre == color(LIGHT, "accent").lower()


def test_dismissing_the_launch_screen_twice_is_harmless(qt_app) -> None:
    """A fatal error and the floor can both ask it to go."""
    from desktop_app.splash import LaunchScreen

    screen = LaunchScreen("dark")
    screen.begin()
    qt_app.processEvents()
    screen.dismiss()
    screen.dismiss()
    pump(qt_app, 0.5)
    assert screen.dismissed and not screen.isVisible()


def test_a_launch_screen_that_never_ran_leaves_cleanly(qt_app) -> None:
    """It can be asked to go before it was ever shown."""
    from desktop_app.splash import LaunchScreen

    screen = LaunchScreen("dark")
    screen.dismiss()
    assert screen.dismissed and not screen.isVisible()


def test_the_workspaces_page_never_scrolls_sideways(window, qt_app) -> None:
    """A wide label or a long checkbox label sets the width for the page.

    The page's own intro was 129 unwrapped characters, which on its own
    demanded more than the window had, and a checkbox's minimum is the
    width of its whole label with no word wrap to fall back on. Both grew a
    horizontal scrollbar under the content at the preferred window size,
    which is the same fault the sidebar list had.
    """
    window.navigate(win.WORKSPACES_PAGE)
    for width in (760, 1000, 1330, 1700):
        window.resize(width, 800)
        pump(qt_app, 0.25)
        page = window.stack.currentWidget()
        content = page.widget()
        assert content.minimumSizeHint().width() <= page.viewport().width(), (
            f"at {width}px the page needs {content.minimumSizeHint().width()}px "
            f"but has {page.viewport().width()}px, so it scrolls sideways")


def test_the_sidebar_list_does_not_scroll_sideways(window, qt_app) -> None:
    """A long agent name used to grow a scrollbar under the list."""
    window.agents = [{"id": "a-very-long-agent-name-for-testing-wrap", "online": True,
                      "provider": "ollama", "model": "m"}]
    window.render_agents()
    assert not window.sidebar_agents.horizontalScrollBar().isVisible(), (
        "the agent list has a horizontal scrollbar; the names should wrap")


# --- the settings controls ----------------------------------------------


def wheel_event(delta=-120):
    """One wheel notch, as a page scroll would deliver it."""
    return QWheelEvent(
        QPointF(8, 8), QPointF(8, 8 - abs(delta) // 8), QPoint(0, 0), QPoint(0, delta),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate, False)


def test_the_theme_picker_ignores_the_mouse_wheel(window, qt_app) -> None:
    """Scrolling the Settings page used to rewrite the stored theme.

    Qt's combo box advances on any wheel notch over it, so a scroll meant
    to move the page kept changing the theme instead, for as long as the
    scroll continued. A wheel is not a pick out of a closed list; the popup
    is the way to choose.

    The page is opened first on purpose: a hidden widget is given no wheel
    events at all, so the test would pass against an unfixed picker purely
    by looking at the wrong one. The starting row is in the middle for the
    same reason, since a picker at either end cannot move and would pass
    however it behaved.
    """
    window.navigate(win.SETTINGS_PAGE)
    window.show()
    qt_app.processEvents()
    picker = window.theme_picker
    assert picker.isVisible(), "the picker under test is not on screen"
    assert picker.count() >= 3, "there is no room to move in the test"

    middle = picker.count() // 2
    for delta in (-120, 120):
        picker.setCurrentIndex(middle)
        before = picker.currentIndex()
        for _ in range(4):
            QApplication.sendEvent(picker, wheel_event(delta))
        qt_app.processEvents()
        assert picker.currentIndex() == before, (
            f"a {delta:+d} notch moved the theme from row {before} to "
            f"{picker.currentIndex()}")

    # The control must still work; ignoring the wheel is not disabling it.
    picker.setCurrentIndex(0)
    assert picker.currentIndex() == 0, "the picker no longer accepts a choice"


def test_the_dialog_dropdowns_ignore_the_wheel(window, qt_app) -> None:
    """The same hazard sat on every provider, model and relay dropdown.

    Scroll inside a dialog and the provider silently changes, which is
    worse than no scroll at all because the choice is what gets sent.
    """
    from desktop_app.dialogs import AgentDialog, InviteDialog

    dialogs = [AgentDialog(window, "ollama", {}), InviteDialog(window)]
    try:
        combos = [c for d in dialogs for c in d.findChildren(win.Select)]
        assert len(combos) >= 4, f"only {len(combos)} dropdowns were found"
        for combo in combos:
            assert isinstance(combo, win.Select), (
                f"{type(combo).__name__} can still be moved by the wheel")
            # Most of these are filled by the runtime listing providers, so
            # only the ones with room either side can be moved at all.
            if combo.count() < 3:
                continue
            middle = combo.count() // 2
            for delta in (-120, 120):
                combo.setCurrentIndex(middle)
                before = combo.currentIndex()
                for _ in range(3):
                    QApplication.sendEvent(combo, wheel_event(delta))
                qt_app.processEvents()
                assert combo.currentIndex() == before, (
                    f"a {type(combo.parent()).__name__} dropdown moved "
                    f"on a {delta:+d} notch")
    finally:
        for dialog in dialogs:
            dialog.deleteLater()


def test_a_checkbox_paints_nothing_behind_itself(window, qt_app) -> None:
    """It used to inherit the page background and draw a panel.

    The rule for QCheckBox set a colour but no background, so it fell
    through to the shared QWidget rule and painted the window base on top
    of the card it sat in. Sampled rather than read from the stylesheet,
    because "the rule says transparent" and "nothing is painted" are not
    the same claim.
    """
    window.navigate(win.SETTINGS_PAGE)
    window.show()
    end = time.time() + 0.5          # let the page fade finish
    while time.time() < end:
        qt_app.processEvents()
        time.sleep(0.005)

    for name in THEME_NAMES:
        window.apply_theme(name)
        qt_app.processEvents()
        image = window.grab().toImage()
        anchor = window.reduce_motion.mapTo(window, QPoint(0, 0))
        sampled = image.pixelColor(
            anchor.x() + window.reduce_motion.width() - 4,
            anchor.y() + window.reduce_motion.height() // 2).name()

        assert sampled.lower() != color(name, "surface_base").lower(), (
            f"{name}: the checkbox is painting the page base behind itself")
        assert sampled.lower() == color(name, "surface").lower(), (
            f"{name}: expected the card surface behind the checkbox, "
            f"found {sampled}")


def test_a_checkbox_hugs_its_label(window, qt_app) -> None:
    """It used to stretch the full width of the card it sat in.

    A checkbox 857px wide takes any hover or focus background across the
    whole row, which reads as a band rather than as a control.
    """
    window.navigate(win.SETTINGS_PAGE)
    window.show()
    qt_app.processEvents()

    assert window.reduce_motion.width() < 400, (
        f"the checkbox is {window.reduce_motion.width()}px wide, so it is "
        f"filling the card instead of sitting beside its label")



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


def test_window_size_is_never_remembered(qt_app, storage) -> None:
    """No size is written on close, so the app cannot reopen as a window.

    Replaced a test that asserted the opposite. Remembering the size is what
    made the app come back up in a window rather than maximised, and the
    window it opened at was chosen by whatever the last session happened to
    leave behind.
    """
    instance = win.MainWindow(storage)
    try:
        instance.resize(1150, 760)
        instance.close()
        assert "window_size" not in storage.settings
    finally:
        instance.network.shutdown()
        instance.network.wait(10000)


def test_the_window_opens_maximised(qt_app, storage) -> None:
    """The window is maximised, and a stale stored size is ignored."""
    storage.settings["window_size"] = [1150, 760]
    instance = win.MainWindow(storage)
    try:
        instance.showMaximized()
        assert instance.isMaximized()
        # A size left behind by an earlier version must not shrink the
        # window back to a restored rectangle.
        assert instance.size() != win.QSize(1150, 760)
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


def read_settings_resilient(path, attempts=8, pause=0.005):
    """Read settings.json, tolerating a writer part way through a replace.

    On Windows the reader is the side that loses: replacing a file takes
    DELETE access on it and CPython opens files without FILE_SHARE_DELETE,
    so a read issued while the runtime thread is moving the file into place
    is refused with ERROR_ACCESS_DENIED. The writer retries now, which
    settles that half, but this is the half a reader controls and the
    writer's retries cannot help it.

    Only the test needs this. Nothing in the app reads settings.json after
    Storage has loaded it, so there is no product read to make robust.
    """
    for attempt in range(attempts):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(pause * (attempt + 1))


def test_follow_system_persists_immediately(window) -> None:
    """Removing the stored theme must reach the file, not just memory.

    Only the explicit-choice branch saved, so picking "Follow system"
    left the old theme in settings.json until some unrelated write
    happened to flush it, and the choice came back on the next launch.
    """
    miku_index = [c[1] for c in THEME_CHOICES].index(MIKU)
    window.theme_picker.setCurrentIndex(miku_index)
    assert read_settings_resilient(window.storage.path)["theme"] == MIKU

    window.theme_picker.setCurrentIndex(0)

    on_disk = read_settings_resilient(window.storage.path)
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
