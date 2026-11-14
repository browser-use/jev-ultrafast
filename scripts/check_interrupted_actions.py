"""Real-browser action acknowledgement-loss regressions; no model calls."""

from unittest.mock import patch
from urllib.parse import quote

from jev_ultrafast import Agent
from jev_ultrafast import agent as loop
from jev_ultrafast import browser as runtime

HTML = """<!doctype html><title>Interrupted action</title>
<button onclick="window.submissions=(window.submissions||0)+1">Submit</button>
<label>Message<input oninput="window.inputs=(window.inputs||0)+1"></label>
<select aria-label="Category" onchange="window.changes=(window.changes||0)+1">
<option>First</option><option>Second</option></select>"""


def check(kind):
    with Agent("data:text/html," + quote(HTML), "Exercise an interrupted action") as agent:
        # Initial navigation readiness is independent of these execution checks.
        for _ in range(10):
            page = agent.browser.observe(screenshot=False)
            if any(a["kind"] == kind for a in page["actions"]):
                break
        agent.state["page"] = page
        action = next(a for a in page["actions"] if a["kind"] == kind)
        selected = {
            "choice": action["id"], "operation": {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}[kind],
            "target": "1", "confidence": 1, "probabilities": {action["id"]: 1}, "latency_ms": 0, "usage": {},
        }
        cdp = runtime.cdp
        dropped = False

        def lose_ack(method, **params):
            nonlocal dropped
            result = cdp(method, **params)
            is_mutation = (
                (kind == "click" and method == "Input.dispatchMouseEvent" and params.get("type") == "mouseReleased")
                or (kind == "fill" and method == "Input.insertText")
                or (
                    kind == "select" and method == "Runtime.evaluate"
                    and "e.value=action.value" in params["expression"]
                )
            )
            if is_mutation and not dropped:
                dropped = True
                raise TimeoutError("Injected lost acknowledgement after real browser execution")
            return result

        with (
            patch.object(runtime, "cdp", lose_ack),
            patch.object(loop, "choose", return_value=selected) as choose,
            patch.object(loop, "field_text", return_value=("hello", {"model": "offline", "latency_ms": 0})),
        ):
            try:
                agent.command("tick")
            except TimeoutError:
                pass
            else:
                raise AssertionError("The injected timeout did not reach the caller")
            assert dropped
            counter = {"click": "submissions", "fill": "inputs", "select": "changes"}[kind]
            assert agent.browser.evaluate(f"window.{counter}") == 1, "The actual DOM side effect must be verified"
            # A new run/tick must not repeat an action whose acknowledgement was lost.
            try:
                agent.command("tick")
            except ValueError as error:
                assert "stopped" in str(error)
            else:
                raise AssertionError("A stopped run accepted another tick")
            count = agent.browser.evaluate(f"window.{counter}")
            assert count == 1, f"{kind}: interrupted action replayed; actual DOM event count={count}"
            assert choose.call_count == 1
            assert agent.state["status"] == "blocked"
            assert agent.state["history"][-1]["execution"] == "unknown"
            assert agent.state["history"][-1]["executed_ms"] is None
            assert list(agent.run()) == []
            print(f"PASS {kind}: actual DOM effect once, lost acknowledgement, run stopped")


def main():
    failures = []
    for kind in ("click", "fill", "select"):
        try:
            check(kind)
        except AssertionError as error:
            failures.append(f"{kind}: {error}")
            print(f"FAIL {failures[-1]}")
    assert not failures, failures


if __name__ == "__main__":
    main()
