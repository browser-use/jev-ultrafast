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
    finalization_error = None
    try:
        state = agent.snapshot()
        # Final evidence collection is outside the measured browser work.
        measured_calls = {method: {"count": len(times), "ms": round(sum(times), 3)} for method, times in calls.items()}
        state["error"] = error
        state["cdp"] = measured_calls
        state["source_hashes"] = source_hashes
        state["task_hash"] = hashlib.sha256(json.dumps([URL, GOALS]).encode()).hexdigest()
        state["configuration"] = {
            key: os.environ.get(key)
            for key in ("TYPESAFE_MODEL", "TEXT_MODEL", "TEXT_MODEL_BASE_URL", "TEXT_MODEL_REASONING")
        }
        # A missing fresh observation must not turn the cached page into a successful verification.
        state["final_page"] = None
        try:
            state["final_page"] = agent.browser.observe(screenshot=True)
            state["verification"] = verify(state["final_page"])
        except Exception as exc:
            state["verification"] = {"passed": False, "error": f"{type(exc).__name__}: {exc}"}
            finalization_error = exc
        try:
            state["browser_version"] = agent.browser.call("Browser.getVersion")["product"]
        except Exception as exc:
            state["browser_version"] = None
            state["browser_version_error"] = f"{type(exc).__name__}: {exc}"
            if finalization_error is None:
                finalization_error = exc
        (folder / "state.json").write_text(json.dumps(state, indent=2))
    finally:
        agent.close()
    if finalization_error is not None:
        raise finalization_error
print("VERIFIED", state["verification"]["passed"], "ERROR", error, flush=True)
