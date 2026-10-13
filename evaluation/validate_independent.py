import sys
import json
import math
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict
from typing import Dict, List, Any, Tuple, Set

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rca_v3.causal_graph import CausalGraphV3
from rca_v2.telemetry_policy import TelemetryPolicy
from evaluation.schema import EvaluationCase


def parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_forbidden_engine_metrics() -> Set[str]:
    """Extracts all metric names and aliases defined in the engine policy."""
    policy = TelemetryPolicy()
    forbidden = set()
    all_policies = list(policy.type_policies.values()) + list(policy.service_policies.values())
    for policies in all_policies:
        for p in policies:
            forbidden.add(p.metric.lower())
            for a in p.aliases:
                forbidden.add(a.lower())
    return forbidden


FORBIDDEN_POLICY_METRICS = get_forbidden_engine_metrics()

# Restricted keywords mapping: keywords specific to a particular node type
RESTRICTED_TYPE_KEYWORDS = {
    "database": ("storage", "database", "table", "disk", "sql", "transaction", "abort"),
    "queue": ("queue", "partition", "kafka", "topic"),
    "cache": ("cache", "eviction", "keylookup"),
    "auth": ("token", "identity", "credential", "signature"),
}

ALLOWED_TYPES_FOR_KEYWORDS = {
    "database": {"database", "storage"},
    "queue": {"queue"},
    "cache": {"cache"},
    "auth": {"auth"},
}


