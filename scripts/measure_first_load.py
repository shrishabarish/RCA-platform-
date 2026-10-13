import time
from pathlib import Path
from playwright.sync_api import sync_playwright

PROJECT_ROOT = Path(__file__).resolve().parent.parent
HTML_PATH = (PROJECT_ROOT / "frontend" / "index.html").resolve()


def measure_first_load_20_runs():
    times_ms = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for run in range(20):
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()

            t0 = time.perf_counter()
            page.goto(HTML_PATH.as_uri(), wait_until="load")

            # Switch to Replay tab with 12-node case loaded
            page.locator("button.tab-btn:has-text('Replay Timeline')").click()
            page.wait_for_selector("#tab-replay.active", state="visible")
            
            # Select 12-node case
            page.select_option("#replayCaseSelect", "indep_004_slow_burn_decoy_12node")
            page.wait_for_selector("#replayFrameIdx", state="visible")

            t1 = time.perf_counter()
            elapsed_ms = (t1 - t0) * 1000.0
            times_ms.append(elapsed_ms)
            context.close()

        browser.close()

    times_ms.sort()
    p50 = times_ms[len(times_ms) // 2]
    p95 = times_ms[int(len(times_ms) * 0.95)]
    mean = sum(times_ms) / len(times_ms)

    print("=================================================================")
    print("12-NODE REPLAY FIRST-LOAD BENCHMARK (20 Runs in Headless Chromium)")
    print("=================================================================")
    print(f"Runs:       20")
    print(f"p50 Median: {p50:.2f} ms (vs 375 ms baseline)")
    print(f"Mean:       {mean:.2f} ms")
    print(f"p95:        {p95:.2f} ms")
    print(f"Min:        {times_ms[0]:.2f} ms")
    print(f"Max:        {times_ms[-1]:.2f} ms")
    print("=================================================================")
    return p50, times_ms


if __name__ == "__main__":
    measure_first_load_20_runs()
