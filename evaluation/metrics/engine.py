import math
import random
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel

from evaluation.schema import EvaluationCase
from evaluation.baselines.adapter import BaselineResult
from backend.app.models.event import CloudEvent, Severity
from rca_engine.correlation import TimeWindowCorrelator
from datetime import datetime, timezone


class MetricSummary(BaseModel):
    metric_name: str
    value: float
    ci_lower: Optional[float] = None
    ci_upper: Optional[float] = None
    sample_size: int
    is_na: bool = False
    flag_low_sample: bool = False  # Flagged if sample_size < 10


def compute_top_k_accuracy(predictions: List[Tuple[EvaluationCase, BaselineResult]], k: int = 1) -> MetricSummary:
    """Computes Top-K accuracy on single-root cases requiring DIAGNOSE."""
    single_root_cases = [
        (c, p) for c, p in predictions
        if len(c.ground_truth.true_root_causes) == 1 and c.ground_truth.expected_verdict == "DIAGNOSE"
    ]
    n = len(single_root_cases)
    if n == 0:
        return MetricSummary(metric_name=f"Top-{k}", value=0.0, sample_size=0, is_na=True, flag_low_sample=True)

    correct = 0
    for c, p in single_root_cases:
        true_root = c.ground_truth.true_root_causes[0]
        predicted_top_k = p.predicted_root_causes[:k]
        if true_root in predicted_top_k:
            correct += 1

    acc = correct / n
    ci_low, ci_high = bootstrap_ci([1.0 if c.ground_truth.true_root_causes[0] in p.predicted_root_causes[:k] else 0.0 for c, p in single_root_cases])
    return MetricSummary(
        metric_name=f"Top-{k}",
        value=round(acc * 100.0, 2),
        ci_lower=round(ci_low * 100.0, 2),
        ci_upper=round(ci_high * 100.0, 2),
        sample_size=n,
        flag_low_sample=(n < 10)
    )


def compute_multiroot_f1(predictions: List[Tuple[EvaluationCase, BaselineResult]]) -> MetricSummary:
    """Computes set-based multi-root Precision, Recall, and F1 across multi-root cases."""
    multi_cases = [
        (c, p) for c, p in predictions
        if len(c.ground_truth.true_root_causes) >= 2 and c.ground_truth.expected_verdict == "DIAGNOSE"
    ]
    n = len(multi_cases)
    if n == 0:
        return MetricSummary(metric_name="MultiRoot_F1", value=0.0, sample_size=0, is_na=True, flag_low_sample=True)

    f1_scores = []
    for c, p in multi_cases:
        true_set = set(c.ground_truth.true_root_causes)
        # Take top K predictions where K = len(true_set)
        pred_set = set(p.predicted_root_causes[:len(true_set)])
        overlap = len(true_set.intersection(pred_set))
        prec = overlap / len(pred_set) if pred_set else 0.0
        rec = overlap / len(true_set) if true_set else 1.0
        f1 = (2 * prec * rec) / (prec + rec) if (prec + rec) > 0 else 0.0
        f1_scores.append(f1)

    mean_f1 = sum(f1_scores) / n
    ci_low, ci_high = bootstrap_ci(f1_scores)
    return MetricSummary(
        metric_name="MultiRoot_F1",
        value=round(mean_f1 * 100.0, 2),
        ci_lower=round(ci_low * 100.0, 2),
        ci_upper=round(ci_high * 100.0, 2),
        sample_size=n,
        flag_low_sample=(n < 10)
    )


def compute_abstention_metrics(predictions: List[Tuple[EvaluationCase, BaselineResult]]) -> Tuple[MetricSummary, MetricSummary]:
    """
    Computes Abstention Precision and Recall:
    Abstention Precision = correct abstentions / all abstentions
    Abstention Recall = correct abstentions / all cases requiring abstention
    """
    total_cases = len(predictions)
    all_abstentions = sum(1 for _, p in predictions if p.verdict == "ABSTAIN")
    cases_requiring_abstention = sum(1 for c, _ in predictions if c.ground_truth.expected_verdict == "ABSTAIN")
    correct_abstentions = sum(1 for c, p in predictions if p.verdict == "ABSTAIN" and c.ground_truth.expected_verdict == "ABSTAIN")

    if all_abstentions == 0:
        prec_summary = MetricSummary(metric_name="Abstention_Precision", value=0.0, sample_size=0, is_na=True)
    else:
        prec = correct_abstentions / all_abstentions
        prec_summary = MetricSummary(metric_name="Abstention_Precision", value=round(prec * 100.0, 2), sample_size=all_abstentions, flag_low_sample=(all_abstentions < 10))

    if cases_requiring_abstention == 0:
        rec_summary = MetricSummary(metric_name="Abstention_Recall", value=100.0, sample_size=0, is_na=True)
    else:
        rec = correct_abstentions / cases_requiring_abstention
        rec_summary = MetricSummary(metric_name="Abstention_Recall", value=round(rec * 100.0, 2), sample_size=cases_requiring_abstention, flag_low_sample=(cases_requiring_abstention < 10))

    return prec_summary, rec_summary


