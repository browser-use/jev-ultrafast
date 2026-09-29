"""The complete agent loop. Typed choices, observable state, bounded execution."""

import base64
import time
from pathlib import Path

from .browser import Browser, StalePage
from .model import action_space, choose, field_context, field_text
from .questions import MAX_NO_PROGRESS, MAX_STALE_REPLAY, MAX_STEPS


class Agent:
    def __init__(self, url, goals, *, record_dir=None, screenshots=False):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        self.pending_text = None
        # The decision a pre-input rejection threw away, kept only to re-offer against an unchanged table.
        self.pending_stale = None
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
            # Action ids that left this page unchanged; JSON-safe list because the panel serialises state.
            no_progress=[],
            # Consecutive rejections of one byte-identical table, all on the same control.
            stale_replays=0,
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
            "elements": action_space(self.state["page"]["actions"], self.state["no_progress"])[0],
        }

    def refresh(self):
        """Observe again, keeping the excluded ids only while the action table is byte-identical.

        Skipped ids are positional into the observed actions, and the browser's freshness guard
        churns on sites like Google Flights even when nothing the model can act on has changed.
        """
        previous = self.state["page"]["fingerprint"]
        self.state["page"] = self.state["browser"].observe(screenshot=self.screenshots)
        if self.state["page"]["fingerprint"] != previous:
            self.state["no_progress"] = []

    def after_stale(self):
        """Re-observe after a rejection, and keep the answer when the table has not moved.

        Every StalePage out of the act path is raised before input reaches the page, so re-offering a
        decision cannot double-click. The table is what the model was shown, so when the re-observation
        matches it byte for byte the next request is the same question: on Google Flights' date dialog
        that cost 85 calls and 1.18M input tokens for the same answer, and one flipped answer 43 s later.
        """
        state = self.state
        rejected, self.pending_stale = self.pending_stale, None
        state["decision"] = None
        previous = state["page"]["fingerprint"]
        self.refresh()
        if rejected is None or state["page"]["fingerprint"] != previous:
            state["stale_replays"] = 0
            return False
        if state["stale_replays"] >= MAX_STALE_REPLAY:
            # Nothing this table can offer has been accepted for the whole cap. Stop offering the control
            # that refused, so the runner-up gets a turn and the no-progress cap can end a stuck episode.
            state["no_progress"] = [*state["no_progress"], rejected["choice"]]
            state["stale_replays"] = 0
            return False
        state["stale_replays"] += 1
        state["decision"] = rejected
        return True

    def command(self, name, body=None):
        body = body or {}
        state = self.state
        if name == "tick":
            # The cap lives in after_stale; this bound only guarantees a tick cannot spin if it is wrong.
            for attempt in range(MAX_STALE_REPLAY + 2):
                try:
                    if attempt == 0:
                        self.command("predict", {})
                    return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
                except StalePage:
                    state["status"] = "ready"
                    if not self.after_stale():
                        break
            state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
            return self.snapshot()
        elif name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
            if not state["browser"].fresh(state["page"]):
                self.refresh()
            state["decision"] = None
            self.pending_stale = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            if len(state["decisions"]) >= MAX_STEPS * 2:
                raise ValueError("Reached the demo's model-call budget")
            state["decision"] = choose(
                state["page"], state["goal"], state["history"], skip=state["no_progress"]
            )
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
            # StalePage from here on is raised before input reaches the page, so after_stale may re-offer
            # this same decision against a byte-identical re-observation without risking a second click.
            self.pending_stale = decision
            selected = decision["choice"]
            if selected in {"DONE", "BLOCKED"}:
                if not state["browser"].fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                self.pending_stale = None
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = int(selected == "DONE")
                state["elapsed_ms"] = round((time.perf_counter() - state["started_at"]) * 1000)
                return self.snapshot()
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
            self.pending_stale = None
            state["stale_replays"] = 0
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
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                overlay_reads=state["page"]["overlay_reads"],
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            if state["record"]:
                (self.record_dir / f"{state['elapsed_ms']:06d}.jpg").write_bytes(
                    base64.b64decode(state["page"]["screenshot"])
                )
            last = state["history"][-1]
            if last["page_changed"] or last["kind"] == "wait":
                state["no_progress"] = []
            else:
                # Keeping ids across this re-observation is safe only because fingerprint() hashes the
                # action table: page_changed False means these positional ids still denote the same elements.
                state["no_progress"] = [*state["no_progress"], last["choice"]]
            repeated = state["history"][-3:]
            consecutive = len(repeated) == 3 and all(
                h["page_changed"] is False and h["kind"] != "wait" for h in repeated
            )
            # Derived from history, so the panel and this rule cannot disagree about what was a no-op.
            no_ops = sum(
                1 for h in state["history"] if h["page_changed"] is False and h["kind"] != "wait"
            )
            state["status"] = "blocked" if consecutive or no_ops >= MAX_NO_PROGRESS else "ready"
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
