terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }

  # Remote state is recommended for production use. Uncomment and point at
  # an S3 bucket + DynamoDB lock table in the Raava AWS account before the
  # first `terraform apply` in shared environments.
  #
  # backend "s3" {
  #   bucket         = "raava-terraform-state"
  #   key            = "monolith/workspace-api/terraform.tfstate"
  #   region         = "us-east-1"
  #   dynamodb_table = "raava-terraform-locks"
  #   encrypt        = true
  # }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "monolith"
      Service     = "workspace-api"
      ManagedBy   = "terraform"
      Environment = var.environment
      Owner       = "raava-solutions"
    }
  }
}
