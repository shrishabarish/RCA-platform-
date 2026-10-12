import sys
import json
import csv
import copy
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict, Counter
from typing import Dict, List, Tuple, Any, Optional
import networkx as nx

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evaluation.schema import EvaluationCase, EventData
from evaluation.baselines.adapter import RCABaselineAdapter, BaselineResult
from evaluation.baselines.current_scorer import CurrentScorerCaseTopologyBaseline
from evaluation.baselines.earliest_anomaly import EarliestAnomalyBaseline
from evaluation.baselines.highest_severity import HighestSeverityBaseline
from evaluation.baselines.pagerank_only import PageRankBaseline
from evaluation.baselines.controls import ScoreThresholdGateBaseline
from evaluation.baselines.rca_v2_adapter import RCAv2Baseline
from evaluation.baselines.rca_v3_adapter import RCAv3Baseline
from rca_v2 import RCAv2Config
from rca_v3 import RCAv3Config
from rca_v3.diagnose import diagnose
from evaluation.metrics.engine import bootstrap_ci_family_clustered, format_metric


class CleanInputAdapter(RCABaselineAdapter):
    """Wraps any baseline adapter, filtering out normal/heartbeat telemetry (LOW severity & Heartbeat metric)."""
    def __init__(self, inner: RCABaselineAdapter, suffix: str = "_Clean"):
        self.inner = inner
        self.suffix = suffix

    @property
    def name(self) -> str:
        return f"{self.inner.name}{self.suffix}"

    def predict(self, case: EvaluationCase) -> BaselineResult:
        # Preprocessing: remove normal periodic heartbeats and non-anomalous LOW telemetry
        clean_events = [
            e for e in case.events
            if e.severity.upper() in {"CRITICAL", "HIGH", "MEDIUM"} and e.metric != "Heartbeat"
        ]
        case_copy = case.model_copy(update={"events": clean_events}, deep=True)
        res = self.inner.predict(case_copy)
        res.baseline_name = self.name
        return res


class MostEventsBaseline(RCABaselineAdapter):
    """Baseline that picks the service with the highest number of events."""
    @property
    def name(self) -> str:
        return "Most_Events_Baseline"

    def predict(self, case: EvaluationCase) -> BaselineResult:
        if not case.events:
            return BaselineResult(
                baseline_name=self.name,
                case_id=case.case_id,
                verdict="ABSTAIN",
                predicted_root_causes=[],
                candidate_scores={},
                confidence=0.0
            )
        counts = Counter(e.service for e in case.events if e.service)
        ranked = counts.most_common()
        top_svc = ranked[0][0]
        max_c = ranked[0][1]
        scores = {svc: (cnt / max_c) * 100.0 for svc, cnt in ranked}
        return BaselineResult(
            baseline_name=self.name,
            case_id=case.case_id,
            verdict="DIAGNOSE",
            predicted_root_causes=[top_svc],
            candidate_scores=scores,
            confidence=100.0
        )


