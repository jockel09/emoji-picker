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

from emoji_data import EMOJI_CATEGORIES, ALL_EMOJIS, SKIN_TONE_EMOJIS
from search_tags import SEARCH_TAGS

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

SKIN_TONE_MODIFIERS = [
    ("", "#FBBF24"),        # default (gelb)
    ("\U0001F3FB", "#FDDBB4"),  # hell
    ("\U0001F3FC", "#E8AA80"),  # mittel-hell
    ("\U0001F3FD", "#C68642"),  # mittel
    ("\U0001F3FE", "#8B5A2B"),  # mittel-dunkel
    ("\U0001F3FF", "#4A2912"),  # dunkel
]


DEFAULT_CONFIG = {
    "favorites": [],
    "recent": [],
    "max_recent": 36,
    "columns": 9,
    "emoji_size": 28,
    "close_on_select": True,
    "insert_method": "ydotool",  # "ydotool", "clipboard"
    "skin_tone": "",
}


def load_config():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                saved = json.load(f)
            config = DEFAULT_CONFIG.copy()
            config.update(saved)
            return config
        except (json.JSONDecodeError, IOError):
            pass
    return DEFAULT_CONFIG.copy()


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


class EmojiButton(QToolButton):
    """A clickable emoji button with color rendering."""
    emoji_selected = pyqtSignal(str)
    emoji_fav_toggle = pyqtSignal(str)

    def __init__(self, emoji, name="", parent=None):
        super().__init__(parent)
        self.emoji = emoji
        self.emoji_name = name
        self.setFixedSize(42, 42)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(name)

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
        """)
        self.clicked.connect(lambda: self.emoji_selected.emit(self.emoji))

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

    def __init__(self, columns=9, parent=None):
        super().__init__(parent)
        self.columns = columns
        self.layout_ = QGridLayout(self)
        self.layout_.setSpacing(2)
        self.layout_.setContentsMargins(4, 4, 4, 4)
        self.buttons = []

    def set_emojis(self, emojis):
        """emojis: list of (emoji, name) tuples"""
        # Clear existing
        for btn in self.buttons:
            self.layout_.removeWidget(btn)
            btn.deleteLater()
        self.buttons.clear()

        for i, (emoji, name) in enumerate(emojis):
            btn = EmojiButton(emoji, name)
            btn.emoji_selected.connect(self.emoji_selected.emit)
            btn.emoji_fav_toggle.connect(self.emoji_fav_toggle.emit)
            row = i // self.columns
            col = i % self.columns
            self.layout_.addWidget(btn, row, col)
            self.buttons.append(btn)

        # Add stretch at the bottom
        self.layout_.setRowStretch(len(emojis) // self.columns + 1, 1)


class EmojiPicker(QWidget):
    """Main emoji picker window."""

    def __init__(self):
        super().__init__()
        self.config = load_config()
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
        self.search.setPlaceholderText("Emoji suchen…")
        self.search.setClearButtonEnabled(True)
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
        layout.addWidget(self.search)

        # Category bar
        cat_layout = QHBoxLayout()
        cat_layout.setSpacing(2)

        self.category_buttons = {}
        categories = [
            ("⏱️", "recent", "Kürzlich"),
            ("⭐", "favorites", "Favoriten"),
            ("😀", "smileys", "Smileys"),
            ("👋", "people", "Personen"),
            ("🐻", "animals", "Tiere & Natur"),
            ("🍔", "food", "Essen & Trinken"),
            ("✈️", "travel", "Reisen & Orte"),
            ("⚽", "activities", "Aktivitäten"),
            ("💡", "objects", "Objekte"),
            ("🔣", "symbols", "Symbole"),
            ("🏁", "flags", "Flaggen"),
        ]

        for icon, key, label in categories:
            btn = CategoryButton(icon, label)
            btn.clicked.connect(lambda checked, k=key: self.show_category(k))
            cat_layout.addWidget(btn)
            self.category_buttons[key] = btn

        cat_layout.addStretch()

        # Skin tone selector
        cat_layout.addSpacing(6)
        self.skin_tone_buttons = []
        current_tone = self.config.get("skin_tone", "")
        tooltips = ["Standard", "Hell", "Mittel-hell", "Mittel", "Mittel-dunkel", "Dunkel"]
        for (tone, color), tooltip in zip(SKIN_TONE_MODIFIERS, tooltips):
            btn = SkinToneButton(tone, color, tooltip)
            btn.setChecked(tone == current_tone)
            btn.clicked.connect(lambda checked, t=tone: self.set_skin_tone(t))
            cat_layout.addWidget(btn)
            self.skin_tone_buttons.append(btn)

        layout.addLayout(cat_layout)

        # Separator
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("background: #3f4147; max-height: 1px;")
        layout.addWidget(sep)

        # Emoji scroll area
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        self.scroll.setWidget(self.emoji_grid)
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

    def _apply_skin_tone(self, emojis):
        """Apply the configured skin tone modifier to all compatible emojis."""
        tone = self.config.get("skin_tone", "")
        if not tone:
            return emojis
        result = []
        for emoji, name in emojis:
            if emoji in SKIN_TONE_EMOJIS:
                result.append((emoji + tone, name))
            else:
                result.append((emoji, name))
        return result

    def set_skin_tone(self, tone):
        self.config["skin_tone"] = tone
        save_config(self.config)
        for btn in self.skin_tone_buttons:
            btn.setChecked(btn.tone == tone)
        if self._search_text:
            self._do_search()
        elif self.current_category:
            self.show_category(self.current_category)

    def show_category(self, category):
        self.current_category = category
        self.search.clear()

        # Update button states
        for key, btn in self.category_buttons.items():
            btn.setChecked(key == category)

        if category == "recent":
            emojis = []
            for e in self.config.get("recent", []):
                name = ALL_EMOJIS.get(e, "")
                emojis.append((e, name))
            self.emoji_grid.set_emojis(self._apply_skin_tone(emojis))
            self.status.setText(f"{len(emojis)} kürzlich verwendet")
        elif category == "favorites":
            emojis = []
            for e in self.config.get("favorites", []):
                name = ALL_EMOJIS.get(e, "")
                emojis.append((e, name))
            self.emoji_grid.set_emojis(self._apply_skin_tone(emojis))
            self.status.setText(f"{len(emojis)} Favoriten  •  Rechtsklick zum Entfernen")
        else:
            emojis = EMOJI_CATEGORIES.get(category, [])
            self.emoji_grid.set_emojis(self._apply_skin_tone(emojis))
            self.status.setText(f"{len(emojis)} Emojis  •  Rechtsklick = Favorit")

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

        self.emoji_grid.set_emojis(self._apply_skin_tone(results))
        self.status.setText(f"{len(results)} Ergebnis{'se' if len(results) != 1 else ''}")
        self.scroll.verticalScrollBar().setValue(0)

    def on_emoji_selected(self, emoji):
        # Add to recent
        recent = self.config.get("recent", [])
        if emoji in recent:
            recent.remove(emoji)
        recent.insert(0, emoji)
        max_recent = self.config.get("max_recent", 36)
        self.config["recent"] = recent[:max_recent]
        save_config(self.config)

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
            self.status.setText(f"Favorit entfernt")
        else:
            favs.append(emoji)
            self.status.setText(f"Favorit hinzugefügt ⭐")
        self.config["favorites"] = favs
        save_config(self.config)

        # Refresh if we're on favorites
        if self.current_category == "favorites":
            self.show_category("favorites")

    def showEvent(self, event):
        super().showEvent(event)
        self.search.setFocus()
        self.center_on_screen()

    def focusOutEvent(self, event):
        super().focusOutEvent(event)

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
