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
    echo "  ⚠️  Wayland not detected (XDG_SESSION_TYPE=$XDG_SESSION_TYPE)"
    echo "  Emoji Picker is built for Wayland / KDE Plasma 6."
    read -p "  Continue anyway? [y/N] " answer
    if [[ "$answer" != "y" && "$answer" != "Y" ]]; then
        echo "  Aborted."
        exit 1
    fi
fi

# ── 2. Detect package manager ────────────────
if command -v apt &>/dev/null; then
    PKG_MANAGER="apt"
    PKG_PYQT6="python3-pyqt6"
    PKG_CAIRO="python3-cairo"
    PKG_GI="gir1.2-pango-1.0 python3-gi python3-gi-cairo"
    PKG_YDOTOOL="ydotool"
    PKG_WLCLIP="wl-clipboard"
elif command -v dnf &>/dev/null; then
    PKG_MANAGER="dnf"
    PKG_PYQT6="python3-pyqt6"
    PKG_CAIRO="python3-cairo"
    PKG_GI="python3-gobject3"
    PKG_YDOTOOL="ydotool"
    PKG_WLCLIP="wl-clipboard"
else
    echo "  ❌ No supported package manager found (apt/dnf)."
    echo "  Please install dependencies manually:"
    echo "  python3-pyqt6, python3-cairo, python3-gi, ydotool, wl-clipboard"
    exit 1
fi

pkg_install() {
    sudo "$PKG_MANAGER" install -y "$@"
}

echo "  📦 Package manager: $PKG_MANAGER"

# ── 3. Check dependencies ───────────────────
echo "  🔍 Checking dependencies..."
MISSING=()

if ! python3 -c "import PyQt6" 2>/dev/null; then
    MISSING+=("$PKG_PYQT6")
fi

if ! python3 -c "import cairo" 2>/dev/null; then
    MISSING+=("$PKG_CAIRO")
fi

if ! python3 -c "import gi; gi.require_version('Pango','1.0'); gi.require_version('PangoCairo','1.0'); from gi.repository import Pango, PangoCairo" 2>/dev/null; then
    # shellcheck disable=SC2206
    MISSING+=($PKG_GI)
fi

if ! command -v ydotool &>/dev/null; then
    MISSING+=("$PKG_YDOTOOL")
fi

if ! command -v wl-copy &>/dev/null; then
    MISSING+=("$PKG_WLCLIP")
fi

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "  📦 Missing packages: ${MISSING[*]}"
    read -p "  Install now? [Y/n] " answer
    if [[ "$answer" != "n" && "$answer" != "N" ]]; then
        pkg_install "${MISSING[@]}"
    else
        echo "  Aborted. Please install packages manually:"
        echo "  sudo $PKG_MANAGER install ${MISSING[*]}"
        exit 1
    fi
fi

echo "  ✅ All dependencies satisfied"

# ── 4. Setup ydotool ─────────────────────────
echo ""
echo "  🔧 Configuring ydotool..."

NEED_RELOGIN=false

# Add user to input group if needed
if ! groups "$USER" | grep -qw input; then
    echo "  Adding $USER to group 'input'..."
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

echo "  ✅ ydotool configured"

# ── 5. Install files ─────────────────────────
echo ""
echo "  📁 Installing files..."

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$DESKTOP_DIR"

cp emoji_picker.py "$INSTALL_DIR/"
cp emoji_data.py "$INSTALL_DIR/"
cp search_tags.py "$INSTALL_DIR/"
cp -r locales "$INSTALL_DIR/"

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
Comment=Fast emoji picker for KDE Plasma / Wayland
Exec=$BIN_DIR/emoji-picker
Icon=face-smile
Terminal=false
Categories=Utility;
Keywords=emoji;smiley;unicode;emoticon;
StartupNotify=false
SingleMainWindow=true
DESKTOP

echo "  ✅ Files installed"

# ── 6. Check PATH ────────────────────────────
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "  ⚠️  $BIN_DIR is not in your PATH."
    echo "  Add the following line to your ~/.bashrc:"
    echo ""
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
fi

# ── 7. Done ──────────────────────────────────
echo ""
echo "  ════════════════════════════════════════"
echo "  ✅ Emoji Picker installed successfully!"
echo "  ════════════════════════════════════════"
echo ""
echo "  Run:         emoji-picker"
echo "  Uninstall:   ./uninstall.sh"
echo ""
echo "  📌 Set up a keyboard shortcut:"
echo "  System Settings → Keyboard → Shortcuts → + Add New → Command"
echo "  → Command:  emoji-picker"
echo "  → Shortcut: Meta+."
echo ""
echo "  (Disable the default KDE emoji shortcut first)"
echo ""

if [ "$NEED_RELOGIN" = true ]; then
    echo "  ⚠️  IMPORTANT: You were added to the 'input' group."
    echo "  Please log out and back in for ydotool"
    echo "  (direct emoji insertion) to work."
    echo ""
fi
