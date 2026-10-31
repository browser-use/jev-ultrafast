import os
import subprocess
import time
from pathlib import Path

from jev_ultrafast.demo import load_environment

load_environment()

CHROME_PATH = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
USER_DATA = Path(os.environ.get("TEMP", ".")) / "chrome-automation"
PORT = 9222

chrome_proc = subprocess.Popen(
    [
        CHROME_PATH,
        f"--remote-debugging-port={PORT}",
        f"--user-data-dir={USER_DATA}",
        "--headless=new",
        "--no-first-run",
        "--no-default-browser-check",
    ],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
time.sleep(1.5)
os.environ["BU_CDP_URL"] = f"http://127.0.0.1:{PORT}"

try:
    from jev_ultrafast import Agent
    from jev_ultrafast.model import choose, field_context, field_text

    goal = "Use the destination search and filters to find Lisbon stays with free cancellation, then open Casa Flora."
    print("Initializing Agent...")
    with Agent("http://127.0.0.1:8766/fixture.html?scenario=travel", goal) as agent:
        print("Page loaded:", agent.state["page"]["title"])
        page = agent.state["page"]
        print("Observed elements count:", len(page["actions"]))
        
        # Test direct choose() call
        t0 = time.perf_counter()
        decision = choose(page, goal, [])
        latency = (time.perf_counter() - t0) * 1000
        print(f"choose() completed in {latency:.1f} ms:")
        print(f"  Operation: {decision['operation']}")
        print(f"  Target: {decision['target']}")
        print(f"  Choice: {decision['choice']}")
        print(f"  Confidence: {decision['confidence']}")
        print(f"  Model: {decision['model']}")
        print(f"  Usage: {decision['usage']}")

        # Test field_text() call on destination search field
        dest_action = next(a for a in page["actions"] if a.get("label") == "Destination")
        ctx = field_context(goal, dest_action, page, [])
        t0 = time.perf_counter()
        val, meta = field_text(ctx)
        lat2 = (time.perf_counter() - t0) * 1000
        print(f"field_text() completed in {lat2:.1f} ms:")
        print(f"  Value: '{val}'")
        print(f"  Model: {meta['model']}")
        print(f"  Usage: {meta['usage']}")

finally:
    chrome_proc.terminate()
    try:
        chrome_proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        chrome_proc.kill()
