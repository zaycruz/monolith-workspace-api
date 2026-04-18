# =============================================================================
# monolith-workspace-api — AWS App Runner infrastructure
#
# Provisioned resources:
#   * ECR repository (image registry for the Dockerfile build)
#   * IAM role App Runner uses to pull the image (access role)
#   * IAM role the running service assumes (instance role — reads Secrets Manager)
#   * CloudWatch log group
#   * App Runner autoscaling configuration (min 1, max 10)
#   * App Runner service (source = ECR, health check = /health)
#   * Optional custom-domain association for api-workspace.raavasolutions.com
#
# Secrets Manager: this stack expects the secret names listed in var.secrets to
# already exist. Creating the secrets is deliberately NOT automated here — the
# operator writes secret *values* manually so TF state never contains plaintext.
# =============================================================================

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  region     = var.aws_region
}

# ── ECR ─────────────────────────────────────────────────────────────────────
resource "aws_ecr_repository" "app" {
  name                 = var.service_name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 20 tagged images"
        selection = {
          tagStatus      = "tagged"
          tagPatternList = ["*"]
          countType      = "imageCountMoreThan"
          countNumber    = 20
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Remove untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = { type = "expire" }
      },
    ]
  })
}

# ── IAM: App Runner access role (pull from ECR) ─────────────────────────────
data "aws_iam_policy_document" "apprunner_access_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["build.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_access" {
  name               = "${var.service_name}-apprunner-access"
  assume_role_policy = data.aws_iam_policy_document.apprunner_access_assume.json
}

resource "aws_iam_role_policy_attachment" "apprunner_access_ecr" {
  role       = aws_iam_role.apprunner_access.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/service-role/AWSAppRunnerServicePolicyForECRAccess"
}

# ── IAM: App Runner instance role (runtime, reads Secrets Manager) ──────────
data "aws_iam_policy_document" "apprunner_instance_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["tasks.apprunner.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apprunner_instance" {
  name               = "${var.service_name}-apprunner-instance"
  assume_role_policy = data.aws_iam_policy_document.apprunner_instance_assume.json
}

data "aws_iam_policy_document" "secrets_read" {
  statement {
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = [
      for name in values(var.secrets) :
      "arn:${local.partition}:secretsmanager:${local.region}:${local.account_id}:secret:${name}-*"
    ]
  }
}

resource "aws_iam_policy" "secrets_read" {
  name   = "${var.service_name}-secrets-read"
  policy = data.aws_iam_policy_document.secrets_read.json
}

resource "aws_iam_role_policy_attachment" "apprunner_instance_secrets" {
  role       = aws_iam_role.apprunner_instance.name
  policy_arn = aws_iam_policy.secrets_read.arn
}

# ── CloudWatch logs ─────────────────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "app" {
  name              = "/aws/apprunner/${var.service_name}/application"
  retention_in_days = var.log_retention_days
}

# ── App Runner: autoscaling + service ───────────────────────────────────────
resource "aws_apprunner_auto_scaling_configuration_version" "app" {
  auto_scaling_configuration_name = "${var.service_name}-asc"
  min_size                        = var.min_size
  max_size                        = var.max_size
  max_concurrency                 = var.max_concurrency
}

resource "aws_apprunner_service" "app" {
  service_name = var.service_name

  source_configuration {
    auto_deployments_enabled = false

    authentication_configuration {
      access_role_arn = aws_iam_role.apprunner_access.arn
    }

    image_repository {
      image_identifier      = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
      image_repository_type = "ECR"

      image_configuration {
        port = "8080"

        runtime_environment_variables = var.runtime_env

        runtime_environment_secrets = {
          for env_name, secret_name in var.secrets :
          env_name => "arn:${local.partition}:secretsmanager:${local.region}:${local.account_id}:secret:${secret_name}"
        }
      }
    }
  }

  instance_configuration {
    cpu               = var.cpu
    memory            = var.memory
    instance_role_arn = aws_iam_role.apprunner_instance.arn
  }

  auto_scaling_configuration_arn = aws_apprunner_auto_scaling_configuration_version.app.arn

  health_check_configuration {
    protocol            = "HTTP"
    path                = "/health"
    interval            = 20
    timeout             = 5
    healthy_threshold   = 1
    unhealthy_threshold = 3
  }

  # VPC connector is intentionally omitted. Supabase is reached over the
  # public internet; if workspace-api ever needs private RDS / ElastiCache
  # access, wire an `aws_apprunner_vpc_connector` and add a
  # `network_configuration { egress_configuration { ... } }` block here.

  tags = {
    Name = var.service_name
  }
}

# ── Custom domain (Cloudflare points CNAME here) ────────────────────────────
resource "aws_apprunner_custom_domain_association" "app" {
  count       = var.enable_custom_domain ? 1 : 0
  service_arn = aws_apprunner_service.app.arn
  domain_name = var.custom_domain
}

# ── IAM: GitHub Actions OIDC role (build + push + deploy) ───────────────────
# Creates a trust relationship between the GitHub Actions workflow in
# ${var.github_owner}/${var.github_repo} and an AWS role with the specific
# permissions CI needs. Long-lived access keys are never issued.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "github_oidc_assume" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_owner}/${var.github_repo}:*"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name               = "${var.service_name}-github-deploy"
  assume_role_policy = data.aws_iam_policy_document.github_oidc_assume.json
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid    = "ECRAuth"
    effect = "Allow"
    actions = [
      "ecr:GetAuthorizationToken",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ECRPushPull"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:DescribeRepositories",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [aws_ecr_repository.app.arn]
  }

  statement {
    sid    = "AppRunnerStartDeployment"
    effect = "Allow"
    actions = [
      "apprunner:StartDeployment",
      "apprunner:DescribeService",
      "apprunner:ListOperations",
    ]
    resources = [aws_apprunner_service.app.arn]
  }
}

resource "aws_iam_policy" "github_deploy" {
  name   = "${var.service_name}-github-deploy"
  policy = data.aws_iam_policy_document.github_deploy.json
}

resource "aws_iam_role_policy_attachment" "github_deploy" {
  role       = aws_iam_role.github_deploy.name
  policy_arn = aws_iam_policy.github_deploy.arn
}
