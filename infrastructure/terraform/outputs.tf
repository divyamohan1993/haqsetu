# =============================================================================
# HaqSetu - Terraform Outputs
# =============================================================================

output "cloud_run_url" {
  value       = google_cloud_run_v2_service.app.uri
  description = "The URL of the deployed HaqSetu Cloud Run service."
}

output "redis_host" {
  value       = google_redis_instance.cache.host
  description = "The IP address of the Memorystore Redis instance."
  sensitive   = false
}

output "redis_port" {
  value       = google_redis_instance.cache.port
  description = "The port of the Memorystore Redis instance."
}

output "artifact_registry_url" {
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker.repository_id}"
  description = "The Artifact Registry repository URL for Docker images."
}

output "service_account_email" {
  value       = google_service_account.app.email
  description = "The email of the application service account."
}

output "firestore_database" {
  value       = google_firestore_database.main.name
  description = "The Firestore database name."
}

output "storage_bucket" {
  value       = google_storage_bucket.data.name
  description = "The Cloud Storage bucket name for audio and data files."
}
