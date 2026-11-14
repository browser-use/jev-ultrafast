"""Independent verifier for the httpbin pizza form: `--verify-file examples/verify_pizza.py`.

Edit EXPECTED to match the goal you gave the agent. The check reads the server's echoed JSON,
so it proves what httpbin received, not what the model believed it typed.
"""

import json

EXPECTED = {
    "custname": "Kai Zhang",
    "custtel": "090-1234-5678",
    "custemail": "kai@example.com",
    "size": "large",
    "topping": ["cheese", "onion"],
    "comments": "Leave at the front desk",
    "delivery": "",
}


def verify(page):
    checks = {"result_page": "httpbin.org/post" in page["url"]}
    form = {}
    try:
        form = json.loads(page["text"]).get("form", {})
    except (ValueError, AttributeError):
        checks["echo_is_json"] = False
    for key, want in EXPECTED.items():
        got = form.get(key, "" if not isinstance(want, list) else [])
        checks[key] = sorted(got) == sorted(want) if isinstance(want, list) else got == want
    return {"passed": all(checks.values()), "checks": checks, "received": form}
