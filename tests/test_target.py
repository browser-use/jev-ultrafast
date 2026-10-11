"""Where TARGET_JS clicks, run in Node against a small fake page (no browser, no paid API)."""

import json
import shutil
import subprocess

import pytest

from jev_ultrafast.browser import TARGET_JS

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

# A choice card: <label> (100,100 200x150) holds a visible text span and a radio hidden at 1x1 px. The label covers
# the radio, as a styled choice does. An unrelated overlay can cover everything instead.
FAKE_PAGE = r"""
const rect = (x, y, width, height) => ({x, y, width, height});
function node(tag, r, extra = {}) {
  return {tagName: tag, isConnected: true, parent: null, r, ...extra,
    matches(sel) {
      if (sel === ':disabled') return !!this.disabled;
      return sel.split(',').some(s => s.trim() === `input[type=${this.type}]` && this.tagName === 'INPUT');
    },
    closest(sel) {
      const tags = sel.split(',').map(t => t.trim());
      for (let n = this; n; n = n.parent) if (tags.includes(n.tagName.toLowerCase())) return n;
      return null;
    },
    getAttribute() { return null; },
    checkVisibility() { return this.visible !== false; },
    getBoundingClientRect() { return this.r; },
    contains(o) { for (let n = o; n; n = n.parent) if (n === this) return true; return false; }};
}
const label = node('LABEL', rect(100, 100, 200, 150));
const text = node('SPAN', rect(110, 110, 180, 30)); text.parent = label;
const radio = node('INPUT', rect(105, 105, 1, 1), {type: 'radio', labels: [label]}); radio.parent = label;
const overlay = node('DIV', rect(0, 0, 1280, 800));
const link = node('A', rect(150, 150, 100, 50)); link.parent = label;  // a "terms" link in the middle of the card
globalThis.innerWidth = 1280; globalThis.innerHeight = 800;
const inText = (x, y) => x >= 110 && x <= 290 && y >= 110 && y <= 140;
const inLink = (x, y) => SCENE === 'link' && x >= 150 && x <= 250 && y >= 150 && y <= 200;
globalThis.document = {elementFromPoint: (x, y) => SCENE === 'overlay' ? overlay : inText(x, y) ? text
  : inLink(x, y) ? link : label};
globalThis.window = {__jevFast: {nodes: new Map([[1, radio]])}};
"""


def where(scene, kind="click"):
    script = f"const SCENE = {json.dumps(scene)};\n{FAKE_PAGE}\n" \
             f"console.log(JSON.stringify(({TARGET_JS})({json.dumps({'kind': kind, 'node': 1})})));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=20, check=True)
    return json.loads(out.stdout)


def test_a_radio_hidden_inside_its_label_is_clicked_on_the_label():
    # A person clicks the card and the radio is chosen; the 1x1 radio itself is covered by its own label.
    assert where("card") == {"x": 200, "y": 175}


def test_a_radio_covered_by_something_else_is_still_refused():
    assert where("overlay") is None


def test_only_a_click_falls_back_to_the_label():
    assert where("card", kind="fill") is None


def test_a_label_whose_centre_holds_a_link_or_button_is_refused():
    # The click would follow the link instead of choosing the option (Orchestrator review, 11 Oct).
    assert where("link") is None
