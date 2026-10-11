import os
from pathlib import Path
from typing import Dict, List, Any, Optional
from pydantic import BaseModel, Field, field_validator
from dotenv import load_dotenv

# Load .env file from project root
ROOT_DIR = Path(__file__).resolve().parent.parent.parent.parent
load_dotenv(ROOT_DIR / ".env")


class DatabaseConfig(BaseModel):
    url: str = Field(
        default_factory=lambda: os.getenv(
            "DATABASE_URL",
            "postgresql://postgres:postgres@localhost:5432/cloudlens_test"
        )
    )
    driver: str = Field(
        default_factory=lambda: os.getenv("DB_DRIVER", "pg8000")
    )
    connect_timeout_seconds: int = 3
    max_db_connections: int = Field(
        default_factory=lambda: int(os.getenv("MAX_DB_CONNECTIONS", "79"))
    )
    workers: int = Field(
        default_factory=lambda: int(os.getenv("WEB_CONCURRENCY", os.getenv("WORKERS", "1")))
    )
    # Derive safe per-worker pool size reserving 20% for admin/migrations
    pool_size: int = Field(
        default_factory=lambda: int(os.getenv(
            "DB_POOL_SIZE",
            str(max(5, int((int(os.getenv("MAX_DB_CONNECTIONS", "79")) * 0.8 / max(1, int(os.getenv("WEB_CONCURRENCY", os.getenv("WORKERS", "1"))))) * 0.6)))
        ))
    )
    max_overflow: int = Field(
        default_factory=lambda: int(os.getenv(
            "DB_MAX_OVERFLOW",
            str(max(5, int((int(os.getenv("MAX_DB_CONNECTIONS", "79")) * 0.8 / max(1, int(os.getenv("WEB_CONCURRENCY", os.getenv("WORKERS", "1"))))) * 0.4)))
        ))
    )
    pool_recycle_seconds: int = 300
    pool_timeout_seconds: int = 10
    thread_limiter_tokens: int = Field(
        default_factory=lambda: int(os.getenv("THREAD_LIMITER_TOKENS", "100"))
    )


class AWSConfig(BaseModel):
    region: str = Field(
        default_factory=lambda: os.getenv("AWS_DEFAULT_REGION", os.getenv("AWS_REGION", "eu-north-1"))
    )
    connect_timeout_seconds: float = 2.0
    read_timeout_seconds: float = 3.0
    max_retries: int = 3
    s3_bucket_name: str = Field(
        default_factory=lambda: os.getenv("S3_BUCKET_NAME", "cloudlens-incident-archive")
    )
    sns_topic_arn: str = Field(
        default_factory=lambda: os.getenv("SNS_TOPIC_ARN", "arn:aws:sns:eu-north-1:123456789012:cloudlens-incident-alerts")
    )
    sqs_queue_url: str = Field(
        default_factory=lambda: os.getenv("SQS_QUEUE_URL", "https://sqs.eu-north-1.amazonaws.com/123456789012/cloudlens-telemetry-buffer")
    )


class CorrelatorConfig(BaseModel):
    window_seconds: int = 60
    max_incident_window_duration_seconds: int = 3600  # Cap incident duration to 1 hour
    max_events_per_incident: int = 1000               # Prevent memory bloat and O(N^2) explosion
    dedup_window_seconds: int = 5
    flapping_threshold: int = 4
    rca_debounce_seconds: int = Field(
        default_factory=lambda: int(os.getenv("RCA_DEBOUNCE_SECONDS", "5"))
    )
    shared_store_type: str = Field(
        default_factory=lambda: os.getenv("DEDUP_STORE_TYPE", "memory")  # 'memory' or 'redis'
    )
    redis_url: str = Field(
        default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )
    incident_inactivity_resolve_minutes: int = Field(
        default_factory=lambda: int(os.getenv("INCIDENT_INACTIVITY_RESOLVE_MINUTES", "15"))
    )
    embedded: bool = Field(
        default_factory=lambda: os.getenv("CORRELATOR_EMBEDDED", "true").lower() == "true"
    )
    deploy_cutoff_timestamp: Optional[str] = Field(
        default_factory=lambda: os.getenv("DEPLOY_CUTOFF_TIMESTAMP", None)
    )
    lag_warning_threshold_seconds: float = Field(
        default_factory=lambda: float(os.getenv("CORRELATOR_LAG_WARN_SEC", "10.0"))
    )
    unassigned_warning_threshold: int = Field(
        default_factory=lambda: int(os.getenv("CORRELATOR_UNASSIGNED_WARN", "2000"))
    )


class TopologyConfig(BaseModel):
    max_path_search_depth: int = 5                    # Cutoff for nx.all_simple_paths
    cache_ttl_seconds: int = 60                       # Topology graph metrics cache TTL


class ScoringConfig(BaseModel):
    max_temporal_score: int = 25
    max_topology_score: int = 30
    max_symptom_score: int = 20
    max_metric_score: int = 15
    max_severity_score: int = 10
    time_decay_lambda: float = 0.02
    min_decay_floor: int = 10

    severity_weights: Dict[str, int] = {
        "CRITICAL": 10,
        "HIGH": 8,
        "MEDIUM": 5,
        "LOW": 2
    }

    # Metric threshold gates
    cpu_critical_threshold: float = 90.0
    throttle_critical_threshold: float = 1000.0
    latency_critical_threshold_ms: float = 2000.0
    error_rate_critical_threshold: float = 50.0

    @field_validator("max_temporal_score", "max_topology_score", "max_symptom_score", "max_metric_score", "max_severity_score")
    @classmethod
    def validate_positive_weights(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"Score category weight must be positive, got {v}")
        return v


class AppConfig(BaseModel):
    env: str = Field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    host: str = Field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = Field(default_factory=lambda: int(os.getenv("PORT", "8000")))
    log_level: str = Field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    auto_simulate_on_startup: bool = Field(
        default_factory=lambda: os.getenv("CLOUDLENS_AUTO_SIMULATE", "false").lower() == "true"
    )

    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    aws: AWSConfig = Field(default_factory=AWSConfig)
    correlator: CorrelatorConfig = Field(default_factory=CorrelatorConfig)
    topology: TopologyConfig = Field(default_factory=TopologyConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)

    service_aliases: Dict[str, str] = {
        "codereview-backend": "API",
        "code-review-backend": "API",
        "backend": "API",
        "backend-service": "API",
        "postgresql": "DynamoDB",
        "postgres": "DynamoDB",
        "database": "DynamoDB",
        "db-primary": "DynamoDB",
        "rds": "DynamoDB",
        "frontend": "Application",
        "code-review-frontend": "Application",
        "frontend-service": "Application",
        "ui": "Application",
        "client": "Application",
        "ec2-instance": "EC2",
        "compute": "EC2",
        "mongodb": "Worker"
    }


# Global validated configuration instance
config = AppConfig()
