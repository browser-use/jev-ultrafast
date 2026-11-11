"""Offline contract for the snapshot.js role mapping. Evaluates the real source in plain node; no DOM, browser, or model APIs."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "jev_ultrafast" / "snapshot.js").read_text()


def snippet():
    """The `roles` list and the `role` function, copied verbatim out of snapshot.js."""
    start = SOURCE.index("const roles=[")
    role = SOURCE.index("const role = e => {")
    end = SOURCE.index("\n  };", role) + len("\n  };")
    return SOURCE[start:end]


def role_of(element):
    node = shutil.which("node")
    if not node:
        pytest.skip("plain node is required to evaluate the role mapping")
    script = f"{snippet()}\nconsole.log(JSON.stringify(role({element})));"
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def input_(type_="", explicit_role=None):
    explicit = "null" if explicit_role is None else f"'{explicit_role}'"
    return "{tagName:'INPUT',type:'" + type_ + "',isContentEditable:false,getAttribute:()=>" + explicit + "}"


CASES = [
    # Native date/time controls must stay in the action table as fillable textboxes (#215).
    ("input[type=date]", input_("date"), "textbox"),
    ("input[type=time]", input_("time"), "textbox"),
    ("input[type=datetime-local]", input_("datetime-local"), "textbox"),
    ("input[type=month]", input_("month"), "textbox"),
    ("input[type=week]", input_("week"), "textbox"),
    # range and color are not text fields; the executor sets a range by its numeric
    # value, while color keeps its click-to-open picker action.
    ("input[type=range]", input_("range"), "slider"),
    ("input[type=color]", input_("color"), "button"),
    # The mappings that already worked stay unchanged.
    ("input[type=text]", input_("text"), "textbox"),
    ("input[type=email]", input_("email"), "textbox"),
    ("input[type=url]", input_("url"), "textbox"),
    ("input[type=tel]", input_("tel"), "textbox"),
    ("input[type=search]", input_("search"), "searchbox"),
    ("input[type=number]", input_("number"), "spinbutton"),
    ("input[type=checkbox]", input_("checkbox"), "checkbox"),
    ("input[type=radio]", input_("radio"), "radio"),
    ("input[type=button]", input_("button"), "button"),
    ("input[type=submit]", input_("submit"), "button"),
    ("input[type=reset]", input_("reset"), "button"),
    ("input[type=image]", input_("image"), "button"),
    # An explicit ARIA role always wins over the type mapping.
    ("input[type=date][role=searchbox]", input_("date", "searchbox"), "searchbox"),
    # Non-input elements keep their roles.
    ("button", "{tagName:'BUTTON',isContentEditable:false,getAttribute:()=>null}", "button"),
    ("a[href]", "{tagName:'A',isContentEditable:false,getAttribute:()=>null}", "link"),
    ("select", "{tagName:'SELECT',isContentEditable:false,getAttribute:()=>null}", "combobox"),
    ("textarea", "{tagName:'TEXTAREA',isContentEditable:false,getAttribute:()=>null}", "textbox"),
    ("div[role=tab]", "{tagName:'DIV',isContentEditable:false,getAttribute:()=>'tab'}", "tab"),
    # Elements with no role at all are still dropped from the table.
    ("plain div", "{tagName:'DIV',isContentEditable:false,getAttribute:()=>null}", None),
]


@pytest.mark.parametrize("element,expected", [(case[1], case[2]) for case in CASES], ids=[case[0] for case in CASES])
def test_role_mapping(element, expected):
    assert role_of(element) == expected
