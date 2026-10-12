# Stage 3c Pre-registration Document (Generator 3.0 & VAL3 Benchmark)

## Objective
Evaluate `rca_v3` against `CurrentScorer`, `ScoreThresholdGate`, and `SeverityThresholdGate` on Generator 3.0 dataset splits (`dev3` and `val3`), where severity leakage has been removed (root failures and downstream symptoms draw severity from the identical distribution).

## Protocol & Blinding Rules
1. **Blind Evaluation**: Blinded split `test3` ($N=80$) is strictly forbidden from inspection or execution. It is hashed in `manifest_v3.json` and locked.
2. **Strict Tuning Separation**: Hyperparameters are frozen on `dev3`. `val3` is held out and evaluated exactly ONCE.
3. **Reproducibility**: All metrics and confidence intervals are computed from committed scripts reading saved CSV files.

## Dataset Splits (Generator 3.0)
- **dev3**: 100 cases (20 single metric, 10 single silent, 35 multi-root, 35 idle-silent controls).
- **val3**: 100 cases with disjoint seeds and families (20 single metric, 10 single silent, 35 multi-root, 35 idle-silent controls).
- **test3**: 80 cases (strictly blinded, hashed and locked).

## Pre-registered Hypotheses (V7 to V12)

### (V7) rca_v3 Top-1 vs CurrentScorer Delta
- **Condition**: Diagnosable cases in `val3`.
- **Criterion**: $\Delta = \text{Top-1}(\text{rca\_v3}) - \text{Top-1}(\text{CurrentScorer}) > 0$, with family-clustered 95% bootstrap CI strictly excluding 0.

### (V8) rca_v3 vs Score Gate at 80% Coverage
- **Condition**: Diagnosable cases in `val3` at 80% coverage.
- **Criterion**: $\text{Top-1}(\text{rca\_v3} \mid \text{cov}=80\%) \ge \text{Top-1}(\text{ScoreGate} \mid \text{cov}=80\%)$.

### (V9) rca_v3 vs Severity Gate
- **Condition**: All diagnosable cases in `val3` with uninformative severity.
- **Criterion**: $\text{Top-1}(\text{rca\_v3}) > \text{Top-1}(\text{SeverityGate})$ with family-clustered 95% CI.

### (V10) CONFIRMED Precision > PROBABLE Precision
- **Condition**: Verdicts in `val3` with support $N \ge 20$.
- **Criterion**: $\text{Precision}(\text{CONFIRMED}) > \text{Precision}(\text{PROBABLE})$.

### (V11) Multi-Root Set F1 Delta ($N \ge 30$)
- **Condition**: Multi-root failure scenarios in `val3` ($N = 35 \ge 30$).
- **Criterion**: Evaluated with $\alpha = 0.85$ frozen on `dev3`. Report paired delta and 95% CI.

### (V12) Hallucination Rate & False-Abstention Rate
- **Condition 1**: Idle-silent non-root controls ($N = 35 \ge 30$). Hallucination rate $\le$ CurrentScorer (target 0.0%).
- **Condition 2**: Diagnosable cases in `val3`. False-abstention rate $\le 15\%$.
- **Criterion**: Both conditions satisfied.