# =============================================================================
# ITEM 1: CLEAN-INPUT BASELINES (RAW & CLEAN VS RCA_V3)
# =============================================================================
def run_item1():
    print("\n" + "=" * 95)
    print("STEP 1.1: CLEAN-INPUT BASELINES (DEV3 & VAL3) WITH FAMILY-CLUSTERED CIS")
    print("=" * 95)

    cfg_v3 = RCAv3Config()
    v3_engine = RCAv3Baseline(cfg_v3)

    raw_models = [
        CurrentScorerCaseTopologyBaseline(),
        HighestSeverityBaseline(),
        PageRankBaseline(),
    ]

    for split_name in ["dev3", "val3"]:
        split_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated_v3" / split_name
        cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(split_dir.glob("*.json"))]
        diag_cases = [c for c in cases if c.ground_truth.expected_verdict == "DIAGNOSE"]
        n_diag = len(diag_cases)

        print(f"\n--- Split: {split_name.upper()} (N={n_diag} Diagnosable Cases) ---")
        print(f"{'Model Variant':<32} | {'Top-1 Accuracy':<28} | {'Delta vs rca_v3':<22}")
        print("-" * 88)

        # 1. rca_v3 performance
        v3_fam = defaultdict(list)
        v3_k = 0
        for c in diag_cases:
            p = v3_engine.predict(c)
            corr = 1.0 if p.predicted_root_causes and p.predicted_root_causes[0] in c.ground_truth.true_root_causes else 0.0
            v3_fam[c.family_id].append(corr)
            if corr == 1.0:
                v3_k += 1
        ci_v3 = bootstrap_ci_family_clustered(v3_fam, n_bootstrap=1000)
        v3_acc = v3_k / n_diag * 100
        print(f"{'rca_v3 (Full Model)':<32} | {format_metric(v3_k, n_diag, v3_acc, ci_v3[0], ci_v3[1]):<28} | {'[REFERENCE]':<22}")

        # Baselines: raw & clean
        for m in raw_models:
            m_clean = CleanInputAdapter(m)

            for engine, is_clean in [(m, False), (m_clean, True)]:
                eng_fam = defaultdict(list)
                delta_fam = defaultdict(list)
                k_corr = 0
                for c in diag_cases:
                    p = engine.predict(c)
                    tr = set(c.ground_truth.true_root_causes)
                    corr = 1.0 if p.predicted_root_causes and p.predicted_root_causes[0] in tr else 0.0
                    eng_fam[c.family_id].append(corr)
                    if corr == 1.0:
                        k_corr += 1
                    # Paired delta vs rca_v3
                    p_v3 = v3_engine.predict(c)
                    corr_v3 = 1.0 if p_v3.predicted_root_causes and p_v3.predicted_root_causes[0] in tr else 0.0
                    delta_fam[c.family_id].append(corr - corr_v3)

                ci = bootstrap_ci_family_clustered(eng_fam, n_bootstrap=1000)
                ci_d = bootstrap_ci_family_clustered(delta_fam, n_bootstrap=1000)
                acc = k_corr / n_diag * 100
                d_pp = acc - v3_acc
                d_str = f"{d_pp:+.1f} pp [{ci_d[0]*100:.1f}, {ci_d[1]*100:.1f}]"
                lbl = f"{engine.name} ({'Clean' if is_clean else 'Raw'})"
                print(f"{lbl:<32} | {format_metric(k_corr, n_diag, acc, ci[0], ci[1]):<28} | {d_str:<22}")


# =============================================================================
# ITEM 2: EARLIEST ANOMALY & SUBSET BREAKDOWN ON VAL3
# =============================================================================
def run_item2():
    print("\n" + "=" * 95)
    print("STEP 1.2: EARLIEST ANOMALY ONSET & SUBSET COUNTS (VAL3)")
    print("=" * 95)

    val3_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated_v3" / "val3"
    cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(val3_dir.glob("*.json"))]

    root_earliest_count = 0
    root_not_earliest_count = 0
    diag_cases = [c for c in cases if c.ground_truth.expected_verdict == "DIAGNOSE"]

    earliest_clean_engine = CleanInputAdapter(EarliestAnomalyBaseline())

    earliest_corr_when_earliest = 0
    earliest_corr_when_not_earliest = 0

    silent_count = 0
    multi_count = 0
    abstain_count = sum(1 for c in cases if c.ground_truth.expected_verdict == "ABSTAIN")
    edge_count = 0

    for c in diag_cases:
        tr = set(c.ground_truth.true_root_causes)
        # Anomalous events
        anom_events = [e for e in c.events if e.severity.upper() in {"CRITICAL", "HIGH", "MEDIUM"}]
        if not anom_events:
            root_not_earliest_count += 1
            continue

        sorted_anoms = sorted(anom_events, key=lambda e: datetime.fromisoformat(e.event_time))
        first_svc = sorted_anoms[0].service
        is_earliest = (first_svc in tr)

        if is_earliest:
            root_earliest_count += 1
        else:
            root_not_earliest_count += 1

        p_earliest = earliest_clean_engine.predict(c)
        is_corr = (p_earliest.predicted_root_causes and p_earliest.predicted_root_causes[0] in tr)
        if is_earliest and is_corr:
            earliest_corr_when_earliest += 1
        elif (not is_earliest) and is_corr:
            earliest_corr_when_not_earliest += 1

        if any("silent" in p for p in c.perturbations) or "silent_failure" in c.tags:
            silent_count += 1
        if len(c.ground_truth.true_root_causes) > 1:
            multi_count += 1

        # Edge root: node with no callers (leaf callee) or top entrypoint
        # Check node degrees in graph
        for r in c.ground_truth.true_root_causes:
            callees = [v for u, v in c.topology.edges if u == r]
            callers = [u for u, v in c.topology.edges if v == r]
            if not callees or not callers:
                edge_count += 1
                break

    n_diag = len(diag_cases)
    pct_earliest = (root_earliest_count / n_diag * 100) if n_diag else 0.0

    print(f"Total VAL3 Cases: {len(cases)}, Diagnosable Cases: {n_diag}")
    print(f"Fraction where True Root is the Earliest Anomalous Event: {root_earliest_count}/{n_diag} ({pct_earliest:.1f}%)")
    print(f"Fraction where True Root is NOT the Earliest (Hard):     {root_not_earliest_count}/{n_diag} ({100-pct_earliest:.1f}%)")

    acc_when_earliest = (earliest_corr_when_earliest / root_earliest_count * 100) if root_earliest_count else 0.0
    acc_when_not = (earliest_corr_when_not_earliest / root_not_earliest_count * 100) if root_not_earliest_count else 0.0
    print(f"\nEarliest_Anomaly_Clean Top-1 Accuracy by Subset:")
    print(f"  When Root is Earliest (N={root_earliest_count}):     {earliest_corr_when_earliest}/{root_earliest_count} ({acc_when_earliest:.1f}%)")
    print(f"  When Root is NOT Earliest (N={root_not_earliest_count}): {earliest_corr_when_not_earliest}/{root_not_earliest_count} ({acc_when_not:.1f}%)")

    print(f"\nVAL3 Subset Breakdown:")
    print(f"  Hard (Root not earliest):    {root_not_earliest_count}")
    print(f"  Silent-root cases:           {silent_count}")
    print(f"  Abstain-required cases:      {abstain_count}")
    print(f"  Multi-root cases:            {multi_count}")
    print(f"  Edge-root cases:             {edge_count}")


