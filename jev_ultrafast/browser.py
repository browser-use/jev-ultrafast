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
        # Target.createTarget then attachToTarget is two separate round trips; the freshly
        # created background target can occasionally be gone by the second one ("No target
        # with given id found"). The same -32602 error can also hit the immediately-following
        # setup calls (setDeviceMetricsOverride, Page.navigate) if the session goes stale
        # before they execute. Retry the entire create+attach+setup sequence as a unit.
        last_error = None
        for attempt in range(6):
            try:
                self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
                self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
                self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
                # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
                self.call("Emulation.setFocusEmulationEnabled", enabled=True)
                self.call("Page.navigate", url=url)
                last_error = None
                break
            except RuntimeError as error:
                last_error = error
                try:
                    cdp("Target.closeTarget", targetId=getattr(self, "target", None) or "")
                except RuntimeError:
                    pass
                if attempt < 5:
                    time.sleep(0.1 * 2**attempt)  # 0.1s, 0.2s, 0.4s, 0.8s, 1.6s
        if last_error is not None:
            raise RuntimeError(
                "Could not open a Chrome tab. Make sure Chrome is running and connected "
                "(run: uv run browser-harness --doctor)."
            ) from last_error
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.evaluate("document.readyState") == "complete":
                break
            time.sleep(0.02)

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def evaluate(self, expression):
        response = self.call(
            "Runtime.evaluate", expression=expression, returnByValue=True, _response_timeout=20
        )
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const autocomplete=action.kind==='fill' && !!(field?.getAttribute('role')==='combobox' ||
                        field?.getAttribute('aria-autocomplete') ||
                        field?.getAttribute('aria-controls') ||
                        field?.getAttribute('aria-owns'));
                      // A click on an opener (aria-expanded=false or aria-haspopup) waits up to 400 ms
                      // for a dialog/grid/listbox to appear (e.g. a date-range or autocomplete overlay).
                      const opener=action.kind==='click' && !!(field?.getAttribute('aria-haspopup') ||
                        field?.getAttribute('aria-expanded')==='false');
                      // A click inside an already-open calendar waits 200 ms so an auto-advancing picker
                      // (e.g. Google Flights opening the return-date view after a departure click) can render.
                      const inCalendar=!opener && action.kind==='click' &&
                        !!field?.closest('[role="grid"],[role="dialog"]');
                      let frames=0, stopped=false;
                      const finish=()=>{stopped=true;resolve()};
                      setTimeout(finish,autocomplete ? 200 : opener ? 400 : inCalendar ? 200 : 50);
                      const check=e=>{const r=e.getBoundingClientRect();
                        return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                          e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});};
                      const ready=()=>{
                        if (stopped) return;
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        const options=roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                        const overlays=opener ?
                          [...document.querySelectorAll('[role="dialog"],[role="grid"],[role="listbox"]')] : [];
                        if (++frames>=2 && (
                          (!autocomplete && !opener && !inCalendar) ||
                          (autocomplete && options.some(check)) ||
                          (opener && overlays.some(check))
                        )) finish();
                        else requestAnimationFrame(ready);
                      };
                      requestAnimationFrame(ready);
                    }))(""" + json.dumps(action) + ")",
                    awaitPromise=True,
                    returnByValue=True,
                    _response_timeout=20,
                )
            except RuntimeError:
                pass
        # A same-target navigation to a new origin (e.g. a search submit that leaves the page for a
        # results site) can briefly invalidate the execution context, the same way the initial
        # Page.navigate in __init__ does. Wait for the document to settle again before reading it;
        # on the common case (no full navigation happened) this returns immediately.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                if self.evaluate("document.readyState") == "complete":
                    break
            except StalePage:
                pass
            time.sleep(0.05)
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
        result = browser_operation({"operation": "act", "session": self.session, "action": action, "text": text})
        self.after_input = action if action["kind"] != "wait" else None
        return result

    def close(self):
        if self.target:
            try:
                cdp("Target.closeTarget", targetId=self.target)
            except RuntimeError:
                pass
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
        # A full-page snapshot walk (READ_STATE) can be slow on a heavy page that just loaded
        # (e.g. a results page still fetching/rendering), past the 5s default IPC timeout.
        result = call("Runtime.evaluate", expression=expression, returnByValue=True, _response_timeout=20)
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
                    # A heavy page (e.g. a results page still loading right after a search submit)
                    # can leave the renderer too busy to ack a CDP command within the 5s IPC default.
                    call(
                        "Input.dispatchMouseEvent",
                        type=event,
                        x=x,
                        y=y,
                        button="left",
                        clickCount=1,
                        _response_timeout=20,
                    )
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
