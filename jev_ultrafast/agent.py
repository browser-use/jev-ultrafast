"""The complete agent loop. Typed choices, observable state, bounded execution."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

from .browser import Browser, StalePage, create_backend  # noqa: F401 - retained for old integrations
from .metrics import RunMetrics
from .model import action_space, choose, field_context, field_text
from .preflight import preflight, selected_backend
from .questions import MAX_STALE_RETRIES, MAX_STEPS
from .timeline import Timeline


class Agent:
    def __init__(self, url, goals, *, record_dir=None, screenshots=None, backend=None, preflight_check=True):
        task = goals.strip() if isinstance(goals, str) else "\n".join(goals).strip()
        if not task:
            raise ValueError("Supply a task")
        plan = [task]
        self.pending_text = None
        self.record_dir = Path(record_dir) if record_dir else None
        # Screenshots default to on while recording. A measured run can turn
        # them off explicitly: they are optional, the model never reads them,
        # and capturing them costs a different amount of time per backend.
        self.screenshots = bool(self.record_dir) if screenshots is None else bool(screenshots)
        self._closed = False
        self.backend_name = (
            selected_backend(backend)
            if isinstance(backend, str) or backend is None
            else str(getattr(backend, "backend_name", backend.__class__.__name__)).lower()
        )
        self.metrics = RunMetrics(self.backend_name)
        self.timeline = Timeline(self.record_dir)
        self.browser = None
        self.state = None
        try:
            self._write_artifacts()
            if preflight_check and isinstance(backend, (str, type(None))):
                preflight(self.backend_name)
            backend_started = time.perf_counter()
            self.browser = backend if hasattr(backend, "observe") else create_backend(url, self.backend_name)
            self.backend_name = str(getattr(self.browser, "backend_name", self.backend_name)).lower()
            self.metrics.backend = self.backend_name
            attach = getattr(self.browser, "attach_timeline", None)
            if callable(attach):
                attach(self.timeline)
            page = self._backend_observe(self.screenshots)
            self.metrics.backend_startup_ms = round((time.perf_counter() - backend_started) * 1000)
        except BaseException as error:
            if self.browser is not None:
                try:
                    self.browser.close()
                except BaseException:
                    pass
                self.metrics.merge_backend(self.browser)
            self._finish_metrics("error", error)
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
            stale_retries=0,
        )
        self.metrics.start_policy()
        if self.record_dir:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            self._write_screenshot(page, "000000.jpg")

    def _write_screenshot(self, page: dict[str, Any], name: str) -> None:
        encoded = page.get("screenshot") if isinstance(page, dict) else None
        if self.record_dir and encoded:
            self.record_dir.mkdir(parents=True, exist_ok=True)
            self.record_dir.joinpath(name).write_bytes(base64.b64decode(encoded))

    def _backend_observe(self, screenshot=False):
        started = time.perf_counter()
        try:
            return self.browser.observe(screenshot=screenshot)
        except StalePage:
            self.metrics.record_stale()
            raise
        finally:
            self.metrics.record_backend_operation("observe", (time.perf_counter() - started) * 1000)

    def _backend_fresh(self, page):
        started = time.perf_counter()
        generation = page.get("generation") if isinstance(page, dict) else None
        try:
            result = self.browser.fresh(page)
            if result is False:
                self.metrics.record_stale()
                self._event("stale", source="fresh_check", generation=generation)
            return result
        except StalePage as error:
            self.metrics.record_stale()
            self._event("stale", source="fresh_check", generation=generation, detail=str(error))
            raise
        finally:
            self.metrics.record_backend_operation("fresh", (time.perf_counter() - started) * 1000)

    def _backend_act(self, action, page, text=None):
        started = time.perf_counter()
        try:
            return self.browser.act(action, page, text=text)
        except StalePage as error:
            self.metrics.record_stale()
            self._event("stale", source="act", generation=page.get("generation") if isinstance(page, dict) else None,
                        index=str(action.get("id")), ref=action.get("ref"), operation=str(action.get("kind")),
                        detail=str(error))
            raise
        finally:
            self.metrics.record_backend_operation("act", (time.perf_counter() - started) * 1000)

    def _event(self, event, **fields):
        timeline = getattr(self, "timeline", None)
        if timeline is not None:
            timeline.record(event, backend=getattr(self, "backend_name", None), **fields)

    def _set_elapsed(self):
        if self.state is not None:
            self.state["elapsed_ms"] = (
                round((time.perf_counter() - self.state["started_at"]) * 1000)
                if self.state["started_at"]
                else 0
            )

    def snapshot(self):
        if self.state is None:
            return {
                "page": None,
                "status": self.metrics.status,
                "history": [],
                "decision": None,
                "metrics": self.metrics.snapshot(),
            }
        return {
            **{k: v for k, v in self.state.items() if k != "browser"},
            "elements": action_space(self.state["page"]["actions"])[0] if self.state.get("page") else [],
            "metrics": self.metrics.snapshot(),
        }

    def command(self, name, body=None):
        # Keep the small __new__ based test doubles and old integrations compatible.
        if not hasattr(self, "metrics"):
            self.metrics = RunMetrics("browser_harness")
        if not hasattr(self, "record_dir"):
            self.record_dir = None
        if not hasattr(self, "screenshots"):
            self.screenshots = False
        body = body or {}
        state = self.state
        if state is None:
            raise ValueError("Agent failed before initialization")
        if not hasattr(self, "browser"):
            self.browser = state.get("browser")
        if name == "tick":
            try:
                self.command("predict", {})
                return self.command("act", {"fingerprint": state["page"]["fingerprint"]})
            except StalePage:
                state["decision"] = None
                state["stale_retries"] = state.get("stale_retries", 0) + 1
                self._event("stale_retry", generation=state["page"].get("generation"),
                            retries=state["stale_retries"], limit=MAX_STALE_RETRIES)
                if state["stale_retries"] >= MAX_STALE_RETRIES:
                    state["status"] = "blocked"
                    self._event("fuse", fuse="max_stale_retries", retries=state["stale_retries"])
                    self._finish_metrics("blocked")
                    return self.snapshot()
                state["status"] = "ready"
                state["page"] = self._backend_observe(screenshot=self.screenshots)
                self._set_elapsed()
                return self.snapshot()
        if name == "predict":
            if not state["browser"]:
                raise ValueError("Start a demo first")
            if state["started_at"] is None:
                state["started_at"] = time.perf_counter()
                self.metrics.start_policy()
            if not self._backend_fresh(state["page"]):
                state["page"] = self._backend_observe(screenshot=self.screenshots)
            state["decision"] = None
            if state["status"] in {"done", "blocked"}:
                raise ValueError("This run has stopped. Start a fresh demo.")
            if len(state["decisions"]) >= MAX_STEPS * 2:
                raise ValueError("Reached the demo's model-call budget")
            started = time.perf_counter()
            self.metrics.record_jev()
            try:
                state["decision"] = choose(state["page"], state["goal"], state["history"])
            finally:
                self.metrics.jev_total_latency_ms += round((time.perf_counter() - started) * 1000)
            state["decisions"].append(
                {
                    **state["decision"],
                    "fingerprint": state["page"]["fingerprint"],
                    "elapsed_ms": round((time.perf_counter() - state["started_at"]) * 1000),
                }
            )
            selected_action = next(
                (item for item in state["page"]["actions"] if item.get("id") == state["decision"].get("choice")),
                None,
            )
            self._event(
                "decision",
                generation=state["page"].get("generation"),
                index=state["decision"].get("target"),
                ref=(selected_action or {}).get("ref"),
                operation=state["decision"].get("operation"),
                confidence=state["decision"].get("confidence"),
                latency_ms=state["decision"].get("latency_ms"),
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
                if not self._backend_fresh(page):
                    state["status"] = "ready"
                    raise StalePage("Page changed since the decision. Choose again.")
                state["status"] = "done" if selected == "DONE" else "blocked"
                state["plan_index"] = int(selected == "DONE")
                self._set_elapsed()
                self._finish_metrics(state["status"])
                return self.snapshot()
            action = next(a for a in page["actions"] if a["id"] == selected)
            if len(state["history"]) >= MAX_STEPS:
                state["status"] = "blocked"
                self._finish_metrics("blocked")
                raise ValueError(f"Stopped at the {MAX_STEPS}-action demo budget")
            text, helper = None, None
            if action["kind"] == "fill":
                if not self._backend_fresh(page):
                    raise StalePage("Page changed before text generation. Choose again.")
                context = field_context(state["goal"], action, page, state["history"])
                if self.pending_text and self.pending_text[0] == context:
                    _, text, helper = self.pending_text
                else:
                    started = time.perf_counter()
                    try:
                        text, helper = field_text(context)
                    finally:
                        self.metrics.record_text_helper((time.perf_counter() - started) * 1000)
                    self.pending_text = (context, text, helper)
                    state["text_calls"].append({**helper, "field": action["label"], "value": text})
            self.metrics.record_action_attempt(action["kind"])
            # Browser.act checks freshness immediately before input, including after text generation.
            self._backend_act(action, page, text=text)
            self.metrics.record_action_success(action["kind"])
            state["stale_retries"] = 0
            self.pending_text = None
            self._set_elapsed()
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
            state["page"] = self._backend_observe(screenshot=self.screenshots)
            self._set_elapsed()
            state["history"][-1].update(
                page_changed=state["page"]["fingerprint"] != page["fingerprint"],
                url=state["page"]["url"],
                elapsed_ms=state["elapsed_ms"],
            )
            self._write_screenshot(state["page"], f"{state['elapsed_ms']:06d}.jpg")
            repeated = state["history"][-3:]
            state["status"] = (
                "blocked"
                if len(repeated) == 3 and all(h["page_changed"] is False and h["kind"] != "wait" for h in repeated)
                else "ready"
            )
            if state["status"] == "blocked":
                self._event("fuse", fuse="no_progress", actions=len(state["history"]))
                self._finish_metrics("blocked")
        else:
            raise ValueError("Unknown command")
        return self.snapshot()

    def run(self):
        try:
            while self.state["status"] not in {"done", "blocked", "error"}:
                yield self.command("tick")
        except GeneratorExit:
            raise
        except BaseException as error:
            if self.state is not None and self.state["status"] not in {"done", "blocked"}:
                self.state["status"] = "error"
            self._event("error", error_class=type(error).__name__, detail=str(error))
            try:
                self._finish_metrics("error", error)
            finally:
                try:
                    self.close()
                except BaseException:
                    pass
            raise
        finally:
            if self.state is not None and self.state["status"] in {"done", "blocked"}:
                self._finish_metrics(self.state["status"])

    def _safe_trace(self):
        if self.state is None:
            return {"metrics": self.metrics.snapshot()}
        return {
            "backend": self.backend_name,
            "status": self.state["status"],
            "history": [
                {k: value for k, value in item.items() if k not in {"text", "usage"}}
                for item in self.state["history"]
            ],
            "decisions": [
                {k: value for k, value in item.items() if k not in {"raw_answers", "request"}}
                for item in self.state["decisions"]
            ],
            "metrics": self.metrics.snapshot(),
        }

    def _write_artifacts(self):
        if not self.record_dir:
            return
        self.record_dir.mkdir(parents=True, exist_ok=True)
        self.metrics.write(self.record_dir / "metrics.json")
        self.record_dir.joinpath("trace.json").write_text(
            json.dumps(self._safe_trace(), indent=2, sort_keys=True) + "\n"
        )

    def _finish_metrics(self, status, error=None):
        if self.browser is not None:
            self.metrics.merge_backend(self.browser)
        self.metrics.finish(status, error)
        self._write_artifacts()

    def close(self):
        if self._closed:
            return
        self._closed = True
        error = None
        started = time.perf_counter()
        try:
            if self.browser is not None:
                self.browser.close()
        except BaseException as close_error:
            error = close_error
        finally:
            self.metrics.backend_cleanup_ms += round((time.perf_counter() - started) * 1000)
            status = self.state["status"] if self.state is not None else self.metrics.status
            if error is not None:
                status = "error"
            self._finish_metrics(status if status in {"done", "blocked", "error"} else "closed", error)
            timeline = getattr(self, "timeline", None)
            if timeline is not None:
                timeline.close()
        if error is not None:
            raise error

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
