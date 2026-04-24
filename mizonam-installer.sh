#!/usr/bin/env bash
# ══════════════════════════════════════════════════════════════
#  Mizonam — Installer
#  Usage: sudo bash install.sh
# ══════════════════════════════════════════════════════════════
set -e

R="\033[0m"; B="\033[1m"
GR="\033[92m"; RE="\033[91m"; YE="\033[93m"; CY="\033[96m"

ok()   { echo -e "${GR}${B}✓${R} $*"; }
err()  { echo -e "${RE}${B}✗${R} $*"; exit 1; }
warn() { echo -e "${YE}⚠${R}  $*"; }
info() { echo -e "${CY}→${R}  $*"; }

INSTALL_BIN="/usr/local/bin/mizonam"
SERVICE_FILE="/etc/systemd/system/mizonam.service"
DOWNLOAD_URL="https://sinapiranidl.storage.iran.liara.space/mizonam/main.py"

# ── Root check ────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && err "Please run with sudo:  sudo bash install.sh"

echo ""
echo -e "${CY}${B}  ╔══════════════════════════════════╗"
echo -e "  ║   Mizonam Installer             ║"
echo -e "  ╚══════════════════════════════════╝${R}"
echo ""

# ── Step 1: Find or download main.py ─────────────────────────
SCRIPT_SRC=""

if [[ -f "$(pwd)/main.py" ]]; then
    SCRIPT_SRC="$(pwd)/main.py"
    ok "Found main.py in current directory"
else
    warn "main.py not found in current directory"
    echo ""
    echo -e "  ${CY}[1]${R} Iran mirror  (iran.liara.space) — recommended inside Iran"
    echo -e "  ${CY}[2]${R} GitHub       (raw.githubusercontent.com)"
    echo ""
    read -rp "  Select download source [1/2]: " mirror_choice

    case "$mirror_choice" in
        2)
            SELECTED_URL="https://raw.githubusercontent.com/sinapirani/mizonam/refs/heads/main/main.py"
            info "Downloading from GitHub..."
            ;;
        *)
            SELECTED_URL="$DOWNLOAD_URL"
            info "Downloading from Iran mirror..."
            ;;
    esac

    # prefer curl, fallback to wget
    if command -v curl &>/dev/null; then
        curl -fsSL "$SELECTED_URL" -o /tmp/mizonam_main.py \
            || err "Download failed. Check your internet connection."
    elif command -v wget &>/dev/null; then
        wget -q "$SELECTED_URL" -O /tmp/mizonam_main.py \
            || err "Download failed. Check your internet connection."
    else
        err "Neither curl nor wget found. Install one and retry."
    fi

    SCRIPT_SRC="/tmp/mizonam_main.py"
    ok "Downloaded main.py successfully"
fi

# ── Step 2: Validate it's a Python file ──────────────────────
head -1 "$SCRIPT_SRC" | grep -qi "python" \
    || warn "File does not start with a Python shebang — proceeding anyway"

# ── Step 3: Install binary ───────────────────────────────────
info "Installing to $INSTALL_BIN ..."
cp "$SCRIPT_SRC" "$INSTALL_BIN"
chmod 755 "$INSTALL_BIN"

# Ensure Python3 is available
PYTHON_BIN=$(command -v python3 || command -v python || true)
[[ -z "$PYTHON_BIN" ]] && err "Python3 not found. Install it with: apt install python3"

# Patch shebang to use the system python3
sed -i "1s|^#!.*|#!${PYTHON_BIN}|" "$INSTALL_BIN"

ok "Binary installed → $INSTALL_BIN"

# ── Step 4: Create config directory ──────────────────────────
mkdir -p /etc/mizonam
ok "Config directory ready → /etc/mizonam"

# ── Step 5: Create systemd service ───────────────────────────
info "Writing systemd unit..."

cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Mizonam – Asymmetric Upload Balancer
After=network.target
Wants=network.target

[Service]
ExecStart=${INSTALL_BIN} daemon
Restart=always
RestartSec=15
User=root
StandardOutput=null
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

ok "Service file written → $SERVICE_FILE"

# ── Step 6: Enable & start ────────────────────────────────────
info "Reloading systemd..."
systemctl daemon-reload

info "Enabling service (auto-start on boot)..."
systemctl enable mizonam

info "Starting service..."
systemctl restart mizonam

# Give it a moment to start
sleep 2

if systemctl is-active --quiet mizonam; then
    ok "Service is running"
else
    warn "Service may have failed to start. Check with:  journalctl -u mizonam -n 30"
fi

# ── Cleanup ───────────────────────────────────────────────────
[[ -f /tmp/mizonam_main.py ]] && rm -f /tmp/mizonam_main.py

# ── Done ──────────────────────────────────────────────────────
echo ""
echo -e "${GR}${B}  Installation complete!${R}"
echo ""
echo -e "  ${CY}mizonam menu${R}          → interactive dashboard"
echo -e "  ${CY}mizonam status${R}        → quick status"
echo -e "  ${CY}systemctl status mizonam${R}  → service status"
echo -e "  ${CY}journalctl -u mizonam -f${R}  → live logs"
echo ""