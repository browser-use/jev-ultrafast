"""Where TARGET_JS clicks, run in Node against a small fake page (no browser, no paid API)."""

import json
import shutil
import subprocess

import pytest

from jev_ultrafast.browser import TARGET_JS

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

# A choice card: <label> (100,100 200x150) holds a visible text span and a radio hidden at 1x1 px. The label covers
# the radio, as a styled choice does. Each SCENE adds one thing: an overlay over everything, a control at the card's
# centre (a link, a button or a role=checkbox), an inert card, or a second label with the first one covered.
FAKE_PAGE = r"""
const rect = (x, y, width, height) => ({x, y, width, height});
const inside = (r, x, y) => x >= r.x && x <= r.x + r.width && y >= r.y && y <= r.y + r.height;
function hits(n, token) {  // enough of CSS matching for the selectors TARGET_JS uses
  if (token === '[inert]') return !!n.inert;
  const role = /^\[role="?([a-z]+)"?\]$/.exec(token);
  if (role) return n.role === role[1];
  if (token.startsWith('[')) return false;
  const [tag, attr] = token.split('[');
  return tag === n.tagName.toLowerCase() && (!attr || attr !== 'href]' || !!n.href);
}
function node(tag, r, extra = {}) {
  return {tagName: tag, isConnected: true, parent: null, r, ...extra,
    matches(sel) {
      if (sel === ':disabled') return !!this.disabled;
      return sel.split(',').some(s => s.trim() === `input[type=${this.type}]` && this.tagName === 'INPUT');
    },
    closest(sel) {
      const tokens = sel.split(',').map(t => t.trim());
      for (let n = this; n; n = n.parent) if (tokens.some(t => hits(n, t))) return n;
      return null;
    },
    getAttribute() { return null; },
    checkVisibility() { return this.visible !== false; },
    getBoundingClientRect() { return this.r; },
    contains(o) { for (let n = o; n; n = n.parent) if (n === this) return true; return false; }};
}
const label = node('LABEL', rect(100, 100, 200, 150));
// a second <label for> of the same radio, elsewhere; in the 'inert' scene it sits in an inert region
const label2 = node('LABEL', rect(400, 100, 200, 150), {inert: SCENE === 'inert'});
const text = node('SPAN', rect(110, 110, 180, 30)); text.parent = label;
const labels = SCENE === 'two' ? [label, label2] : SCENE === 'inert' ? [label2] : [label];
const radio = node('INPUT', rect(105, 105, 1, 1), {type: 'radio', labels});
radio.parent = label;
const overlay = node('DIV', rect(0, 0, 1280, 800));
const middle = rect(150, 150, 100, 50);
const inner = {link: node('A', middle, {href: '/terms'}), button: node('BUTTON', middle),
               rolebox: node('DIV', middle, {role: 'checkbox'})}[SCENE];
if (inner) inner.parent = label;
globalThis.innerWidth = 1280; globalThis.innerHeight = 800;
globalThis.document = {elementFromPoint: (x, y) =>
  SCENE === 'overlay' || (SCENE === 'two' && inside(label.r, x, y)) ? overlay
  : inside(label2.r, x, y) ? label2 : inside(text.r, x, y) ? text : inner && inside(inner.r, x, y) ? inner : label};
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


@pytest.mark.parametrize("scene", ["link", "button", "rolebox"])
def test_a_label_whose_centre_holds_another_control_is_refused(scene):
    # The click would go to that control (follow the link, press the button) instead of choosing the option.
    assert where(scene) is None


def test_a_label_in_an_inert_region_is_never_clicked():
    assert where("inert") is None  # the click would do nothing there


def test_a_covered_label_gives_way_to_the_next_usable_one():
    assert where("two") == {"x": 500, "y": 175}
