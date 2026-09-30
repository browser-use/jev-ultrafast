"""The panel line is judged by node, because pytest cannot execute app.js.

Covers the gap that `node --check` only proves syntax: a stripped re-read note used to pass every gate.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "scripts" / "panel_note_check.js"
APP_JS = REPO / "jev_ultrafast" / "static" / "app.js"


def run_harness(target=None):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH, so app.js cannot be executed here")
    command = [node, str(HARNESS)]
    if target is not None:
        command.append(str(target))
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8")


def test_the_element_line_reports_what_the_observation_cost():
    result = run_harness()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "5/5 rendered lines match" in result.stdout


def test_the_check_is_not_vacuous(tmp_path):
    # Mutate a copy: app.js belongs to whoever is editing it, and the note must still be proven guessable.
    source = APP_JS.read_text(encoding="utf-8")
    mutated = source.replace("page.overlay_reads ?", "false ?", 1)
    assert mutated != source, "the re-read condition moved; this mutant no longer reaches app.js"
    copy = tmp_path / "app.js"
    copy.write_text(mutated, encoding="utf-8", newline="\n")
    result = run_harness(copy)
    assert result.returncode != 0, "stripping the re-read note left the panel check green"
    assert "3/5 rendered lines differ" in result.stdout