def validate_case(case_data: Dict[str, Any]) -> List[str]:
    errors = []
    cid = case_data.get("case_id", "UNKNOWN")
    topo = case_data.get("topology", {})
    nodes = topo.get("nodes", [])
    node_ids = {n["id"] for n in nodes}
    node_labels = {n.get("label", "") for n in nodes if n.get("label")}
    node_types = {n["id"]: n.get("type", "unknown") for n in nodes}
    events = case_data.get("events", [])
    gt = case_data.get("ground_truth", {})
    true_roots = gt.get("true_root_causes", [])
    perturbations = case_data.get("perturbations", [])
    category = perturbations[0] if perturbations else "unknown"
    outcomes = gt.get("acceptable_outcomes", [])
    max_level_by_root = gt.get("max_level_by_root", {})

    class DummyTopo:
        def __init__(self, n, e):
            self.nodes = n
            self.edges = e
    cg = CausalGraphV3.from_topology(DummyTopo(nodes, topo.get("edges", [])))

    # 1. Check absence of legacy fields
    if "expected_verdict" in case_data or "expected_verdict" in gt:
        errors.append("Legacy field 'expected_verdict' found in case JSON")
    if "acceptable_verdict_level" in case_data or "acceptable_verdict_level" in gt:
        errors.append("Legacy field 'acceptable_verdict_level' found in case JSON")

    # 2. Validation of acceptable_outcomes
    if not outcomes:
        errors.append("Missing ground_truth.acceptable_outcomes")
    else:
        for idx, out in enumerate(outcomes):
            if "verdict" not in out or out["verdict"] not in ("DIAGNOSE", "ABSTAIN"):
                errors.append(f"Outcome #{idx} invalid verdict: {out.get('verdict')}")
            if "roots" not in out or not isinstance(out["roots"], list):
                errors.append(f"Outcome #{idx} roots must be a list: {out.get('roots')}")
            if "max_level" not in out or out["max_level"] not in ("CONFIRMED", "PROBABLE", "ABSTAIN"):
                errors.append(f"Outcome #{idx} invalid max_level: {out.get('max_level')}")

    # Determine verdict purely from acceptable_outcomes
    is_abstain = any(o.get("verdict") == "ABSTAIN" and not o.get("roots") for o in outcomes)

    # Helper: Anomalous events
    anom_events = [
        e for e in events
        if str(e.get("severity", "")).upper() in ("CRITICAL", "HIGH", "MEDIUM")
        and str(e.get("metric", "")).lower() != "heartbeat"
    ]

    # Helper: Heartbeat events
    heartbeat_events = [
        e for e in events
        if str(e.get("metric", "")).lower() == "heartbeat"
    ]

    # Incident window calculation
    if events:
        min_t = min(parse_iso(e["event_time"]) for e in events)
        max_t = max(parse_iso(e["event_time"]) for e in events)
    else:
        min_t = datetime.min.replace(tzinfo=timezone.utc)
        max_t = datetime.max.replace(tzinfo=timezone.utc)

    # 3. Check: Metric naming rules & Neutral Vocabulary
    for e in events:
        metric = str(e.get("metric", ""))
        metric_lower = metric.lower()
        svc = e.get("service", "")
        stype = node_types.get(svc, "service")

        # a. No node id or label in metric name
        for nid in node_ids:
            if len(nid) >= 3 and nid.lower() in metric_lower:
                errors.append(f"Metric '{metric}' contains node id '{nid}'")
        for lbl in node_labels:
            if len(lbl) >= 3 and lbl.lower() in metric_lower:
                errors.append(f"Metric '{metric}' contains node label '{lbl}'")

        # b. Step 1f: No metric string appears in engine alias map or policy
        if metric_lower != "heartbeat":
            if metric_lower in FORBIDDEN_POLICY_METRICS:
                errors.append(f"Step 1f violation: Metric '{metric}' appears in engine policy or alias map")

        # c. Neutral vocabulary rule: No metric name may semantically reference a specific other node type
        if metric_lower != "heartbeat":
            for cat_name, kws in RESTRICTED_TYPE_KEYWORDS.items():
                if any(kw in metric_lower for kw in kws):
                    if stype not in ALLOWED_TYPES_FOR_KEYWORDS[cat_name]:
                        errors.append(
                            f"Neutral vocabulary violation: Metric '{metric}' on service '{svc}' (type '{stype}') "
                            f"semantically references specific node type '{cat_name}'"
                        )

    # 4. Step 1b: Every non-silent true root has at least 2 anomalous events;
    # every decoy service has between 1 and 3 anomalous events
    if category != "silent_root" and category != "no_anomaly" and category != "contradictory":
        for r in true_roots:
            r_anom = [e for e in anom_events if e["service"] == r]
            if len(r_anom) < 2:
                errors.append(f"Step 1b: True root '{r}' has {len(r_anom)} anomalous events (must be >= 2)")

        # Decoy services (non-root services with anomalous events)
        decoy_services = {e["service"] for e in anom_events if e["service"] not in true_roots}
        for d in decoy_services:
            d_count = sum(1 for e in anom_events if e["service"] == d)
            if not (1 <= d_count <= 3):
                errors.append(f"Step 1b: Decoy service '{d}' has {d_count} anomalous events (must be between 1 and 3)")

    # 5. Category-specific assertions
    if category == "root_not_earliest":
        root_anom = [e for e in anom_events if e["service"] in true_roots]
        non_root_anom = [e for e in anom_events if e["service"] not in true_roots]

        if not root_anom:
            errors.append("root_not_earliest: true root has no anomalous events")
        elif not non_root_anom:
            errors.append("root_not_earliest: no non-root anomalous events found")
        else:
            t_root_first = min(parse_iso(e["event_time"]) for e in root_anom)
            earliest_non_root_evt = min(non_root_anom, key=lambda e: parse_iso(e["event_time"]))
            t_non_root_first = parse_iso(earliest_non_root_evt["event_time"])
            decoy_service = earliest_non_root_evt["service"]

            delta_sec = (t_root_first - t_non_root_first).total_seconds()
            if delta_sec < 10.0:
                errors.append(
                    f"root_not_earliest: root onset ({t_root_first.isoformat()}) is not at least 10s later "
                    f"than decoy onset ({t_non_root_first.isoformat()}); delta={delta_sec:.1f}s"
                )

            # Check: Decoy service is NOT a causal descendant of the true root
            for r in true_roots:
                if cg.is_ancestor_cause(r, decoy_service):
                    errors.append(
                        f"Step 1a violation: Decoy service '{decoy_service}' is a causal descendant of true root '{r}' "
                        f"(downstream caller). Early decoy must be non-descendant or unrelated transient."
                    )

            # Decoy comparable event count
            decoy_anoms = [e for e in anom_events if e["service"] == decoy_service]
            for r in true_roots:
                r_anoms = [e for e in anom_events if e["service"] == r]
                if abs(len(decoy_anoms) - len(r_anoms)) > 1:
                    errors.append(
                        f"Decoy count violation: Decoy '{decoy_service}' has {len(decoy_anoms)} anomalous events, "
                        f"which is not within +/-1 of true root '{r}' count ({len(r_anoms)})"
                    )

    elif category == "silent_root":
        for r in true_roots:
            r_evs = [e for e in events if e["service"] == r]
            if len(r_evs) > 0:
                errors.append(f"silent_root: true root '{r}' has {len(r_evs)} events (must be zero)")

        anom_deps = set()
        dep_anom_events = []
        for r in true_roots:
            dependents = cg.get_all_downstream_dependents(r)
            r_anom_evs = [e for e in anom_events if e["service"] in dependents]
            dep_anom_events.extend(r_anom_evs)
            r_anom_deps = {e["service"] for e in r_anom_evs}
            anom_deps.update(r_anom_deps)
            if len(r_anom_deps) < 2:
                errors.append(f"silent_root: root '{r}' has only {len(r_anom_deps)} anomalous dependents (needs >= 2)")

        if dep_anom_events:
            t_dep_first = min(parse_iso(e["event_time"]) for e in dep_anom_events)
            t_dep_last = max(parse_iso(e["event_time"]) for e in dep_anom_events)

            competing_candidates = set()
            for caller in anom_deps:
                caller_deps = cg.get_all_upstream_dependencies(caller)
                for d in caller_deps:
                    if d not in true_roots and d not in anom_deps:
                        competing_candidates.add(d)

            for c in competing_candidates:
                c_hb_inside = [
                    e for e in heartbeat_events
                    if e["service"] == c and t_dep_first <= parse_iso(e["event_time"]) <= t_dep_last
                ]
                if len(c_hb_inside) < 2:
                    errors.append(
                        f"Step 1c: Competing candidate '{c}' has {len(c_hb_inside)} heartbeats inside dependent "
                        f"failure window [{t_dep_first.isoformat()} to {t_dep_last.isoformat()}] (needs >= 2)"
                    )

    elif category == "contradictory":
        if len(true_roots) != 0:
            errors.append(f"Step 1d: Contradiction case true_root_causes must be empty list [], got {true_roots}")
        if not max_level_by_root:
            errors.append("Step 1d: Contradiction case missing ground_truth.max_level_by_root")

        blaming_services = [r for o in outcomes if o["verdict"] == "DIAGNOSE" for r in o["roots"]]
        if not blaming_services:
            errors.append("Step 1e: Contradiction case missing blaming service in acceptable outcomes")
        else:
            for blamer in blaming_services:
                blamer_anoms = [e for e in anom_events if e["service"] == blamer]
                if len(blamer_anoms) < 2:
                    errors.append(
                        f"Step 1e: Blaming service '{blamer}' has {len(blamer_anoms)} anomalous events (needs >= 2)"
                    )

            healthy_metric_events = [
                e for e in events
                if str(e.get("severity", "")).upper() == "LOW"
                and str(e.get("metric", "")).lower() != "heartbeat"
            ]
            if len(healthy_metric_events) < 2:
                errors.append(
                    f"Step 1e: Blamed service has {len(healthy_metric_events)} healthy relevant metric events (needs >= 2)"
                )

    elif category == "misleading_blame":
        # 3 new contradiction/misleading-blame cases where blamed service is healthy
        # and a DIFFERENT service is the true root with its own anomalous events
        if not true_roots:
            errors.append("misleading_blame: true_root_causes must not be empty")

        # Find blaming services (services with max_level == ABSTAIN in max_level_by_root that have anomalies)
        blaming_svcs = [
            s for s, ml in max_level_by_root.items()
            if ml == "ABSTAIN" and s not in true_roots and any(e["service"] == s for e in anom_events)
        ]
        if not blaming_svcs:
            errors.append("misleading_blame: missing blaming service with anomalies and ABSTAIN max level")
        else:
            for blamer in blaming_svcs:
                blamer_anoms = [e for e in anom_events if e["service"] == blamer]
                if len(blamer_anoms) < 2:
                    errors.append(f"misleading_blame: blaming service '{blamer}' has < 2 anomalous events")

        # Check blamed service has healthy metric telemetry (LOW severity, metric != heartbeat)
        blamed_svcs = [
            s for s, ml in max_level_by_root.items()
            if ml == "ABSTAIN" and s not in blaming_svcs and s not in true_roots
        ]
        if not blamed_svcs:
            errors.append("misleading_blame: missing blamed service with ABSTAIN max level")
        else:
            for blamed in blamed_svcs:
                blamed_healthy_evs = [
                    e for e in events
                    if e["service"] == blamed
                    and str(e.get("severity", "")).upper() == "LOW"
                    and str(e.get("metric", "")).lower() != "heartbeat"
                ]
                if len(blamed_healthy_evs) < 2:
                    errors.append(f"misleading_blame: blamed service '{blamed}' has < 2 healthy metric events")

        # True root is distinct from blaming and blamed, and has >= 2 anomalous events
        for r in true_roots:
            if r in blaming_svcs or r in blamed_svcs:
                errors.append(f"misleading_blame: true root '{r}' cannot be the blaming or blamed service")
            r_anoms = [e for e in anom_events if e["service"] == r]
            if len(r_anoms) < 2:
                errors.append(f"misleading_blame: true root '{r}' has < 2 anomalous events")
            if max_level_by_root.get(r) not in ("CONFIRMED", "PROBABLE"):
                errors.append(f"misleading_blame: true root '{r}' must have CONFIRMED/PROBABLE max level")

    elif category == "idle_healthy":
        if not anom_events:
            errors.append("idle_healthy: no anomalous events for real failure")
        all_anom_svcs = {e["service"] for e in anom_events}
        idle_svcs = [n["id"] for n in nodes if n["id"] not in all_anom_svcs]
        if not idle_svcs:
            errors.append("idle_healthy: no idle healthy services found in topology")
        else:
            hb_svcs = {
                e["service"] for e in events
                if str(e.get("metric", "")).lower() == "heartbeat"
                and e["service"] in idle_svcs
                and min_t <= parse_iso(e["event_time"]) <= max_t
            }
            if not hb_svcs:
                errors.append("idle_healthy: no idle healthy non-roots have heartbeats inside window")

    # 6. Check: Every DIAGNOSE case has anomalous true root or is defined silent_root
    if not is_abstain and category != "silent_root":
        for r in true_roots:
            r_anom = [e for e in anom_events if e["service"] == r]
            if not r_anom:
                errors.append(f"DIAGNOSE case: true root '{r}' has no anomalous events and is not silent_root")

    # 7. Causal Order Rule: The true root's first anomalous event must precede the first symptom event of its dependents
    if true_roots and category != "silent_root":
        for r in true_roots:
            r_anoms = [e for e in anom_events if e["service"] == r]
            if r_anoms:
                t_root_first = min(parse_iso(e["event_time"]) for e in r_anoms)
                dep_svcs = [s for s in node_ids if s != r and cg.is_ancestor_cause(r, s)]
                for dep in dep_svcs:
                    dep_anoms = [e for e in anom_events if e["service"] == dep]
                    if dep_anoms:
                        t_dep_first = min(parse_iso(e["event_time"]) for e in dep_anoms)
                        if t_dep_first < t_root_first:
                            errors.append(
                                f"Causal order violation: Dependent '{dep}' has anomalous event at {t_dep_first.isoformat()} "
                                f"before true root '{r}' at {t_root_first.isoformat()} (negative symptom delay: "
                                f"{(t_dep_first - t_root_first).total_seconds()}s < 0)"
                            )

    # 8. Identifiability Rule: For root_not_earliest and misleading_blame, each decoy must either:
    # (a) not lie on any dependency path to symptom services in topology, or
    # (b) have healthy heartbeats/metrics AFTER its decoy events inside the symptom window.
    if category in ("root_not_earliest", "misleading_blame"):
        non_root_anoms = [e for e in anom_events if e["service"] not in true_roots]
        symptom_svcs = {s for s in {e["service"] for e in non_root_anoms} if any(cg.is_ancestor_cause(r, s) for r in true_roots)}
        decoy_svcs = {e["service"] for e in non_root_anoms if e["service"] not in symptom_svcs}
        for d in decoy_svcs:
            lies_on_dep_path = any(cg.is_ancestor_cause(d, s) for s in symptom_svcs)
            if lies_on_dep_path:
                t_d_last = max(parse_iso(e["event_time"]) for e in anom_events if e["service"] == d)
                healthy_after = [
                    e for e in events
                    if e["service"] == d
                    and (str(e.get("metric", "")).lower() == "heartbeat" or str(e.get("severity", "")).upper() == "LOW")
                    and parse_iso(e["event_time"]) > t_d_last
                ]
                if not healthy_after:
                    errors.append(
                        f"Identifiability violation: Decoy '{d}' lies on dependency path to symptom services {sorted(list(symptom_svcs))} "
                        f"but lacks healthy heartbeats/metrics after its decoy burst (last anomaly at {t_d_last.isoformat()})"
                    )

    # 9. Grounded Misleading Blame Rule: Blaming caller carries an explicit blaming signal
    if category in ("misleading_blame", "contradictory"):
        blaming_svcs = [
            s for s, ml in max_level_by_root.items()
            if ml in ("ABSTAIN", "PROBABLE") and s not in true_roots and any(e["service"] == s for e in anom_events)
        ]
        blamed_svcs = [
            s for s, ml in max_level_by_root.items()
            if ml == "ABSTAIN" and s not in blaming_svcs and s not in true_roots
        ]
        for blamer in blaming_svcs:
            blamer_evs = [e for e in anom_events if e["service"] == blamer]
            has_grounded_signal = any(
                e.get("target") in blamed_svcs or (e.get("dimensions") and e.get("dimensions").get("target") in blamed_svcs)
                for e in blamer_evs
            )
            if not has_grounded_signal:
                errors.append(
                    f"Grounded blame violation: Blaming service '{blamer}' does not carry explicit blaming signal "
                    f"('target' field referencing blamed service {blamed_svcs})"
                )

    # 11. Plausible Symptom Explanation Uniqueness Rule:
    # Compute, from case topology and event times, the set of candidates that could plausibly
    # explain each symptom (anomalous or silent ancestors with no recovery evidence before symptom).
    # Fail any DIAGNOSE case where more than one candidate remains plausible, unless the case lists
    # all of them as acceptable.
    diagnose_outcomes = [o for o in outcomes if o.get("verdict") == "DIAGNOSE"]
    if diagnose_outcomes and not is_abstain:
        acceptable_individual_roots = {r for o in diagnose_outcomes for r in o.get("roots", [])}
        symptom_events = [e for e in anom_events if e["service"] not in true_roots]

        for sym_evt in symptom_events:
            s_sym = sym_evt["service"]
            t_sym = parse_iso(sym_evt["event_time"])
            ancestor_candidates = [n["id"] for n in nodes if n["id"] != s_sym and cg.is_ancestor_cause(n["id"], s_sym)]

            plausible_for_sym = set()
            for cand in ancestor_candidates:
                cand_events = [e for e in events if e["service"] == cand]
                cand_anoms_prior = [
                    e for e in anom_events
                    if e["service"] == cand and parse_iso(e["event_time"]) < t_sym
                ]

                if not cand_events:
                    # Silent ancestor with no telemetry
                    plausible_for_sym.add(cand)
                elif cand_anoms_prior:
                    t_cand_last_anom = max(parse_iso(e["event_time"]) for e in cand_anoms_prior)
                    # Recovery evidence: non-heartbeat metric with LOW severity observed after last anomaly and before t_sym
                    recovery_metrics = [
                        e for e in cand_events
                        if parse_iso(e["event_time"]) > t_cand_last_anom
                        and parse_iso(e["event_time"]) < t_sym
                        and str(e.get("severity", "")).upper() == "LOW"
                        and str(e.get("metric", "")).lower() != "heartbeat"
                    ]
                    if not recovery_metrics:
                        plausible_for_sym.add(cand)
                else:
                    # Has telemetry, all healthy, no anomalies -> ruled out
                    pass

            if len(plausible_for_sym) > 1:
                unacceptable = plausible_for_sym - acceptable_individual_roots
                if unacceptable:
                    errors.append(
                        f"Plausibility ambiguity: Multiple candidates {sorted(list(plausible_for_sym))} "
                        f"plausibly explain symptom '{sym_evt.get('metric')}' on '{s_sym}' at {t_sym.isoformat()}, "
                        f"including unacceptable candidate(s) {sorted(list(unacceptable))} "
                        f"(acceptable roots: {sorted(list(acceptable_individual_roots))})"
                    )

    # 10. Inter-Event Gap Check against 60s Correlator Window
    if len(events) >= 2:
        sorted_ev_times = sorted(parse_iso(e["event_time"]) for e in events)
        for i in range(len(sorted_ev_times) - 1):
            gap = (sorted_ev_times[i+1] - sorted_ev_times[i]).total_seconds()
            if gap > 60.0:
                errors.append(
                    f"Correlator window violation: Inter-event gap between {sorted_ev_times[i].isoformat()} "
                    f"and {sorted_ev_times[i+1].isoformat()} is {gap:.1f}s (> 60s sliding window)"
                )

    return errors


