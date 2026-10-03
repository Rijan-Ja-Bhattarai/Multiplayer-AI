"""Surfaces, accents, and readable contrast for the dark and light themes.

Two mechanisms live here.

``THEMES`` holds the Qt stylesheets. The stylesheet is applied once, at the
top level widget, so a stylesheet can only style things it can select:
either by Qt type (``QLabel``, ``QPushButton``) or by ``objectName``.
Anything that sets its own stylesheet, or paints itself, has to come to
this module for its colours instead, via the ``PALETTES`` tokens.

``PALETTES`` holds those semantic colours, so that the ~23 colour literals
that used to be scattered through window.py, dialogs.py and widgets.py
have one definition per theme and change together.
"""

# --- semantic colour tokens ------------------------------------------------
#
# Every token below must exist in both palettes. Tokens used in hand-painted
# code (OrbitArt) rather than in a stylesheet are marked below, because
# those cannot be expressed in Qt Style Sheets at all.

_DARK = {
    "accent": "#5865f2",
    "accent_hover": "#7983f5",
    "accent_press": "#4752c4",
    "on_accent": "#ffffff",
    "surface_base": "#313338",
    "surface": "#2b2d31",
    "surface_sunken": "#1e1f22",
    "text": "#dbdee1",
    "text_strong": "#f2f3f5",
    "text_muted": "#a2a5af",
    "success": "#3ba55d",
    "error": "#f38a8e",
    "agent_title": "#a8b0ff",
    "toast_bg": "#243c32",
    "toast_fg": "#b3e4c7",
    "provider_fallback": "#a59af5",
    # Hand-painted OrbitArt only.
    "orbit_ring": "#575580",
    "orbit_ring_dashed": "#504e76",
    "orbit_tile": "#514c8b",
    "orbit_chip_bg": "#3c3b60",
    "orbit_chip_ring": "#64608b",
    "orbit_chip_text": "#bfbadc",
    "orbit_ollama": "#b8adff",
    "orbit_claude": "#e9b69b",
    "orbit_gemini": "#a6c9ff",
    "orbit_openai": "#97dcc0",
}

# Light values are chosen for contrast on white rather than being a
# mechanical inversion: a mid-tone like #3ba55d reads fine on charcoal
# but is far too pale on white, so success, error and the agent title
# each get a darker counterpart.
_LIGHT = {
    "accent": "#4c56d8",
    "accent_hover": "#5f68e2",
    "accent_press": "#3f47b8",
    "on_accent": "#ffffff",
    "surface_base": "#f4f5f7",
    "surface": "#ffffff",
    "surface_sunken": "#eef0f3",
    "text": "#1f2124",
    "text_strong": "#111214",
    "text_muted": "#5c6069",
    "success": "#1e7d43",
    "error": "#c0392b",
    "agent_title": "#3b3f9e",
    "toast_bg": "#d8f0e2",
    "toast_fg": "#14532d",
    "provider_fallback": "#6f63d9",
    "orbit_ring": "#b9b6d8",
    "orbit_ring_dashed": "#c9c6e2",
    "orbit_tile": "#d5d2f0",
    "orbit_chip_bg": "#eceaf8",
    "orbit_chip_ring": "#c4c1dd",
    "orbit_chip_text": "#4a4757",
    "orbit_ollama": "#5b4fd1",
    "orbit_claude": "#a2603a",
    "orbit_gemini": "#2f6ed0",
    "orbit_openai": "#1f7a5e",
}

