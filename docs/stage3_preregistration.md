# Stage 3 Pre-Registration Document: RCA Engine v3 Evaluation

**Date:** 2026-10-12  
**Branch:** `rca-stage3`  
**Evaluation Splits:** `dev` ($N=125$), `dev_validation` ($N=106$), and `handwritten` ($N=16$)  
**Status:** Pre-registered prior to dev-validation evaluation execution.

---

## 1. Objective and Scope
Evaluate `rca_v3` against `rca_v2`, `current_scorer`, and simple baselines on candidate ranking, root separation, and active evidence verification.
Tuning is conducted strictly on `dev` ($N=125$). Evaluation of pre-registered hypotheses is performed on the unseen `dev_validation` split ($N=106$). Test splits remain strictly blinded and untouched.

---

## 2. Statistical Methodology
- **Resampling Unit:** Family-clustered paired bootstrap with $B = 1,000$ iterations.
- **Confidence Intervals:** Empirical 95% percentile confidence intervals $[CI_{2.5\%}, CI_{97.5\%}]$.
- **Comparators:**
  - `current_scorer`: 5-factor scoring engine using topology graph.
  - `rca_v2`: Evidence-aware Stage 2 engine.
  - `earliest_anomaly`: Baseline selecting earliest anomalous timestamp.

---

## 3. Pre-Registered Hypotheses

### Hypothesis H4: Top-1 Accuracy on Hard & Edge-Root Cases
- **Statement:** `rca_v3` achieves strictly superior Top-1 accuracy compared to `current_scorer` on hard and edge-root cases in `dev_validation`.
- **Metric:** Paired $\Delta \text{Top-1} = \text{Top-1}_{\text{rca\_v3}} - \text{Top-1}_{\text{current\_scorer}}$.
- **Subset:** Cases with `is_hard == True` or belonging to edge-root families in `dev_validation`.
- **Decision Rule:** $\Delta \text{Top-1} > 0$ and the 95% family-clustered bootstrap CI strictly excludes 0 ($\text{CI}_{\text{lower}} > 0$).

### Hypothesis H5: Multi-Root Set F1 Separation
- **Statement:** `rca_v3` candidate set selection achieves higher mean set-level F1 score than `current_scorer` on multi-root incidents.
- **Metric:** Mean multi-root Set F1 score across all multi-root cases in `dev_validation`.
  $$\text{Precision} = \frac{|P \cap T|}{|P|}, \quad \text{Recall} = \frac{|P \cap T|}{|T|}, \quad F_1 = \frac{2 \cdot P \cdot R}{P + R}$$
- **Decision Rule:** $\text{Mean F1}(\text{rca\_v3}) > \text{Mean F1}(\text{current\_scorer})$.

### Hypothesis H6: Non-Root Idle Control & Missing Root Recovery
- **Statement:** On idle-silent non-root control cases, the rate of hallucinated unobserved roots for `rca_v3` is no higher than `current_scorer` (rate = 0.0%), while Top-1 accuracy on `missing_root_telemetry` hard cases improves over `current_scorer`.
- **Decision Rule:**
  1. $\text{Hallucination Rate}_{\text{control}}(\text{rca\_v3}) \le \text{Hallucination Rate}_{\text{control}}(\text{current\_scorer})$.
  2. $\text{Top-1}_{\text{missing\_root}}(\text{rca\_v3}) > \text{Top-1}_{\text{missing\_root}}(\text{current\_scorer})$.

### Hypothesis H7: CONFIRMED Verdict Precision
- **Statement:** `rca_v3` achieves a high precision on cases where it issues a `CONFIRMED` verdict.
- **Metric:** $\text{Precision}_{\text{CONFIRMED}} = \frac{\text{Correct Cases with CONFIRMED}}{\text{Total Cases with CONFIRMED}}$.
- **Decision Rule:** $\text{Precision}_{\text{CONFIRMED}} \ge 90.0\%$ on `dev_validation`. The sample size $n_{\text{CONFIRMED}}$ must be explicitly reported.

### Hypothesis H8: Principled Abstention Performance
- **Statement:** `rca_v3` achieves high abstention precision and higher abstention recall than `rca_v2` on `dev_validation`, while keeping diagnosable base counterparts diagnosed.
- **Decision Rule:**
  1. $\text{Abstention Precision} \ge 85.0\%$.
  2. $\text{Abstention Recall}_{\text{rca\_v3}} > \text{Abstention Recall}_{\text{rca\_v2}}$.
  3. Diagnosable counterpart base cases remain diagnosed (not falsely abstained).

### Hypothesis H9: Non-Inferiority on Overall Diagnosable Top-1
- **Statement:** `rca_v3` does not regress overall Top-1 performance across all diagnosable cases in `dev_validation` by more than a 3 percentage point margin compared to `rca_v2`.
- **Metric:** $\Delta \text{Top-1} = \text{Top-1}_{\text{rca\_v3}} - \text{Top-1}_{\text{rca\_v2}}$.
- **Decision Rule:** $\Delta \text{Top-1} \ge -3.0\text{ pp}$.

---

## 4. Exploratory Analyses (Post-Hoc Disclosed)
Any analysis or metric evaluated outside H4–H9 (e.g. breakdown across individual topology sizes, latency benchmarks) is designated as exploratory.
