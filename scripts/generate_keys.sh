#!/bin/bash
# =============================================================================
# HaqSetu - Secure Key Generation & Rotation Script
#
# Generates cryptographically secure keys on first run.
# Rotates keys on consecutive runs while preserving a backup.
#
# Usage:
#   ./scripts/generate_keys.sh              # Generate or rotate keys
#   ./scripts/generate_keys.sh --rotate     # Force rotate all keys
#   ./scripts/generate_keys.sh --init       # First-time generation only
#
# Security:
#   - Uses /dev/urandom via openssl for CSPRNG
#   - Keys are written to .env with mode 600
#   - Old keys are backed up to .env.backup.TIMESTAMP
#   - Backup files are mode 600
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"
ENV_EXAMPLE="${PROJECT_ROOT}/.env.example"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ---------------------------------------------------------------------------
# Key generation functions using CSPRNG
# ---------------------------------------------------------------------------

generate_encryption_key() {
    # 256-bit key, base64 encoded (for AES-256)
    openssl rand -base64 32
}

generate_redis_password() {
    # 48 bytes of random data, base64 encoded (strong password)
    openssl rand -base64 48 | tr -d '=/+' | head -c 64
}

generate_api_admin_key() {
    # 32 bytes hex-encoded API key
    openssl rand -hex 32
}

generate_session_secret() {
    # 64 bytes hex-encoded session secret
    openssl rand -hex 64
}

# ---------------------------------------------------------------------------
# .env file manipulation
# ---------------------------------------------------------------------------

set_env_value() {
    local key="$1"
    local value="$2"
    local file="$3"

    if grep -q "^${key}=" "${file}" 2>/dev/null; then
        # Key exists: replace the value (handles special chars in value)
        local escaped_value
        escaped_value=$(printf '%s\n' "${value}" | sed 's/[&/\]/\\&/g')
        sed -i "s|^${key}=.*|${key}=${escaped_value}|" "${file}"
    else
        # Key doesn't exist: append it
        echo "${key}=${value}" >> "${file}"
    fi
}

get_env_value() {
    local key="$1"
    local file="$2"
    grep "^${key}=" "${file}" 2>/dev/null | cut -d= -f2-
}

is_placeholder() {
    local value="$1"
    # Returns 0 (true) if the value is a placeholder or empty
    case "${value}" in
        ""|"your-"*|"changeme"|"replace-"*|"CHANGE_ME"*|"TODO"*|"xxx"*)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

MODE="${1:-auto}"