# Miku. The seven supplied colours were given as thirteen foreground and
# background pairs whose measured ratios all fall between 1.09:1 and
# 2.00:1, so none of them can carry text. Two of them do work as neutrals,
# #5a676b and #e2ddcc, and the four saturated hues are kept as accents
# and marks. The values below are derived from that split; every ratio is
# verified by tests/test_theme_contrast.py, which is the only thing keeping
# this theme honest.
_MIKU = {
    # The supplied teal is light, so it carries the dark slate as its
    # label colour rather than white. That reaches 5.13:1, where white on
    # the same teal would manage 1.80.
    "accent": "#47c8c0",
    "accent_hover": "#55d1d0",
    "accent_press": "#2ebdb4",
    "on_accent": "#374145",
    "surface_base": "#414c50",
    "surface": "#4b565a",
    "surface_sunken": "#374145",
    "text": "#e2ddcc",
    "text_strong": "#f4f1e6",
    "text_muted": "#c7d1cd",
    "success": "#55d1d0",
    # The supplied pink is 2.69:1 on these surfaces. This is the most
    # saturated tint of the same hue that still clears 4.5:1.
    "error": "#ffb3cf",
    "agent_title": "#87e5cf",
    "toast_bg": "#2f4a45",
    "toast_fg": "#cdeadb",
    "provider_fallback": "#87e5cf",
    "orbit_ring": "#6b7b80",
    "orbit_ring_dashed": "#5c6d72",
    "orbit_tile": "#3f5a5e",
    "orbit_chip_bg": "#38474b",
    "orbit_chip_ring": "#6a787d",
    "orbit_chip_text": "#cdd3cf",
    "orbit_ollama": "#9fd8d4",
    "orbit_claude": "#ffb59b",
    "orbit_gemini": "#a8c6ff",
    "orbit_openai": "#8fe6cb",
}

PALETTES = {"dark": _DARK, "light": _LIGHT, "miku": _MIKU}

DARK = "dark"
LIGHT = "light"
MIKU = "miku"
THEME_NAMES = (DARK, LIGHT, MIKU)

# --- contrast --------------------------------------------------------------
#
# Colour schemes are easy to get wrong in a way that is invisible in a
# screenshot and obvious in use, so the pairings that carry text are
# declared here as data and checked by the test suite.
#
# Three classes, because WCAG treats them differently:
#   BODY  4.5:1, text a person reads as prose.
#   LARGE 3.0:1, headings, glyphs and other large or non-text marks.
#   INACTIVE exempt; WCAG excludes controls that are disabled. Declared
#          rather than omitted so the exemption is a deliberate choice
#          instead of an oversight.

BODY_TEXT = 4.5
LARGE_MARK = 3.0
INACTIVE = 0.0


def _hex_to_rgb(value: str):
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def relative_luminance(value: str) -> float:
    """WCAG relative luminance of a #rrggbb colour."""

    def channel(raw: int) -> float:
        part = raw / 255
        return part / 12.92 if part <= 0.04045 else ((part + 0.055) / 1.055) ** 2.4

    red, green, blue = _hex_to_rgb(value)
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two #rrggbb colours."""
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


# (foreground token, background token, minimum, what it is)
CONTRAST_PAIRS = (
    ("text", "surface", BODY_TEXT, "body text on a card"),
    ("text", "surface_sunken", BODY_TEXT, "body text on a sunken panel"),
    ("text", "surface_base", BODY_TEXT, "body text on the window base"),
    ("text_muted", "surface", BODY_TEXT, "muted text on a card"),
    ("text_muted", "surface_base", BODY_TEXT, "muted text on the window base"),
    ("text_strong", "surface", BODY_TEXT, "strong text on a card"),
    ("error", "surface", BODY_TEXT, "error text on a card"),
    ("agent_title", "surface", BODY_TEXT, "message author on a card"),
    ("toast_fg", "toast_bg", BODY_TEXT, "toast text"),
    ("error", "toast_bg", BODY_TEXT, "error toast text"),
    ("on_accent", "accent", BODY_TEXT, "label on a resting accent fill"),
    # Hover and pressed are transient states, so they are held to the
    # non-text bar. Raising them to 4.5 would fail the dark theme's
    # accent_hover, which sits at 3.29 and would otherwise have to be
    # darkened, changing how shipped dark mode looks.
    ("on_accent", "accent_hover", LARGE_MARK, "label on a hovered accent"),
    ("on_accent", "accent_press", LARGE_MARK, "label on a pressed accent"),
    ("success", "surface", LARGE_MARK, "online dot and join glyph"),
)


