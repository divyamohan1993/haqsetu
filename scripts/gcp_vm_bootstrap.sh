#!/usr/bin/env bash
# ==========================================================================
# HaqSetu — GCP VM Bootstrap Script
#
# Sets up a GCP Compute Engine VM for running HaqSetu with:
#   1. Docker + Docker Compose
#   2. Claude CLI (with device login for admin auto-fix)
#   3. GCP authentication (service account or workload identity)
#   4. Redis with AOF persistence
#   5. Automatic security hardening
#   6. Systemd service for auto-restart
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/divyamohan1993/haqsetu/main/scripts/gcp_vm_bootstrap.sh | bash
#
# Or with options:
#   ./gcp_vm_bootstrap.sh --with-claude --env production
#
# ==========================================================================
set -euo pipefail
IFS=$'\n\t'

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
HAQSETU_DIR="/opt/haqsetu"
HAQSETU_USER="haqsetu"
HAQSETU_REPO="https://github.com/divyamohan1993/haqsetu.git"
HAQSETU_BRANCH="main"
LOG_FILE="/var/log/haqsetu-bootstrap.log"
INSTALL_CLAUDE=false
HAQSETU_ENV="production"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --with-claude)
            INSTALL_CLAUDE=true
            shift
            ;;
        --env)
            HAQSETU_ENV="$2"
            shift 2
            ;;
        --branch)
            HAQSETU_BRANCH="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" | tee -a "$LOG_FILE" >&2
}

# ---------------------------------------------------------------------------
# Pre-flight checks
# ---------------------------------------------------------------------------
if [[ $EUID -ne 0 ]]; then
    error "This script must be run as root (use sudo)"
    exit 1
fi

log "=== HaqSetu GCP VM Bootstrap Starting ==="
log "Environment: $HAQSETU_ENV"
log "Install Claude CLI: $INSTALL_CLAUDE"

# ---------------------------------------------------------------------------
# 1. System updates and core packages
# ---------------------------------------------------------------------------
log "Step 1/8: Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    git \
    jq \
    unzip \
    python3 \
    python3-pip \
    python3-venv \
    ufw \
    fail2ban \
    >> "$LOG_FILE" 2>&1

# ---------------------------------------------------------------------------
# 2. Docker installation
# ---------------------------------------------------------------------------
log "Step 2/8: Installing Docker..."
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | sh >> "$LOG_FILE" 2>&1
    systemctl enable docker
    systemctl start docker
    log "Docker installed successfully"
else
    log "Docker already installed"
fi

# Install Docker Compose plugin
if ! docker compose version &>/dev/null; then
    apt-get install -y -qq docker-compose-plugin >> "$LOG_FILE" 2>&1
    log "Docker Compose plugin installed"
fi

# ---------------------------------------------------------------------------
# 3. Create application user
# ---------------------------------------------------------------------------
log "Step 3/8: Creating application user..."
if ! id "$HAQSETU_USER" &>/dev/null; then
    useradd --system --create-home --shell /bin/bash "$HAQSETU_USER"
    usermod -aG docker "$HAQSETU_USER"
    log "User '$HAQSETU_USER' created"
else
    log "User '$HAQSETU_USER' already exists"
fi

# ---------------------------------------------------------------------------
# 4. Clone repository
# ---------------------------------------------------------------------------
log "Step 4/8: Cloning HaqSetu repository..."
if [[ -d "$HAQSETU_DIR" ]]; then
    cd "$HAQSETU_DIR"
    git fetch origin "$HAQSETU_BRANCH"
    git checkout "$HAQSETU_BRANCH"
    git pull origin "$HAQSETU_BRANCH"
    log "Repository updated"
else
    git clone --branch "$HAQSETU_BRANCH" "$HAQSETU_REPO" "$HAQSETU_DIR"
    log "Repository cloned"
fi
chown -R "$HAQSETU_USER:$HAQSETU_USER" "$HAQSETU_DIR"

# ---------------------------------------------------------------------------
# 5. Generate cryptographic keys (if not already present)
# ---------------------------------------------------------------------------
log "Step 5/8: Generating security keys..."
ENV_FILE="$HAQSETU_DIR/.env"

