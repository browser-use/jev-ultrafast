"""Contracts for the skills engine (MVP-3)."""

import json
from pathlib import Path

from jev_ultrafast import skills


def test_builtin_skills_load_with_valid_manifests():
    loaded = skills.load_skills()
    ids = {s["id"] for s in loaded}
    assert {"browser", "web-research", "documents", "coding"} <= ids
    for skill in loaded:
        manifest = json.loads(Path(skill["_dir"], "skill.json").read_text())
        assert manifest["id"] == skill["id"]
        assert skill.get("keywords"), f"{skill['id']} needs keywords"
        assert skill.get("tools"), f"{skill['id']} needs tools"
        instructions = Path(skill["_instructions_path"])
        assert instructions.is_file() and instructions.read_text().strip(), f"{skill['id']} needs instructions"


def test_select_skills_matches_by_keyword():
    selected = skills.select_skills("Book a flight on the airline website", available_tools={"browser_task"})
    assert [s["id"] for s in selected] == ["browser"]

    selected = skills.select_skills(
        "Research and compare prices across the web", available_tools={"web_search", "read_page"}
    )
    assert "web-research" in [s["id"] for s in selected]


def test_select_skills_requires_the_tools_to_exist():
    # a browser goal, but this run exposes no browser_task tool
    selected = skills.select_skills("Book a flight on the website", available_tools={"web_search"})
    assert all("browser_task" not in (s.get("tools") or []) for s in selected)


def test_select_skills_empty_when_nothing_matches():
    assert skills.select_skills("tell me a joke", available_tools={"web_search"}) == []


def test_skill_instructions_concatenate_selected():
    selected = skills.select_skills("Analyze the PDF document", available_tools={"parse_document"})
    assert [s["id"] for s in selected] == ["documents"]
    text = skills.skill_instructions(selected)
    assert "PDF" in text or "pdf" in text
    assert skills.skill_instructions([]) == ""


def test_broken_skills_dir_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(skills, "DEFAULT_SKILLS_DIR", tmp_path)
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "skill.json").write_text("{invalid")
    assert skills.load_skills() == []
    assert skills.select_skills("anything") == []
