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

# Grow a matching iframe to fill the emulated viewport, from the parent
# document. Emulation.setDeviceMetricsOverride only works on top-level
# targets, so a detached frame inherits no viewport of its own and paints at
# whatever size its container gives it.
EXPAND_FRAMES = """(() => {
  const match = %s;
  let sized = 0;
  for (const frame of document.querySelectorAll("iframe")) {
    if (match && !(frame.src || "").includes(match)) continue;
    frame.style.setProperty("width", "100vw", "important");
    frame.style.setProperty("height", "100vh", "important");
    frame.style.setProperty("min-width", "100vw", "important");
    frame.style.setProperty("min-height", "100vh", "important");
    sized++;
  }
  return sized;
})()"""

# Count visible, sized controls. A single-page app inside a frame reports
# readyState "complete" long before it has rendered anything, so this is the
# signal for "the frame is actually usable".
FRAME_CONTENT_COUNT = """(() => {
  const nodes = document.querySelectorAll(
    'a[href],button,input,select,textarea,[role="button"],[role="link"],[role="row"],[role="tab"],tr'
  );
  let n = 0;
  for (const el of nodes) {
    const r = el.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) n++;
  }
  return n;
})()"""

class StalePage(ValueError):
    """A decision no longer refers to the observed page."""


def pick_frame_target(targets, page_target_id, match=None):
    """Choose an out-of-process iframe worth observing instead of the top document.

    Cross-origin iframes are separate CDP targets. A snapshot of the top
    document cannot see inside one, so an application that renders its whole UI
    in an iframe (GoHighLevel and other embedded admin consoles do this)
    produces an empty element table and the agent correctly reports BLOCKED.

    ``match`` is an optional substring; when given, only a frame whose URL
    contains it is eligible. Without it, the single child frame of the current
    page is chosen, and ambiguity is refused: guessing between several frames
    would silently act on the wrong surface.

    Returns the chosen target dict, or None to stay on the top document.
    """
    frames = [
        t for t in targets
        if t.get("type") == "iframe"
        and t.get("targetId")
        and t.get("openerId", t.get("parentId")) in (None, page_target_id)
    ]
    if match:
        frames = [t for t in frames if match in (t.get("url") or "")]
        return frames[0] if len(frames) == 1 else None
    return frames[0] if len(frames) == 1 else None


def keep_awake(session):
    """Stop Chrome from throttling a background target.

    Chrome freezes timers, rAF, and canvas work in tabs that are not in the
    foreground, so a single-page app in an owned background tab can render
    nothing at all. Focus emulation alone is not enough for apps that also
    check the page lifecycle state, so both are set.

    Both calls are best-effort: older builds may not expose them, and a
    missing optimisation must not break observation.
    """
    for method, params in (
        ("Emulation.setFocusEmulationEnabled", {"enabled": True}),
        ("Page.enable", {}),
        ("Page.setWebLifecycleState", {"state": "active"}),
    ):
        try:
            cdp(method, session_id=session, **params)
        except RuntimeError:
            pass


class Browser:
    def __init__(self, url):
        ensure_daemon()
        self.target = cdp("Target.createTarget", url="about:blank", background=True)["targetId"]
        self.session = cdp("Target.attachToTarget", targetId=self.target, flatten=True)["sessionId"]
        self.top_session = self.session
        self.frame_target = None
        self.call("Emulation.setDeviceMetricsOverride", width=1120, height=780, deviceScaleFactor=1, mobile=False)
        # Keep rAF/menus rendering in an owned background tab, without activating the user's Chrome tab.
        self.call("Emulation.setFocusEmulationEnabled", enabled=True)
        keep_awake(self.session)
        self.call("Page.navigate", url=url)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if self.evaluate("document.readyState") == "complete":
                break
            time.sleep(0.02)
        self._enter_frame_if_needed()

    def _enter_frame_if_needed(self):
        """Observe an out-of-process iframe when the real UI lives inside one.

        Cross-origin iframes are separate CDP targets, invisible to a snapshot
        of the top document. Embedded admin consoles (GoHighLevel among them)
        render a navigation shell in the top document and the actual working
        surface in a frame, so "the top document has controls" is not a safe
        signal: the shell always has controls.

        This is therefore opt-in. Set JEV_FRAME_URL to the frame's URL
        substring to observe it; leave it unset for ordinary pages.
        """
        match = os.environ.get("JEV_FRAME_URL")
        if not match or os.environ.get("JEV_FRAMES", "1") == "0":
            return

        # The frame is created by the host page's own scripts, so it can appear
        # well after document.readyState is "complete". Poll rather than
        # sampling once.
        chosen = None
        deadline = time.monotonic() + float(os.environ.get("JEV_FRAME_TIMEOUT", "20"))
        while time.monotonic() < deadline:
            try:
                targets = cdp("Target.getTargets").get("targetInfos", [])
            except (RuntimeError, KeyError):
                return
            chosen = pick_frame_target(targets, self.target, match)
            if chosen:
                break
            time.sleep(0.4)

        if not chosen:
            print(f"no unique iframe matching {match!r}; staying on the top document", file=sys.stderr)
            return
        # Size the frame from the PARENT document before attaching. Emulation
        # commands are top-level only ("Command can only be executed on
        # top-level targets"), so the frame cannot set its own viewport. Left
        # at its natural size the frame often paints far smaller than the
        # emulated window, and elements below its fold fail the visibility
        # check, producing an empty element table.
        try:
            cdp(
                "Runtime.evaluate",
                session_id=self.top_session,
                expression=EXPAND_FRAMES % json.dumps(match),
                returnByValue=True,
            )
        except RuntimeError:
            pass

        try:
            session = cdp(
                "Target.attachToTarget", targetId=chosen["targetId"], flatten=True
            )["sessionId"]
        except (RuntimeError, KeyError):
            return
        self.session = session
        self.frame_target = chosen["targetId"]
        keep_awake(self.session)

        # The frame may still be booting its app when it is attached.
        # readyState says the document parsed, not that a single-page app has
        # rendered. A skeleton often paints one or two chrome controls (a skip
        # link, a spinner) well before the real UI, so wait for a threshold
        # rather than the first non-zero count.
        needed = int(os.environ.get("JEV_FRAME_MIN_CONTROLS", "5"))
        frame_deadline = time.monotonic() + float(os.environ.get("JEV_FRAME_RENDER_TIMEOUT", "30"))
        seen = 0
        while time.monotonic() < frame_deadline:
            try:
                count = self.evaluate(FRAME_CONTENT_COUNT)
            except StalePage:
                count = None
            if isinstance(count, int):
                seen = count
                if count >= needed:
                    break
            time.sleep(0.25)
        if seen < needed:
            print(
                f"frame rendered only {seen} control(s) after waiting; "
                "it may require interaction or a foreground tab",
                file=sys.stderr,
            )
        print(f"observing iframe: {(chosen.get('url') or '')[:90]}", file=sys.stderr)

    def call(self, method, **params):
        return cdp(method, session_id=self.session, **params)

    def navigate(self, url):
        """Navigate the top document and re-acquire the frame session.

        A parent navigation destroys and recreates any cross-origin iframe, so
        an existing frame session dies with it ("Session with given id not
        found"). Callers that move the top document must go through here rather
        than issuing Page.navigate themselves.
        """
        self.session = self.top_session
        self.frame_target = None
        cdp("Page.navigate", session_id=self.top_session, url=url)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                if self.evaluate("document.readyState") == "complete":
                    break
            except StalePage:
                pass
            time.sleep(0.05)
        self._enter_frame_if_needed()

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