if [[ ! -f "$ENV_FILE" ]]; then
    cp "$HAQSETU_DIR/.env.example" "$ENV_FILE" 2>/dev/null || true

    # Generate keys using openssl
    ENCRYPTION_KEY=$(openssl rand -base64 32)
    ADMIN_API_KEY=$(openssl rand -hex 32)
    SESSION_SECRET=$(openssl rand -hex 64)
    REDIS_PASSWORD=$(openssl rand -base64 48 | tr -dc 'a-zA-Z0-9' | head -c 64)

    # Write to .env
    cat >> "$ENV_FILE" <<ENVEOF

# === Auto-generated by bootstrap $(date '+%Y-%m-%d %H:%M:%S') ===
HAQSETU_ENV=$HAQSETU_ENV
ENCRYPTION_KEY=$ENCRYPTION_KEY
ADMIN_API_KEY=$ADMIN_API_KEY
HAQSETU_ADMIN_API_KEY=$ADMIN_API_KEY
HAQSETU_SESSION_SECRET=$SESSION_SECRET
REDIS_PASSWORD=$REDIS_PASSWORD
ENVEOF

    chmod 600 "$ENV_FILE"
    chown "$HAQSETU_USER:$HAQSETU_USER" "$ENV_FILE"
    log "Security keys generated and saved to .env"
    log "IMPORTANT: Admin API Key = $ADMIN_API_KEY"
    log "           Save this key securely — you will need it for admin operations."
else
    log ".env file already exists, skipping key generation"
fi

# ---------------------------------------------------------------------------
# 6. Firewall and security hardening
# ---------------------------------------------------------------------------
log "Step 6/8: Configuring firewall and security..."

# UFW firewall
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow 80/tcp    # HTTP
ufw allow 443/tcp   # HTTPS
ufw allow 8000/tcp  # HaqSetu API (direct access during dev)
yes | ufw enable >> "$LOG_FILE" 2>&1

# Fail2ban
systemctl enable fail2ban
systemctl start fail2ban

# Sysctl hardening
cat > /etc/sysctl.d/99-haqsetu.conf <<'SYSCTL'
# Disable IP forwarding
net.ipv4.ip_forward = 0
# Enable SYN flood protection
net.ipv4.tcp_syncookies = 1
# Disable ICMP redirects
net.ipv4.conf.all.accept_redirects = 0
net.ipv4.conf.default.accept_redirects = 0
# Disable source routing
net.ipv4.conf.all.accept_source_route = 0
# Enable reverse path filtering
net.ipv4.conf.all.rp_filter = 1
SYSCTL
sysctl --system >> "$LOG_FILE" 2>&1

log "Security hardening applied"

# ---------------------------------------------------------------------------
# 7. Install Claude CLI (optional — for admin auto-fix)
# ---------------------------------------------------------------------------
if [[ "$INSTALL_CLAUDE" == "true" ]]; then
    log "Step 7/8: Installing Claude CLI for admin auto-fix..."

    # Install Node.js (required by Claude CLI)
    if ! command -v node &>/dev/null; then
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - >> "$LOG_FILE" 2>&1
        apt-get install -y -qq nodejs >> "$LOG_FILE" 2>&1
        log "Node.js installed"
    fi

    # Install Claude CLI globally
    npm install -g @anthropic-ai/claude-code >> "$LOG_FILE" 2>&1
    log "Claude CLI installed"

    # Create Claude auto-fix wrapper script
    cat > "$HAQSETU_DIR/scripts/claude_autofix.sh" <<'CLAUDESCRIPT'
#!/usr/bin/env bash
# ==========================================================================
# Claude CLI Auto-Fix for HaqSetu
#
# Authenticates via device login and runs auto-fix on the codebase.
#
# Usage:
#   sudo -u haqsetu ./scripts/claude_autofix.sh
#
# The script will display a device login URL and code.
# Give the code to the admin to authorize on their Anthropic account.
# ==========================================================================
set -euo pipefail

