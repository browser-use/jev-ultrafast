"""uv run --env-file .env python examples/run.py --url URL --goal 'A narrow goal'"""

import argparse
import json

from jev_ultrafast import Agent

parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True)
parser.add_argument("--goal", action="append", required=True, help="Repeat for an ordered list of goals.")
parser.add_argument(
    "--backend",
    choices=("browser_harness", "ego"),
    help="Browser backend; defaults to the environment or Browser Harness.",
)
parser.add_argument("--record-dir", help="Write metrics.json and a redacted trace.json to this directory.")
args = parser.parse_args()

agent = Agent(args.url, args.goal, backend=args.backend, record_dir=args.record_dir)
with agent:
    for state in agent.run():
        print(f"{state['elapsed_ms']:>5} ms  {len(state['history'])} actions  {state['status']}")
state = agent.snapshot()
print(state["page"]["url"])
print(json.dumps(state["metrics"], sort_keys=True))
