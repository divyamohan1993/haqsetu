# =============================================================================
# HaqSetu - Terraform Configuration for Google Cloud Platform
# Voice-First AI Civic Assistant for Rural India
#
# Flipping var.environment from "development" to "production" scales
# the entire stack to India-level deployment.
# =============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }

  # Production should use a GCS backend; configure via backend.tf or CLI
  # terraform init -backend-config="bucket=haqsetu-tfstate-PROJECTID"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  is_production = var.environment == "production"
  labels = {
    app         = var.app_name
    environment = var.environment
    managed_by  = "terraform"
  }
}

# =============================================================================
# Enable Required GCP APIs
# =============================================================================

resource "google_project_service" "apis" {
  for_each = toset([
    "translate.googleapis.com",
    "speech.googleapis.com",
    "texttospeech.googleapis.com",
    "aiplatform.googleapis.com",
    "firestore.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "secretmanager.googleapis.com",
    "redis.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
  ])

  project                    = var.project_id
  service                    = each.value
  disable_dependent_services = false
  disable_on_destroy         = false
}

# =============================================================================
# Artifact Registry - Docker Image Repository
# =============================================================================

resource "google_artifact_registry_repository" "docker" {
  location      = var.region
  repository_id = "${var.app_name}-docker"
  description   = "Docker images for ${var.app_name}"
  format        = "DOCKER"
  labels        = local.labels

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = local.is_production ? 10 : 5
    }
  }

  depends_on = [google_project_service.apis["artifactregistry.googleapis.com"]]
}

# =============================================================================
# Service Account
# =============================================================================

resource "google_service_account" "app" {
  account_id   = "${var.app_name}-app"
  display_name = "HaqSetu Application Service Account"
  description  = "Service account for the ${var.app_name} Cloud Run service"
}

# IAM roles for the application service account
locals {
  app_roles = [
    "roles/datastore.user",           # Firestore access
    "roles/storage.objectAdmin",       # Cloud Storage access
    "roles/aiplatform.user",           # Vertex AI access
    "roles/cloudtranslate.user",       # Translation API
    "roles/secretmanager.secretAccessor", # Secret Manager
    "roles/logging.logWriter",         # Cloud Logging
    "roles/monitoring.metricWriter",   # Cloud Monitoring
  ]
}

resource "google_project_iam_member" "app_roles" {
  for_each = toset(local.app_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.app.email}"
}

# =============================================================================
# Secret Manager - Sensitive Configuration
# =============================================================================

