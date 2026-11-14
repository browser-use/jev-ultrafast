"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import os
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
        targets_resp = cdp("Target.getTargets")
        self.initial_targets = {t["targetId"] for t in targets_resp.get("targetInfos", [])}
        self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self.owned_targets = [self.target]
        self.target_history = [self.target]
        self._configure_session(self.session)
        self.call("Page.navigate", url=url)
        self._wait_ready()

    def _configure_session(self, session_id):
        width = int(os.environ.get("VIEWPORT_WIDTH", 1120))
        height = int(os.environ.get("VIEWPORT_HEIGHT", 780))
        try:
            cdp(
                "Emulation.setDeviceMetricsOverride",
                session_id=session_id,
                width=width,
                height=height,
                deviceScaleFactor=1,
                mobile=False,
            )
            cdp("Emulation.setFocusEmulationEnabled", session_id=session_id, enabled=True)
        except Exception:
            pass

    def _wait_ready(self, deadline=15):
        end_time = time.monotonic() + deadline
        last_count = -1
        stable_since = None
        while time.monotonic() < end_time:
            try:
                info = self.evaluate("""(() => {
                    if (window.location.href === 'about:blank') return { ready: false, frames: 0, count: 0 };
                    if (document.readyState !== 'complete') return { ready: false, frames: 0, count: 0 };
                    const frames = document.querySelectorAll('iframe, frame');
                    let count = document.querySelectorAll('*').length;
                    let framesLoading = false;
                    for (const f of frames) {
                        try {
                            if (f.contentDocument) {
                                if (f.contentDocument.readyState !== 'complete') framesLoading = true;
                                count += f.contentDocument.querySelectorAll('*').length;
                            }
                        } catch (e) {}
                    }
                    return { ready: !framesLoading, frames: frames.length, count };
                })()""")
                if info and info.get("ready"):
                    if info.get("frames", 0) == 0:
                        break
                    curr_count = info.get("count", 0)
                    now = time.monotonic()
                    if curr_count == last_count and curr_count > 30:
                        if stable_since and (now - stable_since) >= 0.35:
                            break
                    else:
                        last_count = curr_count
                        stable_since = now
            except Exception:
                pass
            time.sleep(0.05)

    def _switch_to_target(self, new_target_id):
        if getattr(self, "target", None) == new_target_id and getattr(self, "session", None):
            return
        self.target = new_target_id
        if not hasattr(self, "owned_targets"):
            self.owned_targets = []
        if new_target_id not in self.owned_targets:
            self.owned_targets.append(new_target_id)
        if not hasattr(self, "target_history"):
            self.target_history = []
        if new_target_id not in self.target_history:
            self.target_history.append(new_target_id)
        self.session = cdp("Target.attachToTarget", targetId=new_target_id, flatten=True)["sessionId"]
        self._configure_session(self.session)
        try:
            cdp("Target.activateTarget", targetId=new_target_id)
        except Exception:
            pass
        self._wait_ready(deadline=5)

    def _sync_targets(self):
        try:
            targets = cdp("Target.getTargets").get("targetInfos", [])
            page_targets = {t["targetId"]: t for t in targets if t.get("type") == "page"}

            # If current target was closed, backtrack in history
            if getattr(self, "target", None) not in page_targets:
                history = getattr(self, "target_history", [])
                while history and history[-1] not in page_targets:
                    history.pop()
                if history:
                    self._switch_to_target(history[-1])
                elif page_targets:
                    self._switch_to_target(next(iter(page_targets.keys())))
                else:
                    raise StalePage("All open pages were closed")

            # Check if a new window/tab was opened from our session
            initial = getattr(self, "initial_targets", set())
            owned = getattr(self, "owned_targets", [])
            new_pages = [
                t for t in targets
                if t.get("type") == "page"
                and (
                    t.get("openerId") in owned
                    or (t["targetId"] not in initial and t["targetId"] not in owned)
                )
                and not t.get("url", "").startswith("chrome://")
            ]
            if new_pages:
                latest = new_pages[-1]
                self._switch_to_target(latest["targetId"])
        except StalePage:
            raise
        except Exception:
            pass

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def evaluate(self, expression):
        response = self.call("Runtime.evaluate", expression=expression, returnByValue=True)
        if response.get("exceptionDetails"):
            raise StalePage("Document changed during evaluation")
        return response.get("result", {}).get("value")

    def observe(self, screenshot=True):
        self._sync_targets()
        if getattr(self, "after_input", None):
            action, self.after_input = self.after_input, None
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
        result = browser_operation({"operation": "act", "session": self.session, "action": action, "text": text})
        self.after_input = action if action["kind"] != "wait" else None
        if action.get("kind") == "click":
            for _ in range(8):
                try:
                    targets = cdp("Target.getTargets").get("targetInfos", [])
                    initial = getattr(self, "initial_targets", set())
                    owned = getattr(self, "owned_targets", [])
                    has_new = any(
                        t.get("type") == "page"
                        and (
                            t.get("openerId") in owned
                            or (t["targetId"] not in initial and t["targetId"] not in owned)
                        )
                        and not t.get("url", "").startswith("chrome://")
                        for t in targets
                    )
                    if has_new:
                        self._sync_targets()
                        break
                except Exception:
                    pass
                time.sleep(0.05)
        return result

    def close(self):
        targets = set(getattr(self, "owned_targets", []))
        if getattr(self, "target", None):
            targets.add(self.target)
        for tid in targets:
            try:
                cdp("Target.closeTarget", targetId=tid)
            except Exception:
                pass
        self.target = None
        self.session = None
        if hasattr(self, "owned_targets"):
            self.owned_targets.clear()
        if hasattr(self, "target_history"):
            self.target_history.clear()


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

              let ox = 0, oy = 0, frame = null;
              let curr = e.ownerDocument;
              while (curr && curr !== document) {
                const f = curr.defaultView?.frameElement;
                if (!f) break;
                const fr = f.getBoundingClientRect();
                ox += fr.x;
                oy += fr.y;
                if (!frame) frame = f;
                curr = f.ownerDocument;
              }

              const r=e.getBoundingClientRect();
              const x = ox + r.x + r.width / 2;
              const y = oy + r.y + r.height / 2;
              if (!r.width || !r.height || x<0 || y<0 || x>=innerWidth || y>=innerHeight) return null;

              if (frame) {
                const topEl = document.elementFromPoint(x, y);
                if (!topEl || (!frame.contains(topEl) && topEl !== frame)) return null;
                const localHit = e.ownerDocument.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
                if (!localHit || !e.contains(localHit)) return null;
              } else {
                if (!e.contains(document.elementFromPoint(x,y))) return null;
              }

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
