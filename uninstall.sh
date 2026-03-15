#!/bin/bash
# ─────────────────────────────────────────────
# Emoji Picker — Uninstaller
# ─────────────────────────────────────────────
set -e

INSTALL_DIR="$HOME/.local/share/emoji-picker"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
CONFIG_DIR="$HOME/.config/emoji-picker"

echo ""
echo "  🗑️  Emoji Picker — Uninstall"
echo "  ─────────────────────────────"
echo ""

read -p "  Really uninstall Emoji Picker? [y/N] " answer
if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
    echo "  Aborted."
    exit 0
fi

# Remove files
echo "  Removing program files..."
rm -rf "$INSTALL_DIR"
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

echo ""
echo "  ✅ Emoji Picker uninstalled!"
echo ""
echo "  Note: ydotool and wl-clipboard were not removed"
echo "  in case you need them for other purposes."
echo "  To remove: sudo apt remove ydotool wl-clipboard"
echo ""
