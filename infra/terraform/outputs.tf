output "ecr_repository_url" {
  description = "Push images to this URL from CI."
  value       = aws_ecr_repository.app.repository_url
}

output "ecr_repository_arn" {
  value = aws_ecr_repository.app.arn
}

output "apprunner_service_arn" {
  description = "Used by CI `aws apprunner start-deployment --service-arn`."
  value       = aws_apprunner_service.app.arn
}

output "apprunner_service_url" {
  description = "The *.awsapprunner.com URL. Point the Cloudflare CNAME here."
  value       = aws_apprunner_service.app.service_url
}

output "apprunner_service_id" {
  value = aws_apprunner_service.app.service_id
}

output "github_deploy_role_arn" {
  description = "Set this as AWS_DEPLOY_ROLE_ARN in GitHub Actions repo secrets."
  value       = aws_iam_role.github_deploy.arn
}

output "custom_domain_validation_records" {
  description = "DNS records to create in Cloudflare once `enable_custom_domain = true`."
  value = try(
    aws_apprunner_custom_domain_association.app[0].certificate_validation_records,
    [],
  )
}

output "cloudwatch_log_group" {
  value = aws_cloudwatch_log_group.app.name
}
