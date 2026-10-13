import math
import random
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict
from pydantic import BaseModel

from evaluation.schema import EvaluationCase
from evaluation.baselines.adapter import BaselineResult
from backend.app.models.event import CloudEvent, Severity
from rca_engine.correlation import TimeWindowCorrelator
from rca_engine.causal_graph import CausalGraphEngine
from datetime import datetime, timezone


class MetricSummary(BaseModel):
    metric_name: str
    value: float
    ci_lower: Optional[float] = None
    ci_upper: Optional[float] = None
    sample_size: int
    count_k: Optional[int] = None
    is_na: bool = False
    flag_low_sample: bool = False  # Flagged if sample_size < 5


def format_metric(k: int, n: int, value: float, ci_lower: Optional[float] = None, ci_upper: Optional[float] = None) -> str:
    """Formats a metric value. If n < 5, suppresses percentage and prints k/n only."""
    if n < 5:
        return f"{k}/{n}"
    if ci_lower is not None and ci_upper is not None:
        return f"{value:.1f}% [{ci_lower:.1f}, {ci_upper:.1f}] ({k}/{n})"
    return f"{value:.1f}% ({k}/{n})"


def format_delta(delta_pp: float, ci_lower: Optional[float] = None, ci_upper: Optional[float] = None, n: int = 0) -> str:
    """Formats paired delta in unified format: +X.X pp [CI] or n/a if n < 5."""
    if 0 < n < 5:
        return f"n/a (n={n})"
    sign = "+" if delta_pp >= 0 else ""
    if ci_lower is not None and ci_upper is not None:
        return f"{sign}{delta_pp:.1f} pp [{ci_lower:.1f}, {ci_upper:.1f}]"
    return f"{sign}{delta_pp:.1f} pp"


def bootstrap_ci_family_clustered(
    family_data: Dict[str, List[float]],
    n_bootstrap: int = 1000,
    alpha: float = 0.05,
    seed: int = 42
) -> Tuple[float, float]:
    """
    Computes family-clustered empirical bootstrap percentile 95% confidence intervals.
    Resamples unique family IDs with replacement to preserve intra-family correlation.
    """
    if not family_data:
        return 0.0, 0.0
    families = list(family_data.keys())
    if len(families) == 1:
        all_vals = [v for vals in family_data.values() for v in vals]
        mean_v = sum(all_vals) / len(all_vals) if all_vals else 0.0
        return mean_v, mean_v

    rng = random.Random(seed)
    n_fam = len(families)
    means = []
    for _ in range(n_bootstrap):
        sampled_fams = [families[rng.randint(0, n_fam - 1)] for _ in range(n_fam)]
        sample = [v for f in sampled_fams for v in family_data[f]]
        if sample:
            means.append(sum(sample) / len(sample))

    if not means:
        return 0.0, 0.0
    means.sort()
    lower_idx = int((alpha / 2.0) * len(means))
    upper_idx = min(len(means) - 1, int((1.0 - alpha / 2.0) * len(means)) - 1)
    return means[lower_idx], means[upper_idx]


def bootstrap_ci(data: List[float], n_bootstrap: int = 1000, alpha: float = 0.05, seed: int = 42) -> Tuple[float, float]:
    """Computes standard empirical bootstrap percentile 95% confidence intervals."""
    if not data:
        return 0.0, 0.0
    if len(data) == 1:
        return data[0], data[0]

    rng = random.Random(seed)
    n = len(data)
    means = []
    for _ in range(n_bootstrap):
        sample = [data[rng.randint(0, n - 1)] for _ in range(n)]
        means.append(sum(sample) / n)

    means.sort()
    lower_idx = int((alpha / 2.0) * n_bootstrap)
    upper_idx = int((1.0 - alpha / 2.0) * n_bootstrap) - 1
    return means[lower_idx], means[upper_idx]