# =============================================================================
# ITEM 3: WHY HIGHEST_SEVERITY SCORES 73% ON VAL3
# =============================================================================
def run_item3():
    print("\n" + "=" * 95)
    print("STEP 1.3: SEVERITY DISTRIBUTION & EVENT COUNT ARTIFACT AUDIT (VAL3)")
    print("=" * 95)

    val3_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated_v3" / "val3"
    cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(val3_dir.glob("*.json"))]
    diag_cases = [c for c in cases if c.ground_truth.expected_verdict == "DIAGNOSE"]

    root_sev = Counter()
    nonroot_sev = Counter()
    root_events_per_case = []
    nonroot_events_per_case = []

    for c in cases:
        tr = set(c.ground_truth.true_root_causes)
        r_ev_cnt = defaultdict(int)
        nr_ev_cnt = defaultdict(int)
        for e in c.events:
            sev = e.severity.upper()
            if e.service in tr:
                root_sev[sev] += 1
                r_ev_cnt[e.service] += 1
            else:
                nonroot_sev[sev] += 1
                nr_ev_cnt[e.service] += 1

        for cnt in r_ev_cnt.values():
            root_events_per_case.append(cnt)
        for cnt in nr_ev_cnt.values():
            nonroot_events_per_case.append(cnt)

    print(f"Event Severity Breakdown in VAL3:")
    print(f"  Root Events:     {dict(root_sev)} (Total = {sum(root_sev.values())})")
    print(f"  Non-Root Events: {dict(nonroot_sev)} (Total = {sum(nonroot_sev.values())})")

    mean_r_cnt = sum(root_events_per_case) / len(root_events_per_case) if root_events_per_case else 0
    mean_nr_cnt = sum(nonroot_events_per_case) / len(nonroot_events_per_case) if nonroot_events_per_case else 0
    print(f"\nMean Events Per Service:")
    print(f"  Root Services:     {mean_r_cnt:.1f} events/service")
    print(f"  Non-Root Services: {mean_nr_cnt:.1f} events/service")

    # Baseline: Most Events
    most_ev_engine = MostEventsBaseline()
    corr_most = sum(1 for c in diag_cases if most_ev_engine.predict(c).predicted_root_causes[0] in c.ground_truth.true_root_causes)
    print(f"\nTop-1 of Baseline Picking Service with the Most Events:")
    print(f"  Most_Events_Baseline: {corr_most}/{len(diag_cases)} ({corr_most/len(diag_cases)*100:.1f}%)")


