"""Offline contract for filling native input controls: date/time pickers and range sliders.

Evaluates the real act snippet (browser.py) and the real snapshot `editable`
expression verbatim in plain node against stub inputs whose value setters follow
the browser contract: native pickers keep only canonical formats and read back
empty otherwise, and range inputs clamp to their own [min,max]. No DOM, browser,
or model APIs.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT_SOURCE = (ROOT / "jev_ultrafast" / "snapshot.js").read_text()
BROWSER_SOURCE = (ROOT / "jev_ultrafast" / "browser.py").read_text()


def act_snippet():
    """The executor expression from browser.py, copied verbatim, up to the action argument."""
    start = BROWSER_SOURCE.index('target = evaluate("""(action => {') + len('target = evaluate("""')
    end = BROWSER_SOURCE.index('})(""" + json.dumps({**action,', start) + len('})(')
    return BROWSER_SOURCE[start:end]


def editable_snippet():
    """The snapshot `editable` expression, copied verbatim out of snapshot.js."""
    start = SNAPSHOT_SOURCE.index("const editable=")
    tag = SNAPSHOT_SOURCE.index("e.tagName", start)
    return SNAPSHOT_SOURCE[start:SNAPSHOT_SOURCE.index("\n", tag) + 1]


# A stand-in for a live <input>: native pickers reject non-canonical value strings
# and read back empty, exactly like the browser; range inputs clamp.
HARNESS = """
function makeInput(type, attrs) {
  attrs = attrs || {};
  const e = {
    tagName: 'INPUT', type, readOnly: false, events: [], isConnected: true,
    matches: () => false, closest: () => null, checkVisibility: () => true,
    getBoundingClientRect: () => ({x: 10, y: 10, width: 100, height: 30}),
    contains: () => true, getAttribute: () => null,
    dispatchEvent(ev) { e.events.push(ev.type); return true; },
  };
  let stored = attrs.value !== undefined ? String(attrs.value) : '';
  if (type === 'range') {
    const min = attrs.min !== undefined ? Number(attrs.min) : 0;
    const max = attrs.max !== undefined ? Number(attrs.max) : 100;
    Object.defineProperty(e, 'value', {
      get() { return String(Math.min(Math.max(Number(stored) || 0, min), max)); },
      set(v) { stored = String(v); },
    });
  } else if (type === 'date') {
    Object.defineProperty(e, 'value', {
      get() { return stored; },
      set(v) { stored = /^\\d{4}-\\d{2}-\\d{2}$/.test(v) ? v : ''; },
    });
  } else if (type === 'time') {
    Object.defineProperty(e, 'value', {
      get() { return stored; },
      set(v) {
        const m = /^([01]\\d|2[0-3]):([0-5]\\d)(?::([0-5]\\d))?$/.exec(v);
        stored = m ? `${m[1]}:${m[2]}` : '';
      },
    });
  } else {
    e.value = stored;
  }
  return e;
}
globalThis.window = {__jevFast: {nodes: new Map()}};
globalThis.document = {elementFromPoint: () => ({})};
globalThis.innerWidth = 1120; globalThis.innerHeight = 780;
globalThis.Event = class { constructor(type, init) { this.type = type; this.bubbles = !!(init && init.bubbles); } };
"""


def run_node(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("plain node is required to evaluate the native input contract")
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def execute(type_, attrs, action):
    """Run the real act snippet against a stub input and report what the page ended up with."""
    script = (
        HARNESS
        + f"const e=makeInput({json.dumps(type_)}, {json.dumps(attrs)});\n"
        + f"window.__jevFast.nodes.set({action['node']}, e);\n"
        + f"const result={act_snippet()}{json.dumps(action)});\n"
        + "console.log(JSON.stringify({"
        + "confirmed: result !== null, "
        + "native: result !== null && result.native === true, "
        + "value: e.value, "
        + "events: e.events}));"
    )
    return run_node(script)


def editable_of(element, rname):
    """Evaluate the real snapshot `editable` expression for one stub element and role."""
    node = shutil.which("node")
    if not node:
        pytest.skip("plain node is required to evaluate the editable mapping")
    script = (
        f"const rname={json.dumps(rname)};\nconst e={element};\n"
        + editable_snippet()
        + "\nconsole.log(JSON.stringify(editable));"
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


# A date picker takes its value as text and fires input/change like a real form field.
def test_date_fill_assigns_the_value_and_fires_events():
    result = execute("date", {}, {"kind": "fill", "node": 1, "text": "2026-10-18"})
    assert result == {
        "confirmed": True, "native": True, "value": "2026-10-18", "events": ["input", "change"],
    }


def test_time_fill_assigns_the_value_and_fires_events():
    result = execute("time", {}, {"kind": "fill", "node": 1, "text": "14:30"})
    assert result == {
        "confirmed": True, "native": True, "value": "14:30", "events": ["input", "change"],
    }


# Native pickers keep only canonical formats; a rejected value is refused, never guessed.
def test_date_fill_with_a_non_canonical_value_is_refused():
    result = execute("date", {}, {"kind": "fill", "node": 1, "text": "18/10/2026"})
    assert result["confirmed"] is False


# Range sliders take the requested value instead of a click on the track centre...
def test_range_fill_sets_the_requested_value():
    result = execute(
        "range", {"value": 50, "min": 0, "max": 100},
        {"kind": "fill", "node": 1, "text": "70"},
    )
    assert result == {
        "confirmed": True, "native": True, "value": "70", "events": ["input", "change"],
    }


# ...the control's own [min,max] still clamps the request...
def test_range_fill_clamps_to_the_control_bounds():
    result = execute(
        "range", {"value": 50, "min": 0, "max": 100},
        {"kind": "fill", "node": 1, "text": "500"},
    )
    assert result == {
        "confirmed": True, "native": True, "value": "100", "events": ["input", "change"],
    }


# ...and text that is not a number is refused.
def test_range_fill_with_non_numeric_text_is_refused():
    result = execute("range", {"value": 50}, {"kind": "fill", "node": 1, "text": "fast"})
    assert result["confirmed"] is False


# Ordinary textboxes keep the click-and-type path; only native controls short-circuit it.
def test_textbox_fill_keeps_the_typing_path():
    result = execute("text", {"value": ""}, {"kind": "fill", "node": 1, "text": "hello"})
    assert result == {"confirmed": True, "native": False, "value": "", "events": []}


# input[type=range] is advertised as a TYPE_TEXT target so the model can request a value...
def test_range_inputs_are_offered_as_fill_targets():
    assert editable_of("{readOnly:false,getAttribute:()=>null,type:'range',tagName:'INPUT'}", "slider") is True


# ...but a generic element merely labelled role=slider stays a click target.
def test_aria_only_sliders_stay_click_targets():
    assert editable_of("{readOnly:false,getAttribute:()=>null,tagName:'DIV'}", "slider") is False


def test_textbox_stays_editable():
    assert editable_of("{readOnly:false,getAttribute:()=>null,type:'text',tagName:'INPUT'}", "textbox") is True


# A valid time with seconds is normalized to HH:MM and accepted.
def test_time_fill_with_seconds_normalizes_and_is_accepted():
    result = execute("time", {}, {"kind": "fill", "node": 1, "text": "14:30:00"})
    assert result == {
        "confirmed": True, "native": True, "value": "14:30", "events": ["input", "change"],
    }


def test_time_fill_with_a_non_canonical_value_is_refused():
    result = execute("time", {"value": "12:00"}, {"kind": "fill", "node": 1, "text": "25:99"})
    assert result["confirmed"] is False
    assert result["value"] == "12:00"


def test_date_fill_with_a_non_canonical_value_restores_previous():
    result = execute("date", {"value": "2026-10-18"}, {"kind": "fill", "node": 1, "text": "18/10/2026"})
    assert result["confirmed"] is False
    assert result["value"] == "2026-10-18"


def test_textbox_fill_dispatches_mouse_and_keyboard_via_cdp(monkeypatch):
    import jev_ultrafast.browser as browser

    calls = []

    def fake_cdp(method, session_id=None, **params):
        calls.append((method, params))
        if method == "Runtime.evaluate":
            return {"result": {"value": {"x": 50, "y": 60, "native": False}}}
        return {"result": {}}

    monkeypatch.setattr(browser, "cdp", fake_cdp)
    browser.browser_operation({
        "operation": "act",
        "session": "test-session",
        "action": {"id": "e1", "kind": "fill", "node": 1},
        "text": "hello",
    })

    methods = [m[0] for m in calls]
    assert "Input.dispatchMouseEvent" in methods
    assert "Input.dispatchKeyEvent" in methods
    assert "Input.insertText" in methods


def test_native_fill_short_circuits_mouse_and_keyboard_cdp(monkeypatch):
    import jev_ultrafast.browser as browser

    calls = []

    def fake_cdp(method, session_id=None, **params):
        calls.append((method, params))
        if method == "Runtime.evaluate":
            return {"result": {"value": {"x": 50, "y": 60, "native": True}}}
        return {"result": {}}

    monkeypatch.setattr(browser, "cdp", fake_cdp)
    browser.browser_operation({
        "operation": "act",
        "session": "test-session",
        "action": {"id": "e1", "kind": "fill", "node": 1},
        "text": "2026-10-18",
    })

    methods = [m[0] for m in calls]
    assert "Runtime.evaluate" in methods
    assert "Input.dispatchMouseEvent" not in methods
    assert "Input.dispatchKeyEvent" not in methods
    assert "Input.insertText" not in methods