def compute_confident_wrong_rate(
    predictions: List[Tuple[EvaluationCase, BaselineResult]],
    confidence_threshold: float = 75.0
) -> MetricSummary:
    """
    Confident-wrong rate = incorrect diagnoses with score >= threshold / all cases.
    """
    n = len(predictions)
    if n == 0:
        return MetricSummary(metric_name="Confident_Wrong_Rate", value=0.0, sample_size=0, is_na=True)

    indicators = []
    for c, p in predictions:
        is_wrong = False
        if c.ground_truth.expected_verdict == "ABSTAIN":
            if p.verdict != "ABSTAIN":
                is_wrong = True
        else:
            # Diagnose case: wrong if top predicted is not among true roots
            true_roots = c.ground_truth.true_root_causes
            top_pred = p.predicted_root_causes[0] if p.predicted_root_causes else None
            if top_pred not in true_roots:
                is_wrong = True

        if is_wrong and p.confidence >= confidence_threshold:
            indicators.append(1.0)
        else:
            indicators.append(0.0)

    rate = sum(indicators) / n
    ci_low, ci_high = bootstrap_ci(indicators)
    return MetricSummary(
        metric_name="Confident_Wrong_Rate",
        value=round(rate * 100.0, 2),
        ci_lower=round(ci_low * 100.0, 2),
        ci_upper=round(ci_high * 100.0, 2),
        sample_size=n,
        flag_low_sample=(n < 10)
    )


def compute_correlation_merge_split_rates(cases: List[EvaluationCase]) -> Tuple[MetricSummary, MetricSummary]:
    """
    Evaluates in-memory TimeWindowCorrelator false merge and false split rates.
    False Merge: multiple independent incidents merged into one (< true count).
    False Split: a single causal incident split into multiple (> true count).
    """
    correlator = TimeWindowCorrelator()
    merge_indicators = []
    split_indicators = []
    multi_incident_cases = 0
    single_incident_cases = 0

    for c in cases:
        # Determine ground truth incident count
        # Cases tagged 'disjoint-failures' have 2 ground truth incidents; all others have 1
        true_inc_count = 2 if "disjoint-failures" in c.tags else 1

        valid_sources = {'cloudwatch', 'cloudtrail', 'config', 'application', 'synthetic', 'kubernetes'}
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

        pred_incidents = correlator.correlate(cloud_events)
        pred_inc_count = len(pred_incidents)

        if true_inc_count > 1:
            multi_incident_cases += 1
            merge_indicators.append(1.0 if pred_inc_count < true_inc_count else 0.0)
        else:
            single_incident_cases += 1
            split_indicators.append(1.0 if pred_inc_count > true_inc_count else 0.0)

    # Merge rate
    if multi_incident_cases > 0:
        m_rate = sum(merge_indicators) / multi_incident_cases
        m_summary = MetricSummary(metric_name="False_Merge_Rate", value=round(m_rate * 100.0, 2), sample_size=multi_incident_cases, flag_low_sample=(multi_incident_cases < 10))
    else:
        m_summary = MetricSummary(metric_name="False_Merge_Rate", value=0.0, sample_size=0, is_na=True)

    # Split rate
    if single_incident_cases > 0:
        s_rate = sum(split_indicators) / single_incident_cases
        s_summary = MetricSummary(metric_name="False_Split_Rate", value=round(s_rate * 100.0, 2), sample_size=single_incident_cases, flag_low_sample=(single_incident_cases < 10))
    else:
        s_summary = MetricSummary(metric_name="False_Split_Rate", value=0.0, sample_size=0, is_na=True)

    return m_summary, s_summary


def bootstrap_ci(data: List[float], n_bootstrap: int = 1000, alpha: float = 0.05, seed: int = 42) -> Tuple[float, float]:
    """Computes empirical bootstrap percentile 95% confidence intervals."""
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
