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

    def observe(self, screenshot=True):
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
            # This is read-only and happens after execution was logged, even if navigation interrupts it.
            try:
                self.call(
                    "Runtime.evaluate",
                    expression="""(action => new Promise(resolve => {
                      const field=window.__jevFast?.nodes.get(action.node);
                      const combobox=field?.getAttribute('role')==='combobox';
                      const autocomplete=action.kind==='fill' && combobox;
                      let ticks=0, stopped=false, rafId=0, timerId=0;
                      const start=performance.now();
                      const finish=()=>{stopped=true;resolve()};
                      const laid=e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0};
                      const visible=e=>{
                        const r=e.getBoundingClientRect();
                        return r.width && r.height && r.bottom>0 && r.top<innerHeight &&
                          e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
                      };
                      const scopedOptions=()=>{
                        const ids=(field?.getAttribute('aria-controls')||field?.getAttribute('aria-owns')||'')
                          .split(/\\s+/).filter(Boolean);
                        const roots=ids.length ? ids.map(id=>document.getElementById(id)).filter(Boolean) : [document];
                        return roots.flatMap(root=>[...root.querySelectorAll('[role="option"]')]);
                      };
                      const portalOpen=()=>field?.getAttribute('aria-expanded')==='true';
                      const portalOptions=()=>{
                        // Menus often render into a body-level portal whose real listbox is NOT
                        // the aria-controls target (observed on Google Flights: aria-controls
                        // names an empty span while the options live in a detached ul). When the
                        // clicked control reports itself expanded, scan for popup items globally.
                        if (!portalOpen()) return [];
                        return [...document.querySelectorAll(
                          '[role="option"],[role="menuitem"],[role="menuitemradio"],[role="menuitemcheckbox"]')]
                          .filter(e=>e.closest('[role="listbox"],[role="menu"]'));
                      };
                      const tick=()=>{
                        if (stopped) return;
                        cancelAnimationFrame(rafId); clearTimeout(timerId);
                        const scoped=scopedOptions();
                        const candidates=(autocomplete||scoped.length) ? scoped : portalOptions();
                        const laidOut=candidates.filter(laid);
                        // Laid-out but invisible items are a popup mid-fade. Fades are driven by
                        // rendering frames, and background tabs throttle rAF (~2 fps on Linux;
                        // focus emulation does not lift it), so a 0.25 s transition can span more
                        // than a wall-clock second. Observing during the fade would see an empty
                        // popup and wrongly treat the goal as BLOCKED. The generous cap only
                        // applies to a clicked popup that still reports itself open, so leftover
                        // hidden items from an already-closed menu — and the typing/autocomplete
                        // path, which keeps its own 200 ms budget — never stall the observation.
                        const fading=!autocomplete && portalOpen() && laidOut.length>0 && !laidOut.some(visible);
                        // Popup items can also be created asynchronously after the click: the
                        // trigger already reports aria-expanded=true while the portal is still
                        // empty (and the page behind it already aria-hidden). Give an expanded
                        // popup control a short appearance grace before observing an empty menu.
                        const awaitingPopup=!autocomplete && !fading && laidOut.length===0 && portalOpen();
                        const elapsed=performance.now()-start;
                        // Caps are bounded but generous: on rAF-throttled background tabs a
                        // sub-second CSS fade was measured to span 1.5-2.5 s of wall-clock time.
                        const cap=fading ? 3000 : (awaitingPopup ? 1000 : (autocomplete ? 200 : 50));
                        if (elapsed>=cap) finish();
                        else if (ticks++>=1 && !fading && !awaitingPopup &&
                                 (!autocomplete || scoped.some(visible))) finish();
                        else {
                          // Poll from a timer as well as rAF; whichever fires first cancels the
                          // other. A throttled tab keeps checking, a foreground tab keeps frame rate.
                          rafId=requestAnimationFrame(tick);
                          timerId=setTimeout(tick,50);
                        }
                      };
                      tick();
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