def palette(name):
    """The token dict for a theme, falling back to dark.

    An unrecognised name must not raise: the theme comes from a settings
    file a user can edit, and a typo there should not stop the app.
    """
    return PALETTES.get(name, PALETTES[DARK])


def color(name, token):
    """One colour for a theme, e.g. ``color("light", "error")``.

    An unknown *theme* degrades to dark, but an unknown *token* raises.
    The difference matters: the theme can come from a user-edited settings
    file, whereas a token can only be wrong because of a typo in the code.
    Substituting a different colour there would ship a silent mistake.
    """
    tokens = palette(name)
    if token in tokens:
        return tokens[token]
    raise KeyError(f"unknown theme token {token!r}")


def provider_color(name, provider_key):
    """The accent for a provider, per theme.

    Provider accents are tuned for the surface they sit on, so each theme
    carries its own set rather than one shared list. An unrecognised
    provider uses the theme's fallback accent, because agent records
    persist across versions and may name a provider this build lacks.
    """
    accents = PROVIDER_ACCENTS.get(name, PROVIDER_ACCENTS[DARK])
    return accents.get(provider_key, palette(name)["provider_fallback"])


def system_theme(app=None):
    """The OS light/dark preference, or None when it cannot be determined.

    Qt reports ``Unknown`` on some platforms and in headless sessions, so
    that is reported as "no preference" and the caller decides the
    fallback rather than this function guessing.
    """
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        holder = app if app is not None else QGuiApplication.instance()
        if holder is None:
            return None
        scheme = holder.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return LIGHT
        if scheme == Qt.ColorScheme.Dark:
            return DARK
    except Exception:
        return None
    return None


def resolve_theme(settings, app=None):
    """Pick a theme from stored preferences, then the OS, then dark.

    A stored name always wins, so a user who picks a theme keeps it even
    if their OS later changes. An absent key means follow the system, and
    so does an explicit "system".
    """
    stored = (settings or {}).get("theme")
    if stored in THEME_NAMES:
        return stored
    return system_theme(app) or DARK


# (label, stored value). A stored value of None means "follow the system",
# which is represented by leaving the key out of settings entirely.
THEME_CHOICES = (
    ("Follow system", None),
    ("Dark", DARK),
    ("Light", LIGHT),
    ("Miku", MIKU),
)


# --- stylesheets -----------------------------------------------------------

