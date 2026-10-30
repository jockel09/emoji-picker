#!/usr/bin/env python3
"""
Emoji Picker — A fast, KDE-styled emoji picker for Wayland.
Inserts emojis directly via ydotool or dotool. Supports categories, search,
favorites, and recently used emojis.
"""

import sys
import os
import atexit
import json
import configparser
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QScrollArea, QGridLayout, QLabel, QFrame,
    QGraphicsDropShadowEffect, QToolButton
)
from PyQt6.QtCore import (
    Qt, QTimer, QEvent, QObject, QRect, QSize,
    pyqtSignal, pyqtSlot, pyqtClassInfo,
)
from PyQt6.QtGui import (
    QFont, QColor, QKeySequence, QShortcut, QCursor, QPixmap, QImage, QIcon,
    QPainter, QPainterPath,
)

try:
    from PyQt6.QtDBus import QDBus, QDBusConnection, QDBusMessage
except ImportError:
    # Without QtDBus the window can't be placed on Wayland. Everything else
    # works, so this is not fatal — the pin button just stays hidden.
    QDBusConnection = None

import cairo
import gi
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Pango, PangoCairo

from emoji_data import (
    EMOJI_CATEGORIES, ALL_EMOJIS, SKIN_TONE_EMOJIS, GENDER_EMOJIS,
    GENDER_SWAP_EMOJIS,
)
from search_tags import SEARCH_TAGS

LOCALE_DIR = Path(__file__).resolve().parent / "locales"


def load_locale(language="en"):
    locale_file = LOCALE_DIR / f"{language}.json"
    if not locale_file.exists():
        locale_file = LOCALE_DIR / "en.json"
    try:
        with open(locale_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def t(locale, key, **kwargs):
    """Look up a translation key, optionally formatting with named placeholders."""
    text = locale.get(key, key)
    for k, v in kwargs.items():
        text = text.replace(f"{{{k}}}", str(v))
    return text

# Cache rendered emoji pixmaps
_emoji_pixmap_cache = {}


# Width of the vertical scrollbar, and it has to match the "width: 6px" in the
# scroll area's stylesheet below. Its space is reserved even while the bar is
# hidden, so column widths stay the same in views that scroll and views that
# don't, and the emojis don't shift sideways when switching between them.
SCROLLBAR_WIDTH = 6


def render_emoji_pixmap(emoji, size=32):
    """Render an emoji character to a QPixmap using Cairo/Pango for full color support."""
    if (emoji, size) in _emoji_pixmap_cache:
        return _emoji_pixmap_cache[(emoji, size)]

    import io

    # Create a Cairo surface
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    ctx = cairo.Context(surface)

    # Create Pango layout
    layout = PangoCairo.create_layout(ctx)
    font_desc = Pango.FontDescription(f"Noto Color Emoji {int(size * 0.65)}")
    layout.set_font_description(font_desc)
    layout.set_text(emoji, -1)
    layout.set_alignment(Pango.Alignment.CENTER)

    # Center the emoji
    ink_rect, logical_rect = layout.get_pixel_extents()
    x = (size - logical_rect.width) / 2 - logical_rect.x
    y = (size - logical_rect.height) / 2 - logical_rect.y
    ctx.move_to(x, y)

    # Render
    PangoCairo.show_layout(ctx, layout)
    surface.flush()

    # Write to PNG in memory, then load into Qt (avoids byte-order issues)
    buf = io.BytesIO()
    surface.write_to_png(buf)
    png_data = buf.getvalue()

    pixmap = QPixmap()
    pixmap.loadFromData(png_data, "PNG")

    _emoji_pixmap_cache[(emoji, size)] = pixmap
    return pixmap


def render_run_pixmap(text, height=20):
    """Render a run of emojis to a QPixmap as wide as it needs to be.

    render_emoji_pixmap() centers a single emoji in a square; a row of them
    needs the width measured first. Not cached — the content changes with
    every pick."""
    import io

    font_desc = Pango.FontDescription(f"Noto Color Emoji {int(height * 0.65)}")

    def layout_for(ctx):
        layout = PangoCairo.create_layout(ctx)
        layout.set_font_description(font_desc)
        layout.set_text(text, -1)
        return layout

    # Measure on a throwaway surface to get the width
    measure = layout_for(cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)))
    _, logical = measure.get_pixel_extents()
    width = max(1, logical.width + 2)

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    ctx = cairo.Context(surface)
    layout = layout_for(ctx)
    _, logical = layout.get_pixel_extents()
    ctx.move_to(1 - logical.x, (height - logical.height) / 2 - logical.y)
    PangoCairo.show_layout(ctx, layout)
    surface.flush()

    buf = io.BytesIO()
    surface.write_to_png(buf)
    pixmap = QPixmap()
    pixmap.loadFromData(buf.getvalue(), "PNG")
    return pixmap


# The Material Symbols "keep" and "keep_off" glyphs, outlined, 24px. Taken at
# weight 700 rather than the default 400: at the 14 pixels they are drawn here
# the lighter outline all but disappears. Both are drawn on a "0 -960 960 960"
# viewBox, so y runs from -960 at the top down to 0.
MATERIAL_KEEP = (
    "m674-515 80 80v127H543v236L480-9l-63-63v-236H206v-127l80-80v-254h-40v-126"
    "h468v126h-40v254Zm-291 81h194l-29-29v-306H412v306l-29 29Zm97 0Z"
)
MATERIAL_KEEP_OFF = (
    "M714-895v126h-40v321L548-574v-195H412v60l-59-60-107-107v-19h468ZM480-9l-63"
    "-63v-236H206v-127l80-80v-49L13-840l67-66 794 794-67 67-263-263-1 2v234L480"
    "-9Zm-98-425h35l-19-18-16 18Zm98-208Zm-82 190Z"
)

_material_pixmap_cache = {}


def material_path(data):
    """Turn one Material Symbols path into a QPainterPath.

    These glyphs are plain polygons — moves, lines and closes, no curves — so
    reading those few commands is enough, and it saves a dependency on QtSvg,
    which Debian's PyQt6 package doesn't ship."""
    tokens = re.findall(r"[A-Za-z]|-?\d*\.?\d+", data)
    path = QPainterPath()
    x = y = start_x = start_y = 0.0
    command = "M"
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command in ("Z", "z"):
                path.closeSubpath()
                x, y = start_x, start_y
            continue
        if command in ("M", "m", "L", "l"):
            first, second = float(tokens[index]), float(tokens[index + 1])
            index += 2
            x, y = (x + first, y + second) if command.islower() else (first, second)
            if command in ("M", "m"):
                path.moveTo(x, y)
                start_x, start_y = x, y
                # Coordinates following a moveto are a line, not another move
                command = "l" if command == "m" else "L"
            else:
                path.lineTo(x, y)
        elif command in ("H", "h"):
            value = float(tokens[index])
            index += 1
            x = x + value if command == "h" else value
            path.lineTo(x, y)
        elif command in ("V", "v"):
            value = float(tokens[index])
            index += 1
            y = y + value if command == "v" else value
            path.lineTo(x, y)
        else:  # a curve would land here, and none of these glyphs has one
            index += 1
    return path


def render_material_icon(data, size, color):
    """Render a Material Symbols glyph to a QPixmap in the given color.

    Drawn at the screen's own pixel density and told about it, so it comes out
    sharp on a HiDPI screen and stays the size it was asked for on any other.
    Getting that ratio wrong makes QIcon hand the button a pixmap in the wrong
    units, which it then draws too large and clips."""
    screen = QApplication.primaryScreen()
    ratio = screen.devicePixelRatio() if screen else 1.0
    key = (data, size, color, ratio)
    if key in _material_pixmap_cache:
        return _material_pixmap_cache[key]

    edge = max(1, round(size * ratio))
    pixmap = QPixmap(edge, edge)
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(ratio)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(0, edge)
    painter.scale(edge / 960.0, edge / 960.0)
    path = material_path(data)
    path.setFillRule(Qt.FillRule.WindingFill)
    painter.fillPath(path, QColor(color))
    painter.end()

    _material_pixmap_cache[key] = pixmap
    return pixmap


