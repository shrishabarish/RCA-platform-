from locust import HttpUser, task, between
import uuid
import time
from datetime import datetime, timezone


class CloudLensUser(HttpUser):
    wait_time = between(0.01, 0.05)

    @task(3)
    def ingest_event(self):
        payload = {
            "event_id": f"locust_evt_{uuid.uuid4().hex[:10]}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "application",
            "event_type": "api_latency_spike",
            "service": "API",
            "resource_id": "api-gateway-01",
            "metric": "Latency",
            "value": 1500.0,
            "severity": "HIGH",
            "metadata": {"locust": True}
        }
        self.client.post("/api/events", json=payload)

    @task(1)
    def get_incidents_rca(self):
        self.client.get("/api/incidents")

    @task(1)
    def get_health(self):
        self.client.get("/api/health")
