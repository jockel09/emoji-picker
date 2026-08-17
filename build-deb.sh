#!/bin/bash
# ─────────────────────────────────────────────
# Emoji Picker — build a .deb
#
#   ./build-deb.sh            version from the latest git tag
#   ./build-deb.sh 1.3.0      explicit version
#
# Pure Python, so the package is Architecture: all — one .deb for every
# architecture. Result lands in dist/.
# ─────────────────────────────────────────────
set -e

cd "$(dirname "$0")"

SRC="packaging/deb"
STAGE="build/deb"
OUT="dist"

# ── Version ──────────────────────────────────
if [ -n "$1" ]; then
    VERSION="$1"
else
    TAG=$(git describe --tags --abbrev=0 2>/dev/null || true)
    if [ -z "$TAG" ]; then
        echo "  ❌ No git tag found. Pass a version: ./build-deb.sh 1.3.0"
        exit 1
    fi
    VERSION="${TAG#v}"   # v1.3.0 -> 1.3.0
fi

# Debian versions must start with a digit
case "$VERSION" in
    [0-9]*) ;;
    *) echo "  ❌ Version '$VERSION' must start with a digit."; exit 1 ;;
esac

PKG="emoji-picker_${VERSION}_all.deb"

echo ""
echo "  📦 Building $PKG"
echo ""

# ── Staging tree ─────────────────────────────
rm -rf "$STAGE"
mkdir -p "$STAGE/DEBIAN" \
         "$STAGE/usr/bin" \
         "$STAGE/usr/share/emoji-picker" \
         "$STAGE/usr/share/applications" \
         "$STAGE/usr/share/doc/emoji-picker"

# Application
install -m 644 emoji_picker.py emoji_data.py search_tags.py "$STAGE/usr/share/emoji-picker/"
cp -r locales "$STAGE/usr/share/emoji-picker/"
find "$STAGE/usr/share/emoji-picker/locales" -type f -exec chmod 644 {} +

# Executables
install -m 755 "$SRC/emoji-picker" "$STAGE/usr/bin/emoji-picker"
install -m 755 "$SRC/emoji-picker-setup" "$STAGE/usr/bin/emoji-picker-setup"

# Desktop entry
install -m 644 "$SRC/emoji-picker.desktop" "$STAGE/usr/share/applications/"

# ── Metadata ─────────────────────────────────
sed "s/@VERSION@/$VERSION/" "$SRC/control.in" > "$STAGE/DEBIAN/control"
chmod 644 "$STAGE/DEBIAN/control"
install -m 755 "$SRC/postinst" "$STAGE/DEBIAN/postinst"

# Policy wants a copyright file and a changelog
install -m 644 "$SRC/copyright" "$STAGE/usr/share/doc/emoji-picker/copyright"

cat > "$STAGE/usr/share/doc/emoji-picker/changelog.Debian" <<EOF
emoji-picker ($VERSION) unstable; urgency=medium

  * Release $VERSION. See https://github.com/jockel09/emoji-picker/releases

 -- Kevin Fischer <web2go.webdesign@googlemail.com>  $(date -R)
EOF
gzip -9n "$STAGE/usr/share/doc/emoji-picker/changelog.Debian"
chmod 644 "$STAGE/usr/share/doc/emoji-picker/changelog.Debian.gz"

# ── Build ────────────────────────────────────
# dpkg-deb keeps whatever the umask produced, and Policy wants 755 on dirs
find "$STAGE" -type d -exec chmod 755 {} +

mkdir -p "$OUT"
dpkg-deb --root-owner-group --build "$STAGE" "$OUT/$PKG" >/dev/null

echo "  ✅ $OUT/$PKG"
echo ""
dpkg-deb --info "$OUT/$PKG" | sed -n '2,8p'
echo "  Install with:"
echo "    sudo apt install ./$OUT/$PKG"
echo "    emoji-picker-setup"
echo ""
