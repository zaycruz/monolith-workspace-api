variable "aws_region" {
  description = "AWS region. App Runner must share a region with consumers (Graviton4 agents live in us-east-1)."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment tag (production, staging, etc.)."
  type        = string
  default     = "production"
}

variable "service_name" {
  description = "App Runner + ECR service name. Keep in sync with the GitHub Actions workflow."
  type        = string
  default     = "monolith-workspace-api"
}

variable "image_tag" {
  description = "ECR image tag to deploy. CI usually pushes both `latest` and the commit SHA."
  type        = string
  default     = "latest"
}

variable "cpu" {
  description = "App Runner instance CPU (0.25, 0.5, 1, 2, 4 vCPU)."
  type        = string
  default     = "1 vCPU"
}

variable "memory" {
  description = "App Runner instance memory (0.5, 1, 2, 3, 4 GB)."
  type        = string
  default     = "2 GB"
}

variable "min_size" {
  description = "Minimum provisioned App Runner instances."
  type        = number
  default     = 1
}

variable "max_size" {
  description = "Maximum App Runner instances under autoscaling."
  type        = number
  default     = 10
}

variable "max_concurrency" {
  description = "Requests per instance before scaling out."
  type        = number
  default     = 100
}

variable "log_retention_days" {
  description = "CloudWatch log retention in days."
  type        = number
  default     = 30
}

variable "custom_domain" {
  description = "Cloudflare-managed CNAME target for this App Runner service."
  type        = string
  default     = "api-workspace.raavasolutions.com"
}

variable "enable_custom_domain" {
  description = "If true, create the App Runner custom-domain association. Set false for initial bootstrap before DNS is pointed."
  type        = bool
  default     = false
}

variable "secrets" {
  description = <<-EOT
    Map of environment-variable names to Secrets Manager secret names. App Runner
    injects these at runtime. Create the Secrets Manager secrets out-of-band
    (AWS console or a separate TF stack) and reference their names here.
  EOT
  type        = map(string)
  default = {
    SUPABASE_URL                        = "monolith/workspace-api/SUPABASE_URL"
    SUPABASE_SERVICE_ROLE_KEY           = "monolith/workspace-api/SUPABASE_SERVICE_ROLE_KEY"
    CLERK_SECRET_KEY                    = "monolith/workspace-api/CLERK_SECRET_KEY"
    CLERK_JWKS_URL                      = "monolith/workspace-api/CLERK_JWKS_URL"
    WORKSPACE_MACHINE_TOKEN_SIGNING_KEY = "monolith/workspace-api/WORKSPACE_MACHINE_TOKEN_SIGNING_KEY"
    DATABASE_URL                        = "monolith/workspace-api/DATABASE_URL"
  }
}

variable "runtime_env" {
  description = "Plain environment variables (non-secret) injected into the running App Runner service."
  type        = map(string)
  default = {
    ENVIRONMENT           = "production"
    VERIFY_CLERK          = "true"
    AUTH_ENABLED          = "true"
    SSE_HEARTBEAT_SECONDS = "15"
    PORT                  = "8080"
    HOST                  = "0.0.0.0"
  }
}

variable "github_owner" {
  description = "GitHub org/user that owns the source repo. Used to scope the OIDC trust policy."
  type        = string
  default     = "raava-solutions"
}

variable "github_repo" {
  description = "GitHub repo name (without owner)."
  type        = string
  default     = "monolith-workspace-api"
}