def compute_top_k_accuracy(predictions: List[Tuple[EvaluationCase, BaselineResult]], k: int = 1) -> MetricSummary:
    """Computes Top-K accuracy on single-root cases requiring DIAGNOSE with family-clustered bootstrap."""
    def _is_single_root_diagnose(c: EvaluationCase) -> bool:
        if not c.ground_truth or len(c.ground_truth.true_root_causes) != 1:
            return False
        outcomes = getattr(c, "acceptable_outcomes", []) or (getattr(c.ground_truth, "acceptable_outcomes", []) if c.ground_truth else [])
        if outcomes:
            return any(o.verdict == "DIAGNOSE" for o in outcomes)
        if hasattr(c.ground_truth, "expected_verdict"):
            return getattr(c.ground_truth, "expected_verdict") == "DIAGNOSE"
        return True

    single_root_cases = [
        (c, p) for c, p in predictions
        if _is_single_root_diagnose(c)
    ]
    n = len(single_root_cases)
    if n == 0:
        return MetricSummary(metric_name=f"Top-{k}", value=0.0, sample_size=0, count_k=0, is_na=True, flag_low_sample=True)

    family_map = defaultdict(list)
    correct = 0
    for c, p in single_root_cases:
        true_root = c.ground_truth.true_root_causes[0]
        predicted_top_k = p.predicted_root_causes[:k]
        hit = 1.0 if true_root in predicted_top_k else 0.0
        if hit == 1.0:
            correct += 1
        family_map[c.family_id].append(hit)

    acc = correct / n
    ci_low, ci_high = bootstrap_ci_family_clustered(family_map)
    return MetricSummary(
        metric_name=f"Top-{k}",
        value=round(acc * 100.0, 2),
        ci_lower=round(ci_low * 100.0, 2),
        ci_upper=round(ci_high * 100.0, 2),
        sample_size=n,
        count_k=correct,
        flag_low_sample=(n < 5)
    )


def extract_predicted_root_set(pred: BaselineResult, alpha: float = 0.85, max_k: int = 3, oracle_len: Optional[int] = None) -> List[str]:
    """
    Standard deterministic rule to convert ranked candidate list into a predicted root set:
    - Top candidate (Rank 1) is always included.
    - If candidate_scores available, candidates 2..max_k with score >= alpha * top_score are included.
    - If oracle_len specified and candidate_scores empty, takes top oracle_len candidates.
    """
    if not pred.predicted_root_causes:
        return []
    if not pred.candidate_scores and oracle_len is not None:
        return pred.predicted_root_causes[:oracle_len]
    
    top_c = pred.predicted_root_causes[0]
    top_score = pred.candidate_scores.get(top_c, pred.confidence)
    root_set = [top_c]
    if top_score > 0:
        for c in pred.predicted_root_causes[1:max_k]:
            sc = pred.candidate_scores.get(c, 0.0)
            if sc >= alpha * top_score:
                root_set.append(c)
    return root_set


def compute_multiroot_f1(predictions: List[Tuple[EvaluationCase, BaselineResult]], alpha: float = 0.85) -> MetricSummary:
    """Computes set-based multi-root Precision, Recall, and F1 across multi-root cases."""
    def _is_multi_root_diagnose(c: EvaluationCase) -> bool:
        if not c.ground_truth or len(c.ground_truth.true_root_causes) < 2:
            return False
        outcomes = getattr(c, "acceptable_outcomes", []) or (getattr(c.ground_truth, "acceptable_outcomes", []) if c.ground_truth else [])
        if outcomes:
            return any(o.verdict == "DIAGNOSE" for o in outcomes)
        if hasattr(c.ground_truth, "expected_verdict"):
            return getattr(c.ground_truth, "expected_verdict") == "DIAGNOSE"
        return True

    multi_cases = [
        (c, p) for c, p in predictions
        if _is_multi_root_diagnose(c)
    ]
    n = len(multi_cases)
    if n == 0:
        return MetricSummary(metric_name="MultiRoot_F1", value=0.0, sample_size=0, count_k=0, is_na=True, flag_low_sample=True)

    family_map = defaultdict(list)
    f1_scores = []
    for c, p in multi_cases:
        true_set = set(c.ground_truth.true_root_causes)
        # Take oracle len if scores not present, or margin-gated set if scores present
        pred_set = set(extract_predicted_root_set(p, alpha=alpha, oracle_len=len(true_set)))
        overlap = len(true_set.intersection(pred_set))
        prec = overlap / len(pred_set) if pred_set else 0.0
        rec = overlap / len(true_set) if true_set else 1.0
        f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0
        f1_scores.append(f1)
        family_map[c.family_id].append(f1)

    mean_f1 = sum(f1_scores) / n
    ci_low, ci_high = bootstrap_ci_family_clustered(family_map)
    return MetricSummary(
        metric_name="MultiRoot_F1",
        value=round(mean_f1 * 100.0, 2),
        ci_lower=round(ci_low * 100.0, 2),
        ci_upper=round(ci_high * 100.0, 2),
        sample_size=n,
        count_k=int(round(sum(f1_scores))),
        flag_low_sample=(n < 5)
    )


