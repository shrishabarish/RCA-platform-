import os
import logging
from typing import Dict, Any, Optional
import boto3
from botocore.config import Config
from backend.app.core.config import config

logger = logging.getLogger("cloudlens.aws_client")


def get_botocore_config() -> Config:
    """Builds standard botocore configuration with fast connect/read timeouts and retries."""
    return Config(
        connect_timeout=config.aws.connect_timeout_seconds,
        read_timeout=config.aws.read_timeout_seconds,
        retries={
            "max_attempts": config.aws.max_retries,
            "mode": "adaptive"
        }
    )


def get_clean_env(key: str, default: Optional[str] = None) -> Optional[str]:
    val = os.getenv(key, default)
    if val is None:
        return None
    val = str(val).strip().strip("'\"")
    return val if val else None


def get_boto3_kwargs(region_name: Optional[str] = None) -> Dict[str, Any]:
    region = region_name or config.aws.region
    kwargs: Dict[str, Any] = {
        "region_name": region,
        "config": get_botocore_config()
    }
    ak = get_clean_env("AWS_ACCESS_KEY_ID")
    sk = get_clean_env("AWS_SECRET_ACCESS_KEY")
    token = get_clean_env("AWS_SESSION_TOKEN")
    if ak and sk:
        kwargs["aws_access_key_id"] = ak
        kwargs["aws_secret_access_key"] = sk
        if token:
            kwargs["aws_session_token"] = token
    return kwargs


def create_boto3_client(service_name: str, region_name: Optional[str] = None):
    """Safely creates a configured Boto3 client with strict timeouts."""
    kwargs = get_boto3_kwargs(region_name)
    try:
        return boto3.client(service_name, **kwargs)
    except Exception as e:
        logger.warning(f"Could not create boto3 client for {service_name}: {e}")
        return None
