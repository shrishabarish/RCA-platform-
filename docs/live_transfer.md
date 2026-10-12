# Live Transfer Analysis: RCA Features, Telemetry Assumptions & Production Reality

**Date**: 2026-10-12  
**Target Environment**: AWS / Hybrid Cloud Production Pipeline  
**Module**: `rca_v3` / `rca_v3h`  

---

## 1. Executive Summary

Evaluation benchmarks operate on synthetic event histories where both anomalous breach alarms and periodic baseline heartbeats are injected. In contrast, the live production pipeline (`backend/app/api/events.py`, `CloudEvent` ingestion) receives discrete alarm threshold breaches, CloudWatch alarms, Datadog alerts, and Kubernetes container state changes.

This document identifies which `rca_v3` features require periodic continuous telemetry, how the live pipeline behaves in production, what `clean_heartbeats` accomplishes in live streaming, and which theoretical features become inert in production.

---

## 2. Telemetry Expectations: Benchmark vs. Live Pipeline

| Dimension | Synthetic Benchmark | Live Production Pipeline |
| :--- | :--- | :--- |
| **Telemetry Nature** | Dense time-series events including synthetic periodic heartbeats every 10–60s. | Sparse alarm bursts emitted only when metrics cross warning or critical thresholds. |
| **Normal State** | Continuous LOW events with `metric == "Heartbeat"` or `metric == "RoutineHealthCheck"`. | Silence. No events emitted while services remain within SLO bounds. |
| **Source Health** | Injected via explicit `SourceHealthRecord` arrays (`HEALTHY`, `DOWN`). | Inferred dynamically from stream silence vs expected ingestion rate (`derive_live_source_health`). |
| **Telemetry Policy** | Explicit JSON map defining expected signals and intervals per service. | Default empty policy unless explicitly provisioned per tenant. |

---

## 3. RCA Feature Audit: Active vs. Inert Live

### Features That Work Reliably Live
1. **Directed Causal Graph Relatedness (Step 2d)**:
   - *Status*: **Active**.
   - *Behavior*: Validates that failure propagation follows directed callee-to-caller call chains (`is_ancestor_cause`, `are_services_related`). Relies solely on service topology metadata, independent of event density.
2. **Multi-Root Disjoint Set Separation (Step 2c)**:
   - *Status*: **Active**.
   - *Behavior*: Identifies concurrent independent failures on disjoint subgraphs using marginal symptom coverage. Works on alarm events without requiring heartbeats.
3. **Safety Circuit Breaker (Step 2f)**:
   - *Status*: **Active**.
   - *Behavior*: Isolates evaluation thread pools from hanging or timing out, fast-failing in <5ms during database or network partitions.
4. **Strict Remediation Guards (Step 3c)**:
   - *Status*: **Active**.
   - *Behavior*: Prohibits automated action when the verdict is not `CONFIRMED`, when upstream causal ancestors are unobserved, or when human approval is missing.

### Features That Depend on Periodic Telemetry or Become Inert Live
1. **Evidence State QUIET vs UNKNOWN**:
   - *Status*: **Inert without explicit Telemetry Policy**.
   - *Explanation*: In live environments without periodic heartbeats, a silent service cannot be distinguished between "healthy and quiet" vs "unmonitored/broken collector" unless an explicit expected signals policy exists. In the absence of positive heartbeats, services evaluate to `UNKNOWN`.
2. **`ROOT_HEALTHY_WHILE_DEPENDENTS_BLAME` Contradiction**:
   - *Status*: **Inert under alarm-only ingestion**.
   - *Explanation*: If production only sends events upon failure, a healthy service emits zero events. A contradiction rule requiring positive healthy heartbeats will never fire unless synthetic pings are ingested into the live stream.
3. **`clean_heartbeats` Live Role**:
   - *Status*: **Protective No-Op / Invariance Guard**.
   - *Explanation*: If external synthetic monitoring or Datadog pings are routed into the event bus, `clean_heartbeats` prevents them from altering candidate rankings, shifting timestamps, or injecting spurious graph distances. If alarms-only are routed, `clean_heartbeats` is a safe pass-through.
4. **Time-Consistent Symptom Scoring Delay Window**:
   - *Status*: **Partially Degraded by Network Latency**.
   - *Explanation*: Relies on timestamp ordering. If CloudWatch alarm delivery experiences jitter or out-of-order delivery across regions, clock skew tolerance must remain configured at $\ge 60$s.

---

## 4. Production Recommendations

1. **Deploy Hybrid Engine (`rca_v3h`)**:
   - Combine `CurrentScorer`'s topology scoring (which thrives on sparse alarms) with `rca_v3`'s multi-root separation and principled safety guards.
2. **Ingest Positive Heartbeats from Ingress / Synthetic Canaries**:
   - Route regular synthetic ping events into the `application` source so that `ROOT_HEALTHY_WHILE_DEPENDENTS_BLAME` and quiet timeline validation can fire in production.
3. **Keep Remediation Auto-Execute Disabled**:
   - Always enforce `human_approval_granted: bool = True` and block automated script execution on `PROBABLE` diagnoses.
