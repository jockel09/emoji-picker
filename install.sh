#!/bin/bash
# ─────────────────────────────────────────────
# Emoji Picker — Installer
# ─────────────────────────────────────────────
set -e

INSTALL_DIR="$HOME/.local/share/emoji-picker"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"

echo ""
echo "  🎨 Emoji Picker — Installation"
echo "  ───────────────────────────────"
echo ""

# ── 1. Check system ──────────────────────────
if [ "$XDG_SESSION_TYPE" != "wayland" ]; then
    echo "  ⚠️  Wayland nicht erkannt (XDG_SESSION_TYPE=$XDG_SESSION_TYPE)"
    echo "  Emoji Picker ist für Wayland/KDE Plasma 6 gebaut."
    read -p "  Trotzdem fortfahren? [j/N] " answer
    if [[ "$answer" != "j" && "$answer" != "J" ]]; then
        echo "  Abgebrochen."
        exit 1
    fi
fi

# ── 2. Check dependencies ───────────────────
echo "  🔍 Prüfe Abhängigkeiten..."
MISSING=()

if ! python3 -c "import PyQt6" 2>/dev/null; then
    MISSING+=("python3-pyqt6")
fi

if ! python3 -c "import cairo" 2>/dev/null; then
    MISSING+=("python3-cairo")
fi

if ! python3 -c "import gi; gi.require_version('Pango','1.0'); gi.require_version('PangoCairo','1.0'); from gi.repository import Pango, PangoCairo" 2>/dev/null; then
    MISSING+=("gir1.2-pango-1.0" "python3-gi" "python3-gi-cairo")
fi

if ! command -v ydotool &>/dev/null; then
    MISSING+=("ydotool")
fi

if ! command -v wl-copy &>/dev/null; then
    MISSING+=("wl-clipboard")
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "  📦 Fehlende Pakete: ${MISSING[*]}"
    read -p "  Jetzt installieren? [J/n] " answer
    if [[ "$answer" != "n" && "$answer" != "N" ]]; then
        sudo apt install -y "${MISSING[@]}"
    else
        echo "  Abgebrochen. Bitte installiere die Pakete manuell:"
        echo "  sudo apt install ${MISSING[*]}"
        exit 1
    fi
fi

echo "  ✅ Alle Abhängigkeiten vorhanden"

# ── 3. Setup ydotool ─────────────────────────
echo ""
echo "  🔧 Konfiguriere ydotool..."

NEED_RELOGIN=false

# Add user to input group if needed
if ! groups "$USER" | grep -qw input; then
    echo "  Füge $USER zur Gruppe 'input' hinzu..."
    sudo usermod -aG input "$USER"
    NEED_RELOGIN=true
fi

# Enable ydotool user service
if ! systemctl --user is-enabled ydotool &>/dev/null; then
    systemctl --user enable ydotool 2>/dev/null || true
fi

# Start ydotool if not running
if ! systemctl --user is-active ydotool &>/dev/null; then
    systemctl --user start ydotool 2>/dev/null || true
fi

echo "  ✅ ydotool konfiguriert"

# ── 4. Install files ─────────────────────────
echo ""
echo "  📁 Installiere Dateien..."

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$DESKTOP_DIR"

cp emoji_picker.py "$INSTALL_DIR/"
cp emoji_data.py "$INSTALL_DIR/"
cp search_tags.py "$INSTALL_DIR/"

# Create launcher script
cat > "$BIN_DIR/emoji-picker" << 'LAUNCHER'
#!/bin/bash
cd "$HOME/.local/share/emoji-picker"
exec python3 emoji_picker.py "$@"
LAUNCHER
chmod +x "$BIN_DIR/emoji-picker"

# Create .desktop file
cat > "$DESKTOP_DIR/emoji-picker.desktop" << DESKTOP
[Desktop Entry]
Type=Application
Name=Emoji Picker
Comment=Schneller Emoji-Picker für KDE Plasma / Wayland
Exec=$BIN_DIR/emoji-picker
Icon=face-smile
Terminal=false
Categories=Utility;
Keywords=emoji;smiley;unicode;emoticon;
StartupNotify=false
SingleMainWindow=true
DESKTOP

echo "  ✅ Dateien installiert"

# ── 5. Check PATH ────────────────────────────
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "  ⚠️  $BIN_DIR ist nicht in deinem PATH."
    echo "  Füge folgende Zeile in deine ~/.bashrc ein:"
    echo ""
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
fi

# ── 6. Done ──────────────────────────────────
echo ""
echo "  ════════════════════════════════════════"
echo "  ✅ Emoji Picker erfolgreich installiert!"
echo "  ════════════════════════════════════════"
echo ""
echo "  Starten:        emoji-picker"
echo "  Deinstallieren:  ./uninstall.sh"
echo ""
echo "  📌 Shortcut einrichten:"
echo "  Systemeinstellungen → Kurzbefehle → + Neu hinzufügen"
echo "  → Befehl: emoji-picker"
echo "  → Shortcut: Meta+."
echo ""
echo "  (Den Standard-KDE-Emoji-Shortcut vorher deaktivieren)"
echo ""
echo "  💡 Rechtsklick auf ein Emoji = Favorit"
echo ""

if [ "$NEED_RELOGIN" = true ]; then
    echo "  ⚠️  WICHTIG: Du wurdest zur Gruppe 'input' hinzugefügt."
    echo "  Bitte einmal abmelden und neu anmelden, damit"
    echo "  ydotool (direktes Einfügen) funktioniert!"
    echo ""
fi