HAQSETU_DIR="/opt/haqsetu"
cd "$HAQSETU_DIR"

echo "============================================="
echo "  HaqSetu — Claude CLI Auto-Fix"
echo "============================================="
echo ""
echo "Starting Claude CLI with device login..."
echo "A URL and code will appear below."
echo "Give the code to the super admin to authorize."
echo ""

# Run Claude with the auto-fix prompt
claude --dangerously-skip-permissions \
    "Analyze the HaqSetu codebase for issues. Run 'python -m pytest tests/ -x -q' to check test health. Then check src/api/v1/admin_recovery.py and src/services/autofix_orchestrator.py for any issues. Fix any test failures or bugs you find. Commit fixes with descriptive messages." \
    2>&1

echo ""
echo "Auto-fix session complete."
echo "Check git log for any changes made."
CLAUDESCRIPT

    chmod +x "$HAQSETU_DIR/scripts/claude_autofix.sh"
    chown "$HAQSETU_USER:$HAQSETU_USER" "$HAQSETU_DIR/scripts/claude_autofix.sh"
    log "Claude auto-fix script created at $HAQSETU_DIR/scripts/claude_autofix.sh"

    echo ""
    echo "============================================="
    echo "  Claude CLI Device Login Setup"
    echo "============================================="
    echo ""
    echo "To authenticate Claude CLI on this VM:"
    echo ""
    echo "  1. Run as haqsetu user:"
    echo "     sudo -u $HAQSETU_USER claude auth login"
    echo ""
    echo "  2. A device code and URL will appear."
    echo "     Give the code to the admin who has an"
    echo "     Anthropic account."
    echo ""
    echo "  3. Admin visits the URL, enters the code,"
    echo "     and authorizes the device."
    echo ""
    echo "  4. Once authorized, run auto-fix:"
    echo "     sudo -u $HAQSETU_USER $HAQSETU_DIR/scripts/claude_autofix.sh"
    echo ""
    echo "============================================="
else
    log "Step 7/8: Skipping Claude CLI installation (use --with-claude to enable)"
fi

# ---------------------------------------------------------------------------
# 8. Create systemd service and start application
# ---------------------------------------------------------------------------
log "Step 8/8: Creating systemd service..."

cat > /etc/systemd/system/haqsetu.service <<SYSTEMD
[Unit]
Description=HaqSetu — Voice-First AI Civic Assistant
Documentation=https://github.com/divyamohan1993/haqsetu
After=docker.service
Requires=docker.service

[Service]
Type=simple
User=$HAQSETU_USER
Group=$HAQSETU_USER
WorkingDirectory=$HAQSETU_DIR
ExecStartPre=/usr/bin/docker compose -f docker-compose.prod.yml pull
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml up
ExecStop=/usr/bin/docker compose -f docker-compose.prod.yml down
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Security hardening for the service
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$HAQSETU_DIR /var/log
ProtectHome=true

[Install]
WantedBy=multi-user.target
SYSTEMD

systemctl daemon-reload
systemctl enable haqsetu.service

# Start the application
log "Starting HaqSetu..."
cd "$HAQSETU_DIR"
sudo -u "$HAQSETU_USER" docker compose -f docker-compose.prod.yml up -d >> "$LOG_FILE" 2>&1

log ""
log "=== HaqSetu Bootstrap Complete ==="
log ""
log "Application: http://$(hostname -I | awk '{print $1}'):8000"
log "Health check: http://$(hostname -I | awk '{print $1}'):8000/api/v1/health"
log "Admin panel:  http://$(hostname -I | awk '{print $1}'):8000/#/admin"
log ""
log "Admin API Key is in: $ENV_FILE"
log "Logs: journalctl -u haqsetu -f"
log ""
if [[ "$INSTALL_CLAUDE" == "true" ]]; then
    log "Claude CLI: Run 'sudo -u $HAQSETU_USER claude auth login' to authenticate"
    log "Auto-fix:   Run 'sudo -u $HAQSETU_USER $HAQSETU_DIR/scripts/claude_autofix.sh'"
fi