main() {
    echo ""
    echo "============================================================================="
    echo "  HaqSetu - Secure Key Generation"
    echo "============================================================================="
    echo ""

    # Ensure openssl is available
    if ! command -v openssl >/dev/null 2>&1; then
        log_error "openssl is required but not found. Please install it."
        exit 1
    fi

    # Create .env from .env.example if it doesn't exist
    if [[ ! -f "${ENV_FILE}" ]]; then
        if [[ -f "${ENV_EXAMPLE}" ]]; then
            cp "${ENV_EXAMPLE}" "${ENV_FILE}"
            chmod 600 "${ENV_FILE}"
            log_ok "Created .env from .env.example"
        else
            touch "${ENV_FILE}"
            chmod 600 "${ENV_FILE}"
            log_warn "Created empty .env file"
        fi
    fi

    # Ensure .env has restrictive permissions
    chmod 600 "${ENV_FILE}"

    local rotated=false
    local generated=false

    # --- ENCRYPTION_KEY ---
    local current_enc_key
    current_enc_key="$(get_env_value 'ENCRYPTION_KEY' "${ENV_FILE}")"

    if [[ "${MODE}" == "--rotate" ]] || is_placeholder "${current_enc_key}"; then
        if [[ -n "${current_enc_key}" ]] && ! is_placeholder "${current_enc_key}"; then
            log_warn "Rotating ENCRYPTION_KEY (old key backed up)"
            rotated=true
        else
            generated=true
        fi
        local new_enc_key
        new_enc_key="$(generate_encryption_key)"
        set_env_value "ENCRYPTION_KEY" "${new_enc_key}" "${ENV_FILE}"
        log_ok "ENCRYPTION_KEY set (256-bit AES key, base64 encoded)"
    else
        log_info "ENCRYPTION_KEY already set, skipping (use --rotate to force)"
    fi

    # --- REDIS_PASSWORD ---
    local current_redis_pw
    current_redis_pw="$(get_env_value 'REDIS_PASSWORD' "${ENV_FILE}")"

    if [[ "${MODE}" == "--rotate" ]] || is_placeholder "${current_redis_pw}"; then
        if [[ -n "${current_redis_pw}" ]] && ! is_placeholder "${current_redis_pw}"; then
            log_warn "Rotating REDIS_PASSWORD (old password backed up)"
            rotated=true
        else
            generated=true
        fi
        local new_redis_pw
        new_redis_pw="$(generate_redis_password)"
        set_env_value "REDIS_PASSWORD" "${new_redis_pw}" "${ENV_FILE}"
        log_ok "REDIS_PASSWORD set (64-char random password)"
    else
        log_info "REDIS_PASSWORD already set, skipping (use --rotate to force)"
    fi

    # --- HAQSETU_ADMIN_API_KEY ---
    local current_admin_key
    current_admin_key="$(get_env_value 'HAQSETU_ADMIN_API_KEY' "${ENV_FILE}")"

    if [[ "${MODE}" == "--rotate" ]] || is_placeholder "${current_admin_key}"; then
        if [[ -n "${current_admin_key}" ]] && ! is_placeholder "${current_admin_key}"; then
            log_warn "Rotating HAQSETU_ADMIN_API_KEY (old key backed up)"
            rotated=true
        else
            generated=true
        fi
        local new_admin_key
        new_admin_key="$(generate_api_admin_key)"
        set_env_value "HAQSETU_ADMIN_API_KEY" "${new_admin_key}" "${ENV_FILE}"
        log_ok "HAQSETU_ADMIN_API_KEY set (64-char hex API key)"
    else
        log_info "HAQSETU_ADMIN_API_KEY already set, skipping (use --rotate to force)"
    fi

    # --- HAQSETU_SESSION_SECRET ---
    local current_session_secret
    current_session_secret="$(get_env_value 'HAQSETU_SESSION_SECRET' "${ENV_FILE}")"

    if [[ "${MODE}" == "--rotate" ]] || is_placeholder "${current_session_secret}"; then
        if [[ -n "${current_session_secret}" ]] && ! is_placeholder "${current_session_secret}"; then
            log_warn "Rotating HAQSETU_SESSION_SECRET (old secret backed up)"
            rotated=true
        else
            generated=true
        fi
        local new_session_secret
        new_session_secret="$(generate_session_secret)"
        set_env_value "HAQSETU_SESSION_SECRET" "${new_session_secret}" "${ENV_FILE}"
        log_ok "HAQSETU_SESSION_SECRET set (128-char hex secret)"
    else
        log_info "HAQSETU_SESSION_SECRET already set, skipping (use --rotate to force)"
    fi

    # Backup if we rotated any keys
    if [[ "${rotated}" == "true" ]]; then
        local backup_file="${ENV_FILE}.backup.$(date +%Y%m%d%H%M%S)"
        cp "${ENV_FILE}" "${backup_file}"
        chmod 600 "${backup_file}"
        log_ok "Previous .env backed up to ${backup_file}"
    fi

    echo ""
    echo "============================================================================="
    if [[ "${generated}" == "true" ]]; then
        echo -e "  ${GREEN}Keys generated successfully.${NC}"
    elif [[ "${rotated}" == "true" ]]; then
        echo -e "  ${GREEN}Keys rotated successfully.${NC}"
    else
        echo -e "  ${BLUE}All keys already set. No changes made.${NC}"
    fi
    echo ""
    echo "  IMPORTANT:"
    echo "    - .env file permissions set to 600 (owner read/write only)"
    echo "    - NEVER commit .env to version control"
    echo "    - For production, use GCP Secret Manager instead of .env"
    echo ""
    if [[ "${rotated}" == "true" ]]; then
        echo -e "  ${YELLOW}WARNING: If Redis is running, update its password and restart.${NC}"
        echo -e "  ${YELLOW}WARNING: Active sessions will be invalidated after key rotation.${NC}"
    fi
    echo "============================================================================="
    echo ""
}

main "$@"
