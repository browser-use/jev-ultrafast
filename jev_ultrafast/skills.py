"""Skills: load-on-demand procedural packages selected per task.

Each skill is a directory under skills/ with a skill.json manifest (id, name,
description, keywords, tools) and an instructions.md the orchestrator appends
to its system prompt when the skill is selected. Selection is keyword-driven
and deliberately minimal: only the skills the task mentions are loaded.
"""

import json
import os
from pathlib import Path

DEFAULT_SKILLS_DIR = Path(__file__).parent.parent / "skills"


def skills_dir():
    override = os.environ.get("JEV_SKILLS_DIR")
    return Path(override) if override else DEFAULT_SKILLS_DIR


def load_skills():
    """Every valid skill manifest found in the skills directory."""
    skills = []
    directory = skills_dir()
    if not directory.is_dir():
        return skills
    for manifest_path in sorted(directory.glob("*/skill.json")):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict) or not manifest.get("id"):
            continue
        instructions_path = manifest_path.parent / manifest.get("instructions", "instructions.md")
        manifest["_dir"] = str(manifest_path.parent)
        manifest["_instructions_path"] = str(instructions_path)
        skills.append(manifest)
    return skills


def select_skills(goal, available_tools=None):
    """The minimal skill set for a goal: a skill matches when any keyword appears.

    Laya (optional, open decision engine) picks semantically in any language
    first; the keyword match stays as the always-available fallback.
    """
    loaded = load_skills()
    from . import laya_local

    picked = laya_local.pick_skills(goal, loaded, available_tools=available_tools)
    if picked is not None:
        return picked
    available = set(available_tools or [])
    lowered = goal.lower()
    selected = []
    for skill in loaded:
        keywords = skill.get("keywords") or []
        if not any(str(keyword).lower() in lowered for keyword in keywords):
            continue
        if available and not set(skill.get("tools") or []) & available:
            continue  # the skill needs tools this run does not expose
        selected.append(skill)
    return selected


def skill_instructions(selected):
    """The concatenated instructions of the selected skills, ready for the prompt."""
    sections = []
    for skill in selected:
        path = Path(skill.get("_instructions_path", ""))
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            sections.append(text)
    return "\n\n".join(sections)