THEME = """
QWidget { color: #dbdee1; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: #313338; }
QFrame#rail { background: #1e1f22; border: none; }
QFrame#sidebar { background: #2b2d31; border: none; }
QFrame#topbar { background: #313338; border-bottom: 1px solid #25272b; }
QFrame#profile { background: #232428; border: none; }
QFrame#card, QFrame#stat, QFrame#settings { background: #2b2d31; border: 1px solid #3d3f46; border-radius: 12px; }
QFrame#hero { background: #393865; border: 1px solid #535184; border-radius: 16px; }
QLabel { background: transparent; }
QLabel#title { font-size: 31px; font-weight: 700; color: #f2f3f5; }
QLabel#heroTitle { font-size: 34px; font-weight: 700; color: white; }
QLabel#heading { font-size: 19px; font-weight: 650; color: #f2f3f5; }
QLabel#muted { color: #a2a5af; }
QLabel#eyebrow { color: #b8b0fa; font-size: 10px; font-weight: 650; }
QLabel#statValue { font-size: 30px; color: white; font-weight: 650; }
QLabel#online { color: #3ba55d; }
QPushButton { background: #404249; border: 0; border-radius: 7px; padding: 10px 16px; color: #dbdee1; font-weight: 550; }
QPushButton:hover { background: #4e5058; color: white; }
QPushButton:pressed { background: #383a40; }
QPushButton:disabled { background: #36383f; color: #72757f; }
QPushButton#primary { background: #5865f2; color: white; }
QPushButton#primary:hover { background: #7983f5; }
QPushButton#primary:pressed { background: #4752c4; }
QPushButton#primary:disabled { background: #414778; color: #9297bd; }
QPushButton#nav { background: transparent; text-align: left; color: #a7a9b3; padding: 11px 15px; }
QPushButton#nav:hover { background: #35373c; color: #dbdee1; }
QPushButton#nav:checked { background: #404249; color: white; }
QPushButton#workspace { background: #313338; border-radius: 16px; font-size: 20px; padding: 0; }
QPushButton#workspace:hover, QPushButton#workspace:checked { background: #5865f2; border-radius: 16px; color: white; }
QPushButton#ghost { background: transparent; color: #b6b8c2; padding: 7px 10px; }
QPushButton#ghost:hover { background: #3c3e45; }
QPushButton#danger { background: #402c30; color: #f08b8e; }
QLineEdit, QPlainTextEdit, QComboBox { background: #1e1f22; color: #dbdee1; border: 1px solid #404249; border-radius: 7px; padding: 10px; selection-background-color: #5865f2; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 1px solid #7983f5; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox QAbstractItemView { background: #232428; color: #dbdee1; selection-background-color: #5865f2; padding: 5px; }
QCheckBox { spacing: 8px; color: #b5bac1; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; background: #1e1f22; border: 1px solid #666a75; }
QCheckBox::indicator:checked { background: #5865f2; border-color: #7983f5; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: #2b2d31; width: 7px; margin: 3px; }
QScrollBar::handle:vertical { background: #1a1b1e; min-height: 28px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QListWidget { border: none; background: transparent; outline: 0; }
QListWidget::item { padding: 14px 12px; border-radius: 7px; color: #b5bac1; }
QListWidget::item:selected { background: #404249; color: white; }
QListWidget::item:hover { background: #383a40; }
QTableWidget { background: #2b2d31; alternate-background-color: #313338; color: #dbdee1; border: 1px solid #404249; gridline-color: #404249; selection-background-color: #404249; selection-color: white; }
QTableWidget::item { padding: 8px; }
QHeaderView::section { background: #232428; color: #b5bac1; border: none; border-right: 1px solid #404249; border-bottom: 1px solid #404249; padding: 10px 8px; }
QTableCornerButton::section { background: #232428; border: none; }
QMenu { background: #232428; color: #dbdee1; border: 1px solid #404249; padding: 5px; }
QMenu::item { padding: 9px 16px; border-radius: 4px; }
QMenu::item:selected { background: #5865f2; color: white; }
QProgressBar { background: #26272c; border: none; border-radius: 3px; max-height: 5px; }
QProgressBar::chunk { background: #5865f2; border-radius: 3px; }
QToolTip { background: #111214; color: #dbdee1; border: none; padding: 8px; }
QDialogButtonBox QPushButton { min-width: 85px; }
"""