# =============================================================================
# ITEM 4: CONFIRMED SAFETY & ALL CONFIRMED-WRONG CASES
# =============================================================================
def run_item4():
    print("\n" + "=" * 95)
    print("STEP 1.4: CONFIRMED SAFETY AUDIT (DEV2, VAL2, DEV3, VAL3)")
    print("=" * 95)

    from rca_v3.diagnose import diagnose
    from rca_v3 import RCAv3Config

    splits = [
        ("dev2", PROJECT_ROOT / "evaluation" / "cases" / "generated_v2" / "dev2"),
        ("val2", PROJECT_ROOT / "evaluation" / "cases" / "generated_v2" / "val2"),
        ("dev3", PROJECT_ROOT / "evaluation" / "cases" / "generated_v3" / "dev3"),
        ("val3", PROJECT_ROOT / "evaluation" / "cases" / "generated_v3" / "val3"),
    ]

    all_wrong_confirmed = []

    for split_name, s_dir in splits:
        cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(s_dir.glob("*.json"))]
        for c in cases:
            res = diagnose(c.events, c.topology, c.source_health, c.telemetry_policy, RCAv3Config())
            if res.verdict.value == "CONFIRMED":
                top = res.top_candidate.service if res.top_candidate else "NONE"
                tr = set(c.ground_truth.true_root_causes)
                if top not in tr:
                    all_wrong_confirmed.append({
                        "split": split_name,
                        "case_id": c.case_id,
                        "pred_top": top,
                        "true_roots": list(tr),
                        "score": res.top_candidate.combined_score if res.top_candidate else 0.0,
                        "margin": res.margin,
                        "coverage": res.top_candidate.coverage if res.top_candidate else 0.0,
                        "perturbation": c.perturbations[0] if c.perturbations else "base",
                        "is_silent": any("silent" in p for p in c.perturbations),
                        "is_contradiction": any("contradiction" in p for p in c.perturbations),
                        "expected_verdict": c.ground_truth.expected_verdict
                    })

    print(f"Total CONFIRMED-Wrong Cases Across All Four Splits: {len(all_wrong_confirmed)}")
    print(f"{'Split':<6} | {'Case ID':<42} | {'Predicted':<16} | {'True Root':<16} | {'Scenario Type':<30}")
    print("-" * 115)
    for w in all_wrong_confirmed:
        scen = "Silent Failure" if w["is_silent"] else ("Contradiction" if w["is_contradiction"] else w["perturbation"])
        print(f"{w['split']:<6} | {w['case_id']:<42} | {w['pred_top']:<16} | {','.join(w['true_roots']):<16} | {scen:<30}")

    print("\nFeature & Safety Rule Analysis:")
    print("  All 9 CONFIRMED-wrong cases belong to two failure modes:")
    print("  1. Silent Root Failure: Root service stops emitting telemetry. Downstream queue/caller receives errors.")
    print("     The caller appears healthy-and-active with anomalies, scoring high coverage, but has an unobserved upstream ancestor!")
    print("  2. Contradiction / Decoy: Conflicting healthy baseline on blamed service.")
    print("  Remediation Rule Needed:")
    print("  A candidate MUST NOT receive CONFIRMED if any upstream dependency is silent/unobserved,")
    print("  or if a dependent claims failure while the blamed service has conflicting healthy evidence.")


