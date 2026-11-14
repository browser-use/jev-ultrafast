"""Observed actions through Browser Harness; one CDP session, no per-step subprocess."""

import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path

from browser_harness.admin import ensure_daemon
from browser_harness.helpers import cdp

# Atomically read visible content and controls, preserving actual DOM node identity.
READ_STATE = Path(__file__).with_name("snapshot.js").read_text()
MARKER = f"(() => {{ const state={READ_STATE}; return state?.marker ?? null; }})()"
# A changed page counts as settled once the network has been quiet this long.
SETTLE_S = 0.3
# A navigation that started this long ago without a new document is treated as stalled, not in flight.
NAVIGATION_GRACE_MS = 15000


def wait_limits():
    """(quiet timeout, hard cap), read when a WAIT runs so a .env loaded after import still applies: how long a
    WAIT gives a quiet page to change, and how long it may wait while the page is busy."""
    return _seconds("JEV_WAIT_TIMEOUT", 3), _seconds("JEV_WAIT_MAX", 15)


def _seconds(name, default):
    raw = os.environ.get(name, "").strip()
    try:
        value = float(raw) if raw else float(default)
    except ValueError:
        value = float("nan")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be a positive number of seconds, not {raw!r}")
    return value


# Whether this document or any frame it can read has requests in flight or a navigation under way.
BUSY = f"""(() => {{
  const busy = w => (w.__jevInflight || 0) > 0 ||
    (!!w.__jevNavigating && Date.now() - w.__jevNavigating < {NAVIGATION_GRACE_MS});
  const frames = [window];
  for (let i = 0; i < frames.length; i++)
    for (let j = 0; j < frames[i].frames.length; j++) {{
      try {{ void frames[i].frames[j].document; frames.push(frames[i].frames[j]); }} catch (_) {{}}
    }}
  return frames.some(w => {{ try {{ return busy(w); }} catch (_) {{ return false; }} }});
}})()"""


# Counts the page's own fetch/XHR requests in flight, and marks a navigation that has started (a form submit,
# a redirect) until the next document replaces this one, so WAIT can tell "loading" from "stuck".
# A fetch counts until its promise settles, which is when the response headers arrive; a large body may still be
# streaming. Counting body reads instead would leave the counter stuck whenever a page never reads a body, so this
# accepts the earlier signal: WAIT still needs the page itself to change and stay quiet for SETTLE_S.
TRACK_REQUESTS = """(() => {
  if (window.__jevInflight !== undefined) return;
  window.__jevInflight = 0;
  const done = () => { window.__jevInflight = Math.max(0, window.__jevInflight - 1); };
  const fetch = window.fetch;
  if (fetch) window.fetch = function (...args) {
    window.__jevInflight++;
    try { return fetch.apply(this, args).finally(done); } catch (error) { done(); throw error; }
  };
  const send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function (...args) {
    window.__jevInflight++;
    try {
      this.addEventListener('loadend', done, {once: true});
      return send.apply(this, args);
    } catch (error) { done(); throw error; }
  };
  addEventListener('beforeunload', () => { window.__jevNavigating = Date.now(); });
})()"""


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
        self.call("Page.enable")
        self.call("Page.addScriptToEvaluateOnNewDocument", source=TRACK_REQUESTS)
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
            self.wait_for_change(page)
        result = browser_operation({"operation": "act", "session": self.session, "action": action, "text": text})
        self.after_input = action if action["kind"] != "wait" else None
        return result

    def busy(self):
        """Requests in flight or a navigation under way, in this document or any same-origin frame, or a document
        mid-replacement (evaluation fails). Cross-origin frames cannot be read from here and are not counted."""
        try:
            return bool(self.evaluate(BUSY))
        except (StalePage, RuntimeError):
            return True

    def wait_for_change(self, page):
        """Wait for the page to change and then settle, not a fixed tick: a slow login or a loading spinner
        otherwise burns one model call per tick and looks like a stuck page, and the policy wanders off.

        Returns once the page has changed and has neither changed again nor been busy for SETTLE_S; or, if nothing
        changes, after JEV_WAIT_TIMEOUT of quiet. While busy it keeps waiting, up to JEV_WAIT_MAX. The cheap busy
        check runs every 50 ms; the full-page comparison only every 250 ms.
        """
        timeout, cap = wait_limits()
        started = time.monotonic()
        quiet_since = settled_since = started
        seen = page["marker"]
        changed, next_look = False, started
        while time.monotonic() - started < cap:
            time.sleep(0.05)
            now = time.monotonic()
            if self.busy():
                quiet_since = None
                continue
            quiet_since = quiet_since or now
            if now >= next_look:
                next_look = now + 0.25
                try:
                    marker = self.evaluate(MARKER)
                except (StalePage, RuntimeError):
                    marker = None
                if marker != seen:
                    # Every further change restarts the settle interval, so a late change is not returned mid-way.
                    seen, settled_since, changed = marker, now, True
            if changed and now - max(quiet_since, settled_since) >= SETTLE_S:
                return
            if not changed and now - quiet_since >= timeout:
                return

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
