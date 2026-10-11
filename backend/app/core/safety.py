import os
import re
from urllib.parse import urlparse


def mask_database_url(url: str) -> str:
    """Masks database credentials and host for safe logging without exposing secrets."""
    if not url:
        return "<EMPTY>"
    try:
        if url.startswith("sqlite"):
            return url

        # Separate scheme and remainder
        if "://" in url:
            scheme, remainder = url.split("://", 1)
        else:
            scheme, remainder = "unknown", url

        # Mask credentials if present
        if "@" in remainder:
            creds, host_part = remainder.split("@", 1)
            user = creds.split(":")[0] if ":" in creds else creds
            masked_creds = f"{user}:***@"
        else:
            masked_creds = ""
            host_part = remainder

        # Mask RDS cluster/subdomain identifier
        if "rds.amazonaws.com" in host_part.lower():
            parts = host_part.split("/")
            host_port = parts[0]
            db_name = "/" + parts[1] if len(parts) > 1 else ""
            host = host_port.split(":")[0]
            port = (":" + host_port.split(":")[1]) if ":" in host_port else ""
            subdomains = host.split(".")
            if len(subdomains) >= 4:
                masked_host = f"{subdomains[0]}.***.rds.amazonaws.com{port}{db_name}"
            else:
                masked_host = f"***.rds.amazonaws.com{port}{db_name}"
            return f"{scheme}://{masked_creds}{masked_host}"
        else:
            return f"{scheme}://{masked_creds}{host_part}"
    except Exception:
        return "***"


class ProductionDatabaseAccessError(RuntimeError):
    """Raised when an unauthorized connection to a production database is attempted."""
    pass


def check_database_safety(url: str, entry_point: str = "application") -> str:
    """
    Enforces the Hard Guard:
    Refuses any database URL connecting to a host containing 'rds.amazonaws.com'
    unless the environment variable ALLOW_PRODUCTION=1 is explicitly set.
    """
    masked = mask_database_url(url)
    allow_prod = os.getenv("ALLOW_PRODUCTION", "0").strip()

    if "rds.amazonaws.com" in (url or "").lower() and allow_prod != "1":
        error_msg = (
            f"FATAL SAFETY GUARD: [{entry_point}] Connection to production database ({masked}) "
            f"refused! ALLOW_PRODUCTION=1 is required to connect to any host containing 'rds.amazonaws.com'."
        )
        raise ProductionDatabaseAccessError(error_msg)

    return masked


def print_database_host(url: str, entry_point: str = "script") -> str:
    """Prints the masked database host at the start of a script, migration, or process run."""
    masked = mask_database_url(url)
    print(f"[{entry_point.upper()}] Database Target: {masked}")
    return masked
