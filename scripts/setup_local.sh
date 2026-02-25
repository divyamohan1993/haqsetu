#!/bin/bash
# =============================================================================
# HaqSetu - Local Development Setup
# Sets up the local development environment.
#
# Usage: ./scripts/setup_local.sh
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

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
# Step 1: Check Python version
# ---------------------------------------------------------------------------
check_python() {
    log_info "Checking Python version..."

    if ! command -v python3 >/dev/null 2>&1; then
        log_error "Python 3 is not installed. Please install Python 3.12+."
        exit 1
    fi

    local python_version
    python_version="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    local major minor
    major="$(echo "${python_version}" | cut -d. -f1)"
    minor="$(echo "${python_version}" | cut -d. -f2)"

    if [[ "${major}" -lt 3 ]] || { [[ "${major}" -eq 3 ]] && [[ "${minor}" -lt 12 ]]; }; then
        log_error "Python 3.12+ is required. Found: Python ${python_version}"
        exit 1
    fi

    log_ok "Python ${python_version} found."
}

# ---------------------------------------------------------------------------
# Step 2: Create virtual environment
# ---------------------------------------------------------------------------
setup_venv() {
    log_info "Setting up virtual environment..."

    local venv_dir="${PROJECT_ROOT}/.venv"

    if [[ -d "${venv_dir}" ]]; then
        log_ok "Virtual environment already exists at ${venv_dir}"
    else
        python3 -m venv "${venv_dir}"
        log_ok "Virtual environment created at ${venv_dir}"
    fi

    # Activate venv for subsequent commands
    # shellcheck disable=SC1091
    source "${venv_dir}/bin/activate"

    # Upgrade pip
    pip install --upgrade pip --quiet
    log_ok "pip upgraded."
}

# ---------------------------------------------------------------------------
# Step 3: Install dependencies
# ---------------------------------------------------------------------------
install_deps() {
    log_info "Installing dependencies..."

    cd "${PROJECT_ROOT}"

    # Install the package with dev dependencies
    pip install -e ".[dev]" --quiet
    log_ok "All dependencies installed (including dev extras)."
}

# ---------------------------------------------------------------------------
# Step 4: Set up environment file
# ---------------------------------------------------------------------------
setup_env_file() {
    log_info "Setting up environment file..."

    local env_file="${PROJECT_ROOT}/.env"
    local env_example="${PROJECT_ROOT}/.env.example"

    if [[ -f "${env_file}" ]]; then
        log_ok ".env file already exists. Skipping copy."
    elif [[ -f "${env_example}" ]]; then
        cp "${env_example}" "${env_file}"
        chmod 600 "${env_file}"
        log_ok ".env file created from .env.example"
        log_warn "Please edit .env and fill in your GCP credentials."
    else
        log_warn "No .env.example found. You will need to create a .env file manually."
    fi

    # Generate secure keys if they are placeholders
    local keygen_script="${PROJECT_ROOT}/scripts/generate_keys.sh"
    if [[ -x "${keygen_script}" ]]; then
        log_info "Generating secure keys..."
        "${keygen_script}" --init
    fi
}

# ---------------------------------------------------------------------------
# Step 5: Print instructions
# ---------------------------------------------------------------------------
print_instructions() {
    echo ""
    echo "============================================================================="
    echo -e "${GREEN}  HaqSetu Local Setup Complete${NC}"
    echo "============================================================================="
    echo ""
    echo "  Activate the virtual environment:"
    echo "    source .venv/bin/activate"
    echo ""
    echo "  Configure GCP credentials (choose one):"
    echo ""
    echo "    Option A - Application Default Credentials (recommended for dev):"
    echo "      gcloud auth application-default login"
    echo ""
    echo "    Option B - Service Account Key:"
    echo "      export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json"
    echo ""
    echo "  Edit your .env file:"
    echo "    - Set GCP_PROJECT_ID to your Google Cloud project"
    echo "    - Set ENCRYPTION_KEY to a base64-encoded 256-bit key"
    echo ""
    echo "  Run the application:"
    echo "    make dev          # Run with uvicorn (auto-reload)"
    echo "    make docker-dev   # Run with Docker Compose"
    echo ""
    echo "  Run tests:"
    echo "    make test"
    echo ""
    echo "============================================================================="
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    echo ""
    echo "============================================================================="
    echo "  HaqSetu - Local Development Setup"
    echo "============================================================================="
    echo ""

    cd "${PROJECT_ROOT}"

    check_python
    setup_venv
    install_deps
    setup_env_file
    print_instructions
}

main "$@"
