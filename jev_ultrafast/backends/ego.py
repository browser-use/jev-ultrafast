"""Task-scoped persistent Ego Browser backend."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from ..browser import StalePage
from .transport import (
    EgoMalformedResponse,
    EgoProcessExited,
    EgoRemoteError,
    EgoTimeout,
    EgoTransport,
    EgoTransportError,
)

READ_STATE = Path(__file__).resolve().parents[1].joinpath("snapshot.js").read_text()
READ_PROBE = Path(__file__).resolve().parents[1].joinpath("probe.js").read_text().strip()
# A fill can replace the field or open an asynchronous autocomplete popup.  Wait
# until the observed action set stops changing instead of guessing one delay.
# A mutation can re-render the page well after the call returns: a fill starts
# an autocomplete popup, and a click that opens or dismisses a popup animates one
# into place.  The next observation waits, bounded and read-only, until the
# action set has been quiet for long enough to be the real page.
#
# The quiet period is measured.  Google Flights' ticket-type menu animates for
# about two seconds (31 actions -> 34 -> partial frames -> the 5-action menu),
# and the popup it dismisses stays in the tree for about 2.5s after the one-way
# search is committed.  A short "three identical reads" rule accepts those
# transitional frames because each one is stable for hundreds of milliseconds.
_SETTLE_INITIAL_MS = 200
_SETTLE_INTERVAL_MS = 200
_SETTLE_QUIET_MS = 1_200
_SETTLE_BUDGET_MS = 4_000
_WAIT_DEFAULT_MS = 500
_WAIT_MAX_MS = 3_000
# A control can be briefly re-parented by page scripts without changing the
# observed decision.  Re-read the probe a few times before calling it stale.
_PROBE_SETTLE_DELAYS_MS = (100, 200, 300)
_TARGET_KINDS = {"click", "fill", "select"}

_DISPATCHER = r"""
globalThis.__jevUltrafastRuntime ||= {};
globalThis.__jevUltrafastDispatch = async request => {
  const payload = request?.payload || {};
  const runtime = globalThis.__jevUltrafastRuntime;
  if (request?.op === "init") {
    if (!runtime.task) {
      runtime.task = await taskSpace(String(payload.task_name));
      runtime.page = runtime.task.page(String(payload.page_label || "p1"));
      await runtime.page.goto(String(payload.url), {
        waitUntil: "domcontentloaded",
        timeout: Number(payload.timeout_ms || 15000),
      });
    }
    return {
      space_id: runtime.task.spaceId,
      page_label: runtime.page.label,
      page_target_id: runtime.page.targetId,
    };
  }
  if (!runtime.page) throw new Error("Ego task space is not initialized");
  if (request?.op === "observe") {
    const state = await runtime.page.evaluate(globalThis.__jevReadState);
    if (!state) throw new Error("Ego page is navigating");
    const semantic = await runtime.page.snapshot({scope: "only_within_viewport"});
    let screenshot = null;
    if (payload.screenshot) {
      const fs = await import("node:fs/promises");
      const path = `/tmp/jev-ultrafast-${Date.now()}-${Math.random().toString(16).slice(2)}.jpg`;
      try {
        await runtime.page.screenshot({path, scale: "css"});
        screenshot = (await fs.readFile(path)).toString("base64");
      } finally {
        await fs.unlink(path).catch(() => {});
      }
    }
    return {
      state,
      semantic,
      screenshot,
      url: await runtime.page.url(),
      title: await runtime.page.title(),
    };
  }
  if (request?.op === "fresh") {
    const probe = await runtime.page.evaluate(
      "(" + globalThis.__jevProbeSource + ")(" + JSON.stringify({nodes: payload.nodes || []}) + ")",
    );
    if (!probe) throw new Error("Ego page is navigating");
    return {probe};
  }
  if (request?.op === "act") {
    const action = payload.action || {};
    const ref = action.ref;
    const label = String(action.label || action.kind || "browser action");
    /* The observed action set, as a cheap identity for "what the page shows
       right now".  It is only compared with itself, never acted on. */
    const readActionSignature = async (page) => {
      const state = await page.evaluate(globalThis.__jevReadState).catch(() => null);
      if (!state) return "navigating";
      return JSON.stringify((state.actions || []).map((item) => item.id || item.label || item.role || ""));
    };
    if (action.kind === "click") {
      if (!ref) throw new Error("unknown ref");
      const before = await runtime.page.url().catch(() => "");
      const signature = await readActionSignature(runtime.page);
      await runtime.page.click(ref, {label});
      const after = await runtime.page.url().catch(() => "");
      if (after && after !== before) {
        /* A click that changes the URL replaces the view.  The document it
           navigated away from can stay in the tree for seconds — Google
           Flights keeps its ticket-type popup for ~2.5s after the one-way
           search is committed — and that popup is a *stable* frame, so
           waiting for stability alone would return it.  Wait instead until
           the observation actually reflects the navigation, bounded. */
        const deadline = Date.now() + 2500;
        while (Date.now() < deadline) {
          await runtime.page.waitForTimeout(150);
          if ((await readActionSignature(runtime.page)) !== signature) break;
        }
      }
    } else if (action.kind === "fill") {
      if (!ref) throw new Error("unknown ref");
      await runtime.page.fill(ref, String(payload.text ?? ""), {clearFirst: true});
      /* Typing commonly starts an asynchronous autocomplete that re-renders
         the surrounding form.  Let that activity finish before the next
         observation, so the decision is made on the settled page. */
      await runtime.page.waitForLoadState("networkidle", {timeout: 1200, idleMs: 300}).catch(() => {});
    } else if (action.kind === "select") {
      if (!ref) throw new Error("unknown ref");
      const option = String(action.value ?? "");
      const marker = String(action.label || "").lastIndexOf(" → ");
      const choice = option ? {value: option} : marker >= 0 ? {label: action.label.slice(marker + 3)} : option;
      await runtime.page.selectOption(ref, choice);
    } else if (action.kind === "scroll") {
      await runtime.page.mouse.wheel(0, Number(action.delta || 0), {label});
    } else if (action.kind === "wait") {
      const wait = Math.min(Math.max(Number(action.wait_ms || payload.wait_ms || 500), 50), 3000);
      await runtime.page.waitForTimeout(wait);
      await runtime.page.waitForLoadState("load", {timeout: 1500}).catch(() => {});
    } else {
      throw new Error(`unsupported action kind: ${String(action.kind)}`);
    }
    return {executed: action.id || action.kind};
  }
  if (request?.op === "evaluate") {
    /* A code-owned, read-only DOM read.  It exists for independent outcome
       verification after a run (and for the benchmark validator); the agent
       loop never sends model output here. */
    const expression = String(payload.expression || "");
    if (!expression) throw new Error("evaluate needs an expression");
    return {value: await runtime.page.evaluate(expression)};
  }
  if (request?.op === "finish") {
    if (runtime.task) return await runtime.task.finish({keep: []});
    return null;
  }
  throw new Error(`unsupported Ego operation: ${String(request?.op)}`);
};
"""


_CONTROL_ROLES = {
    "anchor": "link",
    "button": "button",
    "checkbox": "checkbox",
    "combobox": "combobox",
    # Ego names an input that owns a popup list by its composite role, while
    # the page itself declares role="combobox".
    "comboboxgrouping": "combobox",
    "gridcell": "gridcell",
    "link": "link",
    "listboxoption": "option",
    "menuitem": "menuitem",
    "menuitemradio": "menuitemradio",
    "option": "option",
    "radio": "radio",
    "searchbox": "searchbox",
    "spinbutton": "spinbutton",
    "switch": "switch",
    "tab": "tab",
    "textbox": "textbox",
    "treeitem": "treeitem",
}
_ROLE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_-]*)\b")
# Ego prints the quoted accessible name before the attribute block, so a name
# containing " @word" (for example `button "Email us @support" [ref=7]`) must not
# be mistaken for a ref. Prefer the authoritative ref= attribute and only then a
# bare numeric @N, which is only valid inside the bracketed attribute block.
_REF_RE = re.compile(r"ref=(@?[A-Za-z0-9_-]+)")
_BARE_REF_RE = re.compile(r"\[[^\]]*?@(\d+)\b")
_QUOTE_RE = re.compile(r'\s"((?:\\.|[^"\\])*)"')


def _ref_match(line: str):
    """Return the ref match for one Ego snapshot line, attribute form first."""
    match = _REF_RE.search(line)
    if match:
        return match
    return _BARE_REF_RE.search(line)


def _unquote(value: str) -> str:
    try:
        return str(json.loads('"' + value + '"'))
    except (TypeError, ValueError):
        return value.replace('\\"', '"')


def _label_relates(action_label: str, candidate_label: str) -> bool:
    """True when a semantic node may name this control.

    Ego's state labels describe the control's job ("Change ticket type. Round
    trip") while the semantic tree names what it shows ("Round trip").  A
    nameless candidate never contradicts, and one name containing the other
    (both already normalized to single-spaced casefolded text) is the same
    control.  Two unrelated names must not match: attaching the wrong ref would
    click the wrong element, which is worse than leaving the target unmapped.
    """
    candidate = _norm(candidate_label)
    if not candidate:
        return True
    action = _norm(action_label)
    return action == candidate or candidate in action or action in candidate


def _snapshot_nodes(snapshot: str | None) -> list[dict[str, str]]:
    """Extract only code-owned refs and names from Ego's semantic snapshot."""
    if not isinstance(snapshot, str):
        return []
    lines = snapshot.splitlines()
    nodes: list[dict[str, str]] = []
    for index, line in enumerate(lines):
        match = _ROLE_RE.match(line)
        if not match:
            continue
        raw_role = match.group(1).lower()
        role = _CONTROL_ROLES.get(raw_role)
        if role is None:
            continue
        ref_match = _ref_match(line)
        if not ref_match:
            continue
        label_match = _QUOTE_RE.search(line)
        label = _unquote(label_match.group(1)) if label_match else ""
        indent = len(line) - len(line.lstrip())
        child_text: list[str] = []
        if not label:
            for child in lines[index + 1 :]:
                child_indent = len(child) - len(child.lstrip())
                if child.strip() and child_indent <= indent:
                    break
                text_match = re.match(r'^\s*text\s+"((?:\\.|[^"\\])*)"', child)
                if text_match:
                    child_text.append(_unquote(text_match.group(1)))
        if child_text:
            label = " ".join(child_text).strip()
        attrs = ref_match.group(0).lstrip("[")
        value_match = re.search(r'value="((?:\\.|[^"\\])*)"', line)
        nodes.append(
            {
                "role": role,
                "ref": "@" + ref_match.group(1).lstrip("@"),
                "label": label,
                "value": _unquote(value_match.group(1)) if value_match else "",
                "attrs": attrs,
            }
        )
    return nodes


def _norm(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _state_fingerprint(state: dict[str, Any]) -> str:
    # ``ref`` is an adapter detail.  Ego's semantic ref is re-created by each
    # snapshot, while Jev freshness is defined by the observed page meaning.
    actions = [
        {key: value for key, value in action.items() if key not in {"ref", "rect"}}
        for action in state.get("actions", [])
        if isinstance(action, dict)
    ]
    content = {
        "url": state.get("url"),
        "text": state.get("text"),
        "actions": actions,
        "scroll": state.get("scroll"),
    }
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _action_signature(page: dict[str, Any]) -> str:
    actions = [
        {key: value for key, value in action.items() if key not in {"id", "ref", "rect"}}
        for action in page.get("actions", [])
        if isinstance(action, dict)
    ]
    guards = {}
    for node, guard in page.get("guards", {}).items():
        # Keep code-owned identity and target state, while ignoring nearby
        # scope text that can change as autocomplete renders.
        guards[str(node)] = guard[:-1] if isinstance(guard, list) else guard
    content = {"url": page.get("url"), "actions": actions, "guards": guards}
    return hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _target_guard(guard: Any) -> Any:
    """Keep the code-owned part of a guard and drop nearby container text.

    The trailing scope text is the nearest form/list/article text.  It changes
    for reasons that have nothing to do with the observed target, so comparing
    it turned ordinary page churn into a stale decision.
    """
    return guard[:-1] if isinstance(guard, list) else guard


# Ego resolves a target before it touches the page, so a resolution failure
# proves no input happened and the decision is safely re-obtainable.
_RESOLUTION_MARKERS = (
    "unknown ref",
    "invalid ref",
    "take a new snapshot",
    "matched 0 elements",
    "no matching element",
    "no actionable",
    "elementresolutionerror",
    "strict mode violation",
)
# The action reached the page and failed there.  Replaying it as "stale" would
# both hide the real fault and risk repeating a partial mutation.
_ACTION_MARKERS = (
    "page.fill failed",
    "page.click failed",
    "page.selectoption failed",
    "page.hover failed",
    "page.dblclick failed",
    "page.draganddrop failed",
    "page.press failed",
    "is not an input",
    "not an input, textarea, or contenteditable",
    "intercepts pointer events",
    "outside of the viewport",
    "element is not visible",
    "timed out after",
    "could not confirm",
    "could not verify",
    "unsupported action kind",
    "not editable",
)
_NAVIGATION_MARKERS = ("page is navigating", "document is navigating", "execution context")
# The target element stopped existing.  That is the page changing under the
# decision, so the loop re-observes and re-decides; it never replays the
# mutation.  Reported as stale with the raw Ego message in the timeline.
_DETACHED_MARKERS = (
    "element is not connected",
    "no longer attached",
    "not attached to the dom",
    "element is detached",
)


class EgoActionError(RuntimeError):
    """The page rejected an executed action; this is not a stale target."""


def classify_ego_error(error: BaseException, *, phase: str) -> BaseException:
    """Map one Ego failure to stale, action, or transport semantics.

    ``phase`` is ``"probe"`` for the freshness check and ``"act"`` for the
    mutation itself, because the same message means different things depending
    on whether any input was attempted.
    """
    if isinstance(error, EgoRemoteError):
        name = str(getattr(error, "ego_name", "") or "").casefold()
        message = str(error).casefold()
        if any(marker in message for marker in _NAVIGATION_MARKERS):
            return StalePage(str(error))
        if any(marker in message for marker in _DETACHED_MARKERS):
            return StalePage(str(error))
        # Ego resolves a target before it touches the page, so a resolution
        # failure proves that no input happened and the decision is stale.
        if any(marker in message for marker in _RESOLUTION_MARKERS) or name == "elementresolutionerror":
            return StalePage(str(error))
        if phase == "probe":
            if any(marker in message for marker in _ACTION_MARKERS):
                return EgoActionError(str(error))
            return StalePage(str(error))
        return EgoActionError(str(error))
    if isinstance(error, EgoTransportError):
        return error
    return EgoTransportError(f"{type(error).__name__}: {error}")


class EgoBrowserBackend:
    """One Ego TaskSpace/Page reused by every observe and action in a task."""

    backend_name = "ego"

    def __init__(
        self,
        url: str,
        *,
        transport: EgoTransport | Any | None = None,
        task_name: str | None = None,
        timeout_ms: int = 15_000,
        page_label: str = "p1",
    ) -> None:
        self.url = url
        self.timeout_ms = timeout_ms
        self.page_label = page_label
        # Every backend instance creates its own Ego TaskSpace, so a named
        # space is still isolated.  A benchmark can name the space per run so
        # the evidence records which space belonged to which run; the default
        # stays unique and never touches a space a person is using.
        self.task_name = task_name or os.environ.get("ULTRAFAST_EGO_TASK_NAME") or f"ultrafast:{uuid.uuid4().hex}"
        self.transport = transport or EgoTransport(timeout_ms=timeout_ms)
        self._generation = 0
        self._current_page: dict[str, Any] | None = None
        self._pending_settle = False
        self._closed = False
        self._init_result: dict[str, Any] = {}
        self.timeline: Any | None = None
        self.timings: dict[str, int] = {"startup_ms": 0, "cleanup_ms": 0}
        self.ego_timings: dict[str, Any] = {
            "observe": {"count": 0, "total_ms": 0, "max_ms": 0, "last_ms": 0},
            "fresh": {"count": 0, "total_ms": 0, "max_ms": 0, "last_ms": 0},
            "act": {"count": 0, "total_ms": 0, "max_ms": 0, "last_ms": 0},
            "ego_total_operation_ms": 0,
        }
        self.cleanup_status: dict[str, bool | None] = {
            "graceful_exit": None,
            "forced_terminate": None,
            "forced_kill": None,
            "orphan_process_remaining": None,
        }
        self.runtime_spawn_count = 0
        started = time.perf_counter()
        try:
            if hasattr(self.transport, "start"):
                try:
                    self.transport.start(self._dispatcher_source())
                except TypeError:
                    self.transport.start()
            self.runtime_spawn_count = int(getattr(self.transport, "runtime_spawn_count", 1))
            self._init_result = self._request_raw(
                "init",
                {"url": url, "task_name": self.task_name, "page_label": page_label, "timeout_ms": timeout_ms},
            ) or {}
        except BaseException:
            self.close()
            raise
        finally:
            self.timings["startup_ms"] = round((time.perf_counter() - started) * 1000)

    @staticmethod
    def _dispatcher_source() -> str:
        # The interactive REPL treats a physical newline as the end of a
        # submission.  Keep the bootstrap one line even though the source
        # above is formatted for maintenance.
        dispatcher = re.sub(r"\s+", " ", _DISPATCHER).strip()
        return (
            f"globalThis.__jevReadState={json.dumps(READ_STATE)};"
            f"globalThis.__jevProbeSource={json.dumps(READ_PROBE)};"
            f"{dispatcher}"
        )

    def _request_raw(self, operation: str, payload: dict[str, Any] | None = None) -> Any:
        return self.transport.request(operation, payload or {}, timeout_ms=self.timeout_ms)

    def _request_timed(self, operation: str, payload: dict[str, Any] | None = None) -> Any:
        started = time.perf_counter()
        try:
            result = self._request_raw(operation, payload)
            return result
        finally:
            elapsed = round((time.perf_counter() - started) * 1000)
            item = self.ego_timings.setdefault(operation, {"count": 0, "total_ms": 0, "max_ms": 0, "last_ms": 0})
            if isinstance(item, dict):
                item["count"] += 1
                item["total_ms"] += elapsed
                item["max_ms"] = max(item["max_ms"], elapsed)
                item["last_ms"] = elapsed
            self.ego_timings["ego_total_operation_ms"] += elapsed

    def _normalize_page(self, response: dict[str, Any]) -> dict[str, Any]:
        state = response.get("state", response)
        if not isinstance(state, dict):
            raise EgoMalformedResponse("Ego observation did not contain a state object")
        page = dict(state)
        semantic = response.get("semantic") if isinstance(response, dict) else None
        actions = [dict(action) for action in page.get("actions", []) if isinstance(action, dict)]
        refs = _snapshot_nodes(semantic)
        by_node: dict[str, str] = {}
        used: set[str] = set()
        for action in actions:
            if action.get("ref"):
                continue
            node_key = str(action.get("node", ""))
            if node_key in by_node:
                action["ref"] = by_node[node_key]
                continue
            role = str(action.get("role", "")).casefold()
            label = _norm(action.get("label", ""))
            base_label = _norm(str(action.get("label", "")).split(" → ", 1)[0])
            value = _norm(action.get("value", ""))
            candidates = [item for item in refs if item["role"] == role]
            if not candidates and label:
                # A control can carry an explicit ARIA role that Ego renders
                # under a different name (an option rendered as an anchor, for
                # example).  Fall back to an unambiguous label match so the
                # decision still gets a code-owned ref instead of going stale.
                candidates = [item for item in refs if _norm(item["label"]) == label]
            if label and candidates:
                # Prefer a candidate whose name agrees with the control instead
                # of attaching a ref that contradicts it.  Ego's semantic names
                # are often the field's own placeholder ("City or route" for the
                # page's "Destination city"), so an unrelated name is ranked
                # last rather than rejected: the node is usually still the same
                # live element, and leaving it unmapped would make a real
                # control impossible to use.
                candidates = sorted(candidates, key=lambda item: not _label_relates(label, item["label"]))
            ranked = sorted(
                candidates,
                key=lambda item: (
                    item["ref"] in used,
                    _norm(item["label"]) != label,
                    bool(base_label) and base_label not in _norm(item["label"]),
                    bool(value) and value != _norm(item["value"]),
                ),
            )
            if ranked:
                selected = ranked[0]
                action["ref"] = selected["ref"]
                by_node[node_key] = selected["ref"]
                used.add(selected["ref"])
        offered: list[dict[str, Any]] = []
        unmapped: list[str] = []
        for action in actions:
            # A target this adapter cannot act on must never be offered as a
            # choice: selecting it can only burn a stale retry and fuse the run.
            # It stays visible as evidence of what the page contained.
            if action.get("kind") in _TARGET_KINDS and not action.get("ref"):
                unmapped.append(str(action.get("label") or action.get("id") or ""))
            else:
                offered.append(action)
        page["actions"] = offered
        page["unmapped_targets"] = unmapped
        page.setdefault("url", response.get("url", self.url))
        page.setdefault("title", response.get("title", ""))
        page.setdefault("text", "")
        page.setdefault("scroll", {"y": 0, "height": 0})
        page.setdefault("guards", {})
        page["guards"] = {str(key): _target_guard(value) for key, value in page["guards"].items()}
        page["fingerprint"] = page.get("fingerprint") or _state_fingerprint(page)
        page["generation"] = self._generation
        page["ego_space_id"] = self._init_result.get("space_id")
        page["ego_page_label"] = self.page_label
        screenshot = response.get("screenshot") if isinstance(response, dict) else None
        if isinstance(screenshot, str) and screenshot:
            page["screenshot"] = screenshot
        return page

    def _observe_page(self, screenshot: bool = False) -> dict[str, Any]:
        response = None
        for attempt in range(10):
            try:
                response = self._request_timed("observe", {"screenshot": bool(screenshot)})
                break
            except EgoRemoteError as error:
                if not _looks_navigation(str(error)) or attempt == 9:
                    if _looks_navigation(str(error)):
                        raise StalePage(str(error)) from error
                    raise
                # Read-only settle retry. Browser mutations never use this
                # loop, so a late action cannot be replayed accidentally.
                time.sleep(0.02)
        if not isinstance(response, dict):
            raise EgoMalformedResponse("Ego observe response is not an object")
        return self._normalize_page(response)

    def _settled_observe(self, screenshot: bool) -> dict[str, Any]:
        # A mutation can replace the view or animate a popup well after the call
        # returns.  Observe until the action set has been unchanged for the quiet
        # period, so the next decision is made on the page as it ends up and not
        # on a transitional frame with a partial action list.
        deadline = time.monotonic() + _SETTLE_BUDGET_MS / 1000
        time.sleep(_SETTLE_INITIAL_MS / 1000)
        page = self._observe_page(screenshot=screenshot)
        previous = _action_signature(page)
        changed_at = time.monotonic()
        while time.monotonic() - changed_at < _SETTLE_QUIET_MS / 1000:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(_SETTLE_INTERVAL_MS / 1000, remaining))
            candidate = self._observe_page(screenshot=screenshot)
            signature = _action_signature(candidate)
            if signature != previous:
                changed_at = time.monotonic()
                previous = signature
            page = candidate
        return page

    def observe(self, screenshot: bool = False) -> dict[str, Any]:
        if self._closed:
            raise EgoTransportError("Ego backend is closed")
        if self._pending_settle:
            page = self._settled_observe(screenshot)
            self._pending_settle = False
        else:
            page = self._observe_page(screenshot)
        self._generation += 1
        page["generation"] = self._generation
        self._current_page = page
        mapped = sum(1 for action in page["actions"] if action.get("ref"))
        unmapped = len(page.get("unmapped_targets") or [])
        self._event(
            "observe",
            generation=self._generation,
            url=page.get("url"),
            title=page.get("title"),
            actions=len(page["actions"]),
            mapped_refs=mapped,
            unmapped_targets=unmapped,
        )
        return page

    def _probe(self, page: dict[str, Any], nodes: list[int]) -> dict[str, Any]:
        response = self._request_timed("fresh", {"nodes": nodes, "observed_epoch": page.get("epoch")})
        payload = response.get("probe") if isinstance(response, dict) else None
        if not isinstance(payload, dict):
            raise EgoMalformedResponse("Ego freshness probe returned no state")
        return payload

    def _navigated(self, page: dict[str, Any], probe: dict[str, Any]) -> bool:
        identity = probe.get("identity")
        if not isinstance(identity, dict):
            return True
        return identity.get("epoch") != page.get("epoch") or identity.get("url") != page.get("url")

    def _target_reason(self, page: dict[str, Any], target: dict[str, Any], probe: dict[str, Any]) -> str:
        node_key = str(target["node"])
        entry = (probe.get("nodes") or {}).get(node_key)
        if not isinstance(entry, dict):
            return "target_detached"
        if entry.get("guard") != page.get("guards", {}).get(node_key):
            return "target_changed"
        if not entry.get("actionable"):
            return "not_actionable"
        if target.get("kind") == "fill" and not entry.get("writable"):
            return "not_writable"
        return "ok"

    def fresh(self, page: dict[str, Any], action: dict[str, Any] | None = None) -> bool:
        """Check the observed decision without rebuilding Ego's ref map.

        Same tab, same document, and (for a targeted decision) the same live,
        usable element.  Unrelated page text, layout, or scroll movement no
        longer invalidates a decision.
        """
        generation = page.get("generation") if isinstance(page, dict) else None
        url = page.get("url") if isinstance(page, dict) else None
        if self._closed or not isinstance(page, dict) or generation != self._generation:
            self._event("fresh", generation=generation, url=url, result=False, reason="generation")
            return False
        target = action if isinstance(action, dict) and action.get("kind") in _TARGET_KINDS else None
        if target is not None and (target.get("node") is None or not target.get("ref")):
            self._event("fresh", generation=generation, url=url, index=str(target.get("id")),
                        operation=str(target.get("kind")), result=False, reason="missing_ref")
            return False
        nodes = [target["node"]] if target is not None else []
        try:
            probe = self._probe(page, nodes)
        except (EgoRemoteError, EgoTransportError) as error:
            raise classify_ego_error(error, phase="probe") from error
        if self._navigated(page, probe):
            self._event("fresh", generation=generation, url=url, result=False, reason="navigation")
            return False
        if target is None:
            self._event("fresh", generation=generation, url=url, result=True, scope="document")
            return True
        event = {
            "generation": generation,
            "url": url,
            "index": str(target.get("id")),
            "ref": target.get("ref"),
            "operation": str(target.get("kind")),
        }
        reason = self._target_reason(page, target, probe)
        if reason != "ok":
            # A page can briefly move or re-render a control around the input
            # without replacing the observed decision.  Re-read a few times,
            # bounded, and never replay a mutation to do it.
            for delay_ms in _PROBE_SETTLE_DELAYS_MS:
                time.sleep(delay_ms / 1000)
                probe = self._probe(page, nodes)
                if self._navigated(page, probe):
                    reason = "navigation"
                    break
                reason = self._target_reason(page, target, probe)
                if reason == "ok":
                    break
        if reason != "ok":
            self._event("fresh", result=False, reason=reason, **event)
            return False
        self._event("fresh", result=True, scope="target", **event)
        return True

    def act(self, action: dict[str, Any], page: dict[str, Any], text: str | None = None) -> Any:
        if action.get("kind") in {"done", "blocked"}:
            return {"executed": action.get("id", action.get("kind"))}
        if self._closed:
            raise EgoTransportError("Ego backend is closed")
        if page.get("generation") != self._generation:
            raise StalePage("This decision belongs to an older observation. Observe again.")
        if action.get("kind") in _TARGET_KINDS and not action.get("ref"):
            self._event("stale", generation=page.get("generation"), index=str(action.get("id")),
                        operation=str(action.get("kind")), source="act", reason="missing_ref")
            raise StalePage("Observed Ego target has no valid ref")
        if not self.fresh(page, action):
            self._event("stale", generation=page.get("generation"), url=page.get("url"),
                        index=str(action.get("id")), ref=action.get("ref"), operation=str(action.get("kind")),
                        source="act", reason="fresh_check_failed")
            raise StalePage("Page changed since this decision. Observe again.")
        try:
            result = self._request_timed(
                "act",
                {"action": {key: value for key, value in action.items() if key != "rect"}, "text": text},
            )
            if action.get("kind") in {"fill", "click", "select"}:
                # A click that dismisses a popup, and a select that closes one,
                # leave the overlay in the tree for a moment: the very next
                # observation would otherwise be a transitional frame.
                self._pending_settle = True
            self._event("act", generation=page.get("generation"), url=page.get("url"), index=str(action.get("id")),
                        ref=action.get("ref"), operation=str(action.get("kind")), result="success")
            return result
        except (EgoRemoteError, EgoProcessExited, EgoTimeout, EgoMalformedResponse) as error:
            classified = classify_ego_error(error, phase="act")
            self._event("act", generation=page.get("generation"), url=page.get("url"), index=str(action.get("id")),
                        ref=action.get("ref"), operation=str(action.get("kind")), result="error",
                        error_class=type(classified).__name__, detail=str(error))
            if isinstance(classified, StalePage):
                self._event("stale", generation=page.get("generation"), url=page.get("url"),
                            index=str(action.get("id")), ref=action.get("ref"), operation=str(action.get("kind")),
                            source="act", error_class="StalePage", detail=str(error))
            raise classified from error

    def evaluate_script(self, expression: str) -> Any:
        """Run one code-owned, read-only expression and return its JSON value.

        Used for independent outcome verification outside the decision loop.
        ``Browser.evaluate`` is the equivalent on the Browser Harness backend.
        """
        if self._closed:
            raise EgoTransportError("Ego backend is closed")
        response = self._request_timed("evaluate", {"expression": str(expression)})
        if isinstance(response, dict) and "value" in response:
            return response["value"]
        raise EgoMalformedResponse("Ego evaluate response contained no value")

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        started = time.perf_counter()
        try:
            # Only a runtime that actually finished init owns a live REPL. After a
            # failed construction the transport has never started, and sending
            # finish would lazily spawn the CLI a second time.
            if self._init_result and hasattr(self.transport, "request"):
                try:
                    self.transport.request("finish", {}, timeout_ms=min(self.timeout_ms, 2_000))
                except BaseException:
                    pass
            if hasattr(self.transport, "close"):
                self.transport.close()
        finally:
            self.timings["cleanup_ms"] = round((time.perf_counter() - started) * 1000)
            status = getattr(self.transport, "cleanup_status", None)
            if isinstance(status, dict):
                self.cleanup_status.update(status)
            self.runtime_spawn_count = int(
                getattr(self.transport, "runtime_spawn_count", self.runtime_spawn_count)
            )

    @property
    def orphan_process_remaining(self) -> bool | None:
        return self.cleanup_status.get("orphan_process_remaining")

    @property
    def graceful_exit(self) -> bool | None:
        return self.cleanup_status.get("graceful_exit")

    @property
    def forced_terminate(self) -> bool | None:
        return self.cleanup_status.get("forced_terminate")

    @property
    def forced_kill(self) -> bool | None:
        return self.cleanup_status.get("forced_kill")

    def attach_timeline(self, timeline: Any | None) -> None:
        """Record ref/stale lifecycle events into an optional diagnostic timeline."""
        self.timeline = timeline

    def _event(self, event: str, **fields: Any) -> None:
        timeline = getattr(self, "timeline", None)
        if timeline is not None:
            timeline.record(event, backend=self.backend_name, **fields)

    def __enter__(self) -> "EgoBrowserBackend":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _looks_navigation(message: str) -> bool:
    value = message.casefold()
    return any(marker in value for marker in _NAVIGATION_MARKERS)


__all__ = [
    "EgoActionError",
    "EgoBrowserBackend",
    "EgoMalformedResponse",
    "EgoProcessExited",
    "EgoRemoteError",
    "EgoTimeout",
    "EgoTransport",
    "EgoTransportError",
    "classify_ego_error",
]