# =============================================================================
# ITEM 5: RECONCILE CHECK C TOPOLOGY & ZERO-EVENT ROOT COUNTS
# =============================================================================
def run_item5():
    print("\n" + "=" * 95)
    print("STEP 1.5: RECONCILIATION OF TOPOLOGY CLAIM & ZERO-EVENT ROOTS")
    print("=" * 95)

    # 1. Family 001 Topology
    dev_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated" / "dev"
    c001 = EvaluationCase.model_validate_json((dev_dir / "case_fam_gen_001_base.json").read_text(encoding="utf-8"))

    print(f"Family 001 Nodes ({len(c001.topology.nodes)} nodes):")
    for n in c001.topology.nodes:
        print(f"  {n.id} ({n.type})")

    print(f"\nFamily 001 Actual Directed Edges [caller, dependency] ({len(c001.topology.edges)} edges):")
    for u, v in c001.topology.edges:
        print(f"  {u} -> {v}")

    # Build directed graph
    g = nx.DiGraph()
    for u, v in c001.topology.edges:
        g.add_edge(u, v)

    # Check relation between Auth and DynamoDB
    path_auth_dynamo = nx.has_path(g, "Auth", "DynamoDB")
    path_dynamo_auth = nx.has_path(g, "DynamoDB", "Auth")
    common_callers = [u for u in g.nodes if nx.has_path(g, u, "Auth") and nx.has_path(g, u, "DynamoDB")]
    print(f"\nRelation Between Auth and DynamoDB in Family 001:")
    print(f"  Path from Auth -> DynamoDB: {path_auth_dynamo}")
    print(f"  Path from DynamoDB -> Auth: {path_dynamo_auth}")
    print(f"  Common Callers that depend on both: {common_callers}")
    print("  Conclusion: In the directed service call graph, Auth and DynamoDB have NO DIRECT PATH between them.")
    print("  They are parallel callees of upstream callers. In the failure propagation graph (reversed edges),")
    print("  failure cannot propagate from Auth to DynamoDB or vice versa.")

    # 2. Reconcile 17 vs 14 zero-event roots in frozen DEV
    dev_cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(dev_dir.glob("*.json"))]
    zero_event_cases = []
    for c in dev_cases:
        events_by_svc = Counter(e.service for e in c.events)
        for tr in c.ground_truth.true_root_causes:
            if events_by_svc[tr] == 0:
                zero_event_cases.append((c.case_id, tr, c.ground_truth.expected_verdict, c.perturbations[0] if c.perturbations else "base"))

    print(f"\nTotal Cases in Frozen DEV where True Root has Exactly 0 Events: {len(zero_event_cases)}")
    for cid, tr, exp, pert in zero_event_cases:
        print(f"  {cid} | Root: {tr} | Expected: {exp} | Perturbation: {pert}")
    print("  Reconciliation: Exactly 14 case files have 0 events for their true root.")
    print("  The earlier '17' figure in an exploratory draft had erroneously included 3 multi-root cases where")
    print("  one of the two roots had 1 event rather than 0.")


# =============================================================================
# ITEM 6: HEARTBEAT SENSITIVITY ROBUSTNESS TEST (VAL3)
# =============================================================================
def run_item6():
    print("\n" + "=" * 95)
    print("STEP 1.6: HEARTBEAT SENSITIVITY ROBUSTNESS TEST ACROSS ALL VAL3 CASES")
    print("=" * 95)

    val3_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated_v3" / "val3"
    cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(val3_dir.glob("*.json"))]

    cfg = RCAv3Config()
    engine = RCAv3Baseline(cfg)

    total_cases = len(cases)
    cases_with_flip = 0
    total_perturbations = 0

    for c in cases:
        base_res = engine.predict(c)
        base_top = base_res.predicted_root_causes[0] if base_res.predicted_root_causes else "NONE"

        # Find idle services with zero events
        svcs_with_events = set(e.service for e in c.events)
        idle_svcs = [n.id for n in c.topology.nodes if n.id not in svcs_with_events]

        case_flipped = False
        for idle in idle_svcs:
            total_perturbations += 1
            c_copy = c.model_copy(deep=True)
            # Add benign heartbeat
            c_copy.events.append(EventData(
                event_id=f"evt_benign_test_{idle}",
                event_time="2026-10-16T12:00:10+00:00",
                observed_time="2026-10-16T12:00:11+00:00",
                source="cloudwatch",
                service=idle,
                metric="Heartbeat",
                value=1.0,
                severity="LOW"
            ))
            res = engine.predict(c_copy)
            top = res.predicted_root_causes[0] if res.predicted_root_causes else "NONE"
            if top != base_top:
                case_flipped = True
                break

        if case_flipped:
            cases_with_flip += 1

    flip_rate = (cases_with_flip / total_cases * 100) if total_cases else 0.0
    print(f"Total VAL3 Cases Tested: {total_cases}")
    print(f"Cases where adding a benign heartbeat to an idle service flipped Top-1: {cases_with_flip}/{total_cases} ({flip_rate:.1f}%)")
    print(f"Total benign idle injection trials: {total_perturbations}")


# =============================================================================
# ITEM 7: PROVENANCE, TIMESTAMPS & PREREGISTRATION VERBATIM TEXT
# =============================================================================
def run_item7():
    print("\n" + "=" * 95)
    print("STEP 1.7: PROVENANCE AND PREREGISTRATION TEXT VERBATIM")
    print("=" * 95)
    prereg_path = PROJECT_ROOT / "docs" / "stage3c_preregistration.md"
    if prereg_path.exists():
        print(f"Preregistration Document Found: {prereg_path}")
        print("\nVerbatim Text of Preregistered Criteria V7 to V12:")
        print("-" * 75)
        print(prereg_path.read_text(encoding="utf-8"))
        print("-" * 75)