CONFIG_DIR = Path.home() / ".config" / "emoji-picker"
CONFIG_FILE = CONFIG_DIR / "config.json"
KAOMOJI_FILE = CONFIG_DIR / "kaomoji.json"

DEFAULT_KAOMOJI = [
    {"text": "¯\\_(ツ)_/¯",          "name": "shrug"},
    {"text": "(╯°□°）╯︵ ┻━┻",       "name": "table flip"},
    {"text": "┬─┬ノ( º _ ºノ)",      "name": "table unflip"},
    {"text": "( ͡° ͜ʖ ͡°)",          "name": "lenny face"},
    {"text": "(ง'̀-'́)ง",             "name": "fight"},
    {"text": "(づ｡◕‿‿◕｡)づ",         "name": "hug"},
    {"text": "ʕ•ᴥ•ʔ",               "name": "bear"},
    {"text": "(ノ°益°)ノ",            "name": "rage"},
    {"text": "¯\\(°_o)/¯",           "name": "confused"},
    {"text": "(◕‿◕✿)",               "name": "cute"},
    {"text": "凸(¬‿¬)凸",            "name": "middle finger"},
    {"text": "(•̀ᴗ•́)و",             "name": "motivated"},
    {"text": "٩(◕‿◕｡)۶",            "name": "happy"},
    {"text": "(｡•́︿•̀｡)",            "name": "sad"},
    {"text": "(＾▽＾)",               "name": "smile"},
    {"text": "o(≧▽≦)o",             "name": "excited"},
    {"text": "(;一_一)",              "name": "annoyed"},
    {"text": "( •_•)>⌐■-■",         "name": "deal with it"},
    {"text": "(ಠ_ಠ)",               "name": "disapproval"},
    {"text": "(*≧▽≦)",              "name": "joy"},
]


