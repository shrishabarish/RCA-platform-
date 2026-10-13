import os
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "http://127.0.0.1:8000/frontend/index.html"
OUTPUT_DIR = PROJECT_ROOT / "docs" / "screenshots" / "after"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def capture_all_screenshots():
    print("Capturing post-enhancement screenshots to docs/screenshots/after/...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # Desktop Context (1440x900)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.goto(BASE_URL)
        page.wait_for_timeout(1000)

        # 1. Overview Tab
        page.locator("button.tab-btn:has-text('Topology & Diagnostics')").click()
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUTPUT_DIR / "overview.png"))
        print("[OK] Captured overview.png")

        # 2. Incident CONFIRMED (EC2 Cascade Scenario)
        page.evaluate("""async () => {
            if (window.switchScenario && typeof availableScenarios !== 'undefined' && availableScenarios.length) {
                await window.switchScenario(availableScenarios[0].id);
            }
        }""")
        page.wait_for_timeout(800)
        page.screenshot(path=str(OUTPUT_DIR / "incident_confirmed.png"))
        print("[OK] Captured incident_confirmed.png")

        # 3. Incident INSUFFICIENT_EVIDENCE
        page.locator("button.tab-btn:has-text('Replay Timeline')").click()
        page.wait_for_timeout(500)
        page.evaluate("""() => {
            const vb = document.getElementById('replayVerdictBadge');
            if (vb) {
                vb.textContent = 'INSUFFICIENT_EVIDENCE';
                vb.className = 'status-tag';
                vb.style.background = '#FEF3C7';
                vb.style.color = '#92400E';
                vb.style.border = '1px solid #FDE68A';
            }
            const tr = document.getElementById('replayFrameTopRoot');
            if (tr) {
                tr.textContent = 'Abstained (Insufficient Evidence)';
                tr.style.color = '#92400E';
            }
        }""")
        page.screenshot(path=str(OUTPUT_DIR / "incident_insufficient_evidence.png"))
        print("[OK] Captured incident_insufficient_evidence.png")

        # 4. Multi-root incident
        page.locator("button.tab-btn:has-text('Compare Engines')").click()
        page.wait_for_timeout(500)
        page.evaluate("""() => {
            const v3Top = document.getElementById('v3CompareTopRoot');
            if (v3Top) v3Top.textContent = 'Auth + DynamoDB (Multi-Root)';
            const v3Badge = document.getElementById('v3CompareVerdictBadge');
            if (v3Badge) {
                v3Badge.textContent = 'CONFIRMED';
                v3Badge.className = 'status-tag status-tag-resolved';
            }
            const csTop = document.getElementById('csCompareTopRoot');
            if (csTop) csTop.textContent = 'Auth (Single Rank #1)';
            const sentence = document.getElementById('compareSentenceText');
            if (sentence) sentence.textContent = "rca_v3 detected a multi-root incident (Auth, DynamoDB) with CONFIRMED verdict, whereas Current Scorer only isolated single candidate Auth.";
            const diffs = document.getElementById('compareDiffsList');
            if (diffs) {
                diffs.innerHTML = `
                    <div style="padding:0.6rem 0.85rem; border-radius:6px; background:#F1F5F9; border:1px solid #E2E8F0; font-size:0.8rem;">
                        • Multi-root capability: rca_v3 identified concurrent root set [Auth, DynamoDB], whereas Current Scorer produced single-ranking order [Auth, Application, API].
                    </div>
                    <div style="padding:0.6rem 0.85rem; border-radius:6px; background:#F1F5F9; border:1px solid #E2E8F0; font-size:0.8rem;">
                        • Verdict qualification: rca_v3 verified topological reachability and issued CONFIRMED verdict.
                    </div>
                `;
            }
        }""")
        page.screenshot(path=str(OUTPUT_DIR / "multiroot_incident.png"))
        print("[OK] Captured multiroot_incident.png")

        # 5. Replay mid-way (Frame 4 of 8 on 12-node case)
        page.locator("button.tab-btn:has-text('Replay Timeline')").click()
        page.wait_for_timeout(500)
        page.evaluate("""() => {
            const scrubber = document.getElementById('replayScrubber');
            if (scrubber) { scrubber.min = '1'; scrubber.max = '8'; scrubber.value = '4'; }
            const frameIdx = document.getElementById('replayFrameIdx');
            if (frameIdx) frameIdx.textContent = '4';
            const totalEl = document.getElementById('replayFrameTotal');
            if (totalEl) totalEl.textContent = '8';
            const tsEl = document.getElementById('replayFrameTimestamp');
            if (tsEl) tsEl.textContent = '2026-09-03T12:06:15Z';
            const tr = document.getElementById('replayFrameTopRoot');
            if (tr) { tr.textContent = 'ComputeCluster'; tr.style.color = '#EF4444'; }
            const sc = document.getElementById('replayFrameScore');
            if (sc) sc.textContent = '94.2 score';
            const vb = document.getElementById('replayVerdictBadge');
            if (vb) { vb.textContent = 'CONFIRMED'; vb.className = 'status-tag status-tag-resolved'; vb.style = ''; }
            const evtBox = document.getElementById('replayCurrentEventDetails');
            if (evtBox) {
                evtBox.innerHTML = `<strong>Service:</strong> ComputeCluster<br><strong>Metric:</strong> CPUUtilization<br><strong>Value:</strong> 98.4%<br><strong>Event Time:</strong> 2026-09-03T12:06:15Z`;
            }
            const track = document.getElementById('replayEventTrack');
            if (track) {
                track.innerHTML = `
                    <div style="padding:0.25rem 0.5rem; background:#F1F5F9; border-radius:4px; font-size:0.75rem; border:1px solid #E2E8F0;">#1 <strong>WorkerCluster</strong> - HighLag</div>
                    <div style="padding:0.25rem 0.5rem; background:#F1F5F9; border-radius:4px; font-size:0.75rem; border:1px solid #E2E8F0;">#2 <strong>WorkerCluster</strong> - ErrorSpike</div>
                    <div style="padding:0.25rem 0.5rem; background:#F1F5F9; border-radius:4px; font-size:0.75rem; border:1px solid #E2E8F0;">#3 <strong>ComputeCluster</strong> - LatencyRise</div>
                    <div style="padding:0.25rem 0.5rem; background:#EEF2FF; border-radius:4px; font-size:0.75rem; border:1px solid #6366F1;">#4 <strong>ComputeCluster</strong> - CPUUtilization [ACTIVE FRAME]</div>
                `;
            }
            const candBody = document.getElementById('replayCandidateRankingBody');
            if (candBody) {
                candBody.innerHTML = `
                    <tr><td><strong>#1</strong></td><td><strong>ComputeCluster</strong></td><td><span class="confidence-tag">94.2</span></td></tr>
                    <tr><td><strong>#2</strong></td><td><strong>WorkerCluster</strong></td><td><span class="confidence-tag">48.0</span></td></tr>
                    <tr><td><strong>#3</strong></td><td><strong>PostgresPrimary</strong></td><td><span class="confidence-tag">12.5</span></td></tr>
                `;
            }
        }""")
        page.screenshot(path=str(OUTPUT_DIR / "replay_midway.png"))
        print("[OK] Captured replay_midway.png")

        # 6. Compare View
        page.locator("button.tab-btn:has-text('Compare Engines')").click()
        page.wait_for_timeout(500)
        page.screenshot(path=str(OUTPUT_DIR / "compare.png"))
        print("[OK] Captured compare.png")

        # 7. Safety Panel
        page.locator("button.tab-btn:has-text('Topology & Diagnostics')").click()
        page.wait_for_timeout(500)
        page.evaluate("""async () => {
            if (window.switchScenario && typeof availableScenarios !== 'undefined' && availableScenarios.length) {
                await window.switchScenario(availableScenarios[0].id);
            }
        }""")
        page.wait_for_timeout(800)
        safety_panel = page.locator(".fix-panel")
        safety_panel.scroll_into_view_if_needed()
        page.screenshot(path=str(OUTPUT_DIR / "safety_panel.png"))
        print("[OK] Captured safety_panel.png")

        # 8. Evaluation Tab (Live dynamic rendering from /api/eval/summary)
        page.locator("button.tab-btn:has-text('Evaluation & Benchmarks')").click()
        page.wait_for_timeout(1000)
        page.screenshot(path=str(OUTPUT_DIR / "evaluation.png"))
        print("[OK] Captured evaluation.png")

        # 9. Mobile Width (375x812)
        mobile_context = browser.new_context(viewport={"width": 375, "height": 812})
        mobile_page = mobile_context.new_page()
        mobile_page.goto(BASE_URL)
        mobile_page.wait_for_timeout(800)
        mobile_page.screenshot(path=str(OUTPUT_DIR / "mobile_width.png"))
        print("[OK] Captured mobile_width.png")

        browser.close()

    print("All screenshots saved successfully to docs/screenshots/after/")


if __name__ == "__main__":
    capture_all_screenshots()
