"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import math
import time
from collections.abc import Callable
from pathlib import Path

from .browser import Browser, StalePage
from .model import action_space, choose, field_context, field_text
from .questions import MAX_STEPS


class Agent:
    """Run the Jev policy against an isolated browser tab.

    ``confidence_floor`` is an opt-in local policy. When it is greater than
    zero, an ordinary action is paused if either the operation or selected
    target confidence is below the floor. The action is recorded with
    ``executed=False`` and ``status`` becomes ``uncertain``; ``on_uncertain``
    receives that event after it has been recorded. The floor is a routing
    threshold, not a calibrated safety guarantee. A target that no longer
    matches the observed page is also recorded as uncertain with
    ``reason="stale_target"`` before any browser mutation.
    """

    def __init__(
        self,
        url,
        goals,
        *,
        record_dir=None,
        screenshots=False,
        confidence_floor=0.0,
        on_uncertain: Callable[[dict[str, object]], None] | None = None,
    ):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        if (
            isinstance(confidence_floor, bool)
            or not isinstance(confidence_floor, (int, float))
            or not math.isfinite(confidence_floor)
            or not 0 <= confidence_floor <= 1
        ):
            raise ValueError("confidence_floor must be a finite number between 0 and 1")
        if on_uncertain is not None and not callable(on_uncertain):
            raise TypeError("on_uncertain must be callable")
        plan = [task]
        self.confidence_floor = float(confidence_floor)
        self.on_uncertain = on_uncertain
        self.pending_text = None
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
            uncertainty=None,
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

    def snapshot(self):
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0],
        }

    def _record_uncertain(self, action, decision, reason, failed_confidences=None):
        state = self.state
        event = {
            "step": len(state["history"]) + 1,
            "action": action["label"],
            "kind": action["kind"],
            "choice": decision["choice"],
            "operation": decision["operation"],
            "target": decision["target"],
            "confidence": decision["confidence"],
            "target_confidence": decision.get("target_confidence"),
            "confidence_floor": getattr(self, "confidence_floor", 0.0),
            "failed_confidences": failed_confidences or [],
            "reason": reason,
            "executed": False,
            "page_changed": None,
            "text": None,
            "text_helper": None,
            "text_latency_ms": 0,
            "latency_ms": decision["latency_ms"],
            "usage": decision["usage"],
            "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
            "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
        }
        state["history"].append(event)
        state["elapsed_ms"] = event["elapsed_ms"]
        state["uncertainty"] = event
        state["status"] = "uncertain"
        self.pending_text = None
        on_uncertain = getattr(self, "on_uncertain", None)
        if on_uncertain is not None:
            on_uncertain(dict(event))
        return self.snapshot()

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            try:
                self.command("predict", {})
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["status"] = "ready"
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked", "uncertain"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            if len(state["decisions"]) >= MAX_STEPS * 2:
                raise ValueError("Reached the demo's model-call budget")
            state["decision"] = choose(state["page"], state["goal"], state["history"])
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
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = int(selected == "DONE")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            if len(state["history"]) >= MAX_STEPS:
                state["status"] = "blocked"
                raise ValueError(f"Stopped at the {MAX_STEPS}-action demo budget")
            confidence_floor = getattr(self, "confidence_floor", 0.0)
            failed_confidences = []
            if decision["confidence"] < confidence_floor:
                failed_confidences.append("operation")
            target_confidence = decision.get("target_confidence")
            if target_confidence is not None and target_confidence < confidence_floor:
                failed_confidences.append("target")
            if failed_confidences:
                return self._record_uncertain(
                    action,
                    decision,
                    "below_confidence_floor",
                    failed_confidences,
                )
            target_action = action["kind"] in {"click", "fill", "select"}
            if target_action and not state["browser"].fresh(page, action):
                return self._record_uncertain(action, decision, "stale_target")
            text, helper = None, None
            if action["kind"] == "fill":
                if not state["browser"].fresh(page, action):
                    return self._record_uncertain(action, decision, "stale_target")
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
                    "target_confidence": decision.get("target_confidence"),
                    "executed": True,
                    "page_changed": None,
                    "url": page["url"],
                    "usage": decision["usage"],
                    "executed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                    "elapsed_ms": state["elapsed_ms"],
                }
            )
            state["page"] = state["browser"].observe(screenshot=self.screenshots)
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
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
        while self.state["status"] not in {"done", "blocked", "uncertain"}:
            yield self.command("tick")

    def close(self):
        self.browser.close()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