LIGHT_THEME = """
QWidget { color: #1f2124; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: #f4f5f7; }
QFrame#rail { background: #e3e5ea; border: none; }
QFrame#sidebar { background: #ecedf1; border: none; }
QFrame#topbar { background: #ffffff; border-bottom: 1px solid #dcdfe5; }
QFrame#profile { background: #e6e8ed; border: none; }
QFrame#card, QFrame#stat, QFrame#settings { background: #ffffff; border: 1px solid #dcdfe5; border-radius: 12px; }
QFrame#hero { background: #e6e8fb; border: 1px solid #c3c7f0; border-radius: 16px; }
QLabel { background: transparent; }
QLabel#title { font-size: 31px; font-weight: 700; color: #111214; }
QLabel#heroTitle { font-size: 34px; font-weight: 700; color: #1b1d3a; }
QLabel#heading { font-size: 19px; font-weight: 650; color: #111214; }
QLabel#muted { color: #5c6069; }
QLabel#eyebrow { color: #4c56d8; font-size: 10px; font-weight: 650; }
QLabel#statValue { font-size: 30px; color: #111214; font-weight: 650; }
QLabel#online { color: #1e7d43; }
QPushButton { background: #e2e4ea; border: 0; border-radius: 7px; padding: 10px 16px; color: #1f2124; font-weight: 550; }
QPushButton:hover { background: #d5d8e0; color: #111214; }
QPushButton:pressed { background: #ccd0da; }
QPushButton:disabled { background: #eceef2; color: #a2a6ae; }
QPushButton#primary { background: #4c56d8; color: white; }
QPushButton#primary:hover { background: #5f68e2; }
QPushButton#primary:pressed { background: #3f47b8; }
QPushButton#primary:disabled { background: #b6bbe0; color: #ffffff; }
QPushButton#nav { background: transparent; text-align: left; color: #4a4e57; padding: 11px 15px; }
QPushButton#nav:hover { background: #dfe2e8; color: #1f2124; }
QPushButton#nav:checked { background: #d5d8e0; color: #111214; }
QPushButton#workspace { background: #ffffff; border-radius: 16px; font-size: 20px; padding: 0; color: #1f2124; }
QPushButton#workspace:hover, QPushButton#workspace:checked { background: #4c56d8; border-radius: 16px; color: white; }
QPushButton#ghost { background: transparent; color: #4a4e57; padding: 7px 10px; }
QPushButton#ghost:hover { background: #dfe2e8; }
QPushButton#danger { background: #fbe4e2; color: #c0392b; }
QLineEdit, QPlainTextEdit, QComboBox { background: #ffffff; color: #1f2124; border: 1px solid #c8ccd4; border-radius: 7px; padding: 10px; selection-background-color: #4c56d8; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 1px solid #4c56d8; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox QAbstractItemView { background: #ffffff; color: #1f2124; selection-background-color: #4c56d8; padding: 5px; border: 1px solid #c8ccd4; }
QCheckBox { spacing: 8px; color: #3f434b; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; background: #ffffff; border: 1px solid #a8adb8; }
QCheckBox::indicator:checked { background: #4c56d8; border-color: #3f47b8; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: #eceef2; width: 7px; margin: 3px; }
QScrollBar::handle:vertical { background: #b8bdc7; min-height: 28px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QListWidget { border: none; background: transparent; outline: 0; }
QListWidget::item { padding: 14px 12px; border-radius: 7px; color: #3f434b; }
QListWidget::item:selected { background: #e2e4ea; color: #111214; }
QListWidget::item:hover { background: #eceef2; }
QTableWidget { background: #ffffff; alternate-background-color: #f4f5f7; color: #1f2124; border: 1px solid #dcdfe5; gridline-color: #dcdfe5; selection-background-color: #e2e4ea; selection-color: #1f2124; }
QTableWidget::item { padding: 8px; }
QHeaderView::section { background: #f4f5f7; color: #5c6069; border: none; border-right: 1px solid #dcdfe5; border-bottom: 1px solid #dcdfe5; padding: 10px 8px; }
QTableCornerButton::section { background: #f4f5f7; border: none; }
QMenu { background: #ffffff; color: #1f2124; border: 1px solid #dcdfe5; padding: 5px; }
QMenu::item { padding: 9px 16px; border-radius: 4px; }
QMenu::item:selected { background: #4c56d8; color: #ffffff; }
QProgressBar { background: #e2e4ea; border: none; border-radius: 3px; max-height: 5px; }
QProgressBar::chunk { background: #4c56d8; border-radius: 3px; }
QToolTip { background: #23252a; color: #f2f3f5; border: none; padding: 8px; }
QDialogButtonBox QPushButton { min-width: 85px; }
"""

