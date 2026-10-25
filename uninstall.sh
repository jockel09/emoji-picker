#!/bin/bash
# ─────────────────────────────────────────────
# Emoji Picker — Uninstaller
# ─────────────────────────────────────────────
set -e

INSTALL_DIR="$HOME/.local/share/emoji-picker"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
CONFIG_DIR="$HOME/.config/emoji-picker"
RECENT_FILE="$INSTALL_DIR/recent.json"

echo ""
echo "  🗑️  Emoji Picker — Uninstall"
echo "  ─────────────────────────────"
echo ""

read -p "  Really uninstall Emoji Picker? [y/N] " answer
if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
    echo "  Aborted."
    exit 0
fi

# Remove files. Named one by one rather than as the whole directory, because
# the recents list lives in there too and gets its own question below.
echo "  Removing program files..."
rm -f "$INSTALL_DIR/emoji_picker.py"
rm -f "$INSTALL_DIR/emoji_data.py"
rm -f "$INSTALL_DIR/search_tags.py"
rm -rf "$INSTALL_DIR/locales"
rm -rf "$INSTALL_DIR/__pycache__"
rm -f "$BIN_DIR/emoji-picker"
rm -f "$DESKTOP_DIR/emoji-picker.desktop"

# Ask about config
if [ -d "$CONFIG_DIR" ]; then
    read -p "  Also delete settings (favorites, config)? [y/N] " answer
    if [[ "$answer" == "y" || "$answer" == "Y" ]]; then
        rm -rf "$CONFIG_DIR"
        echo "  ✅ Settings deleted"
    else
        echo "  Settings kept at: $CONFIG_DIR"
    fi
fi

# Ask about the recents, separately: it's a different kind of loss than the
# settings, and whoever keeps their favorites may well want their history too.
if [ -f "$RECENT_FILE" ]; then
    read -p "  Also delete the list of recently used emojis? [y/N] " answer
    if [[ "$answer" == "y" || "$answer" == "Y" ]]; then
        rm -f "$RECENT_FILE"
        echo "  ✅ Recents deleted"
    else
        echo "  Recents kept at: $RECENT_FILE"
    fi
fi

# Only goes away once nothing of ours is left in it
rmdir "$INSTALL_DIR" 2>/dev/null || true

echo ""
echo "  ✅ Emoji Picker uninstalled!"
echo ""
echo "  Note: ydotool and wl-clipboard were not removed"
echo "  in case you need them for other purposes."
echo "  To remove: sudo apt remove ydotool wl-clipboard"
echo ""
