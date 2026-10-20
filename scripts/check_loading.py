"""Real asynchronous page loading during stubbed decisions; no model calls or external websites."""

from urllib.parse import quote

from jev_ultrafast import agent as loop

HTML = """<!doctype html><title>Loading checks</title>
<p id="status">Loading</p>
<script>window.inputs=0</script>"""


def main():
    original_choose = loop.choose
    calls = 0
    with loop.Agent("data:text/html," + quote(HTML), "Wait for the form and continue.") as agent:
        def choose(page, _goal, _history):
            nonlocal calls
            calls += 1
            action = next((a for a in page["actions"] if a["label"] == "Continue"), None)
            if action is None:
                # Model latency overlaps rendering: the observation is stale before BLOCKED returns.
                agent.browser.evaluate("""
                  document.querySelector('#status').textContent='Header loaded';
                  setTimeout(()=>{
                    const button=document.createElement('button');
                    button.textContent='Continue';
                    button.onclick=()=>{window.inputs++;document.querySelector('#status').textContent='Complete'};
                    document.body.append(button);
                  },400)
                """)
            selected = action["id"] if action else "BLOCKED"
            return {
                "choice": selected,
                "operation": "CLICK" if action else "BLOCKED",
                "target": "1" if action else None,
                "confidence": 1.0,
                "probabilities": {selected: 1.0},
                "latency_ms": 0,
                "usage": {},
            }

        loop.choose = choose
        try:
            state = agent.command("tick")
            assert state["status"] == "rechecking"
            assert any(a["label"] == "Continue" for a in state["page"]["actions"])
            assert state["history"] == []
            assert state["rejections"] == []
            assert len(state["blocked_rechecks"]) == 1
            state = agent.command("tick")
            assert state["status"] == "ready"
            assert len(state["history"]) == 1
            assert agent.browser.evaluate("window.inputs") == 1
            assert "Complete" in state["page"]["text"]
            assert calls == 2
        finally:
            loop.choose = original_choose
    print("PASS: stale BLOCKED waits for real async content; the next decision uses it; one input executes")


if __name__ == "__main__":
    main()
