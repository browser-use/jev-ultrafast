"""Per-role model connectivity report — the same check the sidebar runs at startup.
Re-run it with a push to this file or from the repo's Actions tab (workflow_dispatch).

Usage:
    uv run --env-file .env python scripts/check_providers.py   # local, reads .env
    python scripts/check_providers.py                           # env already set

Prints a markdown table with one row per configured role (planner / policy /
text): model, latency, or the provider's exact error. Purely diagnostic: it
always exits 0 and never prints credentials.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jev_ultrafast.firefox import check_providers, load_environment  # noqa: E402


def main():
    load_environment()
    results = check_providers()
    print("### Model provider check\n")
    print("| Role | Model | Status |")
    print("| --- | --- | --- |")
    for entry in results.values():
        if entry["ok"]:
            status = f"🟢 ok · {entry['latency_ms']} ms"
        else:
            status = f"🔴 {entry['detail'].replace('|', '/')}"
        print(f"| {entry['role']} | `{entry['model'] or '—'}` | {status} |")


if __name__ == "__main__":
    main()
