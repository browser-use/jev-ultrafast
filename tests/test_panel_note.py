"""The panel lines are judged by node, because pytest cannot execute app.js.

Covers the gap that `node --check` only proves syntax: a stripped re-read note used to pass every gate.
Each group of promises gets its own single-place mutant, so a green run says which promise is pinned and a
red run says which one moved -- and the other two groups stay green, which is what proves the mutant bit
only where it was aimed.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "scripts" / "panel_note_check.js"
APP_JS = REPO / "jev_ultrafast" / "static" / "app.js"

GREEN = ("notes: 5/5 match", "trace: 3/3 match", "cost: 2/2 match", "TOTAL 10/10 rendered strings match")

# (anchor, replacement, expected tallies) -- one source place per mutant, never two.
MUTANTS = [
    pytest.param(
        "page.overlay_reads ?",
        "false ?",
        ("notes: 2/5 match", "trace: 3/3 match", "cost: 2/2 match", "TOTAL 7/10 rendered strings match"),
        id="overlay-re-read-note-dropped",
    ),
    pytest.param(
        "<small>${escape(h.text_helper)}</small>",
        "",
        ("notes: 5/5 match", "trace: 1/3 match", "cost: 2/2 match", "TOTAL 8/10 rendered strings match"),
        id="text-helper-credit-dropped",
    ),
    pytest.param(
        'h.page_changed ? "Page changed" : "No change observed"',
        'h.page_changed ? "Page changed" : "Page changed"',
        ("notes: 5/5 match", "trace: 2/3 match", "cost: 2/2 match", "TOTAL 9/10 rendered strings match"),
        id="observed-effect-collapse",
    ),
    pytest.param(
        "state.cost.input_tokens.toLocaleString()",
        "0",
        ("notes: 5/5 match", "trace: 3/3 match", "cost: 0/2 match", "TOTAL 8/10 rendered strings match"),
        id="input-token-count-dropped",
    ),
]


def run_harness(target=None):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH, so app.js cannot be executed here")
    command = [node, str(HARNESS)]
    if target is not None:
        command.append(str(target))
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8")


def test_the_panel_pins_its_element_trace_and_cost_lines():
    result = run_harness()
    assert result.returncode == 0, result.stdout + result.stderr
    for line in GREEN:
        assert line in result.stdout


@pytest.mark.parametrize(("anchor", "replacement", "expected"), MUTANTS)
def test_the_check_is_not_vacuous(tmp_path, anchor, replacement, expected):
    # Mutate a copy: app.js belongs to whoever is editing it, and every line must still be proven guessable.
    source = APP_JS.read_text(encoding="utf-8")
    assert source.count(anchor) == 1, f"anchor moved or duplicated in app.js: {anchor}"
    mutated = source.replace(anchor, replacement, 1)
    assert mutated != source, f"the anchor is gone; this mutant no longer reaches app.js: {anchor}"
    copy = tmp_path / "app.js"
    copy.write_text(mutated, encoding="utf-8", newline="\n")
    result = run_harness(copy)
    assert result.returncode != 0, f"{anchor} -> {replacement!r} left the panel check green"
    for line in expected:
        assert line in result.stdout, f"{anchor}: expected {line}\n{result.stdout}"
