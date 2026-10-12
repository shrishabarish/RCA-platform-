import sys
import json
import csv
import time
import threading
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
from evaluation.baselines.controls import ScoreThresholdGateBaseline
from evaluation.baselines.rca_v2_adapter import RCAv2Baseline
from evaluation.baselines.rca_v3_adapter import RCAv3Baseline
from rca_v2 import RCAv2Config
from rca_v3 import RCAv3Config
from evaluation.metrics.engine import bootstrap_ci_family_clustered, format_metric


def run_item1_check_c_audit():
    print("=" * 80)
    print("STEP 1.1: CHECK C AUDIT AT STAGE3-FROZEN")
    print("=" * 80)

    # We evaluate dev cases using the pre-fix verifier logic
    dev_dir = PROJECT_ROOT / "evaluation" / "cases" / "generated" / "dev"
    dev_cases = [EvaluationCase.model_validate_json(f.read_text(encoding="utf-8")) for f in sorted(dev_dir.glob("*.json"))]
    single_root_dev = [c for c in dev_cases if c.ground_truth.expected_verdict == "DIAGNOSE" and len(c.ground_truth.true_root_causes) == 1]

    # Pre-fix verifier logic reconstruction
    from rca_engine.causal_graph import CausalGraphEngine
    from rca_v3.causal_graph import CausalGraphV3
    from rca_v3.candidate_scorer import CandidateScorerV3
    from rca_v3.symptom_scoring import SymptomScorerV3
    from rca_v3.graph_candidates import GraphCandidateGeneratorV3

    config_prefix = RCAv3Config()

    competing_cases = []
    relatedness_results = []
    case_details = []

    for c in single_root_dev:
        cg_engine = CausalGraphEngine.from_topology(c.topology)
        graph_v3 = CausalGraphV3(cg_engine)
        cand_gen = GraphCandidateGeneratorV3(graph_v3, config_prefix)
        # In pre-fix, candidates were observed + unobserved without parsimony
        cand_services = cand_gen.generate_candidates(c.events, c.topology.nodes)
        
        # Scorer
        cand_scorer = CandidateScorerV3(graph_v3, config_prefix)
        ranked = cand_scorer.score_candidates(cand_services, c.events, c.topology.nodes)
        
        sym_scorer = SymptomScorerV3(graph_v3, config_prefix)
        evaluated = sym_scorer.score_symptom_explanation(ranked, c.events, c.topology.nodes)

        if len(evaluated) >= 2:
            top_cand = evaluated[0]
            c2 = evaluated[1]
            top_svc = top_cand.service
            c2_svc = c2.service

            # Pre-fix Check C condition
            are_rel = graph_v3.are_services_related(top_svc, c2_svc, enable_directed=config_prefix.enable_directed_relatedness)
            relatedness_results.append(are_rel)

            top_explains_c2 = graph_v3.is_ancestor_cause(top_svc, c2_svc)
            c2_explains_top = graph_v3.is_ancestor_cause(c2_svc, top_svc)

            try:
                t1 = datetime.fromisoformat(top_cand.earliest_event_time)
                t2 = datetime.fromisoformat(c2.earliest_event_time)
                delta_onset = abs((t1 - t2).total_seconds())
            except Exception:
                delta_onset = 0.0

            fired = False
            if not are_rel:
                if (not top_explains_c2) and (not c2_explains_top) and delta_onset <= config_prefix.clock_skew_tolerance_sec:
                    if c2.ranking_score >= 65.0 or c2.combined_score >= 60.0:
                        fired = True

            if fired:
                competing_cases.append(c.case_id)
                case_details.append({
                    "case_id": c.case_id,
                    "top_svc": top_svc,
                    "c2_svc": c2_svc,
                    "are_services_related": are_rel,
                    "top_explains_c2": top_explains_c2,
                    "c2_explains_top": c2_explains_top,
                    "c2_ranking_score": c2.ranking_score,
                    "c2_combined_score": c2.combined_score,
                    "delta_onset": delta_onset,
                    "base_path": (
                        nx.shortest_path(cg_engine.graph, top_svc, c2_svc) if nx.has_path(cg_engine.graph, top_svc, c2_svc)
                        else (nx.shortest_path(cg_engine.graph, c2_svc, top_svc) if nx.has_path(cg_engine.graph, c2_svc, top_svc) else None)
                    ),
                    "rev_path": (
                        nx.shortest_path(graph_v3.reversed_graph, top_svc, c2_svc) if nx.has_path(graph_v3.reversed_graph, top_svc, c2_svc)
                        else (nx.shortest_path(graph_v3.reversed_graph, c2_svc, top_svc) if nx.has_path(graph_v3.reversed_graph, c2_svc, top_svc) else None)
                    ),
                    "common_callers": list(set(cg_engine.graph.predecessors(top_svc)) & set(cg_engine.graph.predecessors(c2_svc))) if (top_svc in cg_engine.graph and c2_svc in cg_engine.graph) else []
                })

    print(f"Total single-root dev cases examined: {len(single_root_dev)}")
    print(f"Cases where COMPETING_DISJOINT_ROOTS fired: {len(competing_cases)} cases:")
    print(competing_cases)

    print("\nDetailed breakdown for 5 sample cases where Check C fired:")
    for d in case_details[:5]:
        print(f"\nCase ID: {d['case_id']}")
        print(f"  c1: {d['top_svc']}, c2: {d['c2_svc']}")
        print(f"  are_services_related: {d['are_services_related']}")
        print(f"  c2 ranking_score: {d['c2_ranking_score']:.1f}, combined_score: {d['c2_combined_score']:.1f}")
        print(f"  delta_onset: {d['delta_onset']:.1f}s")
        print(f"  base_path: {d['base_path']}")
        print(f"  rev_path: {d['rev_path']}")
        print(f"  common_callers: {d['common_callers']}")

    # Histogram of are_services_related across all top-2 pairs in single-root dev
    print("\nHistogram of are_services_related across all top-2 candidate pairs in single-root DEV:")
    counts = Counter(relatedness_results)
    for k, v in counts.items():
        print(f"  are_services_related == {k}: {v} cases ({v/len(relatedness_results)*100:.1f}%)")


if __name__ == "__main__":
    run_item1_check_c_audit()