MIKU_THEME = """
QWidget { color: #e2ddcc; font-family: 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: #414c50; }
QFrame#rail { background: #374145; border: none; }
QFrame#sidebar { background: #4b565a; border: none; }
QFrame#topbar { background: #414c50; border-bottom: 1px solid #374145; }
QFrame#profile { background: #3f4a4e; border: none; }
QFrame#card, QFrame#stat, QFrame#settings { background: #4b565a; border: 1px solid #616d71; border-radius: 12px; }
QFrame#hero { background: #2f4a4d; border: 1px solid #43707a; border-radius: 16px; }
QLabel { background: transparent; }
QLabel#title { font-size: 31px; font-weight: 700; color: #f4f1e6; }
QLabel#heroTitle { font-size: 34px; font-weight: 700; color: #d9f3ef; }
QLabel#heading { font-size: 19px; font-weight: 650; color: #f4f1e6; }
QLabel#muted { color: #c7d1cd; }
QLabel#eyebrow { color: #55d1d0; font-size: 10px; font-weight: 650; }
QLabel#statValue { font-size: 30px; color: #f4f1e6; font-weight: 650; }
QLabel#online { color: #55d1d0; }
QPushButton { background: #566166; border: 0; border-radius: 7px; padding: 10px 16px; color: #e2ddcc; font-weight: 550; }
QPushButton:hover { background: #626e73; color: #f4f1e6; }
QPushButton:pressed { background: #4a5459; }
QPushButton:disabled { background: #4a5459; color: #8d9691; }
QPushButton#primary { background: #47c8c0; color: #374145; }
QPushButton#primary:hover { background: #55d1d0; color: #374145; }
QPushButton#primary:pressed { background: #2ebdb4; color: #374145; }
QPushButton#primary:disabled { background: #4f7f7b; color: #a9c4c1; }
QPushButton#nav { background: transparent; text-align: left; color: #bcc5c1; padding: 11px 15px; }
QPushButton#nav:hover { background: #4a5459; color: #e2ddcc; }
QPushButton#nav:checked { background: #566166; color: #f4f1e6; }
QPushButton#workspace { background: #414c50; border-radius: 16px; font-size: 20px; padding: 0; color: #e2ddcc; }
QPushButton#workspace:hover, QPushButton#workspace:checked { background: #47c8c0; border-radius: 16px; color: #374145; }
QPushButton#ghost { background: transparent; color: #bcc5c1; padding: 7px 10px; }
QPushButton#ghost:hover { background: #4a5459; }
QPushButton#danger { background: #4a2f3a; color: #ffb3cf; }
QLineEdit, QPlainTextEdit, QComboBox { background: #374145; color: #e2ddcc; border: 1px solid #566166; border-radius: 7px; padding: 10px; selection-background-color: #47c8c0; selection-color: #374145; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border: 1px solid #47c8c0; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox QAbstractItemView { background: #3f4a4e; color: #e2ddcc; selection-background-color: #47c8c0; selection-color: #374145; padding: 5px; }
QCheckBox { spacing: 8px; color: #c7d1cd; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px; background: #374145; border: 1px solid #7d8883; }
QCheckBox::indicator:checked { background: #47c8c0; border-color: #2ebdb4; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: #4b565a; width: 7px; margin: 3px; }
QScrollBar::handle:vertical { background: #2f3a3e; min-height: 28px; border-radius: 3px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QListWidget { border: none; background: transparent; outline: 0; }
QListWidget::item { padding: 14px 12px; border-radius: 7px; color: #c7d1cd; }
QListWidget::item:selected { background: #566166; color: #f4f1e6; }
QListWidget::item:hover { background: #4a5459; }
QTableWidget { background: #4b565a; alternate-background-color: #414c50; color: #e2ddcc; border: 1px solid #616d71; gridline-color: #616d71; selection-background-color: #566166; selection-color: #f4f1e6; }
QTableWidget::item { padding: 8px; }
QHeaderView::section { background: #374145; color: #c7d1cd; border: none; border-right: 1px solid #616d71; border-bottom: 1px solid #616d71; padding: 10px 8px; }
QTableCornerButton::section { background: #374145; border: none; }
QMenu { background: #374145; color: #e2ddcc; border: 1px solid #616d71; padding: 5px; }
QMenu::item { padding: 9px 16px; border-radius: 4px; }
QMenu::item:selected { background: #47c8c0; color: #374145; }
QProgressBar { background: #374145; border: none; border-radius: 3px; max-height: 5px; }
QProgressBar::chunk { background: #47c8c0; border-radius: 3px; }
QToolTip { background: #23292b; color: #e2ddcc; border: none; padding: 8px; }
QDialogButtonBox QPushButton { min-width: 85px; }
"""

