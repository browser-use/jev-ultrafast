"""uv run --env-file .env python examples/run.py --url URL --goal 'A narrow goal'"""

import argparse

from jev_ultrafast import Agent

parser = argparse.ArgumentParser()
parser.add_argument("--url", required=True)
parser.add_argument("--goal", action="append", required=True, help="Repeat for an ordered list of goals.")
parser.add_argument("--file", action="append", default=[], help="A file UPLOAD_FILE may attach. Repeatable.")
args = parser.parse_args()

with Agent(args.url, args.goal, files=args.file) as agent:
    for state in agent.run():
        print(f"{state['elapsed_ms']:>5} ms  {len(state['history'])} actions  {state['status']}")
    print(state["page"]["url"])