resource "google_secret_manager_secret" "encryption_key" {
  secret_id = "${var.app_name}-encryption-key"
  labels    = local.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

resource "google_secret_manager_secret" "redis_password" {
  count     = local.is_production ? 1 : 0
  secret_id = "${var.app_name}-redis-password"
  labels    = local.labels

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}

# =============================================================================
# Firestore Database
# =============================================================================

resource "google_firestore_database" "main" {
  project         = var.project_id
  name            = "${var.app_name}-db"
  location_id     = var.region
  type            = "FIRESTORE_NATIVE"
  deletion_policy = local.is_production ? "DELETE" : "DELETE"

  depends_on = [google_project_service.apis["firestore.googleapis.com"]]
}

# =============================================================================
# Cloud Storage - Audio Files and Data
# =============================================================================

resource "google_storage_bucket" "data" {
  name          = "${var.project_id}-${var.app_name}-data"
  location      = "asia-south1"
  force_destroy = !local.is_production
  labels        = local.labels

  uniform_bucket_level_access = true

  versioning {
    enabled = local.is_production
  }

  lifecycle_rule {
    condition {
      age = local.is_production ? 365 : 30
    }
    action {
      type = "Delete"
    }
  }

  # Audio uploads typically accessed within 30 days
  lifecycle_rule {
    condition {
      age                = 30
      matches_storage_class = ["STANDARD"]
    }
    action {
      type          = "SetStorageClass"
      storage_class = "NEARLINE"
    }
  }

  cors {
    origin          = var.cors_allowed_origins
    method          = ["GET", "HEAD", "PUT", "POST"]
    response_header = ["Content-Type"]
    max_age_seconds = 3600
  }
}

# =============================================================================
# Memorystore Redis
# =============================================================================

resource "google_redis_instance" "cache" {
  name           = "${var.app_name}-cache"
  tier           = local.is_production ? "STANDARD_HA" : "BASIC"
  memory_size_gb = local.is_production ? 4 : 1
  region         = var.region
  redis_version  = "REDIS_7_0"
  display_name   = "HaqSetu Cache"
  labels         = local.labels

  # Production: enable auth and persistence
  auth_enabled            = local.is_production
  transit_encryption_mode = local.is_production ? "SERVER_AUTHENTICATION" : "DISABLED"

  redis_configs = {
    maxmemory-policy = "allkeys-lru"
    notify-keyspace-events = ""
  }

  maintenance_policy {
    weekly_maintenance_window {
      day = "SUNDAY"
      start_time {
        hours   = 2
        minutes = 0
        seconds = 0
        nanos   = 0
      }
    }
  }

  depends_on = [google_project_service.apis["redis.googleapis.com"]]
}

# =============================================================================
# Cloud Run Service
# =============================================================================

resource "google_cloud_run_v2_service" "app" {
  name     = var.app_name
  location = var.region
  labels   = local.labels

  template {
    service_account = google_service_account.app.email

    scaling {
      min_instance_count = local.is_production ? 2 : 0
      max_instance_count = local.is_production ? 50 : 2
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}/${var.app_name}:latest"

      ports {
        container_port = 8000
      }

      resources {
        limits = {
          cpu    = local.is_production ? "4" : "1"
          memory = local.is_production ? "4Gi" : "512Mi"
        }
        cpu_idle          = !local.is_production
        startup_cpu_boost = true
      }

      # Environment variables
      env {
        name  = "HAQSETU_ENV"
        value = var.environment
      }
      env {
        name  = "GCP_PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "GCP_REGION"
        value = var.region
      }
      env {
        name  = "REDIS_URL"
        value = "redis://${google_redis_instance.cache.host}:${google_redis_instance.cache.port}/0"
      }
      env {
        name  = "FIRESTORE_DATABASE"
        value = google_firestore_database.main.name
      }
      env {
        name  = "VERTEX_AI_LOCATION"
        value = var.region
      }
      env {
        name  = "LOG_LEVEL"
        value = local.is_production ? "WARNING" : "INFO"
      }
      env {
        name  = "LOG_FORMAT"
        value = "json"
      }
      env {
        name  = "API_WORKERS"
        value = local.is_production ? "4" : "1"
      }

      # Sensitive config from Secret Manager
      env {
        name = "ENCRYPTION_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.encryption_key.secret_id
            version = "latest"
          }
        }
      }

      startup_probe {
        http_get {
          path = "/api/v1/health"
          port = 8000
        }
        initial_delay_seconds = 5
        period_seconds        = 10
        failure_threshold     = 3
      }

      liveness_probe {
        http_get {
          path = "/api/v1/health"
          port = 8000
        }
        period_seconds    = 30
        failure_threshold = 3
      }
    }

    # Cloud Run execution environment
    execution_environment = "EXECUTION_ENVIRONMENT_GEN2"

    # VPC connector for Redis access (requires VPC connector setup)
    dynamic "vpc_access" {
      for_each = var.vpc_connector_name != "" ? [1] : []
      content {
        connector = var.vpc_connector_name
        egress    = "PRIVATE_RANGES_ONLY"
      }
    }

    # Request timeout
    timeout = local.is_production ? "300s" : "60s"

    # Max concurrent requests per instance
    max_instance_request_concurrency = local.is_production ? 80 : 10
  }

  # Traffic routing - all to latest
  traffic {
    type    = "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST"
    percent = 100
  }

  depends_on = [
    google_project_service.apis["run.googleapis.com"],
    google_project_iam_member.app_roles,
  ]
}

# Allow unauthenticated access (public API)
resource "google_cloud_run_v2_service_iam_member" "public" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.app.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}
