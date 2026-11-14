"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import sys
import time
from pathlib import Path

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"

class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


class Browser:
    def __init__(self, url):
        ensure_daemon()
        self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        self.call("Page.navigate", url=url)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.evaluate("document.readyState") == "complete":
                break
            time.sleep(0.02)

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    # After an input, wait until the page stops looking busy and then stops changing. A control
    # that disables itself while a handler runs (a submit button during a slow request) is read
    # by the next snapshot as a missing action, because disabled controls are skipped, and the
    # model can only choose BLOCKED. Pure quiescence is not enough on its own — a page can be
    # stable for seconds with a timer still pending — so strong busy signals are honoured until
    # they clear, and the whole wait is capped so a page that is busy forever keeps the
    # immediate observation instead of hanging. An input that produces nothing observable
    # (no busy signal, no marker change) keeps the fast path instead of paying the quiet window.
    _BUSY_JS = """(node => {
      const c = window.__jevFast; const el = c && node != null ? c.nodes.get(node) : null;
      const vis = e => { try { const r = e.getBoundingClientRect();
        return r.width > 0 && r.height > 0 &&
          e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}); } catch (_) { return false; } };
      let weak = false;
      const selectors = '[id*="load" i],[class*="load" i],[class*="spinner" i],[class*="busy" i]';
      for (const e of document.querySelectorAll(selectors)) if (vis(e)) { weak = true; break; }
      if (el && el.matches(':disabled')) return ['acted-node-disabled', weak];
      if (document.querySelector('[aria-busy="true"]')) return ['aria-busy', weak];
      const bars = 'progress,[role="progressbar"]';
      for (const e of document.querySelectorAll(bars)) if (vis(e)) return ['progressbar', weak];
      return [null, weak];
    })"""

    # The weak indicator is an id/class heuristic, reported independently of the strong signals
    # so a strong signal present before the input cannot mask it. Real pages leave such elements
    # visible forever (duplicate ids where only the first is hidden, spinners nobody removes),
    # so it is honoured only while the page has not moved since the input, and only when it was
    # not already showing before it; after the page has moved, a lingering indicator is stale
    # rather than evidence.
    _STRONG = {"acted-node-disabled", "aria-busy", "progressbar", "stale"}

    def _settle_after_input(self, action, quiet=0.4, cap=8.0):
        node = action.get("node") if isinstance(action.get("node"), int) else None
        pre = getattr(self, "_busy_before_input", None) or (None, False)
        t0 = last_change = time.monotonic()
        marker, changes = None, -1  # first read establishes the baseline, not a change
        seen = False  # any busy signal or page change since the input
        while True:
            try:
                busy, weak = self.evaluate(self._BUSY_JS + "(" + json.dumps(node) + ")") or (None, False)
                m = self.evaluate(MARKER)
            except StalePage:
                busy, weak, m = "stale", False, None
            now = time.monotonic()
            if m != marker:
                marker, last_change, changes = m, now, changes + 1
            seen = seen or busy is not None or changes > 0
            if not busy and weak and (changes > 0 or pre[1]):
                weak = False
            if not (busy or weak):
                if not seen and now - last_change >= 0.1:
                    return {"waited_ms": round((now - t0) * 1000), "capped": False, "changes": changes}
                if now - last_change >= quiet:
                    return {"waited_ms": round((now - t0) * 1000), "capped": False, "changes": changes}
            if now - t0 >= cap:
                still = busy or weak
                return {"waited_ms": round((now - t0) * 1000), "capped": True, "still": still, "changes": changes}
            time.sleep(0.1)

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            if action.get("kind") != "wait":
                self.last_settle = self._settle_after_input(action)
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && field?.getAttribute('role')==='combobox';
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,autocomplete ? 200 : 50);
                      const ready=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        if (++frames>=2 && (!autocomplete || options.some(e=>{
                          const r=e.getBoundingClientRect();
                          return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                            e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                        }))) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))(""" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                )
            except RuntimeError:
                pass
        for attempt in range(10):
            try:
                return browser_operation(
                    {"operation": "observe", "session": self.session, "screenshot": screenshot}
                )
            except StalePage:
                if attempt == 9:
                    raise
                time.sleep(0.02)
        raise StalePage("Page did not settle")

    def fresh(self, page, action=None):
        if action is not None and action["kind"] in {"click", "select"}:
            node = action["node"]
            if type(node) is not int:
                return False
            current = self.evaluate(
                "(() => { const c=window.__jevFast; "
                f"return c ? [c.pageKey(),c.guard(c.nodes.get({node}))] : null; }})()"
            )
            return current == [page["page_key"], page["guards"].get(str(node))]
        return self.evaluate(MARKER) == page["marker"]

    def act(self, action, page, text=None):
        if not self.fresh(page, action):
            raise StalePage("Page changed since this decision. Observe again.")
        if action["kind"] == "wait":
            time.sleep(0.1)
        if action["kind"] != "wait":
            # Baseline for the settle: what already looked busy before this input.
            node = action.get("node") if isinstance(action.get("node"), int) else None
            try:
                self._busy_before_input = self.evaluate(self._BUSY_JS + "(" + json.dumps(node) + ")")
            except StalePage:
                self._busy_before_input = None
        result = browser_operation({"operation": "act", "session": self.session, "action": action, "text": text})
        self.after_input = action if action["kind"] != "wait" else None
        return result

    def close(self):
        if self.target:
            cdp("Target.closeTarget", targetId=self.target)
            self.target = None


def fingerprint(state):
    content = {k: state[k] for k in ("url", "text", "actions", "scroll")}
    return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()


def browser_operation(request):
    operation = request["operation"]
    session = request["session"]

    def call(method, **params):
        return cdp(method, session_id=session, **params)

    def evaluate(expression):
        result = call("Runtime.evaluate", expression=expression, returnByValue=True)
        if result.get("exceptionDetails"):
            if operation == "act" and request["action"]["kind"] == "select":
                raise RuntimeError("Dropdown execution was interrupted; inspect before retrying.")
            raise StalePage("Document changed during evaluation")
        return result.get("result", {}).get("value")

    if operation == "act":
        action = request["action"]
        kind = action["kind"]
        if kind == "scroll":
            call("Input.dispatchMouseEvent", type="mouseWheel", x=550, y=650, deltaX=0, deltaY=action["delta"])
        elif kind != "wait":
            if type(action["node"]) is not int:
                raise ValueError("Invalid observed node")
            # Code-owned node IDs refer to actual observed elements, never model-generated selectors.
            target = evaluate("""(action => {
              const e=window.__jevFast?.nodes.get(action.node);
              if (!e?.isConnected || e.matches(':disabled') || e.closest('[aria-disabled="true"],[inert]') ||
                  !e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})) return null;
              if (action.kind==='fill' && (e.readOnly || e.getAttribute('aria-readonly')==='true')) return null;
              const r=e.getBoundingClientRect(), x=r.x+r.width/2, y=r.y+r.height/2;
              if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;
              if (!e.contains(document.elementFromPoint(x,y))) return null;
              if (action.kind==='select') {
                if (e.tagName!=='SELECT' || ![...e.options].some(o=>o.value===action.value &&
                    !o.disabled && !o.closest('optgroup[disabled]'))) return null;
                e.value=action.value;
                e.dispatchEvent(new Event('input',{bubbles:true}));
                e.dispatchEvent(new Event('change',{bubbles:true}));
              }
              return {x,y};
            })(""" + json.dumps(action) + ")")
            if target is None:
                if kind == "select":
                    raise RuntimeError("Dropdown execution was not confirmed; inspect before retrying.")
                raise StalePage("Target changed or is covered. Observe again.")
            if kind != "select":
                x, y = target["x"], target["y"]
                for event in ("mousePressed", "mouseReleased"):
                    call("Input.dispatchMouseEvent", type=event, x=x, y=y, button="left", clickCount=1)
                if kind == "fill":
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyDown",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                        commands=["selectAll"],
                    )
                    call(
                        "Input.dispatchKeyEvent",
                        type="keyUp",
                        key="a",
                        code="KeyA",
                        modifiers=4 if sys.platform == "darwin" else 2,
                    )
                    call("Input.insertText", text=request["text"])
        return {"executed": action["id"]}

    info = evaluate(READ_STATE)
    if info is None:
        raise StalePage("Document is navigating")
    info["fingerprint"] = fingerprint(info)
    if request.get("screenshot", True):
        info["screenshot"] = call("Page.captureScreenshot", format="jpeg", quality=72)["data"]
    return info