def evaluate_fewest_anomalous_events(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    correct_count = 0
    total_diagnosable = 0
    predictions = []

    for c in cases:
        cid = c["case_id"]
        gt = c.get("ground_truth", {})
        true_roots = gt.get("true_root_causes", [])
        outcomes = gt.get("acceptable_outcomes", [])
        is_abstain = any(o.get("verdict") == "ABSTAIN" and not o.get("roots") for o in outcomes)
        category = c.get("perturbations", ["unknown"])[0]

        events = c.get("events", [])
        anom_events = [
            e for e in events
            if str(e.get("severity", "")).upper() in ("CRITICAL", "HIGH", "MEDIUM")
            and str(e.get("metric", "")).lower() != "heartbeat"
        ]

        counts: Dict[str, int] = {}
        for e in anom_events:
            s = e["service"]
            counts[s] = counts.get(s, 0) + 1

        if not counts:
            pred = "ABSTAIN"
            is_correct = is_abstain
        else:
            sorted_svcs = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]))
            pred = sorted_svcs[0][0]
            if is_abstain:
                is_correct = False
            else:
                is_correct = (pred in true_roots)

        if not is_abstain:
            total_diagnosable += 1
            if is_correct:
                correct_count += 1

        predictions.append({
            "case_id": cid,
            "category": category,
            "counts": counts,
            "predicted": pred,
            "true_roots": true_roots,
            "is_correct": is_correct
        })

    return {
        "correct_diagnosable": correct_count,
        "total_diagnosable": total_diagnosable,
        "accuracy_pct": (correct_count / total_diagnosable * 100.0) if total_diagnosable else 0.0,
        "predictions": predictions
    }


