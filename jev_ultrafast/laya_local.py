"""Laya: the open System-1 decision engine (ConvAI Innovations, Apache 2.0).

Laya is the open alternative to TypeSafe's Jev: instead of generating text it
answers typed questions (choice / score / boolean) in one forward pass, with
calibrated confidence, fully local — 322M-421M params, ~33 ms on GPU and a few
tens of ms on CPU. `pip install -e ".[laya]"` adds it; see
docs/modelos-locales.md for the full evaluation.

Used here for two decisions where a generative model is overkill and keywords
are brittle across languages:
- routing a mission: drive the live tab (browser) vs run the tool orchestrator
- picking the skill packages that should guide a mission

Everything is optional and fail-safe: if the package is missing, the weights
cannot load, or the calibrated confidence is below the gate, we fall back to
the keyword implementation without touching the run.
"""

import os
import threading

# Calibrated probability below which we keep the keyword answer. Tuned on the
# measured duel battery (GitHub Actions, real weights, 24 multilingual routing
# cases): every Laya routing decision at >= 0.75 confidence was correct, while
# 0.55-0.74 contained confident-wrong answers that misrouted real missions.
CONFIDENCE_GATE = 0.75
CHECKPOINTS = ("multilingual", "english", "typed-decisions")

_engine = None
_engine_lock = threading.Lock()
_load_error = None  # why the weights could not load (shown once in logs/diagnostics)

ROUTE_QUESTIONS = {
    "route": {
        "type": "choice",
        "instructions": "How should this mission be executed?",
        "criteria": {
            "browser": (
                "interacting with the current web page: clicking, filling forms, "
                "navigating, booking, searching on the site, changing settings there"
            ),
            "orchestrated": (
                "needs web search, reading other pages, downloading files, creating "
                "or editing files, analyzing documents (PDF/DOCX/XLSX), or running "
                "terminal commands and code"
            ),
        },
    }
}


def enabled():
    """Laya participation can be disabled with JEV_LAYA=off."""
    return (os.environ.get("JEV_LAYA", "on").strip().lower() not in {"off", "0", "no", "false"})


def available():
    """Is the laya package importable at all (no weights loaded yet)?"""
    if not enabled():
        return False
    try:
        import laya  # noqa: F401
    except ImportError:
        return False
    return True


def status():
    """One line for diagnostics: unused / off / ready / the load error."""
    if not enabled():
        return "off (JEV_LAYA=off)"
    if _engine is not None:
        return f"ready ({os.environ.get('LAYA_CHECKPOINT', 'multilingual')})"
    if _load_error:  # installed but the weights could not load (network, disk…)
        return _load_error
    if not available():
        return "not installed (pip install -e \".[laya]\")"
    return "not loaded yet"


def engine():
    """The cached laya Agent (one cold load, thread-safe, failure remembered)."""
    global _engine, _load_error
    if not enabled():
        return None
    if _engine is not None:
        return _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        if _load_error:
            return None
        try:
            import laya
        except ImportError:
            return None  # simply not installed; not an error worth reporting
        try:
            checkpoint = os.environ.get("LAYA_CHECKPOINT", "multilingual").strip().lower()
            if checkpoint not in CHECKPOINTS:
                checkpoint = "multilingual"
            model_id, subfolder = laya.DEFAULT_MODELS[checkpoint]
            _engine = laya.load(model_id, subfolder=subfolder)
        except Exception as error:  # noqa: BLE001 - any load failure means: fall back
            _load_error = f"laya unavailable: {str(error)[:180]}"
            return None
    return _engine


def warm():
    """Preload the weights in the background so the first decision is fast."""
    if available():
        threading.Thread(target=engine, daemon=True).start()


def _decide(state, questions, question_id, valid):
    """One predict call → (answer, confidence); (None, None) on any problem."""
    agent = engine()
    if agent is None:
        return None, None
    try:
        result = agent.predict(state, questions)
        answer = (result.get("answers") or {}).get(question_id) or {}
    except Exception:  # noqa: BLE001 - the run must never depend on laya
        return None, None
    choice = answer.get("choice")
    confidence = answer.get("confidence")
    if choice in valid and isinstance(confidence, (int, float)) and confidence >= CONFIDENCE_GATE:
        return choice, confidence
    if isinstance(confidence, (int, float)):
        return None, confidence  # decided, but below the confidence gate → fall back
    return None, None


def route_mission(goal):
    """'browser' or 'orchestrated'; None = let the keyword router decide."""
    choice, _confidence = _decide({"mission": goal}, ROUTE_QUESTIONS, "route", {"browser", "orchestrated"})
    return choice


def pick_skills(goal, skills, available_tools=None):
    """The skill manifests Laya selects, or None to keep the keyword selection.

    Only skills whose tools intersect the available set are offered; the model
    may also answer 'none' when no procedural package fits the mission.
    """
    eligible = [
        skill for skill in skills
        if not available_tools or set(skill.get("tools") or []) & set(available_tools)
    ]
    if not eligible:
        return []
    criteria = {
        skill["id"]: f"{skill.get('name', skill['id'])}: {skill.get('description', '')}"
        for skill in eligible
    }
    criteria["none"] = "no procedural package is needed for this mission"
    questions = {
        "skill": {
            "type": "choice",
            "instructions": "Which procedural package should guide this mission?",
            "criteria": criteria,
        }
    }
    choice, _confidence = _decide({"mission": goal}, questions, "skill", set(criteria))
    if choice is None or choice == "none":
        return None
    return [skill for skill in eligible if skill["id"] == choice]
