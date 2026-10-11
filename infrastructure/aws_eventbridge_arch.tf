# AWS Infrastructure as Code for Cloud Observability Ingestion
# Captures CloudWatch Alarms & CloudTrail API anomalies -> Amazon EventBridge -> RCA Ingestion API

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "rca_api_endpoint" {
  type        = string
  default     = "https://rca.internal.cloud.corp/api/events"
  description = "Target webhook endpoint for the autonomous RCA ingestion server"
}

# 1. Custom EventBridge Bus for Observability
resource "aws_cloudwatch_event_bus" "observability_bus" {
  name = "cloudlens-observability-bus"
}

# 2. EventBridge Rule for CloudWatch Alarm State Changes (ALARM)
resource "aws_cloudwatch_event_rule" "cloudwatch_alarm_rule" {
  name           = "cloudwatch-alarm-state-change"
  description    = "Captures all CloudWatch Alarm transitions to ALARM state"
  event_bus_name = aws_cloudwatch_event_bus.observability_bus.name

  event_pattern = jsonencode({
    "source"      : ["aws.cloudwatch"],
    "detail-type" : ["CloudWatch Alarm State Change"],
    "detail"      : {
      "state" : {
        "value" : ["ALARM"]
      }
    }
  })
}

# 3. Dead Letter Queue for Ingestion Reliability
resource "aws_sqs_queue" "rca_dlq" {
  name                      = "cloudlens-rca-ingestion-dlq"
  message_retention_seconds = 1209600 # 14 days
}

# 4. Lambda Function to Normalize & Forward AWS Events to CloudLens RCA Engine
resource "aws_lambda_function" "event_normalizer_lambda" {
  function_name = "cloudlens-event-normalizer"
  role          = aws_iam_role.lambda_exec_role.arn
  handler       = "index.handler"
  runtime       = "python3.11"
  timeout       = 30

  environment {
    variables = {
      RCA_API_ENDPOINT = var.rca_api_endpoint
    }
  }

  dead_letter_config {
    target_arn = aws_sqs_queue.rca_dlq.arn
  }
}

# 5. IAM Role for Lambda Ingestion
resource "aws_iam_role" "lambda_exec_role" {
  name = "cloudlens_lambda_exec_role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
    }]
  })
}

# 6. Amazon S3 Bucket for Historical Incident Logs & Post-Mortem Reports (Slide 7 & 8)
resource "aws_s3_bucket" "incident_archive" {
  bucket        = "cloudlens-incident-archive"
  force_destroy = false
}

resource "aws_s3_bucket_versioning" "archive_versioning" {
  bucket = aws_s3_bucket.incident_archive.id
  versioning_configuration {
    status = "Enabled"
  }
}

# 7. Amazon SNS Topic for Autonomous Incident Notifications (Slide 7 & 8)
resource "aws_sns_topic" "incident_notifications" {
  name         = "cloudlens-incident-notifications"
  display_name = "CloudLens SRE Incident Alerts"
}

# 8. EventBridge Rule for AWS Config Drift & Compliance Changes (Slide 6 & 8)
resource "aws_cloudwatch_event_rule" "config_compliance_rule" {
  name           = "cloudlens-config-compliance-change"
  description    = "Captures AWS Config compliance & resource drift transitions"
  event_bus_name = aws_cloudwatch_event_bus.observability_bus.name

  event_pattern = jsonencode({
    "source"      : ["aws.config"],
    "detail-type" : ["Config Rules Compliance Change", "Configuration Item Change Notification"]
  })
}