def load_kaomoji():
    if not KAOMOJI_FILE.exists():
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(KAOMOJI_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_KAOMOJI, f, ensure_ascii=False, indent=2)
        return DEFAULT_KAOMOJI
    try:
        with open(KAOMOJI_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return DEFAULT_KAOMOJI

RECENT_FILE = Path.home() / ".local" / "share" / "emoji-picker" / "recent.json"


def load_recent():
    if RECENT_FILE.exists():
        try:
            with open(RECENT_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return []


def save_recent(recent):
    RECENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RECENT_FILE, "w") as f:
        json.dump(recent, f, ensure_ascii=False)

SKIN_TONE_MODIFIERS = [
    ("", "#FBBF24"),
    ("\U0001F3FB", "#FDDBB4"),
    ("\U0001F3FC", "#E8AA80"),
    ("\U0001F3FD", "#C68642"),
    ("\U0001F3FE", "#8B5A2B"),
    ("\U0001F3FF", "#4A2912"),
]

GENDER_MODIFIERS = [
    ("", "○"),                      # neutral
    ("\u200D\u2642\uFE0F", "♂"),    # male:   ZWJ + ♂ + FE0F
    ("\u200D\u2640\uFE0F", "♀"),    # female: ZWJ + ♀ + FE0F
]

# The person that replaces 🧑 in the sequences of GENDER_SWAP_EMOJIS
GENDER_SWAP_PEOPLE = {
    "\u200D\u2642\uFE0F": "👨",
    "\u200D\u2640\uFE0F": "👩",
}

ZWJ = "\u200D"


def apply_modifiers(emoji, tone, gender):
    """Put a skin tone and a gender into one emoji, as far as it takes them.

    Both belong inside the sequence rather than after it. The tone goes on the
    person at the front, and a gender either follows that person as its own
    part or replaces them outright, depending on how the sequence spells it —
    🚶 turns into 🚶‍♀️, while 🧑‍🦯‍➡️ turns into 👩‍🦯‍➡️. Sticking either on the end
    would ruin anything that carries a part behind the person, which is what
    the facing-right people of Emoji 15.1 do."""
    swap = GENDER_SWAP_PEOPLE.get(gender) if emoji in GENDER_SWAP_EMOJIS else None
    takes_tone = bool(tone) and emoji in SKIN_TONE_EMOJIS
    takes_gender = bool(gender) and emoji in GENDER_EMOJIS
    if not takes_tone and not takes_gender and swap is None:
        return emoji

    parts = emoji.split(ZWJ)
    person = swap or parts[0]
    if takes_tone:
        # A tone can't follow the variation selector, it replaces it
        person = person.replace("\uFE0F", "") + tone
    pieces = [person]
    if takes_gender:
        pieces.append(gender.lstrip(ZWJ))
    pieces.extend(parts[1:])
    return ZWJ.join(pieces)

DEFAULT_CONFIG = {
    "favorites": [],
    "max_recent": 36,
    "columns": 9,
    "emoji_size": 28,
    "close_on_select": True,
    "insert_method": "auto",  # "auto", "ydotool", "dotool", "clipboard"
    "paste_shortcut": "ctrl+v",  # "ctrl+v", "ctrl+shift+v" (terminals)
    "skin_tone": "",
    "gender": "",
    "language": "en",
    "kaomoji": False,
    "theme": "auto",  # "auto" (follow KDE accent color), "dark" (built-in)
    "remember_position": False,  # reopen where the window was last pinned
    "window_position": None,  # [x, y] of the pinned position, in screen pixels
}


KDE_GLOBALS = Path.home() / ".config" / "kdeglobals"

# Built-in accent, used when the KDE color can't be read or theme is "dark"
ACCENT_FALLBACK = (82, 148, 226)  # #5294e2

# Current accent as an (r, g, b) tuple — set by init_accent() at startup
ACCENT = ACCENT_FALLBACK


def parse_kde_color(value):
    """Parse a kdeglobals color value ("r,g,b" or "r,g,b,a") into an (r, g, b) tuple."""
    parts = value.split(",")
    if len(parts) < 3:
        return None
    try:
        rgb = tuple(int(p) for p in parts[:3])
    except ValueError:
        return None
    if any(c < 0 or c > 255 for c in rgb):
        return None
    return rgb


def read_kde_accent():
    """Read the accent color from ~/.config/kdeglobals, or None if unavailable.

    [General] AccentColor only exists when the user picked a custom accent (or
    accent-from-wallpaper). With a plain color scheme applied the key is absent,
    and the scheme's selection background is what acts as the accent."""
    parser = configparser.RawConfigParser(strict=False)
    parser.optionxform = str  # kdeglobals keys are case-sensitive
    try:
        parser.read(KDE_GLOBALS, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None

    for section, key in (("General", "AccentColor"),
                         ("Colors:Selection", "BackgroundNormal")):
        try:
            value = parser.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            continue
        rgb = parse_kde_color(value)
        if rgb:
            return rgb
    return None


def init_accent(config):
    """Pick the accent color for this run. Called once before any widget is built."""
    global ACCENT
    if config.get("theme", "auto") == "auto":
        ACCENT = read_kde_accent() or ACCENT_FALLBACK
    else:
        ACCENT = ACCENT_FALLBACK


def accent_hex():
    return "#%02x%02x%02x" % ACCENT


def accent_rgba(alpha):
    r, g, b = ACCENT
    return f"rgba({r}, {g}, {b}, {alpha})"


def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                saved = json.load(f)
            config = DEFAULT_CONFIG.copy()
            config.update(saved)
            # Write back any new keys from DEFAULT_CONFIG that were missing
            new_keys = [k for k in DEFAULT_CONFIG if k not in saved]
            if new_keys:
                with open(CONFIG_FILE, "w") as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
            return config
        except (json.JSONDecodeError, IOError):
            pass
    config = DEFAULT_CONFIG.copy()
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return config


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


SUBPROCESS_ERRORS = (
    subprocess.CalledProcessError,
    FileNotFoundError,
    subprocess.TimeoutExpired,
)


def copy_to_clipboard(emoji):
    """Copy emoji to the Wayland clipboard via wl-copy."""
    try:
        subprocess.run(["wl-copy", "--", emoji], check=True, timeout=2)
        return True
    except SUBPROCESS_ERRORS:
        return False


# The keystroke that pastes, per backend. ydotool speaks Linux keycodes, where
# 29 is left ctrl, 42 left shift and 47 the V key, with :1 pressing and :0
# releasing. dotool takes names.
#
# Ctrl+Shift+V is not "Ctrl+V with shift held", it is a separate shortcut:
# terminals paste with it, browsers and most editors paste without formatting,
# LibreOffice opens its Paste Special dialog, and plenty of Qt and GTK apps
# don't bind it at all. So it can't be the default, only a choice.
PASTE_SHORTCUTS = {
    "ctrl+v": {
        "ydotool": ["29:1", "47:1", "47:0", "29:0"],
        "dotool": "key ctrl+v\n",
    },
    "ctrl+shift+v": {
        "ydotool": ["42:1", "29:1", "47:1", "47:0", "29:0", "42:0"],
        "dotool": "key ctrl+shift+v\n",
    },
}
DEFAULT_PASTE_SHORTCUT = "ctrl+v"


def paste_ydotool(shortcut=DEFAULT_PASTE_SHORTCUT):
    """Paste via ydotool. Needs the ydotoold daemon (systemd user service)."""
    try:
        keys = PASTE_SHORTCUTS[shortcut]["ydotool"]
        subprocess.run(["ydotool", "key", *keys], check=True, timeout=2)
        return True
    except SUBPROCESS_ERRORS:
        return False


def paste_dotool(shortcut=DEFAULT_PASTE_SHORTCUT):
    """Paste via dotool. Daemon-less, reads commands from stdin."""
    try:
        keys = PASTE_SHORTCUTS[shortcut]["dotool"]
        subprocess.run(["dotool"], input=keys, text=True, check=True, timeout=2)
        return True
    except SUBPROCESS_ERRORS:
        return False


# Tried in this order when insert_method is "auto"
PASTE_BACKENDS = {
    "ydotool": paste_ydotool,
    "dotool": paste_dotool,
}


def refocus_delay_ms(method):
    """How long to wait after hiding the picker before injecting Ctrl+V.

    The compositor needs a moment to hand focus back to the window the user
    came from. dotool creates a fresh uinput device on every call and waits
    for the compositor to notice it, so that wait happens anyway inside dotool
    and we only need enough to get our own window off screen first."""
    if method == "clipboard":
        return 0
    # In "auto" mode ydotool is tried first, so it only really runs if it exists
    if method == "dotool" or (method != "ydotool" and not shutil.which("ydotool")):
        return 50
    return 300


def insert_emoji(emoji, method="auto", shortcut=DEFAULT_PASTE_SHORTCUT):
    """Insert emoji into the previously focused window.
    Copies to clipboard, then sends the paste shortcut via the chosen backend.

    Returns True if the paste was simulated, False if the emoji only ended up
    on the clipboard, which the user can still paste by hand."""

    if not copy_to_clipboard(emoji):
        return False

    if method == "clipboard":
        return True

    # A typo in config.json shouldn't stop insertion from working
    if shortcut not in PASTE_SHORTCUTS:
        shortcut = DEFAULT_PASTE_SHORTCUT

    paste = PASTE_BACKENDS.get(method)
    if paste is not None:
        return paste(shortcut)

    # "auto" (and any unknown value): first backend that works wins
    for paste in PASTE_BACKENDS.values():
        if paste(shortcut):
            return True
    return False


# A Wayland client can't put its own window anywhere: move() is silently
# dropped and pos() reports a coordinate Qt invented. KWin will do it on
# request though, so the position is read and written through a throwaway
# script loaded into KWin's script engine. The script finds our window by PID
# and reports the result back over D-Bus, which is why every answer arrives
# asynchronously rather than as a return value.

KWIN_SERVICE = "org.kde.KWin"
KWIN_SCRIPTING_PATH = "/Scripting"
KWIN_SCRIPTING_IFACE = "org.kde.kwin.Scripting"
KWIN_SCRIPT_IFACE = "org.kde.kwin.Script"

PLACER_PATH = "/geometry"
PLACER_IFACE = "org.kde.emojipicker.Geometry"

# Reports "<x> <y>" once it has our window, or "none" when KWin doesn't know
# about it (yet) — a window takes a moment to reach the compositor after
# show(). Anything other than a position has to be a word rather than an empty
# string, which KWin's callDBus quietly drops instead of passing on.
KWIN_SCRIPT = """
var target = null;
try {
    var windows = workspace.windowList();
    for (var i = 0; i < windows.length; i++) {
        if (windows[i].pid === %(pid)d && windows[i].normalWindow) {
            target = windows[i];
            break;
        }
    }
    if (target !== null) {
        %(move)s
        var geometry = target.frameGeometry;
        callDBus("%(service)s", "%(path)s", "%(iface)s", "Report",
                 Math.round(geometry.x) + " " + Math.round(geometry.y));
    } else {
        callDBus("%(service)s", "%(path)s", "%(iface)s", "Report", "none");
    }
} catch (error) {
    callDBus("%(service)s", "%(path)s", "%(iface)s", "Report", "none");
}
"""

KWIN_MOVE = """var previous = target.frameGeometry;
        target.frameGeometry = {
            x: %d, y: %d, width: previous.width, height: previous.height
        };"""

# How many times a script may be handed to KWin before a usable id comes back
LOAD_ATTEMPTS = 8

# How often and for how long a restore keeps asking KWin to place the window.
# KWin needs a few goes before it knows about a window that was just shown.
RESTORE_TRIES = 6
RESTORE_INTERVAL_MS = 80
RESTORE_TIMEOUT_MS = 700


@pyqtClassInfo("D-Bus Interface", PLACER_IFACE)
class KWinPlacer(QObject):
    """Reads and sets the window position by way of KWin's scripting service."""

    reported = pyqtSignal(int, int)  # position after the script ran
    lost = pyqtSignal()  # KWin has no window of ours

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bus = QDBusConnection.sessionBus()
        self._script_count = 0
        # KWin reads a script file when it runs it, not when it is handed the
        # path, so the files have to outlive the calls — they go when we do.
        self._script_dir = tempfile.mkdtemp(prefix="emoji-picker-")
        atexit.register(shutil.rmtree, self._script_dir, ignore_errors=True)
        # Scripts KWin still holds. They are kept for as long as the picker
        # runs and handed back in one go when it quits: KWin hands out script
        # ids by the size of its script list, so unloading one mid-session
        # makes the next id collide with a script that is still loaded, and
        # every call after that runs the wrong script.
        self._loaded = set()
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.unload_all)
        # KWin calls back into this name, so it has to be unique per process
        service = "org.kde.emojipicker.p%d" % os.getpid()
        self._ready = bool(
            self._bus.registerService(service)
            and self._bus.registerObject(
                PLACER_PATH, self, QDBusConnection.RegisterOption.ExportAllSlots
            )
        )
        self._service = service

    @staticmethod
    def usable():
        """True when there is a KWin on the session bus to talk to."""
        if QDBusConnection is None:
            return False
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return False
        interface = bus.interface()
        if interface is None:
            return False
        return bool(interface.isServiceRegistered(KWIN_SERVICE).value())

    def read_position(self):
        return self._run("")

    def move_to(self, x, y):
        return self._run(KWIN_MOVE % (x, y))

    def _run(self, move):
        if not self._ready:
            return False
        script = KWIN_SCRIPT % {
            "pid": os.getpid(),
            "move": move,
            "service": self._service,
            "path": PLACER_PATH,
            "iface": PLACER_IFACE,
        }
        self._script_count += 1
        name = "emoji-picker-%d-%d" % (os.getpid(), self._script_count)
        path = os.path.join(self._script_dir, "%s.js" % name)
        try:
            with open(path, "w") as script_file:
                script_file.write(script)
        except IOError:
            return False

        script_id = self._load(path, name)
        if script_id is None:
            return False
        # Fire and forget: KWin runs the script inside this call, and a
        # blocking wait here would swallow the answer the script sends back.
        self._send("%s/Script%d" % (KWIN_SCRIPTING_PATH, script_id),
                   KWIN_SCRIPT_IFACE, "run", [])
        return True

    def _load(self, path, name):
        """Hand a script to KWin and return an id whose object path is really
        ours, or None.

        KWin numbers a new script by the length of its script list, so once
        anything has been unloaded the number can land on a script that is
        still there. The path then stays with that older script and running it
        does nothing of ours, without a word of complaint. Every load moves the
        number along, so keep loading until it lands somewhere free."""
        for attempt in range(LOAD_ATTEMPTS):
            taken = self._script_ids()
            unique = "%s-%d" % (name, attempt)
            script_id = self._call(KWIN_SCRIPTING_PATH, KWIN_SCRIPTING_IFACE,
                                   "loadScript", [path, unique])
            if script_id is None:
                return None
            self._loaded.add(unique)
            if script_id not in taken:
                return script_id
        return None

    def _script_ids(self):
        """The ids of the scripts KWin currently has an object path for."""
        listing = self._call(KWIN_SCRIPTING_PATH, "org.freedesktop.DBus.Introspectable",
                             "Introspect", [])
        if not isinstance(listing, str):
            return set()
        return {int(found) for found in re.findall(r'node name="Script(\d+)"', listing)}

    def unload_all(self):
        """Hand back every script KWin still holds for us. Blocking, because
        this runs while the picker is on its way out and a queued call would
        never make it onto the bus."""
        for name in sorted(self._loaded):
            self._call(KWIN_SCRIPTING_PATH, KWIN_SCRIPTING_IFACE,
                       "unloadScript", [name], timeout=300)
        self._loaded.clear()

    def _call(self, path, interface, method, arguments, timeout=1000):
        """Blocking call into KWin, for when the answer is the point. Returns
        the first return value, or None if the call failed."""
        reply = self._bus.call(self._message(path, interface, method, arguments),
                               QDBus.CallMode.Block, timeout)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage:
            return None
        values = reply.arguments()
        return values[0] if values else True

    def _send(self, path, interface, method, arguments):
        """Call into KWin without waiting around for a reply."""
        self._bus.call(self._message(path, interface, method, arguments),
                       QDBus.CallMode.NoBlock)

    @staticmethod
    def _message(path, interface, method, arguments):
        message = QDBusMessage.createMethodCall(KWIN_SERVICE, path, interface, method)
        message.setArguments(arguments)
        return message

    @pyqtSlot(str)
    def Report(self, payload):
        parts = payload.split()
        try:
            x, y = int(parts[0]), int(parts[1])
        except (IndexError, ValueError):
            self.lost.emit()
            return
        self.reported.emit(x, y)


class SkinToneButton(QPushButton):
    """A small circular button for skin tone selection."""
    def __init__(self, tone, color, tooltip, parent=None):
        super().__init__(parent)
        self.tone = tone
        self._color = color
        self.setFixedSize(18, 18)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tooltip)
        self._refresh_style()

    def setChecked(self, checked):
        super().setChecked(checked)
        self._refresh_style()

    def _refresh_style(self):
        border = "2px solid #dbdee1" if self.isChecked() else "2px solid transparent"
        self.setStyleSheet(f"""
            QPushButton {{
                background: {self._color};
                border: {border};
                border-radius: 9px;
            }}
            QPushButton:hover {{
                border: 2px solid rgba(255, 255, 255, 0.5);
            }}
        """)