def compute_abstention_metrics(predictions: List[Tuple[EvaluationCase, BaselineResult]]) -> Tuple[MetricSummary, MetricSummary]:
    """
    Computes Abstention Precision and Recall:
    Abstention Precision = correct abstentions / all abstentions
    Abstention Recall = correct abstentions / all cases requiring abstention
    """
    def _requires_abstention(c: EvaluationCase) -> bool:
        outcomes = getattr(c, "acceptable_outcomes", []) or (getattr(c.ground_truth, "acceptable_outcomes", []) if c.ground_truth else [])
        if outcomes:
            return any(o.verdict == "ABSTAIN" and not o.roots for o in outcomes)
        if hasattr(c.ground_truth, "expected_verdict"):
            return getattr(c.ground_truth, "expected_verdict") == "ABSTAIN"
        return len(c.ground_truth.true_root_causes) == 0 if c.ground_truth else False

    all_abstentions = sum(1 for _, p in predictions if p.verdict == "ABSTAIN")
    cases_requiring_abstention = sum(1 for c, _ in predictions if _requires_abstention(c))
    correct_abstentions = sum(1 for c, p in predictions if p.verdict == "ABSTAIN" and _requires_abstention(c))

    prec_fam = defaultdict(list)
    for c, p in predictions:
        if p.verdict == "ABSTAIN":
            prec_fam[c.family_id].append(1.0 if _requires_abstention(c) else 0.0)

    if all_abstentions == 0:
        prec_summary = MetricSummary(metric_name="Abstention_Precision", value=0.0, sample_size=0, count_k=0, is_na=True, flag_low_sample=True)
    else:
        prec = correct_abstentions / all_abstentions
        ci_p_low, ci_p_high = bootstrap_ci_family_clustered(prec_fam)
        prec_summary = MetricSummary(
            metric_name="Abstention_Precision",
            value=round(prec * 100.0, 2),
            ci_lower=round(ci_p_low * 100.0, 2),
            ci_upper=round(ci_p_high * 100.0, 2),
            sample_size=all_abstentions,
            count_k=correct_abstentions,
            flag_low_sample=(all_abstentions < 5)
        )

    rec_fam = defaultdict(list)
    for c, p in predictions:
        if _requires_abstention(c):
            rec_fam[c.family_id].append(1.0 if p.verdict == "ABSTAIN" else 0.0)

    if cases_requiring_abstention == 0:
        rec_summary = MetricSummary(metric_name="Abstention_Recall", value=100.0, sample_size=0, count_k=0, is_na=True, flag_low_sample=True)
    else:
        rec = correct_abstentions / cases_requiring_abstention
        ci_r_low, ci_r_high = bootstrap_ci_family_clustered(rec_fam)
        rec_summary = MetricSummary(
            metric_name="Abstention_Recall",
            value=round(rec * 100.0, 2),
            ci_lower=round(ci_r_low * 100.0, 2),
            ci_upper=round(ci_r_high * 100.0, 2),
            sample_size=cases_requiring_abstention,
            count_k=correct_abstentions,
            flag_low_sample=(cases_requiring_abstention < 5)
        )

    return prec_summary, rec_summary


