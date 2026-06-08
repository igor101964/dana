#!/bin/bash
# dana — dependency installer
# Supports: Ubuntu 22.04+, Debian 12+, Linux Mint 21+

set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[dana]${NC} $1"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $1"; }
error() { echo -e "${RED}[error]${NC} $1"; exit 1; }

echo ""
echo "  dana — Data Analysis Viewer"
echo "  Dependency installer"
echo ""

# ── Check OS ──────────────────────────────────────────────────────────
if [[ "$OSTYPE" == "darwin"* ]]; then
    error "macOS is not supported natively. Use Docker or a Linux VM."
fi

if ! command -v apt-get &>/dev/null; then
    error "This installer requires apt-get (Ubuntu/Debian/Mint)."
fi

# ── System packages ───────────────────────────────────────────────────
info "Updating package list..."
sudo apt-get update -qq

info "Installing GTK4 and WebKitGTK..."
sudo apt-get install -y \
    build-essential \
    gcc \
    make \
    pkg-config \
    libgtk-4-dev \
    libwebkitgtk-6.0-dev \
    libsoup-3.0-dev \
    libglib2.0-dev \
    libpango1.0-dev \
    libcairo2-dev \
    libgdk-pixbuf-2.0-dev

# ── Python ────────────────────────────────────────────────────────────
info "Installing Python 3 and pip..."
sudo apt-get install -y python3 python3-pip

# ── Python packages ───────────────────────────────────────────────────
info "Installing Python dependencies..."
pip3 install --break-system-packages \
    plotly \
    yfinance \
    requests \
    pandas \
    numpy 2>/dev/null || \
pip3 install \
    plotly \
    yfinance \
    requests \
    pandas \
    numpy

# ── Check mshell ──────────────────────────────────────────────────────
echo ""
if command -v mshell &>/dev/null; then
    info "mshell found: $(which mshell)"
else
    warn "mshell not found. AI Analysis requires mshell with IPC."
    warn "See: https://www.appservgrid.com/paw92 for mshell."
fi

# ── Build dana ────────────────────────────────────────────────────────
echo ""
info "Building dana..."
make clean && make

# ── Install dana ──────────────────────────────────────────────────────
info "Installing dana..."
make install

echo ""
echo -e "${GREEN}✓ dana installed successfully!${NC}"
echo ""
echo "  Run from mshell terminal:  dana &"
echo "  Or directly:               ~/.local/bin/dana &"
echo ""
echo "  Note: For AI Analysis, run dana from inside a mshell session"
echo "        with MSHELL_IPC_PID set."
echo ""
