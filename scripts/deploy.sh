#!/bin/bash
# =============================================================================
# HaqSetu - Idempotent Deployment Script
# Deploys infrastructure and application to Google Cloud Platform.
#
# Usage:
#   ENVIRONMENT=development GCP_PROJECT_ID=my-project ./scripts/deploy.sh
#   ENVIRONMENT=production  GCP_PROJECT_ID=my-project ./scripts/deploy.sh
#
# This script is safe to run multiple times. Each step checks whether it has
# already been completed and skips if so.
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ENVIRONMENT="${ENVIRONMENT:-development}"
GCP_PROJECT_ID="${GCP_PROJECT_ID:-}"
GCP_REGION="${GCP_REGION:-asia-south1}"
APP_NAME="${APP_NAME:-haqsetu}"
REPO_NAME="${APP_NAME}-docker"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TERRAFORM_DIR="${PROJECT_ROOT}/infrastructure/terraform"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------

log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

die() {
    log_error "$@"
    exit 1
}

# ---------------------------------------------------------------------------
# Step 0: Validate prerequisites
# ---------------------------------------------------------------------------
check_prerequisites() {
    log_info "Checking prerequisites..."

    local missing=()

    command -v gcloud  >/dev/null 2>&1 || missing+=("gcloud (Google Cloud SDK)")
    command -v terraform >/dev/null 2>&1 || missing+=("terraform")
    command -v docker  >/dev/null 2>&1 || missing+=("docker")

    if [[ ${#missing[@]} -gt 0 ]]; then
        die "Missing required tools: ${missing[*]}"
    fi

    # Validate required environment variables
    if [[ -z "${GCP_PROJECT_ID}" ]]; then
        die "GCP_PROJECT_ID environment variable is required."
    fi

    # Validate environment value
    if [[ "${ENVIRONMENT}" != "development" && "${ENVIRONMENT}" != "production" ]]; then
        die "ENVIRONMENT must be 'development' or 'production'. Got: '${ENVIRONMENT}'"
    fi

    log_ok "All prerequisites satisfied."
    log_info "Environment: ${ENVIRONMENT}"
    log_info "Project:     ${GCP_PROJECT_ID}"
    log_info "Region:      ${GCP_REGION}"
}

# ---------------------------------------------------------------------------
# Step 1: Set GCP project
# ---------------------------------------------------------------------------
set_gcp_project() {
    log_info "Setting GCP project to ${GCP_PROJECT_ID}..."

    local current_project
    current_project="$(gcloud config get-value project 2>/dev/null || true)"

    if [[ "${current_project}" == "${GCP_PROJECT_ID}" ]]; then
        log_ok "GCP project already set to ${GCP_PROJECT_ID}."
    else
        gcloud config set project "${GCP_PROJECT_ID}"
        log_ok "GCP project set to ${GCP_PROJECT_ID}."
    fi
}

# ---------------------------------------------------------------------------
# Step 2: Enable GCP APIs (idempotent - safe to re-run)
# ---------------------------------------------------------------------------
enable_apis() {
    log_info "Enabling required GCP APIs (idempotent)..."

    local apis=(
        "translate.googleapis.com"
        "speech.googleapis.com"
        "texttospeech.googleapis.com"
        "aiplatform.googleapis.com"
        "firestore.googleapis.com"
        "run.googleapis.com"
        "cloudbuild.googleapis.com"
        "secretmanager.googleapis.com"
        "redis.googleapis.com"
        "artifactregistry.googleapis.com"
        "cloudresourcemanager.googleapis.com"
        "iam.googleapis.com"
    )

    # Enable all APIs in a single call (gcloud handles already-enabled APIs gracefully)
    gcloud services enable "${apis[@]}" --project="${GCP_PROJECT_ID}"
    log_ok "All required APIs enabled."
}

# ---------------------------------------------------------------------------
# Step 2b: Rotate secrets (backup .env BEFORE writing new values)
# ---------------------------------------------------------------------------
rotate_secrets() {
    log_info "Rotating deployment secrets..."

    local env_file="${PROJECT_ROOT}/.env"

    if [[ ! -f "${env_file}" ]]; then
        log_warn "No .env file found at ${env_file}. Skipping secret rotation."
        return 0
    fi

    # ── Backup FIRST, before any mutation ──────────────────────────────
    local backup_file="${env_file}.backup.$(date +%Y%m%d%H%M%S)"
    cp "${env_file}" "${backup_file}"
    log_ok "Backed up .env to ${backup_file} (contains previous secrets)."

    # ── Now generate and write new secrets ─────────────────────────────
    if [[ "${ENVIRONMENT}" == "production" ]]; then
        # Rotate ENCRYPTION_KEY if it's still the placeholder
        local current_key
        current_key="$(grep -E '^ENCRYPTION_KEY=' "${env_file}" | cut -d= -f2- || true)"
        if [[ "${current_key}" == "your-256-bit-encryption-key-base64" || -z "${current_key}" ]]; then
            local new_key
            new_key="$(openssl rand -base64 32)"
            if grep -q '^ENCRYPTION_KEY=' "${env_file}"; then
                sed -i "s|^ENCRYPTION_KEY=.*|ENCRYPTION_KEY=${new_key}|" "${env_file}"
            else
                echo "ENCRYPTION_KEY=${new_key}" >> "${env_file}"
            fi
            log_ok "ENCRYPTION_KEY rotated."
        fi

        # Generate ADMIN_API_KEY if not set
        local current_admin_key
        current_admin_key="$(grep -E '^ADMIN_API_KEY=' "${env_file}" | cut -d= -f2- || true)"
        if [[ -z "${current_admin_key}" ]]; then
            local new_admin_key
            new_admin_key="$(openssl rand -hex 32)"
            if grep -q '^ADMIN_API_KEY=' "${env_file}"; then
                sed -i "s|^ADMIN_API_KEY=.*|ADMIN_API_KEY=${new_admin_key}|" "${env_file}"
            else
                echo "ADMIN_API_KEY=${new_admin_key}" >> "${env_file}"
            fi
            log_ok "ADMIN_API_KEY generated."
        fi
    fi

    log_ok "Secret rotation complete. Prior secrets preserved in ${backup_file}."
}

# ---------------------------------------------------------------------------
# Step 3: Run Terraform
# ---------------------------------------------------------------------------
run_terraform() {
    log_info "Running Terraform in ${TERRAFORM_DIR}..."

    cd "${TERRAFORM_DIR}"

    # Initialize Terraform (idempotent)
    if [[ ! -d ".terraform" ]]; then
        log_info "Initializing Terraform..."
        terraform init
    else
        log_info "Terraform already initialized. Running init to check for updates..."
        terraform init -upgrade=false
    fi

    # Plan and apply
    log_info "Applying Terraform configuration..."
    terraform apply \
        -auto-approve \
        -var="project_id=${GCP_PROJECT_ID}" \
        -var="region=${GCP_REGION}" \
        -var="environment=${ENVIRONMENT}" \
        -var="app_name=${APP_NAME}"

    # Capture outputs
    CLOUD_RUN_URL="$(terraform output -raw cloud_run_url 2>/dev/null || echo 'pending')"
    ARTIFACT_REGISTRY_URL="$(terraform output -raw artifact_registry_url 2>/dev/null || echo '')"

    cd "${PROJECT_ROOT}"
    log_ok "Terraform apply completed."
}

# ---------------------------------------------------------------------------
# Step 4: Build and push Docker image
# ---------------------------------------------------------------------------
build_and_push_image() {
    log_info "Building and pushing Docker image..."

    local image_url="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${REPO_NAME}/${APP_NAME}"
    local timestamp
    timestamp="$(date +%Y%m%d%H%M%S)"
    local image_tag="${image_url}:${timestamp}"
    local image_latest="${image_url}:latest"

    # Configure Docker to use gcloud credential helper (idempotent)
    gcloud auth configure-docker "${GCP_REGION}-docker.pkg.dev" --quiet

    # Build the image
    log_info "Building Docker image..."
    docker build \
        -t "${image_tag}" \
        -t "${image_latest}" \
        --platform linux/amd64 \
        "${PROJECT_ROOT}"

    # Push both tags
    log_info "Pushing Docker image..."
    docker push "${image_tag}"
    docker push "${image_latest}"

    IMAGE_TAG="${image_tag}"
    log_ok "Docker image pushed: ${image_tag}"
}

# ---------------------------------------------------------------------------
# Step 5: Deploy to Cloud Run
# ---------------------------------------------------------------------------
deploy_to_cloud_run() {
    log_info "Deploying to Cloud Run..."

    local image_url="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/${REPO_NAME}/${APP_NAME}:latest"

    # Cloud Run deploy (idempotent - updates or creates)
    gcloud run deploy "${APP_NAME}" \
        --image="${image_url}" \
        --region="${GCP_REGION}" \
        --platform=managed \
        --set-env-vars="HAQSETU_ENV=${ENVIRONMENT},GCP_PROJECT_ID=${GCP_PROJECT_ID},GCP_REGION=${GCP_REGION}" \
        --service-account="${APP_NAME}-app@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
        --allow-unauthenticated \
        --quiet

    log_ok "Cloud Run deployment completed."
}

# ---------------------------------------------------------------------------
# Step 6: Print deployment summary
# ---------------------------------------------------------------------------
print_summary() {
    local cloud_run_url
    cloud_run_url="$(gcloud run services describe "${APP_NAME}" \
        --region="${GCP_REGION}" \
        --format='value(status.url)' 2>/dev/null || echo 'unknown')"

    echo ""
    echo "============================================================================="
    echo -e "${GREEN}  HaqSetu Deployment Complete${NC}"
    echo "============================================================================="
    echo ""
    echo "  Environment:  ${ENVIRONMENT}"
    echo "  Project:      ${GCP_PROJECT_ID}"
    echo "  Region:       ${GCP_REGION}"
    echo "  Service URL:  ${cloud_run_url}"
    echo ""
    if [[ "${ENVIRONMENT}" == "production" ]]; then
        echo -e "  ${YELLOW}PRODUCTION deployment. Auto-scaling: 2-50 instances.${NC}"
    else
        echo -e "  ${BLUE}Development deployment. Auto-scaling: 0-2 instances.${NC}"
    fi
    echo ""
    echo "  Next steps:"
    echo "    - Verify health: curl ${cloud_run_url}/health"
    echo "    - View logs:     gcloud run logs read ${APP_NAME} --region=${GCP_REGION}"
    echo "    - Seed data:     make seed"
    echo ""
    echo "============================================================================="
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
main() {
    echo ""
    echo "============================================================================="
    echo "  HaqSetu Deployment - ${ENVIRONMENT}"
    echo "============================================================================="
    echo ""

    check_prerequisites
    set_gcp_project
    rotate_secrets
    enable_apis
    run_terraform
    build_and_push_image
    deploy_to_cloud_run
    print_summary
}

main "$@"