def is_outcome_acceptable(
    case: EvaluationCase,
    pred: BaselineResult,
    confidence_threshold: float = 75.0
) -> Tuple[bool, bool]:
    """
    Evaluates whether an engine prediction is acceptable according to the ground truth.
    Returns (is_acceptable, is_confident_wrong).

    SCORER SPECIFICATION & CONTRACT:
    - Scorer must use ground_truth.acceptable_outcomes and ground_truth.max_level_by_root ONLY.
    - The legacy field ground_truth.acceptable_verdict_level is explicitly IGNORED.
    - If acceptable_outcomes is defined:
        1. An ABSTAIN prediction is acceptable if any AcceptableOutcome in acceptable_outcomes
           has verdict == "ABSTAIN".
        2. A DIAGNOSE prediction is acceptable if there exists an AcceptableOutcome where:
           - outcome.verdict == "DIAGNOSE"
           - The predicted root causes match outcome.roots (or top predicted root in outcome.roots)
           - For all predicted roots, if root in max_level_by_root:
               - If max_level_by_root[root] == "PROBABLE", confidence must be < confidence_threshold.
                 A diagnosis with confidence >= confidence_threshold on a PROBABLE-capped root is confident-wrong.
               - If max_level_by_root[root] == "ABSTAIN", diagnosing this root is wrong.
    - If acceptable_outcomes is not populated, falls back to expected_verdict and true_root_causes.
    """
    gt = case.ground_truth
    acceptable_outcomes = getattr(gt, "acceptable_outcomes", []) or getattr(case, "acceptable_outcomes", [])
    max_level_by_root = getattr(gt, "max_level_by_root", {}) or getattr(case, "max_level_by_root", {})

    if acceptable_outcomes:
        # Check against acceptable_outcomes
        if pred.verdict == "ABSTAIN":
            has_abstain_outcome = any(o.verdict == "ABSTAIN" for o in acceptable_outcomes)
            return has_abstain_outcome, False

        # pred.verdict == "DIAGNOSE"
        top_pred = pred.predicted_root_causes[0] if pred.predicted_root_causes else None
        if not top_pred:
            # Predicted DIAGNOSE with no roots
            return False, False

        # Check if top_pred is allowed by any acceptable outcome
        matching_outcomes = [
            o for o in acceptable_outcomes
            if o.verdict == "DIAGNOSE" and top_pred in o.roots
        ]

        if not matching_outcomes:
            # Blamed a service not in acceptable outcomes (e.g., blamed DynamoDB in contradictory case)
            is_cw = (pred.confidence >= confidence_threshold)
            return False, is_cw

        # In multi-root cases (where an outcome requires > 1 root):
        # Full root set is the only fully correct outcome; single-root answers are scored as "incomplete" (not correct)
        pred_root_set = set(extract_predicted_root_set(pred, alpha=0.85, oracle_len=None) or (pred.predicted_root_causes or []))

        # Check if there is an exact multi-root match
        exact_multi_matches = [
            o for o in matching_outcomes
            if len(o.roots) > 1 and set(o.roots) == pred_root_set
        ]
        if exact_multi_matches:
            # Full root set correctly predicted!
            for r in pred_root_set:
                allowed_max_level = max_level_by_root.get(r)
                if allowed_max_level == "ABSTAIN":
                    return False, (pred.confidence >= confidence_threshold)
                elif allowed_max_level == "PROBABLE" and pred.confidence >= confidence_threshold:
                    return False, True
            return True, False

        # If matching outcome requires multiple roots, but prediction is partial (e.g. single root):
        if any(len(o.roots) > 1 and not getattr(o, "allow_partial", False) for o in matching_outcomes):
            if not any(len(o.roots) == 1 for o in matching_outcomes):
                # Single-root or incomplete answer on multi-root case: NOT correct (incomplete), NOT confident-wrong
                return False, False

        # Single-root outcome match
        single_matches = [o for o in matching_outcomes if len(o.roots) == 1]
        if single_matches:
            allowed_max_level = max_level_by_root.get(top_pred)
            if allowed_max_level == "ABSTAIN":
                is_cw = (pred.confidence >= confidence_threshold)
                return False, is_cw
            elif allowed_max_level == "PROBABLE":
                if pred.confidence >= confidence_threshold:
                    return False, True
                return True, False
            else:
                return True, False

        return False, False

    # Fallback to standard true_root_causes
    is_wrong = False
    exp_verdict = getattr(gt, "expected_verdict", None)
    if exp_verdict == "ABSTAIN" or (exp_verdict is None and len(gt.true_root_causes) == 0):
        if pred.verdict != "ABSTAIN":
            is_wrong = True
    else:
        true_roots = gt.true_root_causes
        top_pred = pred.predicted_root_causes[0] if pred.predicted_root_causes else None
        if top_pred not in true_roots:
            is_wrong = True

    is_cw = (is_wrong and pred.confidence >= confidence_threshold)
    return not is_wrong, is_cw


