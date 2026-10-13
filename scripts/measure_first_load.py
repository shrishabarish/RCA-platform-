import time
from pathlib import Path
from playwright.sync_api import sync_playwright

PAGE_URL = "http://127.0.0.1:8000/frontend/index.html"


def benchmark_first_load_and_replay():
    first_load_times = []
    replay_flow_times = []

    print(f"Running benchmarks against FastAPI server: {PAGE_URL}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        # 1. Page First-Load Benchmark (20 runs)
        for run in range(20):
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()

            t0 = time.perf_counter()
            page.goto(PAGE_URL, wait_until="load")
            t1 = time.perf_counter()

            first_load_times.append((t1 - t0) * 1000.0)
            context.close()

        # 2. 12-Node Replay Flow Benchmark (20 runs)
        for run in range(20):
            context = browser.new_context(viewport={"width": 1440, "height": 900})
            page = context.new_page()

            t0 = time.perf_counter()
            page.goto(PAGE_URL, wait_until="load")

            # Switch to Replay tab
            page.locator("button.tab-btn:has-text('Replay Timeline')").click()
            page.wait_for_selector("#tab-replay.active", state="visible")

            # Select 12-node case and wait for frame render
            page.select_option("#replayCaseSelect", "indep_004_slow_burn_decoy_12node")
            page.wait_for_selector("#replayFrameIdx", state="visible")
            t1 = time.perf_counter()

            replay_flow_times.append((t1 - t0) * 1000.0)
            context.close()

        browser.close()

    def calc_stats(times):
        sorted_t = sorted(times)
        p50 = sorted_t[len(sorted_t) // 2]
        p95 = sorted_t[int(len(sorted_t) * 0.95)]
        mean = sum(sorted_t) / len(sorted_t)
        return p50, p95, mean, sorted_t[0], sorted_t[-1]

    fl_p50, fl_p95, fl_mean, fl_min, fl_max = calc_stats(first_load_times)
    rp_p50, rp_p95, rp_mean, rp_min, rp_max = calc_stats(replay_flow_times)

    print("\n" + "=" * 65)
    print("PAGE FIRST-LOAD BENCHMARK (20 Runs in Headless Chromium)")
    print(f"URL:        {PAGE_URL}")
    print("=" * 65)
    print(f"p50 Median: {fl_p50:.2f} ms")
    print(f"p95:        {fl_p95:.2f} ms")
    print(f"Mean:       {fl_mean:.2f} ms")
    print(f"Min / Max:  {fl_min:.2f} ms / {fl_max:.2f} ms")
    print("=" * 65)

    print("\n" + "=" * 65)
    print("12-NODE REPLAY FLOW BENCHMARK (20 Runs in Headless Chromium)")
    print(f"URL:        {PAGE_URL}")
    print("=" * 65)
    print(f"p50 Median: {rp_p50:.2f} ms (vs 375 ms baseline)")
    print(f"p95:        {rp_p95:.2f} ms")
    print(f"Mean:       {rp_mean:.2f} ms")
    print(f"Min / Max:  {rp_min:.2f} ms / {rp_max:.2f} ms")
    print("=" * 65)

    return (fl_p50, fl_p95), (rp_p50, rp_p95)


if __name__ == "__main__":
    benchmark_first_load_and_replay()