class GenderButton(QPushButton):
    """A small button for gender selection."""
    def __init__(self, gender, symbol, tooltip, parent=None):
        super().__init__(symbol, parent)
        self.gender = gender
        self.setFixedSize(20, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tooltip)
        self._refresh_style()

    def setChecked(self, checked):
        super().setChecked(checked)
        self._refresh_style()

    def _refresh_style(self):
        if self.isChecked():
            bg, border = accent_rgba(0.3), f"2px solid {accent_hex()}"
        else:
            bg, border = "rgba(255,255,255,0.06)", "2px solid transparent"
        self.setStyleSheet(f"""
            QPushButton {{
                background: {bg};
                border: {border};
                border-radius: 10px;
                color: #dbdee1;
                font-size: 10px;
                padding: 0px;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.12);
                border: 2px solid rgba(255, 255, 255, 0.3);
            }}
        """)


class PinButton(QPushButton):
    """A small pin that toggles whether the window position is remembered.

    Unlike the skin tone and gender buttons this one stands alone, so it can't
    lean on its neighbours to show which state it is in. It says so twice
    instead: a struck-through pin and no background when the position isn't
    kept, an upright pin on the accent color when it is."""

    def __init__(self, tooltip, parent=None):
        super().__init__(parent)
        self.setFixedSize(20, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip(tooltip)
        self.setIconSize(QSize(14, 14))
        # setChecked() isn't virtual, so a click would change the state behind
        # the style's back. The signal catches both that and setChecked().
        self.toggled.connect(self._refresh_style)
        self._refresh_style()

    def _refresh_style(self):
        if self.isChecked():
            icon, color = MATERIAL_KEEP, "#dbdee1"
            background, border = accent_rgba(0.3), f"2px solid {accent_hex()}"
        else:
            icon, color = MATERIAL_KEEP_OFF, "#8b8d93"
            background, border = "transparent", "2px solid transparent"
        self.setIcon(QIcon(render_material_icon(icon, 14, color)))
        self.setStyleSheet(f"""
            QPushButton {{
                background: {background};
                border: {border};
                border-radius: 10px;
                padding: 0px;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.12);
            }}
        """)


class EmojiButton(QToolButton):
    """A clickable emoji button with color rendering."""
    emoji_selected = pyqtSignal(str)
    emoji_fav_toggle = pyqtSignal(str)
    emoji_delete = pyqtSignal(str)
    emoji_undo = pyqtSignal()

    def __init__(self, emoji, name="", kaomoji=False, parent=None):
        super().__init__(parent)
        self.emoji = emoji
        self.emoji_name = name
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{emoji}  {name}" if kaomoji else name)

        if kaomoji == "list":
            # Full-width list style (Kaomoji tab)
            self.setFixedHeight(34)
            self.setMinimumWidth(200)
            self.setText(emoji)
            self.setFont(QFont("Monospace", 9))
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        elif kaomoji == "grid":
            # Compact grid style (Recents/Favorites)
            self.setFixedSize(42, 42)
            self.setText(emoji[:6] + "…" if len(emoji) > 6 else emoji)
            self.setFont(QFont("Monospace", 7))
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        else:
            self.setFixedSize(42, 42)
            # Render color emoji via Cairo/Pango
            pixmap = render_emoji_pixmap(emoji, 32)
            self.setIcon(QIcon(pixmap))
            self.setIconSize(QSize(32, 32))

        self.setStyleSheet("""
            QToolButton {
                border: 1px solid transparent;
                border-radius: 6px;
                background: transparent;
                padding: 0px;
            }
            QToolButton:hover {
                background: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.12);
            }
            QToolButton:pressed {
                background: rgba(255, 255, 255, 0.15);
            }
            QToolButton:focus {
                background: rgba(255, 255, 255, 0.10);
                border: 1px solid rgba(255, 255, 255, 0.35);
            }
        """)
        self.clicked.connect(lambda: self.emoji_selected.emit(self.emoji))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.emoji_selected.emit(self.emoji)
        elif event.key() == Qt.Key.Key_Delete:
            self.emoji_delete.emit(self.emoji)
        elif event.key() == Qt.Key.Key_F:
            self.emoji_fav_toggle.emit(self.emoji)
        elif event.key() == Qt.Key.Key_Backspace:
            self.emoji_undo.emit()
        else:
            super().keyPressEvent(event)

    def contextMenuEvent(self, event):
        self.emoji_fav_toggle.emit(self.emoji)


class CategoryButton(QPushButton):
    """A tab-like category button."""
    def __init__(self, icon_emoji, label, parent=None):
        super().__init__(parent)
        self.label = label
        self.setToolTip(label)
        self.setFixedSize(36, 36)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setCheckable(True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # Render color emoji icon
        pixmap = render_emoji_pixmap(icon_emoji, 24)
        self.setIcon(QIcon(pixmap))
        self.setIconSize(QSize(24, 24))

        self._update_style()

    def _update_style(self):
        self.setStyleSheet(f"""
            QPushButton {{
                border: none;
                border-radius: 6px;
                background: transparent;
                padding: 2px;
            }}
            QPushButton:hover {{
                background: rgba(255, 255, 255, 0.08);
            }}
            QPushButton:checked {{
                background: rgba(255, 255, 255, 0.12);
                border-bottom: 2px solid {accent_hex()};
            }}
        """)


class EmojiGrid(QWidget):
    """A grid of emoji buttons."""
    emoji_selected = pyqtSignal(str)
    emoji_fav_toggle = pyqtSignal(str)
    emoji_delete = pyqtSignal(str)
    emoji_move = pyqtSignal(str, int)  # emoji, direction (-1 or +1)
    emoji_undo = pyqtSignal()

    def __init__(self, columns=9, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.layout_ = QGridLayout(self)
        self.layout_.setSpacing(2)
        self.layout_.setContentsMargins(4, 4, 4, 4)
        self.buttons = []
        self._cols = columns
        self._last_row_stretch = 0
        self._last_col_stretch = 0
        self.scroll_area = None

    def set_emojis(self, emojis, kaomoji=False, kaomoji_set=None):
        """emojis: list of (emoji, name) tuples"""
        # Remove all items from layout (releases grid cells, not just widget references)
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # Reset row and column stretches from previous call
        for r in range(self._last_row_stretch + 1):
            self.layout_.setRowStretch(r, 0)
        for c in range(self._last_col_stretch):
            self.layout_.setColumnStretch(c, 0)
        self.buttons.clear()

        cols = 1 if kaomoji else self.columns
        self._cols = cols
        row, col = 0, 0
        for emoji, name in emojis:
            is_kao = kaomoji or bool(kaomoji_set and emoji in kaomoji_set)
            kao_mode = ("list" if kaomoji else "grid") if is_kao else False
            btn = EmojiButton(emoji, name, kaomoji=kao_mode)
            btn.emoji_selected.connect(self.emoji_selected.emit)
            btn.emoji_fav_toggle.connect(self.emoji_fav_toggle.emit)
            btn.emoji_delete.connect(self.emoji_delete.emit)
            btn.emoji_undo.connect(self.emoji_undo.emit)
            btn.installEventFilter(self)
            if is_kao and not kaomoji:
                self.layout_.addWidget(btn, row, col)
                col += 1
                if col >= self.columns:
                    col = 0
                    row += 1
            else:
                self.layout_.addWidget(btn, row, col)
                col += 1
                if col >= cols:
                    col = 0
                    row += 1
            self.buttons.append(btn)

        # Give every column a share, including the ones left empty by a short
        # result set. QGridLayout only sizes columns that hold a widget, so three
        # results would otherwise split the width three ways and sit far apart,
        # while ten results split it nine ways. Same emoji, different position.
        for c in range(cols):
            self.layout_.setColumnStretch(c, 1)
        self._last_col_stretch = cols

        # Add stretch at the bottom so rows don't spread across the scroll area
        self.layout_.setRowStretch(row + 1, 1)
        self._last_row_stretch = row + 1

    def _focus_and_scroll(self, btn):
        btn.setFocus()
        if self.scroll_area:
            QTimer.singleShot(0, lambda: self.scroll_area.ensureWidgetVisible(btn))

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress and obj in self.buttons:
            idx = self.buttons.index(obj)
            key = event.key()
            alt = event.modifiers() & Qt.KeyboardModifier.AltModifier
            if alt and key == Qt.Key.Key_Left:
                self.emoji_move.emit(obj.emoji, -1)
                return True
            elif alt and key == Qt.Key.Key_Right:
                self.emoji_move.emit(obj.emoji, 1)
                return True
            elif key == Qt.Key.Key_Left and idx > 0:
                self._focus_and_scroll(self.buttons[idx - 1])
                return True
            elif key == Qt.Key.Key_Right and idx < len(self.buttons) - 1:
                self._focus_and_scroll(self.buttons[idx + 1])
                return True
            elif key == Qt.Key.Key_Up:
                new_idx = idx - self._cols
                if new_idx >= 0:
                    self._focus_and_scroll(self.buttons[new_idx])
                return True
            elif key == Qt.Key.Key_Down:
                new_idx = idx + self._cols
                if new_idx < len(self.buttons):
                    self._focus_and_scroll(self.buttons[new_idx])
                return True
        return super().eventFilter(obj, event)

    def focus_button(self, idx):
        """Set focus to button at idx, clamped to valid range."""
        if self.buttons:
            self.buttons[max(0, min(idx, len(self.buttons) - 1))].setFocus()


class EmojiPicker(QWidget):
    """Main emoji picker window."""

    def __init__(self):
        super().__init__()
        self.config = load_config()
        init_accent(self.config)
        self.locale = load_locale(self.config.get("language", "en"))
        self.current_category = None
        self._inserting = False
        # Emojis picked so far when close_on_select is off — inserted as one
        # string when the picker closes
        self._pending = []
        self._flushing = False
        # True once something was picked with Shift held. Releasing Shift only
        # inserts if this is set, so a Shift press for a capital letter in the
        # search field can't end a collection that Shift never started.
        self._shift_collecting = False
        # Status text to restore when the collected emojis are taken back
        self._base_status = ""

        # Window placement. X11 can do it with plain move()/pos(); Wayland
        # can't and has to go through KWin.
        self._native_placement = QApplication.instance().platformName() != "wayland"
        self._placer = None
        if not self._native_placement and KWinPlacer.usable():
            self._placer = KWinPlacer(self)
            self._placer.reported.connect(self._on_position)
            self._placer.lost.connect(self._on_position_lost)
        self._pinned = bool(self.config.get("remember_position", False))
        self._moving = False
        self._restore_target = None
        self._restore_tries = 0

        # Nothing tells a client when a compositor-driven drag has ended, so
        # after one the position is polled until it stops changing.
        self._position_timer = QTimer(self)
        self._position_timer.setInterval(400)
        self._position_timer.timeout.connect(self._poll_position)
        self._poll_ticks = 0
        self._poll_last = None
        self._poll_stable = 0

        # Debounce timer for search
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._do_search)
        self._search_text = ""

        self.setup_ui()
        self.setup_shortcuts()
        self.show_category("recent")

        if self._pinned and self.config.get("window_position") and self._placer:
            # KWin can only move a window that already exists, so a restore
            # would show the window at its default spot for a frame or two
            # first. Keep the contents hidden until it has landed — the window
            # itself has a translucent background and draws nothing on its own.
            # (setWindowOpacity() would be the obvious way and does nothing on
            # Wayland.)
            self.container.hide()

    def setup_ui(self):
        self.setWindowTitle("Emoji Picker")
        self.setFixedSize(460, 480)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # Main container with rounded corners
        self.container = QFrame(self)
        self.container.setObjectName("container")
        self.container.setStyleSheet("""
            #container {
                background: #2b2d31;
                border: 1px solid #3f4147;
                border-radius: 12px;
            }
        """)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(8, 8, 8, 8)
        outer_layout.addWidget(self.container)

        # Shadow effect
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(20)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(0, 4)
        self.container.setGraphicsEffect(shadow)

        # Container layout
        layout = QVBoxLayout(self.container)
        layout.setSpacing(6)
        layout.setContentsMargins(10, 10, 10, 10)

        # Search bar
        self.search = QLineEdit()
        self.search.setPlaceholderText(t(self.locale, "search_placeholder"))
        self.search.setClearButtonEnabled(True)
        self.search.installEventFilter(self)
        self.search.setStyleSheet(f"""
            QLineEdit {{
                background: #1e1f22;
                border: 1px solid #3f4147;
                border-radius: 8px;
                padding: 8px 12px;
                color: #dbdee1;
                font-size: 14px;
                selection-background-color: {accent_hex()};
            }}
            QLineEdit:focus {{
                border: 1px solid {accent_hex()};
            }}
        """)
        self.search.textChanged.connect(self.on_search)

        search_row = QHBoxLayout()
        search_row.setSpacing(4)
        search_row.addWidget(self.search)

        self.skin_tone_buttons = []
        current_tone = self.config.get("skin_tone", "")
        skin_tooltip_keys = ["skin_default", "skin_light", "skin_medium_light", "skin_medium", "skin_medium_dark", "skin_dark"]
        for (tone, color), key in zip(SKIN_TONE_MODIFIERS, skin_tooltip_keys):
            btn = SkinToneButton(tone, color, t(self.locale, key))
            btn.setChecked(tone == current_tone)
            btn.clicked.connect(lambda checked, tone=tone: self.set_skin_tone(tone))
            search_row.addWidget(btn)
            self.skin_tone_buttons.append(btn)

        search_row.addSpacing(6)

        self.gender_buttons = []
        current_gender = self.config.get("gender", "")
        gender_tooltip_keys = ["gender_neutral", "gender_male", "gender_female"]
        for (gender, symbol), key in zip(GENDER_MODIFIERS, gender_tooltip_keys):
            btn = GenderButton(gender, symbol, t(self.locale, key))
            btn.setChecked(gender == current_gender)
            btn.clicked.connect(lambda checked, gender=gender: self.set_gender(gender))
            search_row.addWidget(btn)
            self.gender_buttons.append(btn)

        self.pin_button = None
        if self._positioning_available():
            search_row.addSpacing(6)
            self.pin_button = PinButton(self._pin_tooltip())
            self.pin_button.setChecked(self._pinned)
            self.pin_button.clicked.connect(self.toggle_pin)
            search_row.addWidget(self.pin_button)

        layout.addLayout(search_row)

        # Category bar
        cat_layout = QHBoxLayout()
        cat_layout.setSpacing(2)

        self.category_buttons = {}
        categories = [
            ("⏱️", "recent",     t(self.locale, "cat_recent")),
            ("⭐", "favorites",  t(self.locale, "cat_favorites")),
            ("😀", "smileys",    t(self.locale, "cat_smileys")),
            ("👋", "people",     t(self.locale, "cat_people")),
            ("🐻", "animals",    t(self.locale, "cat_animals")),
            ("🍔", "food",       t(self.locale, "cat_food")),
            ("✈️", "travel",     t(self.locale, "cat_travel")),
            ("⚽", "activities", t(self.locale, "cat_activities")),
            ("💡", "objects",    t(self.locale, "cat_objects")),
            ("🔣", "symbols",    t(self.locale, "cat_symbols")),
            ("🏁", "flags",      t(self.locale, "cat_flags")),
        ]
        if self.config.get("kaomoji", True):
            categories.append(("ツ", "kaomoji", t(self.locale, "cat_kaomoji")))

        self.category_order = [key for _, key, _ in categories]
        for icon, key, label in categories:
            btn = CategoryButton(icon, label)
            btn.clicked.connect(lambda checked, k=key: self.show_category(k))
            cat_layout.addWidget(btn)
            self.category_buttons[key] = btn

        cat_layout.addStretch()
        layout.addLayout(cat_layout)

        QShortcut(QKeySequence("Ctrl+Left"), self).activated.connect(
            lambda: self._switch_category(-1)
        )
        QShortcut(QKeySequence("Ctrl+Right"), self).activated.connect(
            lambda: self._switch_category(1)
        )

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #3f4147; max-height: 1px;")
        layout.addWidget(sep)

        # Emoji scroll area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
            }
            /* Without a dark Qt palette (e.g. non-KDE Wayland compositors) the
               scroll viewport paints the light platform palette behind the
               dark #container. Keep the viewport and its children transparent
               so the container's dark background shows through. */
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.15);
                border-radius: 3px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.25);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
        """)

        self.emoji_grid = EmojiGrid(columns=self.config.get("columns", 9))
        self.emoji_grid.emoji_selected.connect(self.on_emoji_selected)
        self.emoji_grid.emoji_fav_toggle.connect(self.on_fav_toggle)
        self.emoji_grid.emoji_delete.connect(self.on_remove_recent)
        self.emoji_grid.emoji_move.connect(self.on_move_favorite)
        self.emoji_grid.emoji_undo.connect(self._undo_collected)
        self.scroll.setWidget(self.emoji_grid)
        self.emoji_grid.scroll_area = self.scroll
        layout.addWidget(self.scroll)

        # Status bar — collected emojis are rendered as a pixmap, because a
        # QLabel would fall back to Qt's text engine and show boxes
        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(6)

        self.collect_preview = QLabel()
        self.collect_preview.setStyleSheet("QLabel { padding: 2px 0px 2px 4px; }")
        self.collect_preview.hide()
        status_row.addWidget(self.collect_preview)

        self.status = QLabel("")
        self.status.setStyleSheet("""
            QLabel {
                color: #8b8d93;
                font-size: 11px;
                padding: 2px 4px;
            }
        """)
        status_row.addWidget(self.status)
        status_row.addStretch()
        layout.addLayout(status_row)

        # See eventFilter — Shift releases have to be caught wherever focus is
        QApplication.instance().installEventFilter(self)

        # Center on screen
        self.center_on_screen()

    def setup_shortcuts(self):
        QShortcut(QKeySequence("Escape"), self, self.close)
        QShortcut(QKeySequence("Ctrl+F"), self, lambda: self.search.setFocus())

    def center_on_screen(self):
        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            x = (geo.width() - self.width()) // 2 + geo.x()
            y = (geo.height() - self.height()) // 2 + geo.y()
            self.move(x, y)

    def _positioning_available(self):
        """Whether this session lets the window be put where we want it."""
        return self._native_placement or self._placer is not None

    def _pin_tooltip(self):
        return t(self.locale, "pin_forget" if self._pinned else "pin_remember")

    def toggle_pin(self, checked):
        """Start or stop remembering where the window sits."""
        self._pinned = checked
        self.config["remember_position"] = checked
        if checked:
            # Take down where the window is now. On Wayland the answer comes
            # back later, through _on_position, and saves itself there.
            self.status.setText(t(self.locale, "status_pinned"))
            self._watch_position()
        else:
            self.config["window_position"] = None
            self.status.setText(t(self.locale, "status_unpinned"))
        save_config(self.config)
        self.pin_button.setToolTip(self._pin_tooltip())

    def mousePressEvent(self, event):
        """Drag the window by any empty spot of its chrome. A Wayland client
        can't move itself, so the compositor is asked to run the drag."""
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.windowHandle()
            if handle is not None and handle.startSystemMove():
                # Should the compositor deactivate the window to run the drag,
                # that must not read as a click outside. It would happen right
                # at the start, so the guard is only held for a moment —
                # holding it longer would keep a plain click on the background
                # from ever letting the window close again.
                self._moving = True
                QTimer.singleShot(400, self._clear_moving)
                self._watch_position()
                event.accept()
                return
        super().mousePressEvent(event)

    def _clear_moving(self):
        self._moving = False

    def _watch_position(self):
        """Follow the window until it settles, saving the position as it goes.

        A compositor-driven drag reports nothing back to the client: no move
        events, no release, no end. Polling is the only way to find out where
        the window was dropped."""
        if not self._pinned or not self._positioning_available():
            return
        self._poll_ticks = 0
        self._poll_last = None
        self._poll_stable = 0
        self._position_timer.start()
        self._read_position()

    def _poll_position(self):
        self._poll_ticks += 1
        if self._poll_ticks > 25:  # ten seconds of dragging, then give up
            self._position_timer.stop()
            return
        self._read_position()

    def _read_position(self):
        """Ask where the window is. X11 answers at once, Wayland answers late."""
        if self._native_placement:
            point = self.pos()
            self._on_position(point.x(), point.y())
        elif self._placer is not None:
            self._placer.read_position()

    def _on_position(self, x, y):
        """A position came back, either from KWin or straight from Qt."""
        self._finish_restore()
        if self._position_timer.isActive():
            if (x, y) == self._poll_last:
                self._poll_stable += 1
                if self._poll_stable >= 2:
                    self._position_timer.stop()
            else:
                self._poll_last = (x, y)
                self._poll_stable = 0
        if self._pinned and self.config.get("window_position") != [x, y]:
            self.config["window_position"] = [x, y]
            save_config(self.config)

    def _on_position_lost(self):
        """KWin has no window of ours. Straight after show() that only means it
        hasn't reached the compositor yet, so a restore tries again."""
        if self._restore_target is None:
            return
        if self._restore_tries < RESTORE_TRIES:
            QTimer.singleShot(30, self._attempt_restore)
        else:
            self._finish_restore()

    def _attempt_restore(self):
        if self._restore_target is None:
            return
        self._restore_tries += 1
        attempt = self._restore_tries
        self._placer.move_to(*self._restore_target)
        # KWin does now and then swallow a script without a word, so don't sit
        # waiting for an answer that may never come.
        QTimer.singleShot(RESTORE_INTERVAL_MS, lambda: self._restore_again(attempt))

    def _restore_again(self, attempt):
        # Only the most recent attempt may start the next one, otherwise a
        # timeout and a late answer from KWin would both fire off a retry
        if (self._restore_target is not None
                and attempt == self._restore_tries
                and attempt < RESTORE_TRIES):
            self._attempt_restore()

    def _restore_position(self):
        """Put the window back where it was pinned. Returns False when there is
        nothing to restore and the caller should center it instead."""
        saved = self.config.get("window_position")
        if not self._pinned or not saved or not self._positioning_available():
            return False
        try:
            x, y = self._clamp_to_screen(int(saved[0]), int(saved[1]))
        except (TypeError, ValueError, IndexError):
            return False
        if self._native_placement:
            self.move(x, y)
            return True
        self._restore_target = (x, y)
        self._restore_tries = 0
        self._attempt_restore()
        # However the restore turns out, the contents must not stay hidden
        QTimer.singleShot(RESTORE_TIMEOUT_MS, self._finish_restore)
        return True

    def _finish_restore(self):
        self._restore_target = None
        if self.container.isHidden():
            self.container.show()

    def _clamp_to_screen(self, x, y):
        """Fall back to the center when the remembered spot is off-screen, the
        way it can be after the monitor layout changed."""
        rect = QRect(x, y, self.width(), self.height())
        for screen in QApplication.screens():
            if screen.availableGeometry().intersects(rect):
                return x, y
        screen = QApplication.primaryScreen()
        if screen is None:
            return x, y
        geometry = screen.availableGeometry()
        return (geometry.x() + (geometry.width() - self.width()) // 2,
                geometry.y() + (geometry.height() - self.height()) // 2)

    def _apply_modifiers(self, emojis):
        """Apply skin tone and/or gender modifiers to compatible emojis."""
        tone = self.config.get("skin_tone", "")
        gender = self.config.get("gender", "")
        if not tone and not gender:
            return emojis
        return [(apply_modifiers(emoji, tone, gender), name) for emoji, name in emojis]

    def _refresh_view(self):
        if self._search_text:
            self._do_search()
        elif self.current_category:
            self.show_category(self.current_category)

    def set_skin_tone(self, tone):
        self.config["skin_tone"] = tone
        save_config(self.config)
        for btn in self.skin_tone_buttons:
            btn.setChecked(btn.tone == tone)
        self._refresh_view()

    def set_gender(self, gender):
        self.config["gender"] = gender
        save_config(self.config)
        for btn in self.gender_buttons:
            btn.setChecked(btn.gender == gender)
        self._refresh_view()

    def _kaomoji_set(self):
        if not self.config.get("kaomoji", True):
            return set()
        return {k["text"] for k in load_kaomoji()}

    def show_category(self, category):
        self.current_category = category
        self.search.clear()

        # Update button states
        for key, btn in self.category_buttons.items():
            btn.setChecked(key == category)

        if category == "recent":
            emojis = [(e, ALL_EMOJIS.get(e, e)) for e in load_recent()]
            self.emoji_grid.set_emojis(self._apply_modifiers(emojis), kaomoji_set=self._kaomoji_set())
            self.status.setText(t(self.locale, "status_recent", n=len(emojis)))
        elif category == "favorites":
            emojis = [(e, ALL_EMOJIS.get(e, e)) for e in self.config.get("favorites", [])]
            self.emoji_grid.set_emojis(self._apply_modifiers(emojis), kaomoji_set=self._kaomoji_set())
            self.status.setText(t(self.locale, "status_favorites", n=len(emojis)))
        elif category == "kaomoji":
            items = [(k["text"], k["name"]) for k in load_kaomoji()]
            self.emoji_grid.set_emojis(items, kaomoji=True)
            self.status.setText(t(self.locale, "status_emojis", n=len(items)))
        else:
            emojis = EMOJI_CATEGORIES.get(category, [])
            self.emoji_grid.set_emojis(self._apply_modifiers(emojis))
            self.status.setText(t(self.locale, "status_emojis", n=len(emojis)))

        self._base_status = self.status.text()
        self._show_collect_status()
        self.scroll.verticalScrollBar().setValue(0)

    def on_search(self, text):
        """Debounced search — waits 150ms after last keystroke."""
        self._search_text = text.strip().lower()
        if not self._search_text:
            self._search_timer.stop()
            if self.current_category:
                self.show_category(self.current_category)
            return
        self._search_timer.start()

    def _do_search(self):
        """Actual search execution after debounce."""
        text = self._search_text
        if not text:
            return

        # Update button states — none selected during search
        for btn in self.category_buttons.values():
            btn.setChecked(False)

        results = []
        seen = set()
        for category_emojis in EMOJI_CATEGORIES.values():
            for emoji, name in category_emojis:
                if emoji in seen:
                    continue
                # Search in name
                if text in name.lower():
                    results.append((emoji, name))
                    seen.add(emoji)
                    continue
                # Search in tags
                tags = SEARCH_TAGS.get(emoji, [])
                if any(text in tag.lower() for tag in tags):
                    results.append((emoji, name))
                    seen.add(emoji)

        # Also search kaomoji
        if self.config.get("kaomoji", True):
            for k in load_kaomoji():
                txt, name = k["text"], k["name"]
                if txt not in seen and text in name.lower():
                    results.append((txt, name))
                    seen.add(txt)

        self.emoji_grid.set_emojis(self._apply_modifiers(results), kaomoji_set=self._kaomoji_set())
        key = "status_results_plural" if len(results) != 1 else "status_results"
        self.status.setText(t(self.locale, key, n=len(results)))
        self._base_status = self.status.text()
        self._show_collect_status()
        self.scroll.verticalScrollBar().setValue(0)

    def on_emoji_selected(self, emoji):
        # Add to recent
        recent = load_recent()
        if emoji in recent:
            recent.remove(emoji)
        recent.insert(0, emoji)
        save_recent(recent[:self.config.get("max_recent", 36)])

        self._pending.append(emoji)

        # Hold Shift to keep picking, let go to insert. close_on_select=false
        # collects every pick instead, and is finished with Escape.
        shift = bool(QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier)
        if shift:
            self._shift_collecting = True
        if shift or not self.config.get("close_on_select", True):
            self._show_collect_status()
            return

        # Insert everything collected so far, including this pick, and close.
        # Inserting per pick would mean hiding and reshowing the window every
        # time, because the simulated Ctrl+V always goes to the focused window.
        self._flushing = True
        # Flag to prevent focus-loss handler from killing the app
        self._inserting = True
        self.hide()
        QTimer.singleShot(self._refocus_delay(), self._flush_pending)

    def _refocus_delay(self):
        return refocus_delay_ms(self.config.get("insert_method", "auto"))

    def _show_collect_status(self):
        """Show the collected emojis next to a short hint."""
        if not self._pending:
            self.collect_preview.clear()
            self.collect_preview.hide()
            return
        shown = self._pending[-12:]
        text = ("… " if len(self._pending) > 12 else "") + "".join(shown)
        self.collect_preview.setPixmap(render_run_pixmap(text, 20))
        self.collect_preview.show()
        key = "status_collected_shift" if self._shift_collecting else "status_collected"
        self.status.setText(t(self.locale, key))

    def _undo_collected(self):
        """Drop the last collected emoji. Returns False if there was none."""
        if not self._pending:
            return False
        self._pending.pop()
        self._show_collect_status()
        if not self._pending:
            # Restore the plain status instead of rebuilding the grid — a rebuild
            # would delete the button whose key event we're handling, and would
            # clear an active search
            self.status.setText(self._base_status)
        return True

    def closeEvent(self, event):
        """Insert everything collected before the window goes away."""
        if self._pending and not self._flushing:
            self._flushing = True
            # Suppress the focus-loss close while we step aside to insert
            self._inserting = True
            event.ignore()
            self.hide()
            QTimer.singleShot(self._refocus_delay(), self._flush_pending)
            return
        super().closeEvent(event)

    def _flush_pending(self):
        insert_emoji("".join(self._pending),
                     self.config.get("insert_method", "auto"),
                     self.config.get("paste_shortcut", DEFAULT_PASTE_SHORTCUT))
        self._pending = []
        QApplication.quit()

    def on_fav_toggle(self, emoji):
        favs = self.config.get("favorites", [])
        if emoji in favs:
            favs.remove(emoji)
            self.status.setText(t(self.locale, "fav_removed"))
        else:
            favs.append(emoji)
            self.status.setText(t(self.locale, "fav_added"))
        self.config["favorites"] = favs
        save_config(self.config)

        # Refresh if we're on favorites
        if self.current_category == "favorites":
            self.show_category("favorites")

    def on_remove_recent(self, emoji):
        focused_idx = next(
            (i for i, btn in enumerate(self.emoji_grid.buttons) if btn.emoji == emoji), 0
        )
        if self.current_category == "recent":
            recent = load_recent()
            if emoji in recent:
                recent.remove(emoji)
                save_recent(recent)
                self.show_category("recent")
                QTimer.singleShot(0, lambda: self.emoji_grid.focus_button(focused_idx))
        elif self.current_category == "favorites":
            self.on_fav_toggle(emoji)
            QTimer.singleShot(0, lambda: self.emoji_grid.focus_button(focused_idx))

    def showEvent(self, event):
        super().showEvent(event)
        # Cap the grid at the width it has while a scrollbar is showing, so the
        # columns keep that width in the views that don't need one. Taken from
        # the scroll area rather than its viewport, because the viewport is
        # already the narrower one whenever a bar happens to be visible.
        self.emoji_grid.setMaximumWidth(self.scroll.width() - SCROLLBAR_WIDTH)
        self.search.setFocus()
        if not self._restore_position():
            self.center_on_screen()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)

    def eventFilter(self, obj, event):
        # Letting go of Shift finishes a collection and inserts it. Watched
        # application-wide because the release goes to whichever widget has
        # focus — the search field, an emoji button, or nothing at all.
        if event.type() == QEvent.Type.KeyRelease and event.key() == Qt.Key.Key_Shift:
            if self._shift_collecting and self._pending and not self._flushing:
                self.close()  # closeEvent inserts what was collected
                return True

        if obj is self.search and event.type() == QEvent.Type.KeyPress:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier and not self.search.text():
                if event.key() == Qt.Key.Key_Left:
                    self._switch_category(-1)
                    return True
                elif event.key() == Qt.Key.Key_Right:
                    self._switch_category(1)
                    return True
            # Backspace in an empty search field takes back the last pick
            if event.key() == Qt.Key.Key_Backspace and not self.search.text():
                if self._undo_collected():
                    return True
        return super().eventFilter(obj, event)

    def on_move_favorite(self, emoji, direction):
        if self.current_category != "favorites":
            return
        favs = self.config.get("favorites", [])
        if emoji not in favs:
            return
        idx = favs.index(emoji)
        new_idx = idx + direction
        if 0 <= new_idx < len(favs):
            favs[idx], favs[new_idx] = favs[new_idx], favs[idx]
            self.config["favorites"] = favs
            save_config(self.config)
            self.show_category("favorites")
            QTimer.singleShot(0, lambda: self.emoji_grid.focus_button(new_idx))

    def _switch_category(self, direction):
        if self.current_category in self.category_order:
            idx = self.category_order.index(self.current_category)
            self.show_category(self.category_order[(idx + direction) % len(self.category_order)])
            QTimer.singleShot(0, lambda: self.emoji_grid.focus_button(0))

    def changeEvent(self, event):
        """Close when window loses focus (unless we're inserting an emoji or
        the compositor is dragging the window around)."""
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                self._moving = False
            elif not getattr(self, '_inserting', False) and not self._moving:
                QTimer.singleShot(100, self.close)
        super().changeEvent(event)


def main():
    # Make sure we can run
    app = QApplication(sys.argv)
    app.setApplicationName("Emoji Picker")
    app.setDesktopFileName("emoji-picker")

    # Global app style
    app.setStyleSheet("""
        QWidget {
            color: #dbdee1;
            font-family: "Noto Sans", "Segoe UI", sans-serif;
        }
        QToolTip {
            background: #1e1f22;
            border: 1px solid #3f4147;
            border-radius: 4px;
            color: #dbdee1;
            padding: 4px 8px;
            font-size: 12px;
        }
    """)

    picker = EmojiPicker()
    picker.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
