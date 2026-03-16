#!/usr/bin/env python3
"""
Emoji Picker — A fast, KDE-styled emoji picker for Wayland.
Inserts emojis directly via wtype. Supports categories, search,
favorites, and recently used emojis.
"""

import sys
import os
import json
import subprocess
from pathlib import Path
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit,
    QPushButton, QScrollArea, QGridLayout, QLabel, QFrame,
    QGraphicsDropShadowEffect, QToolButton
)
from PyQt6.QtCore import Qt, QTimer, QEvent, pyqtSignal, QSize
from PyQt6.QtGui import QFont, QColor, QKeySequence, QShortcut, QCursor, QPixmap, QImage, QIcon

import cairo
import gi
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Pango, PangoCairo

from emoji_data import EMOJI_CATEGORIES, ALL_EMOJIS, SKIN_TONE_EMOJIS, GENDER_EMOJIS
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

DEFAULT_CONFIG = {
    "favorites": [],
    "max_recent": 36,
    "columns": 9,
    "emoji_size": 28,
    "close_on_select": True,
    "insert_method": "ydotool",  # "ydotool", "clipboard"
    "skin_tone": "",
    "gender": "",
    "language": "en",
    "kaomoji": False,
}


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


def insert_emoji(emoji, method="ydotool"):
    """Insert emoji into the previously focused window.
    Copies to clipboard, then simulates Ctrl+V via ydotool."""

    # Copy emoji to clipboard
    try:
        subprocess.run(["wl-copy", "--", emoji], check=True, timeout=2)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False

    # Simulate Ctrl+V via ydotool
    try:
        subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], check=True, timeout=2)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Clipboard filled — user can Ctrl+V manually as last resort
    return False


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
            bg, border = "rgba(82, 148, 226, 0.3)", "2px solid #5294e2"
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


class EmojiButton(QToolButton):
    """A clickable emoji button with color rendering."""
    emoji_selected = pyqtSignal(str)
    emoji_fav_toggle = pyqtSignal(str)
    emoji_delete = pyqtSignal(str)

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
        self.setStyleSheet("""
            QPushButton {
                border: none;
                border-radius: 6px;
                background: transparent;
                padding: 2px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.08);
            }
            QPushButton:checked {
                background: rgba(255, 255, 255, 0.12);
                border-bottom: 2px solid #5294e2;
            }
        """)


class EmojiGrid(QWidget):
    """A grid of emoji buttons."""
    emoji_selected = pyqtSignal(str)
    emoji_fav_toggle = pyqtSignal(str)
    emoji_delete = pyqtSignal(str)
    emoji_move = pyqtSignal(str, int)  # emoji, direction (-1 or +1)

    def __init__(self, columns=9, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.layout_ = QGridLayout(self)
        self.layout_.setSpacing(2)
        self.layout_.setContentsMargins(4, 4, 4, 4)
        self.buttons = []
        self._cols = columns
        self._last_row_stretch = 0
        self.scroll_area = None

    def set_emojis(self, emojis, kaomoji=False, kaomoji_set=None):
        """emojis: list of (emoji, name) tuples"""
        # Remove all items from layout (releases grid cells, not just widget references)
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # Reset row stretches from previous call
        for r in range(self._last_row_stretch + 1):
            self.layout_.setRowStretch(r, 0)
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
        self.locale = load_locale(self.config.get("language", "en"))
        self.current_category = None
        self._inserting = False

        # Debounce timer for search
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._do_search)
        self._search_text = ""

        self.setup_ui()
        self.setup_shortcuts()
        self.show_category("recent")

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
        self.search.setStyleSheet("""
            QLineEdit {
                background: #1e1f22;
                border: 1px solid #3f4147;
                border-radius: 8px;
                padding: 8px 12px;
                color: #dbdee1;
                font-size: 14px;
                selection-background-color: #5294e2;
            }
            QLineEdit:focus {
                border: 1px solid #5294e2;
            }
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
        self.scroll.setWidget(self.emoji_grid)
        self.emoji_grid.scroll_area = self.scroll
        layout.addWidget(self.scroll)

        # Status bar
        self.status = QLabel("")
        self.status.setStyleSheet("""
            QLabel {
                color: #8b8d93;
                font-size: 11px;
                padding: 2px 4px;
            }
        """)
        layout.addWidget(self.status)

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

    def _apply_modifiers(self, emojis):
        """Apply skin tone and/or gender modifiers to compatible emojis."""
        tone = self.config.get("skin_tone", "")
        gender = self.config.get("gender", "")
        if not tone and not gender:
            return emojis
        result = []
        for emoji, name in emojis:
            in_skin = emoji in SKIN_TONE_EMOJIS
            in_gender = emoji in GENDER_EMOJIS
            if not (tone and in_skin) and not (gender and in_gender):
                result.append((emoji, name))
                continue
            base = emoji.replace('\uFE0F', '')
            modified = base
            if tone and in_skin:
                modified += tone
            if gender and in_gender:
                modified += gender
            result.append((modified, name))
        return result

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
        self.scroll.verticalScrollBar().setValue(0)

    def on_emoji_selected(self, emoji):
        # Add to recent
        recent = load_recent()
        if emoji in recent:
            recent.remove(emoji)
        recent.insert(0, emoji)
        save_recent(recent[:self.config.get("max_recent", 36)])

        # Flag to prevent focus-loss handler from killing the app
        self._inserting = True

        # Close first, then insert (so the target window gets focus back)
        if self.config.get("close_on_select", True):
            self.hide()
            # 300ms delay for Wayland compositor to refocus previous window
            QTimer.singleShot(300, lambda: self._do_insert(emoji))
        else:
            self._do_insert(emoji)

    def _do_insert(self, emoji):
        method = self.config.get("insert_method", "wtype")
        insert_emoji(emoji, method)
        if self.config.get("close_on_select", True):
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
        self.search.setFocus()
        self.center_on_screen()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)

    def eventFilter(self, obj, event):
        if obj is self.search and event.type() == QEvent.Type.KeyPress:
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier and not self.search.text():
                if event.key() == Qt.Key.Key_Left:
                    self._switch_category(-1)
                    return True
                elif event.key() == Qt.Key.Key_Right:
                    self._switch_category(1)
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
        """Close when window loses focus (unless we're inserting an emoji)."""
        if event.type() == QEvent.Type.ActivationChange:
            if not self.isActiveWindow() and not getattr(self, '_inserting', False):
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
