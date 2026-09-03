# 🏛️ CloudLens Enterprise RCA Engine - Architecture Specification

## 1. System Overview

CloudLens Enterprise is an autonomous cloud incident correlation and graph-theoretic root-cause analysis (RCA) platform. It processes heterogeneous cloud telemetry (CloudWatch, Kubernetes, CloudTrail, Application Logs), eliminates noise and flapping alarms via sliding-window compaction, models multi-hop microservice topologies using NetworkX DAGs, and executes an explainable 5-factor mathematical ensemble to identify primary and compound root causes with exact causal propagation paths and automated self-healing runbooks.

---

## 2. Architectural Pipeline

```mermaid
flowchart TD
    subgraph Ingestion["1. Telemetry Ingestion & Pre-Filtering"]
        CW[AWS CloudWatch Alarms] --> NF[NoiseFilter & Burst Compactor]
        K8S[Kubernetes Pod Events] --> NF
        APP[Application Exception Logs] --> NF
        SYN[Synthetic Failure Scenarios] --> NF
        NF --> Val[Pydantic V2 Schema Validation]
    end

    subgraph Correlation["2. Temporal & Incident Layer"]
        Val --> Chrono[Chronological Sorter]
        Chrono --> Window[Sliding Time-Window Correlator (60s)]
        Window --> Inc[Correlated Incident Object]
    end

    subgraph Engine["3. Graph-Theoretic Causal Engine"]
        Inc --> CausalGraph[NetworkX Causal Dependency Graph]
        CausalGraph --> PageRank[Random Walk / PageRank Anomaly Diffusion]
        CausalGraph --> PathFinder[Topological Causal Path Tracer]
        CausalGraph --> Blast[Transitive Blast Radius Calculator]
        CausalGraph --> WhatIf[Predictive What-If Outage Simulator]
    end

    subgraph Scoring["4. Multi-Factor Ensemble Scoring"]
        Blast --> Ensemble[Multi-Factor Mathematical Ensemble]
        Ensemble --> TemporalDecay[Exponential Time Decay (25 pts)]
        Ensemble --> TopoCentrality[Graph Centrality & Blast (30 pts)]
        Ensemble --> SymptomOverlap[Symptom Causal Overlap (20 pts)]
        Ensemble --> MetricMag[Metric Anomaly Magnitude (15 pts)]
        Ensemble --> SevWeight[Severity Weight (10 pts)]
        Ensemble --> Reasoning[Step-by-Step Audit Trail & Chain]
    end

    subgraph Presentation["5. API & Visualization Cockpit"]
        Reasoning --> FastAPIServer[FastAPI REST Server v2.0]
        FastAPIServer --> UI[Interactive Glassmorphism Cockpit]
        FastAPIServer --> Swagger[OpenAPI / Swagger UI]
    end
```

---

## 3. Mathematical Scoring Model (Enterprise Ensemble)

The total confidence score $\text{Score}(S) \in [0, 100]$ for candidate service $S$ is computed as:

$$\text{Score}(S) = S_{\text{temporal}}(S) + S_{\text{topo}}(S) + S_{\text{symptom}}(S) + S_{\text{metric}}(S) + S_{\text{severity}}(S)$$

### 3.1. Temporal Lead Time Factor ($S_{\text{temporal}}$, Max 25 pts)
Implements continuous exponential time decay based on lead time $\Delta t = t_S - t_{\text{earliest}}$:

$$S_{\text{temporal}}(S) = \begin{cases} 25 & \text{if } \Delta t = 0 \\ \max(10, \lfloor 25 \cdot e^{-0.02 \cdot \Delta t} \rceil) & \text{if } \Delta t > 0 \end{cases}$$

### 3.2. Topological Centrality & Blast Radius ($S_{\text{topo}}$, Max 30 pts)
Evaluates direct and transitive graph reachability on reversed DAG $G^T$:

$$S_{\text{topo}}(S) = \min(|DirectDependents(S)| \times 15 + \min(|TransitiveBlast(S)| \times 5, 10), 30)$$

### 3.3. Symptom Causal Overlap ($S_{\text{symptom}}$, Max 20 pts)
Calculates the Jaccard/Overlap ratio between graph descendants and active symptom nodes:

$$S_{\text{symptom}}(S) = \text{round}\left( \frac{|\text{ObservedSymptoms} \cap \text{BlastRadius}(S)|}{|\text{ObservedSymptoms}|} \times 20 \right)$$

### 3.4. Metric Anomaly Magnitude ($S_{\text{metric}}$, Max 15 pts)
Quantifies deviation from critical telemetry thresholds (e.g. CPU $\ge 90\%$, Latency $> 2000\text{ms}$, Error $> 50\%$):

$$S_{\text{metric}}(S) \in [10, 15]$$

### 3.5. Anomaly Severity Weight ($S_{\text{severity}}$, Max 10 pts)
Weights highest observed alarm severity:
- `CRITICAL`: 10 pts
- `HIGH`: 8 pts
- `MEDIUM`: 5 pts
- `LOW`: 2 pts

---

## 4. API Endpoints Reference (v2.0)

| Method | Path | Description |
| :--- | :--- | :--- |
| `GET` | `/api/health` | Health status and live telemetry counters |
| `GET` | `/api/scenarios` | Lists pre-packaged failure simulation scenarios |
| `POST` | `/api/scenarios/{id}/simulate` | Triggers a failure scenario and returns correlated incidents |
| `GET` | `/api/what-if/{service}` | Predictive what-if failure simulator & architecture risk rating |
| `GET` | `/api/topology` | Returns graph topology nodes, edges, and real-time status |
| `GET` | `/api/events` | Retrieves all ingested cloud events |
| `POST` | `/api/events` | Ingests a single cloud event |
| `POST` | `/api/events/batch` | Ingests a batch of cloud events |
| `GET` | `/api/incidents` | Retrieves all active incidents and RCA summaries |
| `GET` | `/api/incidents/{id}` | Retrieves full incident analysis, causal paths, and audit trail |
| `GET` | `/api/incidents/{id}/remediation` | Retrieves automated cloud remediation runbook |
| `POST` | `/api/incidents/{id}/mitigate` | Simulates execution of automated remediation |
| `POST` | `/api/reset` | Resets system state |
