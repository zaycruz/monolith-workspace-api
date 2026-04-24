#!/usr/bin/env bash
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────────
# Workspace API → GCP Cloud Run Deployment
#
# Prerequisites:
#   1. gcloud CLI authenticated: gcloud auth login
#   2. Docker authenticated to GCR: gcloud auth configure-docker
#   3. Cloud SQL PostgreSQL instance provisioned with 'workspace' database
#   4. Secrets created in GCP Secret Manager:
#      - raava-workspace-api-staging-DATABASE_URL
#      - raava-workspace-api-staging-WORKSPACE_SERVICE_TOKEN
#      - raava-workspace-api-staging-WORKSPACE_MACHINE_TOKEN_SIGNING_KEY
#   5. Service account with roles:
#      - roles/cloudsql.client
#      - roles/secretmanager.secretAccessor
# ──────────────────────────────────────────────────────────────────────────────

# ── Configuration (override via environment) ─────────────────────────────────
GCP_PROJECT="${GCP_PROJECT:?Set GCP_PROJECT}"
GCP_REGION="${GCP_REGION:-us-east1}"
SERVICE_NAME="${SERVICE_NAME:-raava-workspace-api}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
CLOUD_SQL_INSTANCE="${CLOUD_SQL_INSTANCE:-${GCP_PROJECT}:${GCP_REGION}:raava-fleet-staging}"

IMAGE="gcr.io/${GCP_PROJECT}/${SERVICE_NAME}:${IMAGE_TAG}"

required_secrets=(
  "raava-workspace-api-staging-DATABASE_URL"
  "raava-workspace-api-staging-WORKSPACE_SERVICE_TOKEN"
  "raava-workspace-api-staging-WORKSPACE_MACHINE_TOKEN_SIGNING_KEY"
)

secret_flags=()
for secret_name in "${required_secrets[@]}"; do
  if ! gcloud secrets describe "${secret_name}" --project "${GCP_PROJECT}" >/dev/null 2>&1; then
    echo "ERROR: Missing required secret in Secret Manager: ${secret_name}" >&2
    exit 1
  fi
  # Strip prefix for env var name inside container
  env_var_name="${secret_name#raava-workspace-api-staging-}"
  secret_flags+=(--update-secrets "${env_var_name}=${secret_name}:latest")
done

echo "==> Building image: ${IMAGE}"
docker build -t "${IMAGE}" .

echo "==> Pushing image to GCR"
docker push "${IMAGE}"

echo "==> Deploying to Cloud Run: ${SERVICE_NAME} in ${GCP_REGION}"
gcloud run deploy "${SERVICE_NAME}" \
    --project "${GCP_PROJECT}" \
    --region "${GCP_REGION}" \
    --image "${IMAGE}" \
    --platform managed \
    --port 8080 \
    --memory 512Mi \
    --cpu 1 \
    --min-instances 1 \
    --max-instances 3 \
    --cpu-boost \
    --execution-environment gen2 \
    --add-cloudsql-instances "${CLOUD_SQL_INSTANCE}" \
    --set-env-vars "AUTH_ENABLED=true" \
    --set-env-vars "CORS_ALLOWED_ORIGINS=*" \
    --set-env-vars "SENTRY_ENVIRONMENT=staging" \
    "${secret_flags[@]}" \
    --allow-unauthenticated

echo ""
echo "==> Deployment complete. Fetching service URL..."
URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --project "${GCP_PROJECT}" \
    --region "${GCP_REGION}" \
    --format 'value(status.url)')

echo "Workspace API is live at: ${URL}"
echo ""
echo "==> Smoke test:"
curl -s "${URL}/health" | python3 -m json.tool || echo "Health check failed"
echo ""
echo "==> Next steps:"
echo "  1. Store this URL as WORKSPACE_API_URL in Fleet API secrets"
echo "  2. Deploy Fleet API with WORKSPACE_SERVICE_TOKEN matching this service"