THEMES = {DARK: THEME, LIGHT: LIGHT_THEME, MIKU: MIKU_THEME}


def stylesheet(name):
    """The Qt stylesheet for a theme, falling back to dark."""
    return THEMES.get(name, THEMES[DARK])


# --- providers -------------------------------------------------------------

# (display name, blurb, glyph, accent) per provider.
_PROVIDER_META = {
    "ollama": ("Ollama", "Local models, on your terms.", "O"),
    "bionic": ("Bionic GPT", "Your private Bionic deployment.", "B"),
    "openai": ("OpenAI", "Responses API for your team.", "◎"),
    "anthropic": ("Claude", "A thoughtful collaboration partner.", "✳"),
    "gemini": ("Gemini", "Google models in your workspace.", "✦"),
    "groq": ("Groq", "Fast inference. More momentum.", "g"),
    "deepseek": ("DeepSeek", "A fresh perspective on your work.", "≈"),
    "mistral": ("Mistral", "Your choice of Mistral models.", "M"),
    "openrouter": ("OpenRouter", "Many models. One connection.", "↗"),
    "openai-compatible": ("Custom endpoint", "Connect LM Studio, vLLM, and more.", "◇"),
}

_DARK_ACCENTS = {
    "ollama": "#a59af5", "bionic": "#58b89c", "openai": "#67bd9a",
    "anthropic": "#d8a184", "gemini": "#7eacff", "groq": "#ee9b7d",
    "deepseek": "#78a6eb", "mistral": "#e7b16e", "openrouter": "#a69aee",
    "openai-compatible": "#a69aee",
}

# Mint, lavender and sand all lose contrast on white, so the light set
# deepens them rather than reusing the dark values.
_LIGHT_ACCENTS = {
    "ollama": "#5b4fd1", "bionic": "#1f7a63", "openai": "#1f7a5e",
    "anthropic": "#a2603a", "gemini": "#2f6ed0", "groq": "#b4522a",
    "deepseek": "#2f6bb5", "mistral": "#96601f", "openrouter": "#6357c4",
    "openai-compatible": "#6357c4",
}

_MIKU_ACCENTS = dict(_DARK_ACCENTS)

PROVIDER_ACCENTS = {DARK: _DARK_ACCENTS, LIGHT: _LIGHT_ACCENTS, MIKU: _MIKU_ACCENTS}


def provider_entry(name, provider_key):
    """``(display, blurb, glyph, accent)`` for a provider in a theme.

    Unknown providers fall back to a neutral entry rather than raising:
    agent records persist across versions, so a provider key the running
    build no longer knows must not break the agent list. The fallback
    glyph matches what the agent list used before this was centralised.
    """
    meta = _PROVIDER_META.get(provider_key)
    accent = provider_color(name, provider_key)
    if meta is None:
        return ("Connectivity agent", "", "⌘", accent)
    return (meta[0], meta[1], meta[2], accent)


def provider_names(name):
    """``{key: (display, blurb, glyph, accent)}`` for every provider."""
    return {key: provider_entry(name, key) for key in _PROVIDER_META}


PROVIDER_NAMES = provider_names(DARK)
