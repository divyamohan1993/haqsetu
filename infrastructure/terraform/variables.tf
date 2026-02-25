# =============================================================================
# HaqSetu - Terraform Variables
# =============================================================================

variable "project_id" {
  type        = string
  description = "GCP project ID where HaqSetu resources will be created."

  validation {
    condition     = length(var.project_id) > 0
    error_message = "project_id must not be empty."
  }
}

variable "region" {
  type        = string
  default     = "asia-south1"
  description = "GCP region for deploying resources. Default: asia-south1 (Mumbai) for lowest latency in India."

  validation {
    condition     = can(regex("^[a-z]+-[a-z]+[0-9]$", var.region))
    error_message = "region must be a valid GCP region (e.g., asia-south1)."
  }
}

variable "environment" {
  type        = string
  default     = "development"
  description = "Deployment environment. Controls resource sizing, scaling, and HA configuration."

  validation {
    condition     = contains(["development", "production"], var.environment)
    error_message = "environment must be 'development' or 'production'."
  }
}

variable "app_name" {
  type        = string
  default     = "haqsetu"
  description = "Application name used as a prefix for all resource names."

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,20}$", var.app_name))
    error_message = "app_name must be 3-21 lowercase alphanumeric characters or hyphens, starting with a letter."
  }
}

variable "vpc_connector_name" {
  type        = string
  default     = ""
  description = "Optional VPC connector name for Cloud Run to access Memorystore Redis. If empty, VPC access is not configured."
}

variable "cors_allowed_origins" {
  type        = list(string)
  default     = ["https://haqsetu.in", "https://www.haqsetu.in"]
  description = "Allowed CORS origins for the Cloud Storage bucket. Restrict to your actual domains."
}
