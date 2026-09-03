# 🔌 Frontend Integration & API Contracts Guide

This document defines the **exact REST API data contracts** exposed by the backend. Use this reference when building or modifying your custom frontend (React, D3.js, Chart.js, or plain HTML/JS).

---

## 🌐 Base URL
```text
http://localhost:8000/api
```

---

## 📦 Summary of Available Endpoints

| Category | Method | Endpoint | Description |
| :--- | :---: | :--- | :--- |
| **AWS Integration** | `GET` | `/api/aws/status` | Checks AWS Boto3 credentials & connectivity |
| **AWS Integration** | `POST` | `/api/aws/poll` | Polls live CloudWatch Alarms & CloudTrail errors from real AWS account |
| **Topology** | `GET` | `/api/topology` | Returns graph nodes (with status) & directional edges |
| **Prediction** | `GET` | `/api/what-if/{service}` | Predictive blast radius & risk score if a service fails |
| **Incidents** | `GET` | `/api/incidents` | Returns list of all active correlated incidents |
| **Incidents** | `GET` | `/api/incidents/{id}` | Deep RCA analysis, confidence, causal paths & audit trail |
| **Remediation** | `GET` | `/api/incidents/{id}/remediation` | Returns executable cloud remediation runbook |
| **Remediation** | `POST` | `/api/incidents/{id}/mitigate` | Triggers automated incident resolution |
| **Events** | `GET` | `/api/events` | Returns chronological telemetry stream |
| **Events** | `POST` | `/api/events` | Ingests a new single event (webhook target) |
| **Simulation** | `GET` | `/api/scenarios` | Lists available test scenarios |
| **Simulation** | `POST` | `/api/scenarios/{id}/simulate` | Loads a pre-built failure scenario |
| **System** | `GET` | `/api/health` | Service health status & telemetry counters |
| **System** | `POST` | `/api/reset` | Clears all incidents and event buffers |

---

## 📊 Detailed JSON Payload Specifications

### 1. Topology & Dependency Graph (`GET /api/topology`)
Used by D3.js or SVG canvas to draw the microservice graph.

**Response Payload:**
```json
{
  "nodes": [
    {
      "id": "EC2",
      "label": "EC2 Instances / Cluster",
      "type": "compute",
      "status": "ROOT_CAUSE",
      "criticality": 0.9
    },
    {
      "id": "API",
      "label": "API Gateway / Core Service",
      "type": "service",
      "status": "DEGRADED",
      "criticality": 0.85
    },
    {
      "id": "DynamoDB",
      "label": "DynamoDB NoSQL Table",
      "type": "database",
      "status": "HEALTHY",
      "criticality": 0.95
    }
  ],
  "edges": [
    {
      "source": "API",
      "target": "EC2",
      "relationship": "depends_on"
    },
    {
      "source": "Application",
      "target": "API",
      "relationship": "depends_on"
    }
  ]
}
```

*Node `status` values:*
* `"ROOT_CAUSE"`: The suspected failure origin (render red / pulsing).
* `"DEGRADED"`: Downstream casualty service (render amber / warning).
* `"HEALTHY"`: Nominal operational status (render green).

---

### 2. Deep Incident RCA & Audit Trail (`GET /api/incidents/{incident_id}`)
Used to render the RCA confidence meter, breakdown bars, causal chain, and candidate table.

