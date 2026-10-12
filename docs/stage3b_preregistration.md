# Stage 3b Pre-registration Document

## Objective
Evaluate `rca_v3` (with parsimony, corrected Check C, unified config, and required-signal policy mapping) against `CurrentScorer`, `rca_v2`, and the `ScoreThresholdGate` baseline on Generator 2.0 dataset splits (`dev2` and `val2`).

## Protocol & Blinding Rules
1. **Blind Evaluation**: Blinded splits (`test`, `test_supp`, and `test2`) are strictly forbidden from inspection or execution. They are hashed and locked.
2. **Strict Tuning Separation**: 
   - `dev-validation` from Stage 3 is permanently burned and will NOT be tuned on.
   - Hyperparameter tuning and threshold selection are restricted strictly to `dev2`.
   - `val2` is held-out and evaluated exactly ONCE after pre-registration and tuning on `dev2` are complete.
3. **Regression Baseline**: Old `dev` split will be re-run read-only with no tuning as a regression report.
4. **Reproducibility**: All numbers, metrics, and confidence intervals are computed by committed python scripts writing and reading CSV files.

## Dataset Splits (Generator 2.0)
- **dev2**: 60 synthetic cases across standard and edge failure topologies with baseline telemetry, normal noise, and partial outages.
- **val2**: 80 synthetic cases with disjoint seeds, including 35 idle-silent non-root control cases ($n \ge 30$).
- **test2**: 65 synthetic cases (strictly blinded, hashed in `manifest_v2.json`, never opened).

## Pre-registered Hypotheses

### (V1) Hallucinated-Root Rate on Idle-Silent Controls
- **Condition**: On idle-silent non-root control cases in `val2` ($N = 35 \ge 30$).
- **Metric**: Hallucinated-root rate defined as fraction of cases where any predicted root (or top-1 candidate) is an unobserved service that is not a ground-truth root cause.
- **Criterion**: $\text{Hallucination Rate}(\text{rca\_v3}) \le \text{Hallucination Rate}(\text{CurrentScorer})$. Target: 0.0%.

### (V2) Selective Top-1 vs Score Gate at Matched Coverage
- **Condition**: On all diagnosable cases in `val2`.
- **Metric**: Top-1 accuracy on the subset of cases where the engine produces a diagnosis (non-ABSTAIN).
- **Baseline**: `ScoreThresholdGate` tuned on `dev2` to match the exact coverage level of `rca_v3`.
- **Criterion**: $\text{Top-1}(\text{rca\_v3} \mid \text{diagnosed}) \ge \text{Top-1}(\text{ScoreGate} \mid \text{matched coverage})$ evaluated with paired family-clustered 95% bootstrap confidence interval ($CI_{\Delta} \ge 0$).

### (V3) Multi-Root Set F1
- **Condition**: Multi-root failure scenarios in `val2`.
- **Metric**: Set-level F1 score comparing the predicted set of root causes against ground truth root causes.
- **Criterion**: $\text{F1}(\text{rca\_v3}) > \text{F1}(\text{CurrentScorer})$.

### (V4) CONFIRMED Precision
- **Condition**: Cases where verdict is `CONFIRMED`.
- **Metric**: Precision of `CONFIRMED` diagnoses (diagnosed top candidate $\in$ true roots).
- **Criterion**: $\text{Precision}(\text{CONFIRMED}) \ge 90\%$ with support $N \ge 20$. If $N < 20$, the claim is formally NOT CLAIMED due to insufficient sample size.

### (V5) Abstention Quality
- **Condition**: All cases in `val2` (diagnosable and abstain-expected).
- **Metric 1**: Abstention precision = (True Abstentions) / (Total Abstentions).
- **Metric 2**: False abstention rate = (False Abstentions) / (Diagnosable Cases).
- **Criterion**: $\text{Abstention Precision} \ge 85\%$ AND $\text{False Abstention Rate} \le 15\%$ on diagnosable cases.

### (V6) Hard & Edge-Root Top-1 Delta
- **Condition**: Perturbation families involving unobserved roots, delayed telemetry, collector outages, and cascade noise.
- **Metric**: Top-1 accuracy delta: $\Delta = \text{Top-1}(\text{rca\_v3}) - \text{Top-1}(\text{CurrentScorer})$.
- **Criterion**: $\Delta > 0$ with 95% bootstrap CI strictly excluding 0.

## Ablation Plan
We will evaluate:
1. Full `rca_v3` model.
2. Ablation A: No Graph Candidates (only observed services evaluated).
3. Ablation B: No Symptom Severity Coverage (equal weights for all symptoms).
4. Ablation C: Inertness Check (permuting non-symptomatic telemetry should not change ranking).
