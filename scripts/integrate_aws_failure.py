import os
import re
from pathlib import Path

AWS_FAILURE_DIR = Path(r"C:\Users\shris\Desktop\AWS failure")

def update_structured_logger():
    target = AWS_FAILURE_DIR / "Backend" / "utils" / "structuredLogger.js"
    if not target.exists():
        print(f"Error: {target} does not exist.")
        return False

    content = target.read_text(encoding="utf-8")
    
    # 1. Ensure http/https and sendToRcaWebhook are present
    if "sendToRcaWebhook" not in content:
        imports_replacement = """const Log = require("../models/LogModel");
const { CloudWatchClient, PutMetricDataCommand } = require("@aws-sdk/client-cloudwatch");
const http = require("http");
const https = require("https");

// RCA Webhook: fire-and-forget POST to CloudLens RCA platform
const RCA_WEBHOOK_URL = process.env.RCA_WEBHOOK_URL || "http://localhost:8000/api/events/webhook";

function sendToRcaWebhook(logEntry) {
  if (!RCA_WEBHOOK_URL) return;
  try {
    const parsed = new URL(RCA_WEBHOOK_URL);
    const client = parsed.protocol === "https:" ? https : http;
    const postData = JSON.stringify(logEntry);
    const options = {
      hostname: parsed.hostname,
      port: parsed.port || (parsed.protocol === "https:" ? 443 : 80),
      path: parsed.pathname + (parsed.search || ""),
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(postData),
        "X-Source-App": "aws-failure-codereview",
      },
      timeout: 3000,
    };
    const req = client.request(options, () => {});
    req.on("error", () => {}); // silent failover: RCA platform may be offline or starting
    req.on("timeout", () => req.destroy());
    req.write(postData);
    req.end();
  } catch (_) {}
}"""
        content = re.sub(
            r'const Log = require\("\.\./models/LogModel"\);\s*const \{ CloudWatchClient, PutMetricDataCommand \} = require\("@aws-sdk/client-cloudwatch"\);',
            imports_replacement,
            content
        )

        # 2. Call sendToRcaWebhook in log function
        cw_call_pattern = r'(\s*// Push ERROR/CRITICAL/WARN telemetry to AWS CloudWatch\s*if \(level === "ERROR" \|\| level === "CRITICAL" \|\| level === "WARN"\) \{\s*sendToCloudWatch\(logEntry\);\s*\})'
        rca_cw_replacement = """
    // Push ERROR/CRITICAL/WARN/FAULT telemetry to RCA Webhook & AWS CloudWatch
    if (level === "ERROR" || level === "CRITICAL" || level === "WARN" || isFaultInjected) {
      sendToRcaWebhook(logEntry);
      sendToCloudWatch(logEntry);
    }"""
        content = re.sub(cw_call_pattern, rca_cw_replacement, content)
        target.write_text(content, encoding="utf-8")
        print(f"Updated {target}")
    else:
        print(f"sendToRcaWebhook already present in {target}")
    return True


def update_docker_compose():
    target = AWS_FAILURE_DIR / "docker-compose.yml"
    if not target.exists():
        print(f"Error: {target} does not exist.")
        return False

    content = target.read_text(encoding="utf-8")
    changed = False

    # 1. Add RCA_WEBHOOK_URL under environment if not present
    if "RCA_WEBHOOK_URL" not in content:
        content = content.replace(
            "      AWS_SECRET_ACCESS_KEY: \n",
            "      AWS_SECRET_ACCESS_KEY: \n      RCA_WEBHOOK_URL: ${RCA_WEBHOOK_URL:-http://host.docker.internal:8000/api/events/webhook}\n"
        )
        changed = True

    # 2. Add extra_hosts under backend if not present
    if "host.docker.internal:host-gateway" not in content:
        content = content.replace(
            "    ports:\n      - \"5000:5000\"\n",
            "    ports:\n      - \"5000:5000\"\n    extra_hosts:\n      - \"host.docker.internal:host-gateway\"\n"
        )
        changed = True

    if changed:
        target.write_text(content, encoding="utf-8")
        print(f"Updated {target}")
    else:
        print(f"docker-compose.yml already up-to-date")
    return True


def update_env_files():
    for env_name in [".env", ".env.example"]:
        target = AWS_FAILURE_DIR / "Backend" / env_name
        if not target.exists():
            continue
        content = target.read_text(encoding="utf-8")
        if "RCA_WEBHOOK_URL" not in content:
            content += "\n# RCA Webhook for CloudLens Incident Ingestion\nRCA_WEBHOOK_URL=http://localhost:8000/api/events/webhook\n"
            target.write_text(content, encoding="utf-8")
            print(f"Updated {target}")
        else:
            print(f"{env_name} already contains RCA_WEBHOOK_URL")


def update_terraform():
    target = AWS_FAILURE_DIR / "terraform" / "compute.tf"
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    if "RCA_WEBHOOK_URL" not in content:
        old_block = '{ name = "FAULT_INJECTION_ENABLED", value = "true" }'
        new_block = '{ name = "FAULT_INJECTION_ENABLED", value = "true" },\n        { name = "RCA_WEBHOOK_URL", value = "http://localhost:8000/api/events/webhook" }'
        if old_block in content:
            content = content.replace(old_block, new_block)
            target.write_text(content, encoding="utf-8")
            print(f"Updated {target}")
    return True


def update_postgres_db():
    target = AWS_FAILURE_DIR / "Backend" / "db" / "postgres.js"
    if not target.exists():
        return False
    content = target.read_text(encoding="utf-8")
    if 'password: process.env.DB_PASSWORD || "postgres",' in content:
        content = content.replace(
            'password: process.env.DB_PASSWORD || "postgres",',
            'password: process.env.DB_PASSWORD || "postgrespassword",'
        )
        target.write_text(content, encoding="utf-8")
        print(f"Updated {target}")
    return True


if __name__ == "__main__":
    print("Applying recommended changes to AWS failure project...")
    update_structured_logger()
    update_docker_compose()
    update_env_files()
    update_terraform()
    update_postgres_db()
    print("Done applying changes!")
