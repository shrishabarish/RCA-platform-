# ⚡ CloudLens: Autonomous Cloud Incident Correlation & Root Cause Analysis Platform

[![FastAPI](https://img.shields.io/badge/FastAPI-1.0.0-009688.svg?style=flat-square&logo=FastAPI)](https://fastapi.tiangolo.com)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-3776AB.svg?style=flat-square&logo=python)](https://www.python.org)
[![NetworkX](https://img.shields.io/badge/NetworkX-Graph-blue.svg?style=flat-square)](https://networkx.org)
[![Pytest](https://img.shields.io/badge/Pytest-16%20Passed-brightgreen.svg?style=flat-square)](https://pytest.org)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg?style=flat-square&logo=docker)](https://docker.com)

> **Autonomous, explainable, graph-theoretic Root Cause Analysis (RCA) and self-healing cloud incident management platform for distributed microservices.**

---

## 🌟 Key Features

* **⚡ Sliding-Window Event Correlation:** Groups out-of-order telemetry alerts (CloudWatch, Kubernetes, Application logs) into cohesive incidents using a 60-second sliding time window.
* **🌐 Directed Dependency Topology:** Models complex cloud microservice architecture with NetworkX, calculating downstream blast radius and transitive failure propagation.
* **📊 Explainable Multi-Factor Scoring:** Mathematically evaluates candidate services using 4 transparent dimensions:
  1. *Earliest Temporal Precedence* (Max 25 pts)
  2. *Dependency Graph Centrality / Blast Radius* (Max 30 pts)
  3. *Downstream Symptom Overlap* (Max 20 pts)
  4. *Anomaly Severity Weight* (Max 20 pts)
* **🛠️ Automated Cloud Runbooks & Self-Healing:** Generates targeted remediation scripts (`AWS CLI`, `kubectl rollout`, `Redis cache flushes`) and supports one-click automated mitigation.
* **💻 Interactive Observability Cockpit:** Dark-mode dashboard with animated SVG topology graphs, interactive timeline replay, candidate ranking scoreboard, and custom event injection sandbox.
* **🐳 Docker & AWS Terraform IaC:** Includes container definition, Docker Compose orchestration, and AWS EventBridge + Lambda infrastructure-as-code templates.

---

## 🚀 Quick Start Guide

### 1. Prerequisites
- Python 3.11+
- Virtual environment (recommended)

### 2. Install Dependencies
```bash
pip install -r backend/requirements.txt
```

### 3. Run Standalone CLI Engine
```bash
# Test default cascade scenario
python run_local.py cascade

# Test other failure scenarios
python run_local.py db_exhaustion
python run_local.py auth_outage
python run_local.py memory_leak
```

### 4. Launch FastAPI Web Platform & Visual Dashboard
```bash
python -m uvicorn backend.app.main:app --host 0.0.0.0 --port 8000 --reload
```
Open your browser at:
- **Interactive Dashboard:** [http://localhost:8000](http://localhost:8000)
- **Interactive OpenAPI/Swagger Docs:** [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 🧪 Automated Testing

Run the full automated test suite (Unit tests, Scoring engine tests, API endpoints, Remediation runbooks):
```bash
python -m pytest
```
*16 / 16 tests passing.*

---

## 📁 Repository Structure

```
Cloud_Project/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── __init__.py
│   │   │   └── endpoints.py          # REST endpoints for events, incidents, topology
│   │   ├── models/
│   │   │   ├── __init__.py
│   │   │   └── event.py              # Pydantic V2 models (CloudEvent, Incident, Topology)
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   └── incident_service.py   # State orchestrator & scenario loader
│   │   └── main.py                   # FastAPI app with static mount & CORS
│   └── requirements.txt
├── rca_engine/
│   ├── __init__.py
│   ├── analyzer.py                   # Incident analyzer & candidate selector
│   ├── correlation.py                # Time-window sliding correlator
│   ├── dependency_graph.py           # NetworkX microservice topology & blast radius
│   ├── scoring.py                    # Multi-factor explainable scoring engine
│   └── remediation.py                # Automated cloud runbooks & self-healing
├── event_processor/
│   ├── __init__.py
│   └── normalizer.py                 # Telemetry normalizer & schema validator
├── data/
│   └── synthetic_events/
│       ├── cascade.json              # Scenario 1: EC2 CPU Overload
│       ├── db_exhaustion.json        # Scenario 2: DynamoDB Throttle
│       ├── auth_outage.json          # Scenario 3: Auth JWKS Expiry
│       └── memory_leak.json          # Scenario 4: Worker Pod OOM Crash Loop
├── frontend/
│   ├── index.html                    # Glassmorphism Observability Cockpit
│   ├── styles.css                    # Modern Dark theme & pulse animations
│   └── app.js                        # Topology SVG renderer & reactive controller
├── infrastructure/
│   ├── Dockerfile                    # Container build
│   ├── docker-compose.yml            # Multi-service stack
│   └── aws_eventbridge_arch.tf       # AWS Terraform infrastructure template
├── tests/
│   ├── conftest.py                   # Fixtures & TestClient
│   ├── test_api.py                   # FastAPI route tests
│   ├── test_correlation.py           # Correlation tests
│   ├── test_scoring.py               # Algorithmic scoring tests
│   ├── test_normalizer.py            # Event normalizer tests
│   └── test_remediation.py           # Runbook tests
├── docs/
│   ├── ARCHITECTURE.md               # Technical architectural specification
│   ├── PRESENTATION_SLIDES.md        # 12-Slide deck & Viva Q&A guide
│   └── DEMO_SCRIPT.md                # Speaker script for live demonstration
├── run_local.py                      # Standalone CLI runner
├── pytest.ini
└── README.md
```

---

## 🐳 Docker Deployment

Run the complete platform inside a container with Docker Compose:
```bash
docker compose up --build
```
The platform will be live at `http://localhost:8000`.

---

## 📚 Presentation Materials & Documentation

- **Live Demonstration & Cockpit Guide:** [docs/DEMO.md](docs/DEMO.md)
- **Slide Deck & Viva Guide:** [docs/PRESENTATION_SLIDES.md](docs/PRESENTATION_SLIDES.md)
- **System Architecture Deep Dive:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- **Live Demo Speaker Script:** [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md)

---

## 📄 License
MIT License. Built for Autonomous Cloud Reliability Engineering.
