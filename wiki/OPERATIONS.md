# Operations

Everything an on-call agent or operator needs to run this service in
production without paging a human.

## Production topology

- **Runtime:** AWS App Runner, `us-east-1`.
- **Image registry:** ECR repo `monolith-workspace-api` (same account, same region).
- **Domain:** `api-workspace.raavasolutions.com` via Cloudflare CNAME.
- **DB:** Supabase Postgres (project ID stored in Secrets Manager as `DATABASE_URL`).
- **Secrets:** AWS Secrets Manager, injected into App Runner at start time.
- **Logs:** CloudWatch log group `/aws/apprunner/monolith-workspace-api/application`.
- **Autoscaling:** 1 min → 10 max instances, 100 concurrent req/instance target.

## First-time deploy

See `infra/terraform/README.md` for the full bootstrap sequence
(ECR → image push → `terraform apply` → DNS). Summary:

```bash
cd infra/terraform
terraform init
terraform apply -target=aws_ecr_repository.app         # repo-only

# In repo root:
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin \
    "${ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com"
docker build -t monolith-workspace-api:latest .
docker tag monolith-workspace-api:latest \
  "${ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/monolith-workspace-api:latest"
docker push "${ACCOUNT}.dkr.ecr.us-east-1.amazonaws.com/monolith-workspace-api:latest"

cd infra/terraform && terraform apply                   # full stack
```

## Ongoing deploys (CI)

Merging to `main` runs `.github/workflows/deploy.yml`:

1. `test` — `uv run ruff check` + `uv run pytest` with `WORKSPACE_TEST_SQLITE=1`.
2. `build-and-push` — OIDC into AWS, `docker buildx build --push` to ECR
   with two tags: `latest` and the 12-char commit SHA.
3. `deploy` — `aws apprunner start-deployment`, then poll
   `describe-service` until `Status == RUNNING`.

No long-lived AWS credentials are stored in GitHub. The only secrets
in the repo are:

| GitHub secret | Source |
|---|---|
| `AWS_DEPLOY_ROLE_ARN` | Terraform output `github_deploy_role_arn` |
| `APPRUNNER_SERVICE_ARN` | Terraform output `apprunner_service_arn` |

## GitHub OIDC to AWS — setup (once per account)

If the OIDC provider is not already created in the target AWS account:

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com \
  --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
```

The Terraform stack then creates a role (`monolith-workspace-api-github-deploy`)
whose trust policy restricts assumption to
`repo:raava-solutions/monolith-workspace-api:*`. The policy attached to
that role grants only:

- `ecr:GetAuthorizationToken` on `*`
- ECR push/pull on the single `monolith-workspace-api` repo ARN
- `apprunner:StartDeployment` + `DescribeService` + `ListOperations`
  on the single App Runner service ARN

No `AWSAdministratorAccess`, no wildcard resources outside ECR auth.

## Cloudflare DNS

Once App Runner is running and `enable_custom_domain = true`:

1. `terraform output apprunner_service_url` — e.g.
   `abcd1234.us-east-1.awsapprunner.com`.
2. `terraform output custom_domain_validation_records` — a list of
   validation CNAMEs (ACM-style).
3. In Cloudflare dashboard for `raavasolutions.com`:
   - Add `CNAME api-workspace → <apprunner_service_url>`, **Proxy = DNS only**
     (App Runner handles TLS; Cloudflare proxying breaks the cert chain).
   - Add each validation CNAME from step 2, also **DNS only**.
4. Wait 5–15 minutes for ACM to see the validation records and issue the cert.
5. Smoke test: `curl -I https://api-workspace.raavasolutions.com/health`
   should return `200 OK`.

Cloudflare proxy can be enabled later only after moving to a
App Runner-native TLS-on-custom-domain flow that is compatible with
edge proxying. For MVP: **DNS only**.

## Secrets rotation

```bash
aws secretsmanager put-secret-value \
  --secret-id monolith/workspace-api/CLERK_SECRET_KEY \
  --secret-string "<new-value>" \
  --region us-east-1

# Trigger redeploy so App Runner pulls the new secret value:
aws apprunner start-deployment \
  --service-arn "$(terraform -chdir=infra/terraform output -raw apprunner_service_arn)" \
  --region us-east-1
```

App Runner only re-reads secrets on cold start, so rotation requires a
`start-deployment` cycle.

## Monitoring

- **Logs:** CloudWatch Logs Insights
  ```
  source /aws/apprunner/monolith-workspace-api/application
  | filter @message like /ERROR/ or @message like /WARNING/
  | sort @timestamp desc
  | limit 100
  ```
- **Health:** `curl -f https://api-workspace.raavasolutions.com/health`
  — returns `{"status":"ok"}` with 200.
- **Readiness:** `curl -f https://api-workspace.raavasolutions.com/ready`
  — same but also `SELECT 1` against Postgres.
- **App Runner console:** service metrics (2xx/4xx/5xx rate, CPU, memory).

## Rollback

```bash
# Find the previous SHA tag:
aws ecr describe-images \
  --repository-name monolith-workspace-api \
  --region us-east-1 \
  --query 'sort_by(imageDetails,&imagePushedAt)[-10:].[imageTags[0],imagePushedAt]' \
  --output table

# Retag the known-good SHA as latest, then redeploy:
GOOD_SHA=abc123def456
aws ecr batch-get-image \
  --repository-name monolith-workspace-api \
  --image-ids imageTag="$GOOD_SHA" \
  --region us-east-1 \
  --query 'images[0].imageManifest' --output text \
  > manifest.json
aws ecr put-image \
  --repository-name monolith-workspace-api \
  --image-tag latest \
  --image-manifest "file://manifest.json" \
  --region us-east-1

aws apprunner start-deployment \
  --service-arn "$(terraform -chdir=infra/terraform output -raw apprunner_service_arn)" \
  --region us-east-1
```

## Pause / resume

```bash
aws apprunner pause-service  --service-arn "$ARN" --region us-east-1
aws apprunner resume-service --service-arn "$ARN" --region us-east-1
```

Billing continues for storage (ECR, CloudWatch) but stops for compute.
