import sys
import json
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from event_processor.normalizer import load_synthetic_events
from rca_engine.correlation import TimeWindowCorrelator
from rca_engine.analyzer import analyze_incident
from rca_engine.remediation import generate_remediation_plan
from rca_engine.causal_graph import causal_graph_engine


def run_scenario(scenario_name: str = "cascade"):
    data_path = PROJECT_ROOT / "data" / "synthetic_events" / f"{scenario_name}.json"
    if not data_path.exists():
        print(f"Error: Scenario file {data_path} not found.")
        return

    print("\n" + "="*68)
    print(f"  AUTONOMOUS CLOUD RCA ENGINE (ENTERPRISE CORE) - SCENARIO '{scenario_name.upper()}'")
    print("="*68)

    print(f"\n[1/4] Ingesting & Pre-Filtering Cloud Telemetry from {data_path.name}...")
    events = load_synthetic_events(data_path)
    print(f"      -> Ingested {len(events)} telemetry events.")

    print(f"\n[2/4] Sliding-Window Temporal Correlation & Noise Suppression...")
    correlator = TimeWindowCorrelator(time_window_seconds=60, enable_noise_filter=True)
    incidents = correlator.correlate(events)
    print(f"      -> Grouped into {len(incidents)} active operational incident(s).")

    print(f"\n[3/4] Running Graph-Theoretic Causal Inference & Multi-Factor Scoring...")
    for idx, inc in enumerate(incidents, 1):
        result = analyze_incident(inc)
        remediation = generate_remediation_plan(inc.incident_id, result["probable_root_cause"])

        print("\n" + "-"*68)
        print(f"  OPERATIONAL INCIDENT #{idx} [ID: {result['incident_id']}]")
        print("-"*68)
        print(f"  * Severity Level       : {result['severity']}")
        print(f"  * Primary Root Cause   : {result['probable_root_cause']}")
        print(f"  * Calibrated Confidence: {result['confidence']}%")
        print(f"  * Downstream Casualties: {', '.join(result['affected_services']) if result['affected_services'] else 'None (Isolated)'}")
        print(f"  * Compound Incident    : {'YES (Multi-Root Failure)' if result['is_compound_incident'] else 'NO (Single Primary Root)'}")

        if result.get("causal_paths"):
            print("\n  * Topological Causal Propagation Paths:")
            for path in result["causal_paths"]:
                print(f"    -> {path}")

        print("\n  * Multi-Factor Ensemble Scoring Breakdown:")
        for factor, score in result["score_breakdown"].items():
            print(f"    - {factor.replace('_', ' ').title():<28} : {score} pts")

        print("\n  * Mathematical Reasoning Chain & Audit Trail:")
        for step in result.get("reasoning_chain", []):
            print(f"    {step}")

        print("\n  * Automated Self-Healing Runbook:")
        print(f"    - Recommended Action : {remediation.recommended_action}")
        print(f"    - Action Type        : {remediation.action_type}")
        print(f"    - Estimated MTTR     : {remediation.estimated_mttr_minutes} minutes")
        print(f"    - Execution Command  : {remediation.commands[0] if remediation.commands else 'N/A'}")
        print("-"*68)

    print(f"\n[4/4] Predictive What-If Failure Simulation...")
    what_if = causal_graph_engine.simulate_what_if_failure(result["probable_root_cause"])
    print(f"      -> If '{result['probable_root_cause']}' experiences complete crash:")
    print(f"         Total Blast Radius: {what_if['total_blast_radius_count']} services ({', '.join(what_if['all_impacted_services'])})")
    print(f"         Architecture Risk : {what_if['estimated_architecture_risk_score']} / 100")
    print("="*68 + "\n")


def main():
    scenario = sys.argv[1] if len(sys.argv) > 1 else "cascade"
    run_scenario(scenario)


if __name__ == "__main__":
    main()
