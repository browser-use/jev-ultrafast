"""Explicit live smoke: TypeSafe SDK + paid Jev API. Not run by pytest.

Run with: uv run --with typesafe-sdk python scripts/sdk_smoke.py
"""

import sys

from typesafe_sdk import Noul, TypeSafeClient

from jev_ultrafast.demo import load_environment

TICKET = "I was charged twice. Please fix this ASAP."
MIN_BILLING_PROBABILITY = 0.8


def main():
    load_environment()
    with TypeSafeClient() as client:
        response = client.system_one(
            state={"document": TICKET},
            questions={"billing": Noul(instructions="Is this ticket about billing?")},
        )
    probability = response.nouls["billing"].noul
    print(f"P(billing) = {probability:.2f}")
    if probability < MIN_BILLING_PROBABILITY:
        print(f"FAIL: expected P(billing) >= {MIN_BILLING_PROBABILITY}", file=sys.stderr)
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
