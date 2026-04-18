# workspace-api — Terraform

Infrastructure-as-code for `monolith-workspace-api` on AWS App Runner
(`us-east-1`). Provisions ECR, an App Runner service backed by that ECR
image, the IAM plumbing for both runtime Secrets Manager access and a
GitHub Actions OIDC deploy role, and a CloudWatch log group.

## Prerequisites

1. AWS CLI v2 authenticated against the target account (`aws sts get-caller-identity`).
2. Terraform >= 1.6.0 and the AWS provider ~> 5.70.
3. The GitHub OIDC provider must already exist in the account:
   ```bash
   aws iam create-open-id-connect-provider \
     --url https://token.actions.githubusercontent.com \
     --client-id-list sts.amazonaws.com \
     --thumbprint-list 6938fd4d98bab03faadb97b34396831e3780aea1
   ```
   (This is one-time per AWS account. Skip if already created for other services.)
4. Secrets Manager entries must exist with the names listed in `variables.tf :
   secrets`. Create them with placeholder values the first time, then rotate:
   ```bash
   for name in \
     monolith/workspace-api/SUPABASE_URL \
     monolith/workspace-api/SUPABASE_SERVICE_ROLE_KEY \
     monolith/workspace-api/CLERK_SECRET_KEY \
     monolith/workspace-api/CLERK_JWKS_URL \
     monolith/workspace-api/WORKSPACE_MACHINE_TOKEN_SIGNING_KEY \
     monolith/workspace-api/DATABASE_URL
   do
     aws secretsmanager create-secret \
       --name "$name" \
       --secret-string "REPLACE_ME" \
       --region us-east-1
   done
   ```

## Deploy

```bash
cd infra/terraform

terraform init
terraform plan -out plan.tfplan
terraform apply plan.tfplan
```

### First-time bootstrap order

App Runner refuses to create the service if the ECR tag does not yet exist.
The safe bootstrap sequence is:

1. `terraform apply -target=aws_ecr_repository.app` (creates the repo alone).
2. Push the first image:
   ```bash
   aws ecr get-login-password --region us-east-1 \
     | docker login --username AWS --password-stdin \
       "$(aws sts get-caller-identity --query Account --output text).dkr.ecr.us-east-1.amazonaws.com"
   docker build -t monolith-workspace-api:latest ../..
   docker tag monolith-workspace-api:latest \
     "$(terraform output -raw ecr_repository_url):latest"
   docker push "$(terraform output -raw ecr_repository_url):latest"
   ```
3. `terraform apply` (creates the App Runner service against the now-real image).

### Custom domain

Leave `enable_custom_domain = false` until the service is healthy and the
Cloudflare CNAME has been created. Then:

```bash
terraform apply -var="enable_custom_domain=true"
terraform output custom_domain_validation_records
```

Add the returned CNAME validation records to Cloudflare (proxy = DNS only).

## Outputs

| Output | Purpose |
|---|---|
| `ecr_repository_url` | CI pushes here. |
| `apprunner_service_arn` | CI calls `aws apprunner start-deployment` against this. |
| `apprunner_service_url` | Cloudflare CNAME target for `api-workspace.raavasolutions.com`. |
| `github_deploy_role_arn` | Stored as `AWS_DEPLOY_ROLE_ARN` in GitHub Actions repo secrets. |

## Notes

- Terraform state should be moved to S3 + DynamoDB before the first
  shared-environment apply. The `backend "s3"` block in `versions.tf` is
  commented out; uncomment and reinit once the state bucket exists.
- Secrets Manager values are intentionally not Terraform-managed: we do not
  want plaintext secret material in TF state.
- VPC connectivity is not wired. Supabase is reached over the public
  internet. If a future dependency requires private networking, add an
  `aws_apprunner_vpc_connector` and a `network_configuration {
  egress_configuration { ... } }` block to `aws_apprunner_service.app`.