**Response Payload:**
```json
{
  "incident_id": "inc_305d88ea",
  "severity": "CRITICAL",
  "probable_root_cause": "EC2",
  "resource_id": "i-0abcdef1234567890",
  "confidence": 98.0,
  "score_breakdown": {
    "temporal_correlation": 25,
    "dependency_relationship": 30,
    "downstream_symptoms": 20,
    "metric_anomaly_magnitude": 15,
    "severity": 8
  },
  "affected_services": ["API", "Application"],
  "causal_paths": [
    "EC2 -> API -> Application",
    "EC2 -> Worker -> Application"
  ],
  "reasoning_chain": [
    "1. Temporal Origin: Service 'EC2' registered the earliest anomaly in the incident timeline (25 pts awarded).",
    "2. Graph Blast Radius: 'EC2' occupies a structural dependency position affecting 3 downstream service(s) (30 pts).",
    "3. Symptom Correlation: 2 downstream services (API, Application) exhibited concurrent failure symptoms matching the topology (20 pts).",
    "4. Anomaly Magnitude & Severity: Threshold telemetry breach scored at 15 pts with severity weight of 8 pts.",
    "5. Calibrated Result: 'EC2' determined as Primary Root Cause with 98% confidence."
  ],
  "is_compound_incident": false,
  "contributing_causes": [],
  "related_events": 5,
  "all_candidate_scores": [
    {
      "service": "EC2",
      "total": 98.0,
      "breakdown": {
        "temporal_correlation": 25,
        "dependency_relationship": 30,
        "downstream_symptoms": 20,
        "metric_anomaly_magnitude": 15,
        "severity": 8
      }
    },
    {
      "service": "API",
      "total": 45.0,
      "breakdown": {
        "temporal_correlation": 19,
        "dependency_relationship": 15,
        "downstream_symptoms": 0,
        "metric_anomaly_magnitude": 10,
        "severity": 8
      }
    }
  ],
  "remediation_suggestion": "Trigger Auto-Scaling Group Scale-Out & Reboot Overloaded Instance (i-0abcdef1234567890)",
  "remediation_plan": {
    "incident_id": "inc_305d88ea",
    "target_service": "EC2",
    "recommended_action": "Trigger Auto-Scaling Group Scale-Out & Reboot Overloaded Instance (i-0abcdef1234567890)",
    "action_type": "SCALE_OUT",
    "commands": [
      "aws autoscaling set-desired-capacity --auto-scaling-group-name prod-web-asg --desired-capacity 6",
      "aws ec2 reboot-instances --instance-ids i-0abcdef1234567890"
    ],
    "estimated_mttr_minutes": 3,
    "safety_checks": [
      "Verify target capacity is within AWS account limit quotas",
      "Ensure traffic is drained from terminating nodes via ALB target group"
    ]
  }
}
```

---

### 3. Chronological Telemetry Stream (`GET /api/events`)
Used to render the incident event timeline.

**Response Payload:**
```json
[
  {
    "event_id": "evt_001",
    "timestamp": "2026-09-01T15:00:00Z",
    "source": "cloudwatch",
    "event_type": "cpu_utilization_high",
    "service": "EC2",
    "resource_id": "i-0abcdef1234567890",
    "metric": "CPUUtilization",
    "value": 98.5,
    "severity": "HIGH",
    "metadata": {
      "region": "us-east-1"
    }
  },
  {
    "event_id": "evt_002",
    "timestamp": "2026-09-01T15:00:10Z",
    "source": "application",
    "event_type": "api_latency_high",
    "service": "API",
    "resource_id": "api-gateway-01",
    "metric": "Latency",
    "value": 2500.0,
    "severity": "MEDIUM",
    "metadata": {
      "endpoint": "/v1/users"
    }
  }
]
```

---

### 4. Predictive What-If Failure Simulator (`GET /api/what-if/{service}`)
Used when clicking a node in the graph to preview hypothetical failure impact.

**Response Payload:**
```json
{
  "target_service": "EC2",
  "criticality_level": "HIGH",
  "direct_downstream_count": 2,
  "direct_downstream_services": ["API", "Worker"],
  "total_blast_radius_count": 3,
  "all_impacted_services": ["Application", "Worker", "API"],
  "estimated_architecture_risk_score": 96.0,
  "remediation_recommendation": "Ensure active-active redundancy and circuit breakers are active on EC2."
}
```

---

### 5. Live AWS Status Check (`GET /api/aws/status`)
Used to display AWS connection badge in the UI.

**Connected State:**
```json
{
  "connected": true,
  "account_id": "123456789012",
  "arn": "arn:aws:iam::123456789012:user/admin",
  "region": "us-east-1",
  "message": "Successfully authenticated with AWS"
}
```

**Disconnected / Unconfigured State:**
```json
{
  "connected": false,
  "region": "us-east-1",
  "error": "Unable to locate credentials",
  "message": "AWS credentials not configured. Run 'aws configure' or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY."
}
```
