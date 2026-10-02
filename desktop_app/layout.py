"""Choosing a window size that actually fits the screen it opens on.

The app used to request a fixed 1330x910. On a 1536x960 display that is
almost the whole usable height once the taskbar is accounted for, and on
a 1366x768 laptop it does not fit at all, so the window either opened
clipped or was shrunk by the window manager.

These are pure functions over plain numbers rather than Qt calls, so the
clamping can be tested against every screen shape that matters without
opening a window.
"""

from __future__ import annotations

# Preferred size on a comfortable display.
PREFERRED_WIDTH = 1330
PREFERRED_HEIGHT = 910

# The window may not claim more than this much of the available area, so
# window decorations and the taskbar always have somewhere to go.
MAX_AVAILABLE_FRACTION = 0.94

# A floor, so the layout is never squeezed into something unusable even on
# a very small display. Below this the sidebar collapses instead.
MIN_WIDTH = 720
MIN_HEIGHT = 480

# Below this logical width the agent sidebar is hidden, because rail plus
# sidebar plus the conversation list leaves no room for content.
SIDEBAR_COLLAPSE_WIDTH = 1000


def available_size(screen_size=None):
    """The usable area of the primary screen, falling back to a default."""
    if screen_size and len(screen_size) == 2:
        width, height = screen_size
        if width > 0 and height > 0:
            return int(width), int(height)
    # Conservative, and what Qt reports when there is no usable screen.
    return 1366, 768


def clamp_size(width, height, screen_size=None):
    """Fit a requested size inside the screen without going below the floor."""
    available_width, available_height = available_size(screen_size)
    ceiling_width = max(MIN_WIDTH, int(available_width * MAX_AVAILABLE_FRACTION))
    ceiling_height = max(MIN_HEIGHT, int(available_height * MAX_AVAILABLE_FRACTION))
    return (
        max(MIN_WIDTH, min(int(width), ceiling_width)),
        max(MIN_HEIGHT, min(int(height), ceiling_height)),
    )


def minimum_size(screen_size=None):
    """The smallest sensible window, never larger than the screen allows."""
    available_width, available_height = available_size(screen_size)
    return (
        min(1010, max(MIN_WIDTH, int(available_width * 0.6))),
        min(690, max(MIN_HEIGHT, int(available_height * 0.6))),
    )


def restored_geometry(stored=None):
    """The size to open at: a remembered one, clamped, else the preferred."""
    if stored:
        try:
            width, height = int(stored[0]), int(stored[1])
            if width > 0 and height > 0:
                return clamp_size(width, height, stored[2] if len(stored) > 2 else None)
        except (TypeError, ValueError, IndexError):
            pass
    return PREFERRED_WIDTH, PREFERRED_HEIGHT


def window_size(stored, settings, screen_size=None):
    """Size the window should open at, honouring a remembered size.

    Args:
        stored: ``(width, height)`` from the previous session, or None.
        settings: The preferences dict, which may hold ``window_size``.
        screen_size: The usable screen area, if known.

    Returns:
        The width and height to request.
    """
    remembered = stored or settings.get("window_size")
    size = restored_geometry(remembered)
    return clamp_size(size[0], size[1], screen_size)


def sidebar_should_collapse(width, screen_size=None):
    """Whether the agent sidebar is dropped at this window width."""
    return int(width) < SIDEBAR_COLLAPSE_WIDTH
