"""Offline contract for contenteditable enumeration in snapshot.js.

Per the HTML spec a bare `contenteditable` attribute and `contenteditable=""` put
an element in the editable state, but the enumeration selector used to match only
the literal "true" value, so such elements never reached the action table (#218).

Evaluates the real `roles` list, `selector` string and `role` function verbatim in
plain node against stub elements; no DOM, browser, or model APIs.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "jev_ultrafast" / "snapshot.js").read_text()


def snippet():
    """The `roles` list, `selector` and `role` function, copied verbatim out of snapshot.js."""
    start = SOURCE.index("const roles=[")
    role = SOURCE.index("const role = e => {")
    end = SOURCE.index("\n  };", role) + len("\n  };")
    return SOURCE[start:end]


# A minimal compound-selector matcher for the flat clause list snapshot.js builds:
# tag names, [attr], [attr="value"], and one level of :not(...). It exists only to
# run the real selector offline; a browser's querySelectorAll is the authority.
HARNESS = """
function element(tagName, attrs) {
  return {tagName, type: attrs.type || null, isContentEditable: !!attrs.isContentEditable,
    getAttribute: n => (n in attrs ? attrs[n] : null),
    hasAttribute: n => n in attrs};
}
function matches(el, selector) {
  return selector.split(',').some(clause => matchesCompound(el, clause.trim()));
}
function matchesCompound(el, clause) {
  const not = clause.match(/:not\\((.+)\\)$/);
  if (not) {
    if (matchesCompound(el, not[1])) return false;
    clause = clause.slice(0, -not[0].length);
  }
  const re = /^([a-zA-Z][a-zA-Z0-9-]*)|\\[\\s*([a-zA-Z-]+)\\s*(?:=\\s*(?:"([^"]*)"|'([^']*)'|([^\\]\\s]+)))?\\s*\\]/;
  while (clause.length) {
    const m = clause.match(re);
    if (!m) return false;
    if (m[1] !== undefined) {
      if (el.tagName !== m[1].toUpperCase()) return false;
    } else {
      const attr = m[2], value = m[3] ?? m[4] ?? m[5];
      if (!el.hasAttribute(attr)) return false;
      if (value !== undefined && el.getAttribute(attr) !== value) return false;
    }
    clause = clause.slice(m[0].length);
  }
  return true;
}
"""


def snapshot(tag_name, attrs):
    """Returns {'enumerated': bool, 'role': str|None} for a stub element, per the real source."""
    node = shutil.which("node")
    if not node:
        pytest.skip("plain node is required to evaluate the enumeration contract")
    script = f"{snippet()}\n{HARNESS}\nconst e=element('{tag_name}', {json.dumps(attrs)});\n"
    script += "console.log(JSON.stringify({enumerated:matches(e, selector), role:role(e)}));"
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


CASES = [
    # The short editable forms from #218: a bare attribute and contenteditable=""
    # are the same DOM state (attribute value "") and must enumerate like "true".
    ('div[contenteditable] (bare)', "DIV", {"contenteditable": "", "isContentEditable": True}, True, "textbox"),
    ('div[contenteditable=""]', "DIV", {"contenteditable": "", "isContentEditable": True}, True, "textbox"),
    ('div[contenteditable="true"]', "DIV", {"contenteditable": "true", "isContentEditable": True}, True, "textbox"),
    ('div[contenteditable="plaintext-only"]', "DIV", {"contenteditable": "plaintext-only", "isContentEditable": True}, True, "textbox"),
    # Explicitly non-editable elements stay out of the action table.
    ('div[contenteditable="false"]', "DIV", {"contenteditable": "false", "isContentEditable": False}, False, None),
    # The rest of the selector keeps matching.
    ("button", "BUTTON", {"isContentEditable": False}, True, "button"),
    ("input[type=text]", "INPUT", {"type": "text", "isContentEditable": False}, True, "textbox"),
    ('div[role="textbox"]', "DIV", {"role": "textbox", "isContentEditable": False}, True, "textbox"),
    ("plain div", "DIV", {"isContentEditable": False}, False, None),
]


@pytest.mark.parametrize(
    "tag_name,attrs,expected_enumerated,expected_role",
    [(case[1], case[2], case[3], case[4]) for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_contenteditable_enumeration(tag_name, attrs, expected_enumerated, expected_role):
    result = snapshot(tag_name, attrs)
    assert result == {"enumerated": expected_enumerated, "role": expected_role}
