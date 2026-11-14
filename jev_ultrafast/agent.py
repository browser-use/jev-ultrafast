"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import time
from pathlib import Path

from .browser import Browser, StalePage
from .model import action_space, choose, field_context, field_text
from .questions import MAX_STEPS


class Agent:
    # Defaults so partially constructed agents (tests) behave like "model decides everything".
    done_when = None
    done_min_confidence = 0.0
    done_votes = 0
    blocked_votes = 0
    start_url = None
    terminal_repeats = None

    def __init__(self, url, goals, *, record_dir=None, screenshots=False, done_when=None, done_min_confidence=0.7):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        self.pending_text = None
        # Code owns completion: `done_when(page)` ends the run regardless of the model's DONE choice.
        # A DONE below `done_min_confidence` must be chosen twice in a row before it is accepted.
        self.done_when = done_when
        self.done_min_confidence = done_min_confidence
        self.done_votes = 0
        self.terminal_repeats = {}
        self.start_url = url
        self.browser = Browser(url)
        self.record_dir = Path(record_dir) if record_dir else None
        self.screenshots = screenshots or bool(record_dir)
        try:
            page = self.browser.observe(screenshot=self.screenshots)
        except Exception:
            self.browser.close()
            raise
        self.state = dict(
            browser=self.browser,
            goal="\n".join(plan),
            page=page,
            decision=None,
            history=[],
            status="ready",
            plan=plan,
            plan_index=0,
            decisions=[],
            text_calls=[],
            elapsed_ms=0,
            started_at=None,
            record=bool(self.record_dir),
        )
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            (self.record_dir / "000000.jpg").write_bytes(base64.b64decode(page["screenshot"]))

    def _code_done(self):
        """Return True and mark the run done when the caller-supplied predicate accepts the current page."""
        state = self.state
        if self.done_when and state["status"] not in {"done", "blocked"}:
            try:
                accepted = bool(self.done_when(state["page"]))
            except Exception:
                accepted = False
            if accepted:
                state["status"] = "done"
                state["plan_index"] = 1
                state["done_by"] = "code"
                state["decision"] = None
                if state["started_at"] is not None:
                    state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return True
        return False

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            try:
                self.command("predict", {})
                if state["status"] in {"done", "blocked"}:
                    return self.snapshot()
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["status"] = "ready"
                # Recovery observation is a read; retry while a navigation is still in flight (slow links).
                for attempt in range(24):
                    try:
                        state["page"] = state["browser"].observe(screenshot=self.screenshots)
                        break
                    except StalePage:
                        if attempt == 23:
                            raise
                        time.sleep(0.5)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            # A freshly navigated document may expose no controls yet on a slow link; wait briefly before deciding.
            for _ in range(20):
                if any(a["kind"] in ("click", "fill", "select") for a in state["page"]["actions"]):
                    break
                time.sleep(0.5)
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if self._code_done():
                return self.snapshot()
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            if len(state["decisions"]) >= MAX_STEPS * 2:
                raise ValueError("Reached the demo's model-call budget")
            state["decision"] = choose(state["page"], state["goal"], state["history"], start_url=self.start_url)
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            state["status"] = "predicted"
        elif name == "act":
            decision, page = state["decision"], state["page"]
            if not decision or body.get("fingerprint") != page["fingerprint"]:
                raise ValueError("Observe and choose before acting")
            # Consume once, before any mutation or model call. A retry cannot double-click.
            state["decision"] = None
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                key = (selected, page["url"])
                if not state["browser"].fresh(page):
                    # An animating page never looks fresh; accept the same terminal choice after 3 stale repeats.
                    if self.terminal_repeats is None:
                        self.terminal_repeats = {}
                    self.terminal_repeats[key] = self.terminal_repeats.get(key, 0) + 1
                    if self.terminal_repeats[key] < 3:
                        state["status"] = "ready"
                        raise StalePage("Page changed since the decision. Choose again.")
                if selected == "DONE" and decision["confidence"] < self.done_min_confidence and self.done_votes < 1:
                    self.done_votes += 1
                    state["status"] = "ready"
                    state["page"] = state["browser"].observe(screenshot=self.screenshots)
                    return self.snapshot()
                if selected == "BLOCKED" and self.blocked_votes < 1:
                    # A first BLOCKED is often a half-loaded page or a collapsed menu: wait once and decide again.
                    self.blocked_votes += 1
                    time.sleep(1.0)
                    state["status"] = "ready"
                    state["page"] = state["browser"].observe(screenshot=self.screenshots)
                    return self.snapshot()
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = int(selected == "DONE")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            self.done_votes = 0
            self.blocked_votes = 0
            action = next(a for a in page["actions"] if a["id"] == selected)
            if len(state["history"]) >= MAX_STEPS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at the {MAX_STEPS}-action demo budget")
            text, helper = None, None
            if action["kind"] == "fill":
                if not state["browser"].fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                context = field_context(state["goal"], action, page, state["history"])
                if self.pending_text and self.pending_text[0] == context:
                    _, text, helper = self.pending_text
                else:
                    text, helper = field_text(context)
                    self.pending_text = (context, text, helper)
                    state["text_calls"].append({**helper, "field": action["label"], "value": text})
            # Browser.act checks freshness immediately before input, including after text generation.
            state["browser"].act(action, page, text=text)
            self.pending_text = None
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            # Record execution before observing. A stale post-action observation must not erase the action.
            state["history"].append(
                {
                    "step": len(state["history"]) + 1,
                    "action": action["label"],
                    "kind": action["kind"],
                    "choice": selected,
                    "probability": decision["probabilities"][selected],
                    "confidence": decision["confidence"],
                    "latency_ms": decision["latency_ms"],
                    "text": text,
                    "text_helper": helper["model"] if helper else None,
                    "text_latency_ms": helper["latency_ms"] if helper else 0,
                    "operation": decision["operation"],
                    "target": decision["target"],
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            self._code_done()
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"]:
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            repeated = state["history"][-3:]
            state["status"] = (
                "blocked"
                if len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated)
                else "ready"
            )
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def run(self):
        while self.state["status"] not in {"done", "blocked"}:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
