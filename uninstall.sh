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
echo "  🗑️  Emoji Picker — Deinstallation"
echo "  ──────────────────────────────────"
echo ""

read -p "  Emoji Picker wirklich deinstallieren? [j/N] " answer
if [[ "$answer" != "j" && "$answer" != "J" ]]; then
    echo "  Abgebrochen."
    exit 0
fi

# Remove files
echo "  Entferne Programmdateien..."
rm -rf "$INSTALL_DIR"
rm -f "$BIN_DIR/emoji-picker"
rm -f "$DESKTOP_DIR/emoji-picker.desktop"

# Ask about config
if [ -d "$CONFIG_DIR" ]; then
    read -p "  Auch Einstellungen (Favoriten, Kürzlich) löschen? [j/N] " answer
    if [[ "$answer" == "j" || "$answer" == "J" ]]; then
        rm -rf "$CONFIG_DIR"
        echo "  ✅ Einstellungen gelöscht"
    else
        echo "  Einstellungen beibehalten in: $CONFIG_DIR"
    fi
fi

echo ""
echo "  ✅ Emoji Picker deinstalliert!"
echo ""
echo "  Hinweis: ydotool und wl-clipboard wurden nicht entfernt,"
echo "  falls du sie für andere Zwecke brauchst."
echo "  Zum Entfernen: sudo apt remove ydotool wl-clipboard"
echo ""
