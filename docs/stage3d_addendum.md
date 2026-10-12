# Stage 3d Evaluation Addendum: Clean Baselines & Fair Comparator Audit

**Date**: 2026-10-12  
**Branch**: `rca-stage3e`  
**Reference Frozen Tag**: `stage3c-frozen` (`9fb5f49`), `stage3d-frozen` (`45658b6`)  

---

## 1. Executive Summary & Context

In Stage 3c, `rca_v3` was evaluated against `CurrentScorer` on Generator 3.0 benchmark splits (`dev3` and `val3`), where severity leakage had been removed.
In the raw telemetry comparison, `rca_v3` scored 82.0% on `val3`, whereas `CurrentScorer` scored 65.0%, leading to the preliminary claim that `rca_v3` provided a +17.0 pp advantage.

However, an audit of telemetry preprocessing revealed an unfair comparison:
1. `rca_v3` evaluated only anomalous telemetry breaches and filtered out routine background signals.
2. `CurrentScorer` was fed raw telemetry containing periodic benign heartbeats on idle services, which introduced spurious graph distance and frequency noise.
3. When `CurrentScorer` is supplied with the identical clean input preprocessing (`CurrentScorer_CaseTopology_Clean`), its Top-1 accuracy jumps to **90.0%** on `val3`.
4. As a result, the fair comparison demonstrates that `CurrentScorer_CaseTopology_Clean` outperforms `rca_v3` (82.0%) by **+8.0 percentage points** on the synthetic Generator 3.0 benchmark.

This addendum records the fair reassessment of pre-registered criteria V7 through V12 against `CurrentScorer_CaseTopology_Clean`.

---

## 2. Re-Assessment of Pre-registered Criteria (V7 to V12)

### (V7) Top-1 Accuracy Delta
- **Preregistered Criterion**: $\Delta = \text{Top-1}(\text{rca\_v3}) - \text{Top-1}(\text{CurrentScorer}) > 0$, with 95% CI strictly excluding 0.
- **Fair Comparator Evaluation (VAL3, $N=100$)**:
  - `rca_v3`: 82.0% [75.0, 89.0]
  - `CurrentScorer_CaseTopology_Clean`: 90.0% [84.0, 95.0]
  - $\Delta = -8.0\text{ pp}$ [-13.0, -4.0]
- **Status**: **FAILED**. `CurrentScorer_CaseTopology_Clean` achieves higher top-1 accuracy. In Generator 3.0, 90.0% of cases exhibit the true root as the earliest anomalous event, making clean earliest-anomaly graph scoring highly dominant.

---

### (V8) Performance vs Score Gate at 80% Coverage
- **Preregistered Criterion**: $\text{Top-1}(\text{rca\_v3} \mid \text{cov}=80\%) \ge \text{Top-1}(\text{ScoreGate} \mid \text{cov}=80\%)$.
- **VAL3 Performance**:
  - `rca_v3` at 80% Coverage: 87.5% [81.0, 93.0]
  - `ScoreGate_Clean` at 80% Coverage: 100.0% [100.0, 100.0]
- **Status**: **FAILED**. The clean score gate achieves 100% precision on its top 80 cases.

---

### (V9) Performance vs Severity Gate
- **Preregistered Criterion**: $\text{Top-1}(\text{rca\_v3}) > \text{Top-1}(\text{SeverityGate})$ with family-clustered 95% CI.
- **VAL3 Performance**:
  - `rca_v3`: 82.0% [75.0, 89.0]
  - `SeverityGate_Clean`: 73.0% [65.0, 81.0]
  - $\Delta = +9.0\text{ pp}$ [+2.0, +17.0]
- **Status**: **PASSED**. `rca_v3` significantly outperforms the severity gate, as severity is non-informative in Generator 3.0.

---

### (V10) CONFIRMED Precision > PROBABLE Precision
- **Preregistered Criterion**: $\text{Precision}(\text{CONFIRMED}) > \text{Precision}(\text{PROBABLE})$.
- **VAL3 Performance**:
  - With Step 3b Unobserved Ancestor Guard:
    - $\text{Precision}(\text{CONFIRMED}) = 100.0\%$
    - $\text{Precision}(\text{PROBABLE}) = 62.5\%$
- **Status**: **PASSED**. Principled verification ensures that confirmed verdicts have strictly superior precision to probable diagnoses.

---

### (V11) Multi-Root Set F1 Delta ($N \ge 30$)
- **Preregistered Criterion**: Set F1 of candidate root set exceeds baselines on multi-root cases ($N=35$).
- **VAL3 Performance**:
  - `rca_v3` Set F1: **1.000** [1.000, 1.000]
  - `CurrentScorer_CaseTopology_Clean` Set F1: **0.654** [0.638, 0.672]
  - $\Delta = +0.346$ [+0.328, +0.362]
- **Status**: **PASSED**. `rca_v3`'s marginal symptom coverage root-set selector successfully isolates both disjoint roots, whereas single-candidate scorers miss the secondary root.

---

### (V12) Hallucination Rate & False-Abstention Rate
- **Preregistered Criterion**: Hallucination on idle controls $\le 0.0\%$, False-abstention $\le 15\%$.
- **VAL3 Performance**:
  - Hallucination Rate on Idle Controls: 0.0%
  - False-Abstention Rate on Diagnosable Cases: 0.0% (0/100)
- **Status**: **PASSED**.

---

## 3. Conclusions and Way Forward

The core competitive advantage of `rca_v3` is **not** raw single-root cascade ranking accuracy (where clean earliest-anomaly detection achieves 90%), but rather:
1. **Multi-Root Separation**: Full recovery of disjoint simultaneous failures (Set F1 1.000 vs 0.654).
2. **Abstention Safety**: Refusal to issue false automated remediation when upstream causal ancestors are unobserved or telemetry collectors are down.
3. **Hybrid Architecture (`rca_v3h`)**: Coupling `CurrentScorer_Clean`'s ranking power with `rca_v3`'s principled verifier delivers 90.0% Top-1 ranking and 100.0% CONFIRMED precision.
