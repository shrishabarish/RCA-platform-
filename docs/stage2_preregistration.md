# Stage 2 Evaluation Pre-Registration: Evidence-Aware RCA (rca_v2)

**Registration Date**: 2026-10-11  
**Target Architecture**: `rca_v2` (Pure-function, evidence-aware root cause analysis engine)  
**Evaluation Scope**: DEVELOPMENT split (125 cases) and Hand-Written split (16 cases) ONLY.  
**Constraint**: TEST split (84 cases) is strictly blinded, unexecuted, and unreferenced.

---

## 1. Primary Pre-Registered Hypotheses

### Hypothesis 1 (H1: Selective Diagnosis Superiority over Score Gate)
* **Statement**: At the same fraction of cases diagnosed (matched coverage $\pm 5\%$), `rca_v2` achieves lower confident-wrong rate and higher selective Top-1 accuracy than the Score-Threshold Gate (`Control_ScoreThresholdGate_t73`).
* **Metric**: 
  - Confident-Wrong Rate ($\%$, lower is better) at confidence $\ge 75.0$.
  - Selective Top-1 Accuracy ($\%$, higher is better, computed over diagnosed single-root cases).
* **Comparator**: `Control_ScoreThresholdGate_t73` (Current Scorer gated at DEV threshold $t = 73.0$).
* **Statistical Test**: Two-sided family-clustered paired bootstrap ($B = 1000$ resamples of unique `family_id`s with replacement).
* **Success Criterion**: $\Delta \text{Selective Top-1} > 0$ and $\Delta \text{Confident-Wrong} < 0$ with 95% bootstrap CI excluding zero or within target bounds.

### Hypothesis 2 (H2: Evidence-Aware Abstention Precision and Recall)
* **Statement**: `rca_v2` achieves significantly higher abstention precision and abstention recall than the Score-Threshold Gate, while preserving accurate diagnoses on paired diagnosable counterpart cases within the same families.
* **Metric**: 
  - Abstention Precision ($k/n$, $\%$).
  - Abstention Recall ($k/n$, $\%$).
  - Paired Diagnosable Counterpart Accuracy ($k/n$, $\%$).
* **Comparator**: `Control_ScoreThresholdGate_t73` on the DEV split abstention cases (23 cases requiring abstention).
* **Statistical Test**: Family-clustered paired bootstrap ($B = 1000$ resamples).
* **Success Criterion**: $\text{Abstention Precision} \ge 85.0\%$ and $\text{Abstention Recall} \ge 80.0\%$, paired diagnosable counterpart accuracy $\ge 90.0\%$.

### Hypothesis 3 (H3: Non-Inferiority on Diagnosable Cases)
* **Statement**: On cases requiring diagnosis (`expected_verdict == "DIAGNOSE"`), `rca_v2` suffers no loss in Top-1 accuracy compared to the current 5-factor scorer evaluated with case topology (`CurrentScorer_CaseTopology`), beyond the 95% bootstrap confidence interval.
* **Metric**: Top-1 Accuracy on single-root diagnosable cases.
* **Comparator**: `CurrentScorer_CaseTopology`.
* **Statistical Test**: Family-clustered paired bootstrap difference ($\text{Top-1}(\text{rca\_v2}) - \text{Top-1}(\text{CurrentScorer\_CaseTopology})$).
* **Success Criterion**: Non-inferiority margin $\Delta \ge -3.0\text{ pp}$ with the lower bound of the 95% CI $\ge -5.0\text{ pp}$.

---

## 2. Statistical Methodology

1. **Family Clustered Resampling**:
   Cases derived from the same base scenario share topological structures and failure propagation patterns. To avoid artificially narrow confidence intervals from intra-cluster correlations, the resampling unit in all bootstrap calculations is the unique `family_id` ($N = 45$ dev families). All cases within a chosen family are included in the bootstrap replicate.
2. **Small-Sample Reporting Rule**:
   For any evaluation sub-cell with sample size $n < 5$, percentage values and confidence intervals are strictly suppressed. Output is reported as $k/n$ only.
3. **Unified Delta Format**:
   All paired comparisons are reported in the unified format:
   `+X.X pp [CI_lower, CI_upper]` (or `n/a (n=k)` if $n < 5$).
4. **Frozen Configuration**:
   All `rca_v2` decision rules and feature thresholds are determined strictly on the DEV split via Leave-Families-Out Cross-Validation (LFOCV), committed to disk with SHA-256 hash before evaluation.