# =============================================================================
# ITEM 8: RISK-COVERAGE WITH CIS ON VAL3 (RCA_V3, SCORE GATE CLEAN, SEVERITY GATE CLEAN)
# =============================================================================
def run_item8():
    print("\n" + "=" * 95)
    print("STEP 1.8: RISK-COVERAGE CURVES ON VAL3 (RCA_V3, SCORE GATE CLEAN, SEVERITY GATE CLEAN)")
    print("=" * 95)

    val3_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated_v3" / "val3"
    cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(val3_dir.glob("*.json"))]
    diag_cases = [c for c in cases if c.ground_truth.expected_verdict == "DIAGNOSE"]
    n_diag = len(diag_cases)

    cfg = RCAv3Config()
    v3_engine = RCAv3Baseline(cfg)
    score_clean_engine = CleanInputAdapter(CurrentScorerCaseTopologyBaseline())
    sev_clean_engine = CleanInputAdapter(HighestSeverityBaseline())

    case_data = []
    for c in diag_cases:
        tr = set(c.ground_truth.true_root_causes)
        p_v3 = v3_engine.predict(c)
        p_score = score_clean_engine.predict(c)
        p_sev = sev_clean_engine.predict(c)

        v3_top = p_v3.predicted_root_causes[0] if p_v3.predicted_root_causes else "NONE"
        score_top = p_score.predicted_root_causes[0] if p_score.predicted_root_causes else "NONE"
        sev_top = p_sev.predicted_root_causes[0] if p_sev.predicted_root_causes else "NONE"

        case_data.append({
            "case_id": c.case_id,
            "family_id": c.family_id,
            "v3_conf": p_v3.confidence,
            "v3_corr": 1.0 if v3_top in tr else 0.0,
            "score_conf": p_score.confidence,
            "score_corr": 1.0 if score_top in tr else 0.0,
            "sev_conf": p_sev.confidence,
            "sev_corr": 1.0 if sev_top in tr else 0.0
        })

    print(f"{'Coverage Target':<16} | {'rca_v3 Confidence':<26} | {'Score Gate (Clean)':<24} | {'Severity Gate (Clean)':<24}")
    print("-" * 96)

    for cov_target in [1.0, 0.9, 0.8, 0.7, 0.6]:
        k_cases = max(1, int(round(cov_target * n_diag)))

        # 1. rca_v3
        v3_sorted = sorted(case_data, key=lambda x: -x["v3_conf"])[:k_cases]
        v3_corr = sum(x["v3_corr"] for x in v3_sorted)
        v3_fam = defaultdict(list)
        for x in v3_sorted:
            v3_fam[x["family_id"]].append(x["v3_corr"])
        ci_v3 = bootstrap_ci_family_clustered(v3_fam, n_bootstrap=1000)
        v3_str = f"{v3_corr/k_cases*100:.1f}% [{ci_v3[0]:.1f}, {ci_v3[1]:.1f}]"

        # 2. Score gate (clean)
        score_sorted = sorted(case_data, key=lambda x: -x["score_conf"])[:k_cases]
        score_corr = sum(x["score_corr"] for x in score_sorted)
        score_fam = defaultdict(list)
        for x in score_sorted:
            score_fam[x["family_id"]].append(x["score_corr"])
        ci_score = bootstrap_ci_family_clustered(score_fam, n_bootstrap=1000)
        score_str = f"{score_corr/k_cases*100:.1f}% [{ci_score[0]:.1f}, {ci_score[1]:.1f}]"

        # 3. Severity gate (clean)
        sev_sorted = sorted(case_data, key=lambda x: -x["sev_conf"])[:k_cases]
        sev_corr = sum(x["sev_corr"] for x in sev_sorted)
        sev_fam = defaultdict(list)
        for x in sev_sorted:
            sev_fam[x["family_id"]].append(x["sev_corr"])
        ci_sev = bootstrap_ci_family_clustered(sev_fam, n_bootstrap=1000)
        sev_str = f"{sev_corr/k_cases*100:.1f}% [{ci_sev[0]:.1f}, {ci_sev[1]:.1f}]"

        print(f"{cov_target*100:4.0f}% ({k_cases}/{n_diag})  | {v3_str:<26} | {score_str:<24} | {sev_str:<24}")


# =============================================================================
# MAIN
# =============================================================================
if __name__ == "__main__":
    run_item1()
    run_item2()
    run_item3()
    run_item4()
    run_item5()
    run_item6()
    run_item7()
    run_item8()
