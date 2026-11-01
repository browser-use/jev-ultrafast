"""Offline contract for SELECT execution: options are pinned by observed position.

Evaluates the real select-enumeration block (snapshot.js) and the real act snippet
(browser.py) verbatim in plain node against stub selects whose `value` setter follows
the HTML spec: it selects the first option in tree order with a matching value,
disabled options included. No DOM, browser, or model APIs.
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
    end = BROWSER_SOURCE.index('})(""" + json.dumps(action)', start)
    return BROWSER_SOURCE[start:end + len('})(')]


def select_block():
    """The SELECT enumeration block from snapshot.js, copied verbatim (its `if` is unclosed)."""
    start = SNAPSHOT_SOURCE.index("if (e.tagName==='SELECT') {")
    return SNAPSHOT_SOURCE[start:SNAPSHOT_SOURCE.index("} else {", start)]


# A stand-in for a live <select>: the value setter picks the first option in tree
# order with a matching value, disabled options included, exactly like the browser.
# Options may declare a group; groups named in disabledGroups behave as if they sat
# inside a disabled <optgroup>.
HARNESS = """
function makeSelect(specs, initial, disabledGroups) {
  disabledGroups = disabledGroups || [];
  const options = specs.map((s, i) => ({
    index: i, value: s.value, label: s.label, disabled: !!s.disabled,
    selected: false, group: s.group,
    closest: sel => sel === 'optgroup[disabled]' && s.group && disabledGroups.includes(s.group) ? {} : null,
  }));
  const e = {
    tagName: 'SELECT', readOnly: false, events: [], isConnected: true, options,
    matches: () => false, closest: () => null, checkVisibility: () => true,
    getBoundingClientRect: () => ({x: 10, y: 10, width: 100, height: 30}),
    contains: () => true, getAttribute: () => null,
    dispatchEvent(ev) { e.events.push(ev.type); return true; },
  };
  Object.defineProperty(e, 'value', {
    get() { return e.selectedIndex >= 0 ? options[e.selectedIndex].value : ''; },
    set(v) { e.selectedIndex = options.findIndex(o => o.value === v); },
  });
  Object.defineProperty(e, 'selectedIndex', {
    get() { return e._selectedIndex ?? -1; },
    set(i) {
      e._selectedIndex = i;
      options.forEach((o, j) => { o.selected = j === i; });
      e.selectedOptions = i >= 0 ? [options[i]] : [];
    },
  });
  e.selectedOptions = [];
  e.selectedIndex = initial ?? (specs.length ? 0 : -1);
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
        pytest.skip("plain node is required to evaluate the select contract")
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def execute(specs, action, initial=None, disabled_groups=None):
    """Run the real act snippet against a stub select and report what the page ended up with."""
    script = (
        HARNESS
        + f"const e=makeSelect({json.dumps(specs)}, {json.dumps(initial)}, {json.dumps(disabled_groups)});\n"
        + f"window.__jevFast.nodes.set({action['node']}, e);\n"
        + f"const result={act_snippet()}{json.dumps(action)});\n"
        + "console.log(JSON.stringify({"
        + "confirmed: result !== null, "
        + "selectedIndex: e.selectedIndex, "
        + "selectedLabel: e.options[e.selectedIndex]?.label ?? null, "
        + "events: e.events}));"
    )
    return run_node(script)


def enumerate(specs, initial=None, disabled_groups=None):
    """Run the real snapshot SELECT enumeration and return the offered actions."""
    script = (
        HARNESS
        + f"const e=makeSelect({json.dumps(specs)}, {json.dumps(initial)}, {json.dumps(disabled_groups)});\n"
        + "const actions=[];\n"
        + "const enumerate=new Function('e','base','actions',"
        + json.dumps(select_block() + "} return actions;")
        + ");\n"
        + "console.log(JSON.stringify(enumerate(e, {node: 1, role: 'combobox', label: 'Room'}, actions)));"
    )
    return run_node(script)


# The issue's duplicate-value example: choosing Double must not land on Single.
def test_duplicate_values_select_the_offered_option():
    result = execute(
        [{"value": "1", "label": "Single"}, {"value": "1", "label": "Double"}],
        {"kind": "select", "node": 1, "value": "1", "option_index": 1},
        initial=0,
    )
    assert result == {
        "confirmed": True, "selectedIndex": 1, "selectedLabel": "Double",
        "events": ["input", "change"],
    }


# The issue's disabled-option example: the sold-out first entry must stay unselected.
def test_disabled_duplicate_option_is_not_selected():
    result = execute(
        [
            {"value": "ZRH", "label": "Zurich (sold out)", "disabled": True},
            {"value": "ZRH", "label": "Zurich"},
        ],
        {"kind": "select", "node": 1, "value": "ZRH", "option_index": 1, "option_label": "Zurich"},
        initial=0,
    )
    assert result["confirmed"] is True
    assert result["selectedLabel"] == "Zurich" and result["selectedIndex"] == 1


# The ordinary case is unchanged: a unique value selects and fires both events.
def test_normal_single_match_still_selects_and_fires_events():
    result = execute(
        [{"value": "bern", "label": "Bern"}, {"value": "ZRH", "label": "Zurich"}],
        {"kind": "select", "node": 1, "value": "ZRH", "option_index": 1, "option_label": "Zurich"},
        initial=0,
    )
    assert result == {
        "confirmed": True, "selectedIndex": 1, "selectedLabel": "Zurich",
        "events": ["input", "change"],
    }


# A duplicate-valued option that moved into the pinned position is refused: the
# observed label fingerprints the option the model actually saw.
def test_replaced_duplicate_option_is_refused_by_label():
    result = execute(
        [{"value": "bern", "label": "Bern"}, {"value": "1", "label": "Double"}],
        {"kind": "select", "node": 1, "value": "1", "option_index": 1, "option_label": "Single"},
        initial=0,
    )
    assert result["confirmed"] is False


def test_label_fingerprint_confirms_the_pinned_option():
    result = execute(
        [{"value": "1", "label": "Single"}, {"value": "1", "label": "Double"}],
        {"kind": "select", "node": 1, "value": "1", "option_index": 1, "option_label": "Double"},
        initial=0,
    )
    assert result == {
        "confirmed": True, "selectedIndex": 1, "selectedLabel": "Double",
        "events": ["input", "change"],
    }


# Options inside a disabled <optgroup> are refused, not silently selected.
def test_option_under_a_disabled_optgroup_is_refused():
    result = execute(
        [{"value": "bern", "label": "Bern"}, {"value": "ZRH", "label": "Zurich", "group": "cantons"}],
        {"kind": "select", "node": 1, "value": "ZRH", "option_index": 1, "option_label": "Zurich"},
        initial=0,
        disabled_groups=["cantons"],
    )
    assert result["confirmed"] is False


# A decision whose pinned option no longer matches the page is refused, never guessed.
REFUSED = [
    # No pinned option at all; an enabled value match exists, so value-based execution would fire.
    ([{"value": "ZRH", "label": "Zurich (sold out)", "disabled": True}, {"value": "ZRH", "label": "Zurich"}],
     {"kind": "select", "node": 1, "value": "ZRH"}),
    # Stale list: the pinned position no longer exists.
    ([{"value": "bern", "label": "Bern"}, {"value": "ZRH", "label": "Zurich"}],
     {"kind": "select", "node": 1, "value": "ZRH", "option_index": 5}),
    # The observed value moved off the pinned position onto another enabled option.
    ([{"value": "bern", "label": "Bern"}, {"value": "ZRH", "label": "Zurich"}],
     {"kind": "select", "node": 1, "value": "ZRH", "option_index": 0}),
    # The pinned option itself became disabled.
    ([{"value": "ZRH", "label": "Zurich (sold out)", "disabled": True}, {"value": "ZRH", "label": "Zurich"}],
     {"kind": "select", "node": 1, "value": "ZRH", "option_index": 0}),
]


@pytest.mark.parametrize("specs,action", REFUSED, ids=["unpinned", "out_of_range", "value_moved", "disabled_at_index"])
def test_executor_refuses_decisions_that_no_longer_pin_an_enabled_option(specs, action):
    assert execute(specs, action, initial=0)["confirmed"] is False


# Non-select elements were already refused for select actions and still are.
def test_non_select_element_is_refused():
    script = (
        HARNESS
        + "window.__jevFast.nodes.set(1, {tagName:'INPUT',isConnected:true,matches:()=>false,"
        + "closest:()=>null,checkVisibility:()=>true,getAttribute:()=>null,readOnly:false,"
        + "getBoundingClientRect:()=>({x:10,y:10,width:100,height:30}),contains:()=>true});\n"
        + f"const result={act_snippet()}"
        + json.dumps({"kind": "select", "node": 1, "value": "1", "option_index": 0}) + ");\n"
        + "console.log(JSON.stringify(result));"
    )
    assert run_node(script) is None


# The snapshot must offer every enabled unselected option, numbered by position in
# the full option list, so duplicate values stay distinguishable targets.
def test_snapshot_numbers_options_by_position_in_the_full_list():
    actions = enumerate([
        {"value": "1", "label": "Single"},
        {"value": "1", "label": "Double"},
    ], initial=-1)
    assert [(a["option_index"], a["option_label"], a["label"], a["value"]) for a in actions] == [
        (0, "Single", "Room → Single", "1"),
        (1, "Double", "Room → Double", "1"),
    ]


# Options under a disabled <optgroup> are omitted from the offered actions without
# renumbering the rest, mirroring the disabled-option behaviour.
def test_snapshot_skips_options_under_a_disabled_optgroup_but_keeps_positions():
    actions = enumerate([
        {"value": "bern", "label": "Bern"},
        {"value": "ZRH", "label": "Zurich", "group": "cantons"},
        {"value": "basel", "label": "Basel"},
    ], initial=0, disabled_groups=["cantons"])
    assert [(a["option_index"], a["label"]) for a in actions] == [(2, "Room → Basel")]


# Disabled and already-selected options stay excluded, without renumbering the rest.
def test_snapshot_skips_disabled_and_selected_options_but_keeps_positions():
    actions = enumerate([
        {"value": "a", "label": "A"},
        {"value": "b", "label": "B", "disabled": True},
        {"value": "c", "label": "C"},
    ], initial=0)
    assert len(actions) == 1
    assert actions[0]["option_index"] == 2
    assert actions[0]["label"] == "Room → C" and actions[0]["current_value"] == "A"