def compute_confident_wrong_rate(
    predictions: List[Tuple[EvaluationCase, BaselineResult]],
    confidence_threshold: float = 75.0
) -> MetricSummary:
    """
    Confident-wrong rate = incorrect diagnoses with score >= threshold / all cases.
    Evaluates correctness strictly using acceptable_outcomes and max_level_by_root,
    ignoring legacy acceptable_verdict_level.
    """
    n = len(predictions)
    if n == 0:
        return MetricSummary(metric_name="Confident_Wrong_Rate", value=0.0, sample_size=0, count_k=0, is_na=True, flag_low_sample=True)

    family_map = defaultdict(list)
    cw_count = 0
    for c, p in predictions:
        _, is_cw = is_outcome_acceptable(c, p, confidence_threshold=confidence_threshold)
        ind = 1.0 if is_cw else 0.0
        if ind == 1.0:
            cw_count += 1
        family_map[c.family_id].append(ind)

    rate = cw_count / n
    ci_low, ci_high = bootstrap_ci_family_clustered(family_map)
    return MetricSummary(
        metric_name="Confident_Wrong_Rate",
        value=round(rate * 100.0, 2),
        ci_lower=round(ci_low * 100.0, 2),
        ci_upper=round(ci_high * 100.0, 2),
        sample_size=n,
        count_k=cw_count,
        flag_low_sample=(n < 5)
    )


def compute_paired_family_delta(
    pairs_a: List[Tuple[EvaluationCase, BaselineResult]],
    pairs_b: List[Tuple[EvaluationCase, BaselineResult]],
    metric_type: str = "top1",
    conf_thresh: float = 75.0
) -> Tuple[float, float, float, int]:
    """
    Computes paired family-level delta (Model A - Model B) and 95% bootstrap CI.
    Returns (delta_pp, ci_lower_pp, ci_upper_pp, n).
    """
    dict_b = {c.case_id: p for c, p in pairs_b}
    family_deltas = defaultdict(list)
    total_n = 0

    for c, p_a in pairs_a:
        p_b = dict_b.get(c.case_id)
        if not p_b:
            continue

        if metric_type == "top1":
            if len(c.ground_truth.true_root_causes) != 1 or c.ground_truth.expected_verdict != "DIAGNOSE":
                continue
            true_root = c.ground_truth.true_root_causes[0]
            hit_a = 1.0 if p_a.predicted_root_causes and p_a.predicted_root_causes[0] == true_root else 0.0
            hit_b = 1.0 if p_b.predicted_root_causes and p_b.predicted_root_causes[0] == true_root else 0.0
            diff = hit_a - hit_b
            family_deltas[c.family_id].append(diff)
            total_n += 1

        elif metric_type == "confident_wrong":
            def is_cw(p):
                wrong = False
                if c.ground_truth.expected_verdict == "ABSTAIN":
                    wrong = (p.verdict != "ABSTAIN")
                else:
                    tr = c.ground_truth.true_root_causes
                    top_pred = p.predicted_root_causes[0] if p.predicted_root_causes else None
                    wrong = (top_pred not in tr)
                return 1.0 if (wrong and p.confidence >= conf_thresh) else 0.0

            diff = is_cw(p_a) - is_cw(p_b)
            family_deltas[c.family_id].append(diff)
            total_n += 1

        elif metric_type == "abstain_precision":
            if p_a.verdict != "ABSTAIN" and p_b.verdict != "ABSTAIN":
                continue
            c_a = 1.0 if (p_a.verdict == "ABSTAIN" and c.ground_truth.expected_verdict == "ABSTAIN") else 0.0
            c_b = 1.0 if (p_b.verdict == "ABSTAIN" and c.ground_truth.expected_verdict == "ABSTAIN") else 0.0
            family_deltas[c.family_id].append(c_a - c_b)
            total_n += 1

    if total_n == 0:
        return 0.0, 0.0, 0.0, 0

    all_diffs = [d for diffs in family_deltas.values() for d in diffs]
    delta = sum(all_diffs) / total_n * 100.0
    ci_low, ci_high = bootstrap_ci_family_clustered(family_deltas)
    return round(delta, 2), round(ci_low * 100.0, 2), round(ci_high * 100.0, 2), total_n


