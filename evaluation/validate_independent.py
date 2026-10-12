import sys
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rca_v3.causal_graph import CausalGraphV3


def parse_iso(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def validate_case(case_data: Dict[str, Any]) -> List[str]:
    errors = []
    cid = case_data.get("case_id", "UNKNOWN")
    topo = case_data.get("topology", {})
    nodes = topo.get("nodes", [])
    node_ids = {n["id"] for n in nodes}
    node_labels = {n.get("label", "") for n in nodes if n.get("label")}
    events = case_data.get("events", [])
    gt = case_data.get("ground_truth", {})
    true_roots = gt.get("true_root_causes", [])
    perturbations = case_data.get("perturbations", [])
    category = perturbations[0] if perturbations else "unknown"
    outcomes = gt.get("acceptable_outcomes", [])
    expected_verdict = gt.get("expected_verdict", "DIAGNOSE")

    # 1. Validation of acceptable_outcomes
    if not outcomes:
        errors.append(f"Missing ground_truth.acceptable_outcomes")
    else:
        for idx, out in enumerate(outcomes):
            if "verdict" not in out or out["verdict"] not in ("DIAGNOSE", "ABSTAIN"):
                errors.append(f"Outcome #{idx} invalid verdict: {out.get('verdict')}")
            if "roots" not in out or not isinstance(out["roots"], list):
                errors.append(f"Outcome #{idx} roots must be a list: {out.get('roots')}")
            if "max_level" not in out or out["max_level"] not in ("CONFIRMED", "PROBABLE", "ABSTAIN"):
                errors.append(f"Outcome #{idx} invalid max_level: {out.get('max_level')}")

    # 2. Check: No metric name contains any node id or label
    for e in events:
        metric = e.get("metric", "").lower()
        for nid in node_ids:
            if len(nid) >= 3 and nid.lower() in metric:
                errors.append(f"Metric '{e.get('metric')}' contains node id '{nid}'")
        for lbl in node_labels:
            if len(lbl) >= 3 and lbl.lower() in metric:
                errors.append(f"Metric '{e.get('metric')}' contains node label '{lbl}'")

    # Helper: Anomalous events
    anom_events = [
        e for e in events
        if str(e.get("severity", "")).upper() in ("CRITICAL", "HIGH", "MEDIUM")
        and str(e.get("metric", "")).lower() != "heartbeat"
    ]

    # Incident window calculation
    if events:
        min_t = min(parse_iso(e["event_time"]) for e in events)
        max_t = max(parse_iso(e["event_time"]) for e in events)
    else:
        min_t = datetime.min.replace(tzinfo=timezone.utc)
        max_t = datetime.max.replace(tzinfo=timezone.utc)

    # 3. Category specific assertions
    if category == "root_not_earliest":
        # Root must have anomalous events
        root_anom = [e for e in anom_events if e["service"] in true_roots]
        if not root_anom:
            errors.append("root_not_earliest: true root has no anomalous events")
        else:
            t_root_first = min(parse_iso(e["event_time"]) for e in root_anom)
            non_root_anom = [e for e in anom_events if e["service"] not in true_roots]
            if not non_root_anom:
                errors.append("root_not_earliest: no non-root anomalous events found")
            else:
                t_non_root_first = min(parse_iso(e["event_time"]) for e in non_root_anom)
                delta_sec = (t_root_first - t_non_root_first).total_seconds()
                if delta_sec < 10.0:
                    errors.append(
                        f"root_not_earliest: root onset ({t_root_first.isoformat()}) is not at least 10s later "
                        f"than non-root onset ({t_non_root_first.isoformat()}); delta={delta_sec:.1f}s"
                    )

    elif category == "silent_root":
        # True root has ZERO events
        for r in true_roots:
            r_evs = [e for e in events if e["service"] == r]
            if len(r_evs) > 0:
                errors.append(f"silent_root: true root '{r}' has {len(r_evs)} events (must be zero)")

        # Causal graph for dependents
        class DummyTopo:
            def __init__(self, n, e):
                self.nodes = n
                self.edges = e
        cg = CausalGraphV3.from_topology(DummyTopo(nodes, topo.get("edges", [])))
        anom_deps = set()
        for r in true_roots:
            dependents = cg.get_all_downstream_dependents(r)
            r_anom_deps = {e["service"] for e in anom_events if e["service"] in dependents}
            anom_deps.update(r_anom_deps)
            if len(r_anom_deps) < 2:
                errors.append(f"silent_root: root '{r}' has only {len(r_anom_deps)} anomalous dependents (needs >= 2)")

        # Competing candidates: alternative dependencies that the callers depend on
        # Each competing candidate must have at least 2 healthy heartbeats inside window
        heartbeat_events = [
            e for e in events
            if (str(e.get("metric", "")).lower() == "heartbeat" or str(e.get("severity", "")).upper() == "LOW")
            and min_t <= parse_iso(e["event_time"]) <= max_t
        ]
        hb_counts_by_svc = {}
        for e in heartbeat_events:
            hb_counts_by_svc[e["service"]] = hb_counts_by_svc.get(e["service"], 0) + 1

        competing_candidates = set()
        for caller in anom_deps:
            caller_deps = cg.get_all_upstream_dependencies(caller)
            for d in caller_deps:
                if d not in true_roots and d not in anom_deps:
                    competing_candidates.add(d)

        for c in competing_candidates:
            count = hb_counts_by_svc.get(c, 0)
            if count < 2:
                errors.append(f"silent_root: competing candidate '{c}' has {count} heartbeats inside window (needs >= 2)")

    elif category == "idle_healthy":
        # Contains a real failure
        if not anom_events:
            errors.append("idle_healthy: no anomalous events for real failure")
        # Idle healthy non-roots with heartbeats inside window
        all_anom_svcs = {e["service"] for e in anom_events}
        idle_svcs = [n["id"] for n in nodes if n["id"] not in all_anom_svcs]
        if not idle_svcs:
            errors.append("idle_healthy: no idle healthy services found in topology")
        else:
            hb_svcs = {
                e["service"] for e in events
                if (str(e.get("metric", "")).lower() == "heartbeat" or str(e.get("severity", "")).upper() == "LOW")
                and e["service"] in idle_svcs
                and min_t <= parse_iso(e["event_time"]) <= max_t
            }
            if not hb_svcs:
                errors.append("idle_healthy: no idle healthy non-roots have heartbeats inside window")

    # 4. Check: Every DIAGNOSE case: true root is either anomalous or a defined silent root
    # Note: Only applies to cases where expected_verdict is DIAGNOSE
    if expected_verdict == "DIAGNOSE" and category != "silent_root":
        for r in true_roots:
            r_anom = [e for e in anom_events if e["service"] == r]
            if not r_anom:
                errors.append(f"DIAGNOSE case: true root '{r}' has no anomalous events and is not silent_root")

    return errors


def validate_all_cases() -> Tuple[bool, List[Dict[str, Any]]]:
    indep_dir = PROJECT_ROOT / "evaluation" / "cases" / "independent"
    case_paths = sorted(indep_dir.glob("*.json"))

    results = []
    category_cases: Dict[str, List[Tuple[str, List[Tuple[str, str, str, str]]]]] = {}

    count_6node = 0
    count_12node = 0

    for cp in case_paths:
        data = json.loads(cp.read_text(encoding="utf-8"))
        cid = data["case_id"]
        topo = data.get("topology", {})
        node_count = len(topo.get("nodes", []))
        if node_count == 6:
            count_6node += 1
        elif node_count == 12:
            count_12node += 1

        category = data.get("perturbations", ["unknown"])[0]
        case_errors = validate_case(data)

        # Track event list for uniqueness check per category
        ev_summary = [
            (e.get("service", ""), e.get("metric", ""), e.get("severity", ""), e.get("event_time", ""))
            for e in data.get("events", [])
        ]
        if category not in category_cases:
            category_cases[category] = []
        category_cases[category].append((cid, ev_summary))

        results.append({
            "case_id": cid,
            "category": category,
            "nodes": node_count,
            "status": "PASS" if not case_errors else "FAIL",
            "errors": case_errors
        })

    # Uniqueness check: no two cases in a category have identical event lists
    for cat, cases_list in category_cases.items():
        for i in range(len(cases_list)):
            for j in range(i + 1, len(cases_list)):
                cid1, evs1 = cases_list[i]
                cid2, evs2 = cases_list[j]
                if evs1 == evs2:
                    for r in results:
                        if r["case_id"] in (cid1, cid2):
                            r["status"] = "FAIL"
                            r["errors"].append(f"Duplicate event list with {cid1 if r['case_id'] == cid2 else cid2}")

    # Total counts check
    all_passed = all(r["status"] == "PASS" for r in results)
    if count_6node != 15 or count_12node != 15 or len(results) != 30:
        all_passed = False

    return all_passed, results


if __name__ == "__main__":
    passed, res_list = validate_all_cases()
    print("=" * 105)
    print("INDEPENDENT CASES VALIDATION SUITE")
    print("=" * 105)
    print(f"{'Index':<5} {'Case ID':<46} {'Nodes':<7} {'Category':<22} {'Status':<6}")
    print("-" * 105)
    for idx, r in enumerate(res_list, 1):
        print(f"{idx:<5} {r['case_id']:<46} {r['nodes']:<7} {r['category']:<22} {r['status']:<6}")
        if r["errors"]:
            for err in r["errors"]:
                print(f"      -> ERROR: {err}")
    print("-" * 105)
    total_pass = sum(1 for r in res_list if r["status"] == "PASS")
    print(f"Total: {len(res_list)} cases (Passed: {total_pass}, Failed: {len(res_list) - total_pass})")
    print(f"Topology Balance: 6-node = {sum(1 for r in res_list if r['nodes'] == 6)}/15, 12-node = {sum(1 for r in res_list if r['nodes'] == 12)}/15")
    print(f"Overall Result: {'PASSED' if passed else 'FAILED'}")
    print("=" * 105)
    sys.exit(0 if passed else 1)
