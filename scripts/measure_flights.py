"""One live measured flight search; freeze source externally to compare revisions."""

import argparse
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--source", default=".")
parser.add_argument("--output", required=True)
args = parser.parse_args()
source = Path(args.source).resolve()
sys.path.insert(0, str(source))
from jev_ultrafast import Agent  # noqa: E402
from jev_ultrafast import browser as browser_module  # noqa: E402

sys.path.append(str(Path(__file__).resolve().parents[1]))
from examples.flights import GOALS, URL, verify  # noqa: E402

folder = Path(args.output)
folder.mkdir(parents=True, exist_ok=False)
source_hashes = {
    p.name: hashlib.sha256(p.read_bytes()).hexdigest()
    for p in (source / "jev_ultrafast").iterdir() if p.suffix in {".py", ".js"}
}
raw = browser_module.cdp
calls = defaultdict(list)


def timed(method, *positional, **kwargs):
    t = time.perf_counter()
    result = raw(method, *positional, **kwargs)
    calls[method].append(round((time.perf_counter() - t) * 1000, 3))
    return result


browser_module.cdp = timed
agent = Agent(URL, GOALS)
# Setup is excluded in both arms, as in the original demo.
calls.clear()
error = None
try:
    for state in agent.run():
        last = state["history"][-1] if state["history"] else {}
        print(state["elapsed_ms"], state["status"], last.get("action", ""), flush=True)
except Exception as exc:
    error = f"{type(exc).__name__}: {exc}"
finally:
    state = agent.snapshot()
    measured_calls = {method: {"count": len(times), "ms": round(sum(times), 3)} for method, times in calls.items()}
    # Both arms use a NEW final observation for the independent result check, outside timing.
    final = agent.browser.observe(screenshot=True)
    state["verification"] = verify(final)
    state["error"] = error
    state["cdp"] = measured_calls
    state["source_hashes"] = source_hashes
    state["task_hash"] = hashlib.sha256(json.dumps([URL, GOALS]).encode()).hexdigest()
    state["configuration"] = {
        key: os.environ.get(key)
        for key in (
            "TYPESAFE_MODEL",
            "TYPESAFE_BASE_URL",
            "TEXT_MODEL",
            "TEXT_MODEL_BASE_URL",
            "TEXT_MODEL_REASONING",
        )
    }
    state["browser_version"] = agent.browser.call("Browser.getVersion")["product"]
    state["final_page"] = final
    (folder / "state.json").write_text(json.dumps(state, indent=2))
    agent.close()
print("VERIFIED", state["verification"]["passed"], "ERROR", error, flush=True)