def compute_correlation_merge_split_rates(
    cases: List[EvaluationCase],
    use_case_topology: bool = False
) -> Tuple[MetricSummary, MetricSummary]:
    """
    Evaluates in-memory TimeWindowCorrelator false merge and false split rates.
    False Merge: multiple independent incidents merged into one (< true count).
    False Split: a single causal incident split into multiple (> true count).
    """
    correlator = TimeWindowCorrelator()
    merge_indicators = []
    split_indicators = []
    merge_fams = defaultdict(list)
    split_fams = defaultdict(list)
    multi_incident_cases = 0
    single_incident_cases = 0

    valid_sources = {'cloudwatch', 'cloudtrail', 'config', 'application', 'synthetic', 'kubernetes'}
    for c in cases:
        true_inc_count = 2 if "disjoint-failures" in c.tags else 1
        cloud_events = []
        for ed in c.events:
            dt = datetime.fromisoformat(ed.event_time)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            src = ed.source.lower() if ed.source.lower() in valid_sources else 'application'
            cloud_events.append(CloudEvent(
                event_id=ed.event_id or f"evt_{len(cloud_events)}",
                timestamp=dt,
                source=src,
                event_type=f"{ed.metric.lower()}_anomaly",
                service=ed.service,
                resource_id=f"res_{ed.service.lower()}",
                metric=ed.metric,
                value=ed.value,
                severity=Severity(ed.severity) if ed.severity in ("LOW", "MEDIUM", "HIGH", "CRITICAL") else Severity.HIGH
            ))

        cg = CausalGraphEngine.from_topology(c.topology) if use_case_topology else None
        pred_incidents = correlator.correlate(cloud_events, graph_engine=cg)
        pred_inc_count = len(pred_incidents)

        if true_inc_count > 1:
            multi_incident_cases += 1
            hit = 1.0 if pred_inc_count < true_inc_count else 0.0
            merge_indicators.append(hit)
            merge_fams[c.family_id].append(hit)
        else:
            single_incident_cases += 1
            hit = 1.0 if pred_inc_count > true_inc_count else 0.0
            split_indicators.append(hit)
            split_fams[c.family_id].append(hit)

    # Merge rate
    if multi_incident_cases > 0:
        m_rate = sum(merge_indicators) / multi_incident_cases
        ci_m_low, ci_m_high = bootstrap_ci_family_clustered(merge_fams)
        m_summary = MetricSummary(
            metric_name="False_Merge_Rate",
            value=round(m_rate * 100.0, 2),
            ci_lower=round(ci_m_low * 100.0, 2),
            ci_upper=round(ci_m_high * 100.0, 2),
            sample_size=multi_incident_cases,
            count_k=int(sum(merge_indicators)),
            flag_low_sample=(multi_incident_cases < 5)
        )
    else:
        m_summary = MetricSummary(metric_name="False_Merge_Rate", value=0.0, sample_size=0, count_k=0, is_na=True, flag_low_sample=True)

    # Split rate
    if single_incident_cases > 0:
        s_rate = sum(split_indicators) / single_incident_cases
        ci_s_low, ci_s_high = bootstrap_ci_family_clustered(split_fams)
        s_summary = MetricSummary(
            metric_name="False_Split_Rate",
            value=round(s_rate * 100.0, 2),
            ci_lower=round(ci_s_low * 100.0, 2),
            ci_upper=round(ci_s_high * 100.0, 2),
            sample_size=single_incident_cases,
            count_k=int(sum(split_indicators)),
            flag_low_sample=(single_incident_cases < 5)
        )
    else:
        s_summary = MetricSummary(metric_name="False_Split_Rate", value=0.0, sample_size=0, count_k=0, is_na=True, flag_low_sample=True)

    return m_summary, s_summary
