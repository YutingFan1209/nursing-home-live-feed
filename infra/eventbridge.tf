# ============================================================
# AWS Infrastructure — EventBridge cron + Lambda
# Alternatively, use Cloud Run Jobs on GCP (see cloudrun.yaml)
# ============================================================

# eventbridge.tf — Terraform config for AWS scheduling

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

# ── EventBridge Scheduler ────────────────────────────────────
# Runs the pipeline daily at 6am UTC

resource "aws_scheduler_schedule" "pipeline_daily" {
  name       = "nursing-home-alerts-daily"
  group_name = "default"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression = "cron(0 6 * * ? *)"

  target {
    arn      = aws_lambda_function.pipeline.arn
    role_arn = aws_iam_role.scheduler_role.arn
  }
}

# ── Lambda Function ──────────────────────────────────────────
resource "aws_lambda_function" "pipeline" {
  function_name = "nursing-home-alerts-pipeline"
  role          = aws_iam_role.lambda_role.arn
  package_type  = "Image"
  image_uri     = "${var.ecr_repo_url}:latest"
  timeout       = 900   # 15 min max
  memory_size   = 1024

  environment {
    variables = {
      DATABASE_URL         = var.database_url
      ANTHROPIC_API_KEY    = var.anthropic_api_key
      SENDGRID_API_KEY     = var.sendgrid_api_key
      ALERT_FROM_EMAIL     = var.alert_from_email
      ALERT_TO_EMAILS      = var.alert_to_emails
      ARCHIVE_BUCKET       = var.archive_bucket
    }
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = var.security_group_ids
  }
}

# ── IAM Roles ────────────────────────────────────────────────
resource "aws_iam_role" "lambda_role" {
  name = "nursing-home-alerts-lambda"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

resource "aws_iam_role_policy" "lambda_s3" {
  name = "s3-archive-access"
  role = aws_iam_role.lambda_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject", "s3:GetObject"]
      Resource = "arn:aws:s3:::${var.archive_bucket}/*"
    }]
  })
}

resource "aws_iam_role" "scheduler_role" {
  name = "nursing-home-alerts-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "scheduler.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "invoke-lambda"
  role = aws_iam_role.scheduler_role.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.pipeline.arn
    }]
  })
}

# ── Variables ────────────────────────────────────────────────
variable "ecr_repo_url"       {}
variable "database_url"       { sensitive = true }
variable "anthropic_api_key"  { sensitive = true }
variable "sendgrid_api_key"   { sensitive = true }
variable "alert_from_email"   {}
variable "alert_to_emails"    {}
variable "archive_bucket"     {}
variable "subnet_ids"         { type = list(string) }
variable "security_group_ids" { type = list(string) }