def evaluate_highest_severity(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    sev_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    correct_count = 0
    total_diagnosable = 0
    predictions = []

    for c in cases:
        cid = c["case_id"]
        gt = c.get("ground_truth", {})
        true_roots = gt.get("true_root_causes", [])
        outcomes = gt.get("acceptable_outcomes", [])
        is_abstain = any(o.get("verdict") == "ABSTAIN" and not o.get("roots") for o in outcomes)
        category = c.get("perturbations", ["unknown"])[0]

        events = c.get("events", [])
        anom_events = [
            e for e in events
            if str(e.get("severity", "")).upper() in ("CRITICAL", "HIGH", "MEDIUM")
            and str(e.get("metric", "")).lower() != "heartbeat"
        ]

        if not anom_events:
            pred = "ABSTAIN"
            is_correct = is_abstain
        else:
            max_sev = max(sev_rank.get(str(e.get("severity", "")).upper(), 0) for e in anom_events)
            sev_counts: Dict[str, int] = {}
            for e in anom_events:
                if sev_rank.get(str(e.get("severity", "")).upper(), 0) == max_sev:
                    s = e["service"]
                    sev_counts[s] = sev_counts.get(s, 0) + 1

            sorted_svcs = sorted(sev_counts.items(), key=lambda kv: (-kv[1], kv[0]))
            pred = sorted_svcs[0][0]
            if is_abstain:
                is_correct = False
            else:
                is_correct = (pred in true_roots)

        if not is_abstain:
            total_diagnosable += 1
            if is_correct:
                correct_count += 1

        predictions.append({
            "case_id": cid,
            "category": category,
            "predicted": pred,
            "true_roots": true_roots,
            "is_correct": is_correct
        })

    return {
        "correct_diagnosable": correct_count,
        "total_diagnosable": total_diagnosable,
        "accuracy_pct": (correct_count / total_diagnosable * 100.0) if total_diagnosable else 0.0,
        "predictions": predictions
    }


def evaluate_earliest_anomaly(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    correct_count = 0
    total_diagnosable = 0
    predictions = []

    for c in cases:
        cid = c["case_id"]
        gt = c.get("ground_truth", {})
        true_roots = gt.get("true_root_causes", [])
        outcomes = gt.get("acceptable_outcomes", [])
        is_abstain = any(o.get("verdict") == "ABSTAIN" and not o.get("roots") for o in outcomes)
        category = c.get("perturbations", ["unknown"])[0]

        events = c.get("events", [])
        anom_events = [
            e for e in events
            if str(e.get("severity", "")).upper() in ("CRITICAL", "HIGH", "MEDIUM")
            and str(e.get("metric", "")).lower() != "heartbeat"
        ]

        if not anom_events:
            pred = "ABSTAIN"
            is_correct = is_abstain
        else:
            sorted_evs = sorted(anom_events, key=lambda e: (parse_iso(e["event_time"]), e["service"]))
            pred = sorted_evs[0]["service"]
            if is_abstain:
                is_correct = False
            else:
                is_correct = (pred in true_roots)

        if not is_abstain:
            total_diagnosable += 1
            if is_correct:
                correct_count += 1

        predictions.append({
            "case_id": cid,
            "category": category,
            "predicted": pred,
            "true_roots": true_roots,
            "is_correct": is_correct
        })

    return {
        "correct_diagnosable": correct_count,
        "total_diagnosable": total_diagnosable,
        "accuracy_pct": (correct_count / total_diagnosable * 100.0) if total_diagnosable else 0.0,
        "predictions": predictions
    }


def evaluate_lowest_severity(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    sev_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    correct_count = 0
    total_diagnosable = 0
    predictions = []

    for c in cases:
        cid = c["case_id"]
        gt = c.get("ground_truth", {})
        true_roots = gt.get("true_root_causes", [])
        outcomes = gt.get("acceptable_outcomes", [])
        is_abstain = any(o.get("verdict") == "ABSTAIN" and not o.get("roots") for o in outcomes)
        category = c.get("perturbations", ["unknown"])[0]

        events = c.get("events", [])
        anom_events = [
            e for e in events
            if str(e.get("severity", "")).upper() in ("CRITICAL", "HIGH", "MEDIUM")
            and str(e.get("metric", "")).lower() != "heartbeat"
        ]

        if not anom_events:
            pred = "ABSTAIN"
            is_correct = is_abstain
        else:
            counts: Dict[str, int] = defaultdict(int)
            for e in anom_events:
                counts[e["service"]] += 1
            max_sevs: Dict[str, int] = {}
            for e in anom_events:
                s = e["service"]
                sr = sev_rank.get(str(e.get("severity", "")).upper(), 0)
                max_sevs[s] = max(max_sevs.get(s, 0), sr)
            sorted_svcs = sorted(max_sevs.items(), key=lambda kv: (kv[1], counts[kv[0]], kv[0]))
            pred = sorted_svcs[0][0]
            if is_abstain:
                is_correct = False
            else:
                is_correct = (pred in true_roots)

        if not is_abstain:
            total_diagnosable += 1
            if is_correct:
                correct_count += 1

        predictions.append({
            "case_id": cid,
            "category": category,
            "predicted": pred,
            "true_roots": true_roots,
            "is_correct": is_correct
        })

    return {
        "correct_diagnosable": correct_count,
        "total_diagnosable": total_diagnosable,
        "accuracy_pct": (correct_count / total_diagnosable * 100.0) if total_diagnosable else 0.0,
        "predictions": predictions
    }


def evaluate_second_highest_severity(cases: List[Dict[str, Any]]) -> Dict[str, Any]:
    sev_rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    correct_count = 0
    total_diagnosable = 0
    predictions = []

    for c in cases:
        cid = c["case_id"]
        gt = c.get("ground_truth", {})
        true_roots = gt.get("true_root_causes", [])
        outcomes = gt.get("acceptable_outcomes", [])
        is_abstain = any(o.get("verdict") == "ABSTAIN" and not o.get("roots") for o in outcomes)
        category = c.get("perturbations", ["unknown"])[0]

        events = c.get("events", [])
        anom_events = [
            e for e in events
            if str(e.get("severity", "")).upper() in ("CRITICAL", "HIGH", "MEDIUM")
            and str(e.get("metric", "")).lower() != "heartbeat"
        ]

        if not anom_events:
            pred = "ABSTAIN"
            is_correct = is_abstain
        else:
            counts: Dict[str, int] = defaultdict(int)
            for e in anom_events:
                counts[e["service"]] += 1
            max_sevs: Dict[str, int] = {}
            for e in anom_events:
                s = e["service"]
                sr = sev_rank.get(str(e.get("severity", "")).upper(), 0)
                max_sevs[s] = max(max_sevs.get(s, 0), sr)
            sorted_svcs = sorted(max_sevs.items(), key=lambda kv: (-kv[1], -counts[kv[0]], kv[0]))
            if len(sorted_svcs) > 1:
                pred = sorted_svcs[1][0]
            else:
                pred = sorted_svcs[0][0]
            if is_abstain:
                is_correct = False
            else:
                is_correct = (pred in true_roots)

        if not is_abstain:
            total_diagnosable += 1
            if is_correct:
                correct_count += 1

        predictions.append({
            "case_id": cid,
            "category": category,
            "predicted": pred,
            "true_roots": true_roots,
            "is_correct": is_correct
        })

    return {
        "correct_diagnosable": correct_count,
        "total_diagnosable": total_diagnosable,
        "accuracy_pct": (correct_count / total_diagnosable * 100.0) if total_diagnosable else 0.0,
        "predictions": predictions
    }


def compute_timing_spreads(cases: List[Dict[str, Any]]) -> Dict[str, Dict[str, Tuple[float, float, float, float]]]:
    """Computes min, max, mean, std for root onset, decoy onset, and symptom delay per category."""
    cat_stats = defaultdict(lambda: {"root_onset": [], "decoy_onset": [], "symptom_delay": []})
    for c in cases:
        cat = c.get("perturbations", ["unknown"])[0]
        gt = c.get("ground_truth", {})
        true_roots = set(gt.get("true_root_causes", []))
        events = c.get("events", [])
        if not events:
            continue
        t0 = min(parse_iso(e["event_time"]) for e in events)

        topo = c.get("topology", {})
        class DummyTopo:
            def __init__(self, n, e):
                self.nodes = n
                self.edges = e
        cg = CausalGraphV3.from_topology(DummyTopo(topo.get("nodes", []), topo.get("edges", [])))

        anom_events = [
            e for e in events
            if str(e.get("severity", "")).upper() in ("CRITICAL", "HIGH", "MEDIUM")
            and str(e.get("metric", "")).lower() != "heartbeat"
        ]
        root_evs = [e for e in anom_events if e["service"] in true_roots]
        non_root_evs = [e for e in anom_events if e["service"] not in true_roots]

        if root_evs:
            t_root_first = min(parse_iso(e["event_time"]) for e in root_evs)
            cat_stats[cat]["root_onset"].append((t_root_first - t0).total_seconds())

            # Dependent symptoms
            dep_svcs = {s for s in {e["service"] for e in non_root_evs} if any(cg.is_ancestor_cause(r, s) for r in true_roots)}
            dep_evs = [e for e in non_root_evs if e["service"] in dep_svcs]
            if dep_evs:
                t_dep_first = min(parse_iso(e["event_time"]) for e in dep_evs)
                cat_stats[cat]["symptom_delay"].append((t_dep_first - t_root_first).total_seconds())

        # Decoy onset for categories with decoys
        if non_root_evs and cat in ("root_not_earliest", "misleading_blame", "edge_root"):
            decoy_evs = [e for e in non_root_evs if not any(cg.is_ancestor_cause(r, e["service"]) for r in true_roots)]
            target_evs = decoy_evs if decoy_evs else non_root_evs
            t_decoy_first = min(parse_iso(e["event_time"]) for e in target_evs)
            cat_stats[cat]["decoy_onset"].append((t_decoy_first - t0).total_seconds())

    summary = {}
    for cat, metrics in cat_stats.items():
        summary[cat] = {}
        for m_name, vals in metrics.items():
            if vals:
                mean_v = sum(vals) / len(vals)
                var_v = sum((x - mean_v) ** 2 for x in vals) / len(vals)
                std_v = math.sqrt(var_v)
                summary[cat][m_name] = (min(vals), max(vals), mean_v, std_v)
    return summary


def validate_all_cases() -> Tuple[bool, List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    indep_dir = PROJECT_ROOT / "evaluation" / "cases" / "independent"
    case_paths = sorted(indep_dir.glob("*.json"))

    results = []
    category_offset_patterns: Dict[str, Dict[Tuple[float, ...], str]] = defaultdict(dict)
    raw_cases = []

    count_6node = 0
    count_12node = 0

    for cp in case_paths:
        data = json.loads(cp.read_text(encoding="utf-8"))
        raw_cases.append(data)
        cid = data["case_id"]
        topo = data.get("topology", {})
        node_count = len(topo.get("nodes", []))
        if node_count == 6:
            count_6node += 1
        elif node_count == 12:
            count_12node += 1

        category = data.get("perturbations", ["unknown"])[0]
        case_errors = validate_case(data)

        # Track relative offset pattern for intra-category uniqueness check
        events = data.get("events", [])
        if events:
            t0 = min(parse_iso(e["event_time"]) for e in events)
            offsets = tuple(
                round((parse_iso(e["event_time"]) - t0).total_seconds(), 2)
                for e in sorted(events, key=lambda x: parse_iso(x["event_time"]))
            )
            if offsets in category_offset_patterns[category]:
                prev_cid = category_offset_patterns[category][offsets]
                case_errors.append(
                    f"Step 3 violation: Case shares timing offset pattern with '{prev_cid}' in category '{category}': {offsets}"
                )
            else:
                category_offset_patterns[category][offsets] = cid

        results.append({
            "case_id": cid,
            "category": category,
            "nodes": node_count,
            "status": "PASS" if not case_errors else "FAIL",
            "errors": case_errors
        })

    # Total counts check: exactly 33 cases (17 on 6-node, 16 on 12-node)
    all_passed = all(r["status"] == "PASS" for r in results)
    if count_6node != 17 or count_12node != 16 or len(results) != 33:
        all_passed = False

    # Five static baselines
    fewest_results = evaluate_fewest_anomalous_events(raw_cases)
    highest_results = evaluate_highest_severity(raw_cases)
    lowest_results = evaluate_lowest_severity(raw_cases)
    second_highest_results = evaluate_second_highest_severity(raw_cases)
    earliest_results = evaluate_earliest_anomaly(raw_cases)
    spread_results = compute_timing_spreads(raw_cases)

    # Check baseline target (< 60% each)
    baselines_below_60 = (
        fewest_results["accuracy_pct"] < 60.0 and
        highest_results["accuracy_pct"] < 60.0 and
        lowest_results["accuracy_pct"] < 60.0 and
        second_highest_results["accuracy_pct"] < 60.0 and
        earliest_results["accuracy_pct"] < 60.0
    )
    if not baselines_below_60:
        all_passed = False

    return (
        all_passed, results,
        fewest_results, highest_results, lowest_results, second_highest_results, earliest_results,
        spread_results
    )


if __name__ == "__main__":
    passed, res_list, fewest_info, highest_info, lowest_info, second_highest_info, earliest_info, spread_info = validate_all_cases()
    print("=" * 110)
    print("EXTENDED INDEPENDENT CASES VALIDATION SUITE (Stage 3f Tightened - 33 Cases)")
    print("=" * 110)
    print(f"{'Index':<5} {'Case ID':<50} {'Nodes':<7} {'Category':<23} {'Status':<6}")
    print("-" * 110)
    for idx, r in enumerate(res_list, 1):
        print(f"{idx:<5} {r['case_id']:<50} {r['nodes']:<7} {r['category']:<23} {r['status']:<6}")
        if r["errors"]:
            for err in r["errors"]:
                print(f"      -> ERROR: {err}")
    print("-" * 110)
    total_pass = sum(1 for r in res_list if r["status"] == "PASS")
    print(f"Total: {len(res_list)} cases (Passed: {total_pass}, Failed: {len(res_list) - total_pass})")
    print(f"Topology Balance: 6-node = {sum(1 for r in res_list if r['nodes'] == 6)}/17, 12-node = {sum(1 for r in res_list if r['nodes'] == 12)}/16")
    print(f"Overall Validation Result: {'PASSED' if passed else 'FAILED'}")
    print("=" * 110)
    print("FIVE STATIC BASELINE ANALYSES ON 33 INDEPENDENT CASES (Target each strictly below 60%, No Engine Execution):")
    print(f"  1. Fewest Anomalous Events:       {fewest_info['correct_diagnosable']}/{fewest_info['total_diagnosable']} ({fewest_info['accuracy_pct']:.1f}%) [Target < 60%]")
    print(f"  2. Highest Severity:              {highest_info['correct_diagnosable']}/{highest_info['total_diagnosable']} ({highest_info['accuracy_pct']:.1f}%) [Target < 60%]")
    print(f"  3. Lowest Severity:               {lowest_info['correct_diagnosable']}/{lowest_info['total_diagnosable']} ({lowest_info['accuracy_pct']:.1f}%) [Target < 60%]")
    print(f"  4. Second-Highest Severity:       {second_highest_info['correct_diagnosable']}/{second_highest_info['total_diagnosable']} ({second_highest_info['accuracy_pct']:.1f}%) [Target < 60%]")
    print(f"  5. Earliest Anomaly:              {earliest_info['correct_diagnosable']}/{earliest_info['total_diagnosable']} ({earliest_info['accuracy_pct']:.1f}%) [Target < 60%]")
    print("=" * 110)
    print("TIMING OFFSETS SPREAD PER CATEGORY (Seeded Wide-Range Randomization):")
    for cat in sorted(spread_info.keys()):
        print(f"Category: {cat:<24}")
        for m_name in ["root_onset", "decoy_onset", "symptom_delay"]:
            if m_name in spread_info[cat]:
                mn, mx, mu, sd = spread_info[cat][m_name]
                print(f"  - {m_name:<15}: min={mn:5.1f}s, max={mx:5.1f}s, mean={mu:5.1f}s, std={sd:4.1f}s")
    print("=" * 110)
    sys.exit(0 if passed else 1)
