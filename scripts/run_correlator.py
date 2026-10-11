#!/usr/bin/env python3
"""
Standalone Correlator Consumer Process Entrypoint.
Runs the serialized, advisory-locked correlator worker independently of web workers.
Includes graceful SIGTERM/SIGINT shutdown and continuous health file reporting.
"""

import os
import sys
import json
import time
import signal
import logging
from pathlib import Path
from datetime import datetime, timezone

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.logging_config import setup_structured_logging
setup_structured_logging()
logger = logging.getLogger("cloudlens.correlator_process")

from backend.app.services.correlator_consumer import correlator_consumer


def write_health_file():
    """Writes JSON heartbeat health file for supervisor (systemd/supervisord/docker) probes."""
    health_file = os.getenv("CORRELATOR_HEALTH_FILE", "/tmp/correlator_health.json")
    try:
        data = {
            "pid": os.getpid(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "status": "healthy",
            "is_leader": correlator_consumer.is_leader,
            "metrics": correlator_consumer.metrics_data
        }
        p = Path(health_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write via temporary file
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f)
        tmp.replace(p)
    except Exception as e:
        logger.debug(f"Could not write health file: {e}")


def main():
    logger.info(f"Starting standalone correlator consumer process [PID: {os.getpid()}]...")
    
    stop_event = False

    def handle_signal(sig, frame):
        nonlocal stop_event
        logger.info(f"Signal {sig} received, stopping correlator consumer...")
        stop_event = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    correlator_consumer.start()

    try:
        while not stop_event:
            write_health_file()
            time.sleep(1.0)
    finally:
        logger.info("Stopping correlator consumer worker...")
        correlator_consumer.stop(timeout=5.0)
        # Cleanup health file on exit
        try:
            hf = Path(os.getenv("CORRELATOR_HEALTH_FILE", "/tmp/correlator_health.json"))
            if hf.exists():
                hf.unlink()
        except Exception:
            pass
        logger.info("Standalone correlator process terminated cleanly.")


if __name__ == "__main__":
    main()
