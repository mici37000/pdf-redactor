#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 not found. Install Python 3.10+ and re-run." >&2
    exit 1
fi

echo "Creating virtual environment in .venv/ …"
python3 -m venv .venv
./.venv/bin/pip install --quiet --upgrade pip

echo "Installing dependencies …"
./.venv/bin/pip install --quiet -e .

if [[ "$(uname)" == "Linux" ]]; then
    APPS_DIR="$HOME/.local/share/applications"
    DESKTOP_FILE="$APPS_DIR/pdf-redactor.desktop"
    mkdir -p "$APPS_DIR"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=PDF Redactor
GenericName=PDF Redactor
Comment=Redact PII from PDF reports
Exec=$PROJECT_DIR/.venv/bin/python -m redactor
Path=$PROJECT_DIR
Icon=$PROJECT_DIR/redactor/assets/icon.svg
Terminal=false
Categories=Office;
StartupNotify=true
StartupWMClass=PDF Redactor
EOF
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APPS_DIR" 2>/dev/null || true
    fi
    echo "Installed desktop launcher: $DESKTOP_FILE"
fi

echo
echo "Done."
echo "  Launch from menu: search \"PDF Redactor\""
echo "  Or from terminal: .venv/bin/python -m redactor"
