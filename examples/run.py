"""uv run --env-file .env python examples/run.py --url URL --goal 'A narrow goal' [--record] [--verify-file f.py]"""

import argparse
import importlib.util
import json
import re
import shutil
import sys
import time
from pathlib import Path

from jev_ultrafast import Agent

parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True)
parser.add_argument("--goal", action="append", required=True, help="Repeat for an ordered list of goals.")
parser.add_argument("--keep-open", action="store_true", help="Leave the tab open after the run.")
parser.add_argument("--done-url-contains", help="Code-owned completion: stop when the URL contains this text.")
parser.add_argument("--done-url-not-contains", help="Code-owned completion: stop when the URL no longer contains this.")
parser.add_argument("--done-text-contains", help="Code-owned completion: stop when visible page text contains this.")
parser.add_argument("--done-min-conf", type=float, default=0.7, help="DONE below this needs a second vote.")
parser.add_argument("--verify-file", help="Python file defining verify(page) -> {'passed': bool, 'checks': {...}}.")
parser.add_argument("--done-when-verify", action="store_true", help="Also stop as soon as verify(page) passes.")
parser.add_argument("--record", action="store_true", help="Save a screenshot after every step into the run folder.")
parser.add_argument("--keep-runs", type=int, default=20, help="Keep only the newest N run folders (default 20).")
parser.add_argument("--keep-days", type=int, default=7, help="Delete run folders older than D days (default 7).")
args = parser.parse_args()

RUNS = Path("artifacts/run")
RUN_DIR = RUNS / time.strftime("%Y%m%d-%H%M%S")
RUN_DIR.mkdir(parents=True, exist_ok=True)


def load_verifier(path):
    spec = importlib.util.spec_from_file_location("verifier", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.verify


verify = load_verifier(args.verify_file) if args.verify_file else None


def done_when(page):
    checks = []
    if args.done_url_contains:
        checks.append(args.done_url_contains in page["url"])
    if args.done_url_not_contains:
        checks.append(args.done_url_not_contains not in page["url"])
    if args.done_text_contains:
        checks.append(args.done_text_contains.lower() in page["text"].lower())
    if args.done_when_verify and verify:
        checks.append(bool(verify(page)["passed"]))
    return bool(checks) and all(checks)


def prune_runs(keep_runs, keep_days):
    """Retention for artifacts/run: newest N folders and nothing older than D days; 'latest' is a symlink."""
    folders = sorted(p for p in RUNS.iterdir() if p.is_dir() and re.fullmatch(r"\d{8}-\d{6}", p.name))
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for i, folder in enumerate(reversed(folders)):
        if i >= keep_runs or folder.stat().st_mtime < cutoff:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed


agent = Agent(
    args.url,
    args.goal,
    done_when=done_when,
    done_min_confidence=args.done_min_conf,
    record_dir=RUN_DIR if args.record else None,
)
exit_code = 0
try:
    for state in agent.run():
        decision = state["decisions"][-1] if state["decisions"] else {}
        last = state["history"][-1] if state["history"] else {}
        op = decision.get("operation", "")
        conf = decision.get("confidence", 0)
        print(
            f"{state['elapsed_ms']:>6} ms  {len(state['history']):>2} actions  {state['status']:<9} "
            f"{op:<9} conf={conf:.2f}  {last.get('action', '')[:60]}"
        )
    print(state["page"]["url"], "| done_by:", state.get("done_by", "model"))
finally:
    snapshot = agent.snapshot()
    verification = None
    if verify:
        # Independent check of the final page; the model's DONE is never the evidence.
        verification = verify(snapshot["page"])
        print("verification:", json.dumps(verification, ensure_ascii=False))
        if not verification.get("passed"):
            exit_code = 1
    trace = {
        "goal": snapshot["goal"],
        "status": snapshot["status"],
        "done_by": snapshot.get("done_by", "model"),
        "elapsed_ms": snapshot["elapsed_ms"],
        "final_url": snapshot["page"]["url"],
        "final_title": snapshot["page"]["title"],
        "verification": verification,
        "visited": [h["url"] for h in snapshot["history"]],
        "steps": [
            {k: h.get(k) for k in ("step", "action", "kind", "text", "confidence", "page_changed", "url")}
            for h in snapshot["history"]
        ],
    }
    (RUN_DIR / "trace.json").write_text(json.dumps(trace, indent=2, ensure_ascii=False))
    latest = RUNS / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink() if latest.is_symlink() else shutil.rmtree(latest)
    latest.symlink_to(RUN_DIR.name)
    frames = len(list(RUN_DIR.glob("*.jpg")))
    pruned = prune_runs(args.keep_runs, args.keep_days)
    print(f"saved {RUN_DIR} ({frames} screenshots); pruned {pruned} old run(s)")
    if not args.keep_open:
        agent.close()
sys.exit(exit_code)
