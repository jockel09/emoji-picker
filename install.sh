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

# ── 2. Detect distro and package manager ─────
# ID distinguishes Debian proper from its derivatives; ID_LIKE would not.
DEB_ID=""
DEB_CODENAME=""
if [ -r /etc/os-release ]; then
    DEB_ID=$(. /etc/os-release && echo "${ID:-}")
    DEB_CODENAME=$(. /etc/os-release && echo "${VERSION_CODENAME:-}")
fi

PKG_YDOTOOL="ydotool"
PKG_WLCLIP="wl-clipboard"
# Package name of the color emoji font, only where it is known for certain.
# Empty means: point it out rather than guess a name that may not exist.
PKG_EMOJIFONT=""

if command -v apt &>/dev/null; then
    PKG_MANAGER="apt"
    PKG_PYQT6="python3-pyqt6"
    PKG_CAIRO="python3-cairo"
    PKG_GI="gir1.2-pango-1.0 python3-gi python3-gi-cairo"
    PKG_EMOJIFONT="fonts-noto-color-emoji"
elif command -v dnf &>/dev/null; then
    PKG_MANAGER="dnf"
    PKG_PYQT6="python3-pyqt6"
    PKG_CAIRO="python3-cairo"
    PKG_GI="python3-gobject3"
elif command -v pacman &>/dev/null; then
    PKG_MANAGER="pacman"
    PKG_PYQT6="python-pyqt6"
    PKG_CAIRO="python-cairo"
    PKG_GI="python-gobject pango"
else
    echo "  ❌ No supported package manager found (apt/dnf/pacman)."
    echo "  Please install dependencies manually:"
    echo "  python3-pyqt6, python3-cairo, python3-gi, ydotool, wl-clipboard"
    exit 1
fi

pkg_install() {
    if [ "$PKG_MANAGER" = "pacman" ]; then
        sudo pacman -S --needed --noconfirm "$@"
    else
        sudo "$PKG_MANAGER" install -y "$@"
    fi
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

if ! command -v wl-copy &>/dev/null; then
    MISSING+=("$PKG_WLCLIP")
fi

# The renderer asks Pango for "Noto Color Emoji" by name. Without that font
# every emoji comes out as a box, with nothing explaining why.
FONT_MISSING=false
if command -v fc-list &>/dev/null && ! fc-list 2>/dev/null | grep -qi "Noto Color Emoji"; then
    if [ -n "$PKG_EMOJIFONT" ]; then
        MISSING+=("$PKG_EMOJIFONT")
    else
        FONT_MISSING=true
    fi
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

if [ "$FONT_MISSING" = true ]; then
    echo "  ⚠️  No 'Noto Color Emoji' font found — emojis will render as boxes."
    echo "     Install your distro's Noto colour emoji font package."
fi

# ── 4. Keyboard simulation ───────────────────
# Deliberately separate from the block above: on Debian ydotool only exists in
# backports, so installing it can fail. That must not take the rest down with
# it — the picker still runs and falls back to copying to the clipboard.
echo ""

install_ydotool() {
    if pkg_install "$PKG_YDOTOOL"; then
        echo "  ✅ ydotool installed"
        return 0
    fi
    return 1
}

# Offer to enable Debian's backports, where ydotool lives. Returns 0 only if
# ydotool is installable afterwards.
enable_backports() {
    local list
    echo ""
    echo "  📦 ydotool is not available from your configured package sources."
    echo "     On Debian it only exists in ${DEB_CODENAME}-backports."
    echo "     Enabling it adds an apt source, so this changes your package"
    echo "     management, not just this install."
    read -p "  Enable ${DEB_CODENAME}-backports and install ydotool? [Y/n] " answer
    [[ "$answer" != "n" && "$answer" != "N" ]] || return 1

    list="/etc/apt/sources.list.d/${DEB_CODENAME}-backports.list"
    echo "  🔧 Adding $list"
    echo "deb http://deb.debian.org/debian ${DEB_CODENAME}-backports main" \
        | sudo tee "$list" >/dev/null
    sudo apt-get update || return 1
    apt-get install -s ydotool &>/dev/null
}

YDOTOOL_OK=true
NEED_BACKPORTS=false

if command -v ydotool &>/dev/null; then
    echo "  ✅ ydotool present"
else
    # Ask apt whether it could install ydotool at all, rather than guessing from
    # the sources: this is locale-independent and needs no hardcoded codename.
    # Only genuine Debian qualifies — putting Debian's backports on Ubuntu or a
    # derivative mixes distributions and breaks systems.
    if [ "$PKG_MANAGER" = "apt" ] && ! apt-get install -s ydotool &>/dev/null \
       && [ "$DEB_ID" = "debian" ] && [ -n "$DEB_CODENAME" ]; then
        NEED_BACKPORTS=true
    fi

    if [ "$NEED_BACKPORTS" = true ]; then
        if enable_backports; then
            install_ydotool || YDOTOOL_OK=false
        else
            YDOTOOL_OK=false
        fi
    else
        echo "  📦 ydotool is missing — it is what inserts emojis directly."
        read -p "  Install it now? [Y/n] " answer
        if [[ "$answer" != "n" && "$answer" != "N" ]]; then
            install_ydotool || YDOTOOL_OK=false
        else
            YDOTOOL_OK=false
        fi
    fi
fi

if [ "$YDOTOOL_OK" = false ]; then
    echo ""
    echo "  ⚠️  Continuing without ydotool. The picker will work, but emojis"
    echo "     are only copied to the clipboard, not inserted."
    if [ "$NEED_BACKPORTS" = true ]; then
        echo ""
        echo "     To add it later:"
        echo "       echo 'deb http://deb.debian.org/debian ${DEB_CODENAME}-backports main' \\"
        echo "         | sudo tee /etc/apt/sources.list.d/${DEB_CODENAME}-backports.list"
        echo "       sudo apt update && sudo apt install ydotool"
    fi
    echo ""
    echo "     Afterwards, re-run this script to finish the setup."
fi

# ── 5. Configure ydotool ─────────────────────
echo ""
echo "  🔧 Configuring input access..."

NEED_RELOGIN=false

# Both the daemon and any later ydotool install need /dev/uinput, which udev
# grants to group 'input' — so do this even if ydotool isn't here yet
if ! groups "$USER" | grep -qw input; then
    echo "  Adding $USER to group 'input'..."
    sudo usermod -aG input "$USER"
    NEED_RELOGIN=true
fi

if [ "$YDOTOOL_OK" = true ]; then
    # Enable ydotool user service
    if ! systemctl --user is-enabled ydotool &>/dev/null; then
        systemctl --user enable ydotool 2>/dev/null || true
    fi

    # Start ydotool if not running
    if ! systemctl --user is-active ydotool &>/dev/null; then
        systemctl --user start ydotool 2>/dev/null || true
    fi

    echo "  ✅ ydotool configured"
else
    echo "  ✅ Group set up; ydotool can be added later"
fi

# ── 6. Install files ─────────────────────────
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

# ── 7. Check PATH ────────────────────────────
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo ""
    echo "  ⚠️  $BIN_DIR is not in your PATH."
    echo "  Add the following line to your ~/.bashrc:"
    echo ""
    echo "    export PATH=\"\$HOME/.local/bin:\$PATH\""
    echo ""
fi

# ── 8. Done ──────────────────────────────────
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
    echo "  Please log out and back in for direct emoji insertion to work."
    echo ""
fi

if [ "$YDOTOOL_OK" = false ]; then
    echo "  ⚠️  Without ydotool the picker only copies to the clipboard."
    echo "  See the instructions further up to install it."
    echo ""
fi
