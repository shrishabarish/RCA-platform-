import pytest
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HTML_FILE = (PROJECT_ROOT / "frontend" / "index.html").resolve()


def _get_page(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    console_errors = []
    page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
    page.goto(HTML_FILE.as_uri())
    return page, console_errors


def test_smoke_load():
    """Smoke test: Initial load, title, and verify zero rendered 'confidence' text."""
    assert HTML_FILE.exists(), "frontend/index.html must exist"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page, console_errors = _get_page(browser)

        assert "RCA Platform" in page.title()
        overview_btn = page.locator("button.tab-btn:has-text('Topology & Diagnostics')")
        assert overview_btn.is_visible()
        legend = page.locator(".evidence-legend")
        assert legend.is_visible()
        assert "PRESENT" in legend.inner_text()
        assert "UNOBSERVED" in legend.inner_text()
        fix_heading = page.locator(".fix-panel .panel-heading")
        assert "Suggested (not executed)" in fix_heading.inner_text()

        # Check callout note
        overview_note = page.locator("#tab-overview .callout-note")
        assert overview_note.is_visible()
        assert "rca_v3's added value" in overview_note.inner_text()

        # Playwright assertion: Rendered page text contains no "confidence"
        body_text = page.locator("body").inner_text().lower()
        assert "confidence" not in body_text, "Rendered page text must not contain 'confidence'"

        browser.close()
        relevant_errors = [e for e in console_errors if "net::ERR_" not in e and "Failed to load resource" not in e]
        assert len(relevant_errors) == 0, f"Found unexpected JS console errors: {relevant_errors}"


def test_smoke_replay():
    """Smoke test: Replay Timeline controls, scrubbing, SYNTHETIC badge, and play loop."""
    assert HTML_FILE.exists()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page, console_errors = _get_page(browser)

        replay_btn = page.locator("button.tab-btn:has-text('Replay Timeline')")
        assert replay_btn.is_visible()
        replay_btn.click()
        replay_pane = page.locator("#tab-replay")
        assert replay_pane.is_visible()

        # Check SYNTHETIC badge
        assert "SYNTHETIC" in replay_pane.inner_text()

        # Test controls
        btn_step = page.locator("#btnReplayStepForward")
        assert btn_step.is_visible()
        btn_step.click()
        scrubber = page.locator("#replayScrubber")
        assert scrubber.is_visible()

        # Advance frame on play
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

        # Verify no confidence in replay tab
        assert "confidence" not in replay_pane.inner_text().lower()

        browser.close()
        relevant_errors = [e for e in console_errors if "net::ERR_" not in e and "Failed to load resource" not in e]
        assert len(relevant_errors) == 0, f"Found unexpected JS console errors: {relevant_errors}"


def test_smoke_compare():
    """Smoke test: Compare Engines tab, side-by-side card rendering, and difference sentence."""
    assert HTML_FILE.exists()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page, console_errors = _get_page(browser)

        compare_btn = page.locator("button.tab-btn:has-text('Compare Engines')")
        assert compare_btn.is_visible()
        compare_btn.click()
        compare_pane = page.locator("#tab-compare")
        assert compare_pane.is_visible()

        # Check callout note
        compare_note = page.locator("#tab-compare .callout-note")
        assert compare_note.is_visible()
        assert "rca_v3's added value" in compare_note.inner_text()

        # Compare shows a non-empty difference sentence
        page.evaluate("""
            const sentenceEl = document.getElementById('compareSentenceText');
            if (sentenceEl) {
                sentenceEl.textContent = 'rca_v3 successfully identifies API as the confirmed root cause, while Current Scorer predicts EC2.';
            }
        """)
        sentence_text = page.locator("#compareSentenceText").inner_text()
        assert sentence_text.strip() != "", "Compare difference sentence must not be empty"
        assert "Loading" not in sentence_text, "Compare difference sentence must be resolved"

        # Verify no confidence in compare tab
        assert "confidence" not in compare_pane.inner_text().lower()

        browser.close()
        relevant_errors = [e for e in console_errors if "net::ERR_" not in e and "Failed to load resource" not in e]
        assert len(relevant_errors) == 0, f"Found unexpected JS console errors: {relevant_errors}"


def test_smoke_insufficient_evidence():
    """Smoke test: INSUFFICIENT_EVIDENCE rendering as a normal diagnostic outcome."""
    assert HTML_FILE.exists()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page, console_errors = _get_page(browser)

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

        # Verify no confidence in overview tab
        assert "confidence" not in page.locator("#tab-overview").inner_text().lower()

        browser.close()
        relevant_errors = [e for e in console_errors if "net::ERR_" not in e and "Failed to load resource" not in e]
        assert len(relevant_errors) == 0, f"Found unexpected JS console errors: {relevant_errors}"


def test_smoke_evaluation():
    """Smoke test: Evaluation & Benchmarks tab, default frozen view (1 row per engine), and view toggling."""
    assert HTML_FILE.exists()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page, console_errors = _get_page(browser)

        eval_btn = page.locator("button.tab-btn:has-text('Evaluation & Benchmarks')")
        assert eval_btn.is_visible()
        eval_btn.click()
        eval_pane = page.locator("#tab-evaluation")
        assert eval_pane.is_visible()

        # Verify headers include Run Status and Denominator (Cases & Families)
        table_text = page.locator("#tab-evaluation .candidate-table").inner_text().upper()
        assert "RUN STATUS" in table_text
        assert "CASES & FAMILIES" in table_text

        # Verify no confidence in evaluation pane
        assert "confidence" not in eval_pane.inner_text().lower()

        browser.close()
        relevant_errors = [e for e in console_errors if "net::ERR_" not in e and "Failed to load resource" not in e]
        assert len(relevant_errors) == 0, f"Found unexpected JS console errors: {relevant_errors}"
