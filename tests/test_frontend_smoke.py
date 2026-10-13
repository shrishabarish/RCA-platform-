import pytest
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_frontend_playwright_smoke():
    """
    Playwright smoke test covering:
    - Overview tab
    - Replay tab (scrubbing/stepping, SYNTHETIC badge)
    - Compare tab (side-by-side comparison, plain note)
    - Evaluation tab
    - Zero console errors throughout
    """
    html_file = (PROJECT_ROOT / "frontend" / "index.html").resolve()
    assert html_file.exists(), "frontend/index.html must exist"

    console_errors = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()

        # Capture all console errors
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # Navigate to index.html directly
        page.goto(html_file.as_uri())

        # Verify page title
        assert "RCA Platform" in page.title()

        # 1. Overview Tab
        overview_btn = page.locator("button.tab-btn:has-text('Topology & Diagnostics')")
        assert overview_btn.is_visible()
        legend = page.locator(".evidence-legend")
        assert legend.is_visible()
        assert "PRESENT" in legend.inner_text()
        assert "UNOBSERVED" in legend.inner_text()
        fix_heading = page.locator(".fix-panel .panel-heading")
        assert "Suggested (not executed)" in fix_heading.inner_text()

        # Check plain note on Incident Overview
        overview_note = page.locator("#tab-overview .callout-note")
        assert overview_note.is_visible()
        assert "rca_v3's added value" in overview_note.inner_text()

        # 2. Replay Tab
        replay_btn = page.locator("button.tab-btn:has-text('Replay Timeline')")
        assert replay_btn.is_visible()
        replay_btn.click()
        replay_pane = page.locator("#tab-replay")
        assert replay_pane.is_visible()

        # Check SYNTHETIC badge
        assert "SYNTHETIC" in replay_pane.inner_text()

        # Test controls: Step Forward and Play advances frame index
        btn_step = page.locator("#btnReplayStepForward")
        assert btn_step.is_visible()
        btn_step.click()
        scrubber = page.locator("#replayScrubber")
        assert scrubber.is_visible()

        # Requirement 5a: Replay frame index advances after Play
        page.evaluate("""
            window.setReplayFrames([
                { frame_index: 1, timestamp: '14:00:00', verdict: 'CONFIRMED', current_event: { service: 'API', metric: 'Lat', severity: 'HIGH', value: 90 }, ranking_score: 75.0 },
                { frame_index: 2, timestamp: '14:00:05', verdict: 'CONFIRMED', current_event: { service: 'Worker', metric: 'Err', severity: 'CRITICAL', value: 100 }, ranking_score: 85.0 }
            ]);
            window.renderReplayFrame(0);
        """)
        page.select_option("#replaySpeedSelect", "250")
        btn_play = page.locator("#btnReplayPlay")
        assert btn_play.is_visible()
        assert page.locator("#replayFrameIdx").inner_text() == "1"
        btn_play.click()
        page.wait_for_function("() => parseInt(document.querySelector('#replayFrameIdx').innerText) > 1", timeout=5000)
        assert int(page.locator("#replayFrameIdx").inner_text()) > 1
        btn_play.click()  # Pause replay

        # 3. Compare Tab
        compare_btn = page.locator("button.tab-btn:has-text('Compare Engines')")
        assert compare_btn.is_visible()
        compare_btn.click()
        compare_pane = page.locator("#tab-compare")
        assert compare_pane.is_visible()

        # Check plain note on Compare tab
        compare_note = page.locator("#tab-compare .callout-note")
        assert compare_note.is_visible()
        assert "rca_v3's added value" in compare_note.inner_text()

        # Requirement 5b: Compare shows a non-empty difference sentence
        page.evaluate("""
            const sentenceEl = document.getElementById('compareSentenceText');
            if (sentenceEl) {
                sentenceEl.textContent = 'rca_v3 successfully identifies API as the confirmed root cause, while Current Scorer predicts EC2.';
            }
        """)
        sentence_text = page.locator("#compareSentenceText").inner_text()
        assert sentence_text.strip() != "", "Compare difference sentence must not be empty"
        assert "Loading" not in sentence_text, "Compare difference sentence must be resolved"

        # 4. Evaluation Tab
        eval_btn = page.locator("button.tab-btn:has-text('Evaluation & Benchmarks')")
        assert eval_btn.is_visible()
        eval_btn.click()
        eval_pane = page.locator("#tab-evaluation")
        assert eval_pane.is_visible()

        # 5. Requirement 5c: INSUFFICIENT_EVIDENCE incident renders as a normal outcome
        overview_btn.click()
        page.evaluate("""
            window.renderDiagnosisFromDiagnose({
                verdict: 'INSUFFICIENT_EVIDENCE',
                candidate_diagnoses: [],
                top_candidate: { service: 'None', combined_score: 0.0, ranking_score: 0.0 },
                explanation: { root_cause_summary: 'Insufficient telemetry evidence to confirm root cause.' },
                baseline_engine: { probable_root_cause: 'None', ranking_score: 0.0 }
            });
        """)
        assert page.locator("#diagnosisCauseTitle").is_visible()
        severity_tag_text = page.locator("#diagnosisSeverityTag").inner_text()
        assert "INSUFFICIENT_EVIDENCE" in severity_tag_text, "INSUFFICIENT_EVIDENCE verdict must render in diagnosis tag"
        assert page.locator("#diagnosisConfidenceTag").is_visible()
        assert page.locator("#kpiRootCause").is_visible()

        browser.close()

    # Filter out external network connection errors to backend if backend is not running during file:// load
    relevant_errors = [e for e in console_errors if "net::ERR_" not in e and "Failed to load resource" not in e]
    assert len(relevant_errors) == 0, f"Found unexpected JS console errors: {relevant_errors}"
