"""Normalized parameter schema, presets, and persisted model selection.

Models are data, not branches: the sidebar renders its controls from
PARAMETER_SCHEMA, presets map to per-role parameter values, and the user's
selection is persisted to artifacts/model-config.json and re-applied over
.env at startup.
"""

import json
import os
import re
import time
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("JEV_MODEL_CONFIG", "artifacts/model-config.json"))

PARAMETER_SCHEMA = {
    "roles": [
        {"key": "planner", "label": "Planner", "hint": "Decomposes the mission once"},
        {"key": "policy", "label": "Executor", "hint": "Picks each action"},
        {"key": "text", "label": "Text writer", "hint": "Fills text fields"},
    ],
    "parameters": {
        "reasoning": {
            "type": "enum",
            "values": ["none", "low", "medium", "high"],
            "labels": {"none": "Off", "low": "Low", "medium": "Medium", "high": "High"},
            "description": "Thinking budget; low is fastest",
        },
        "temperature": {
            "type": "number",
            "min": 0,
            "max": 2,
            "step": 0.1,
            "optional": True,
            "description": "Creativity; omit for the provider default",
        },
    },
}

PRESETS = {
    "fast": {
        "label": "Fast",
        "description": "Lowest latency; minimal thinking",
        "params": {
            "planner": {"reasoning": "low"},
            "policy": {"reasoning": "low"},
            "text": {"reasoning": "low"},
        },
    },
    "balanced": {
        "label": "Balanced",
        "description": "General-purpose defaults",
        "params": {
            "planner": {"reasoning": "medium"},
            "policy": {"reasoning": "low"},
            "text": {"reasoning": "low"},
        },
    },
    "deep": {
        "label": "Deep",
        "description": "Heavy reasoning for complex research or planning",
        "params": {
            "planner": {"reasoning": "high"},
            "policy": {"reasoning": "medium"},
        },
    },
    "browser": {
        "label": "Browser",
        "description": "Conservative, fast browser interaction",
        "params": {
            "planner": {"reasoning": "low"},
            "policy": {"reasoning": "low"},
            "text": {"reasoning": "low"},
        },
    },
    "coding": {
        "label": "Coding",
        "description": "Precise output for code and files",
        "params": {
            "planner": {"reasoning": "high"},
            "policy": {"reasoning": "medium", "temperature": 0.2},
            "text": {"temperature": 0.2},
        },
    },
}

ROLE_PARAM_ENV = {
    ("planner", "reasoning"): "PLANNER_REASONING",
    ("planner", "temperature"): "PLANNER_TEMPERATURE",
    ("policy", "reasoning"): "POLICY_REASONING",
    ("policy", "temperature"): "POLICY_TEMPERATURE",
    ("text", "reasoning"): "TEXT_MODEL_REASONING",
    ("text", "temperature"): "TEXT_MODEL_TEMPERATURE",
}

ROLE_MODEL_ENV = {
    "planner": ("PLANNER_PROVIDER", "PLANNER_MODEL"),
    "policy": ("POLICY_PROVIDER", "POLICY_MODEL"),
    "text": ("TEXT_MODEL_PROVIDER", "TEXT_MODEL"),
}


def _validate(role, params):
    if role not in {r["key"] for r in PARAMETER_SCHEMA["roles"]}:
        raise ValueError(f"Unknown role: {role}")
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    cleaned = {}
    for name, value in params.items():
        if name not in PARAMETER_SCHEMA["parameters"]:
            raise ValueError(f"Unknown parameter: {name}")
        schema = PARAMETER_SCHEMA["parameters"][name]
        if value is None:
            cleaned[name] = None  # explicit clear: fall back to the .env/default value
            continue
        if schema["type"] == "enum":
            if value not in schema["values"]:
                raise ValueError(f"{name} must be one of {schema['values']}")
            cleaned[name] = value
        else:
            try:
                number = float(value)
            except (TypeError, ValueError):
                raise ValueError(f"{name} must be a number") from None
            if not schema["min"] <= number <= schema["max"]:
                raise ValueError(f"{name} must be between {schema['min']} and {schema['max']}")
            cleaned[name] = round(number, 2)
    return cleaned


def apply_params(role, params):
    """Validate and apply one role's parameters to the environment + config file."""
    cleaned = _validate(role, params)
    config = load_config()
    stored = config.setdefault("params", {}).setdefault(role, {})
    for name, value in cleaned.items():
        env_name = ROLE_PARAM_ENV[(role, name)]
        if value is None:
            os.environ.pop(env_name, None)
            stored.pop(name, None)
        else:
            os.environ[env_name] = str(value)
            stored[name] = value
    save_config(config)
    return cleaned


def apply_preset(preset_key):
    if preset_key not in PRESETS:
        raise ValueError(f"Unknown preset: {preset_key}")
    applied = {}
    for role, params in PRESETS[preset_key]["params"].items():
        applied[role] = apply_params(role, params)
    return applied


def apply_model(role, provider_name, model_id):
    """Switch one role's provider+model in the environment and config file."""
    if role not in ROLE_MODEL_ENV:
        raise ValueError(f"Unknown role: {role}")
    model_id = model_id.strip()
    if not model_id or any(character.isspace() for character in model_id):
        raise ValueError("Invalid model id")  # colons are fine: Ollama tags look like qwen3.5:4b
    provider_env, model_env = ROLE_MODEL_ENV[role]
    os.environ[provider_env] = provider_name
    os.environ[model_env] = model_id.strip()
    config = load_config()
    config.setdefault("models", {})[role] = {"provider": provider_name, "model": model_id.strip()}
    save_config(config)


def save_profile(name):
    """Snapshot the current models + parameters as a named profile."""
    name = (name or "").strip()
    if not name or len(name) > 40:
        raise ValueError("Profile name must be 1-40 characters")
    selection = current_selection()
    config = load_config()
    config.setdefault("profiles", {})[name] = {
        "savedAt": time.time(),
        "models": {
            role: {"provider": data["provider"], "model": data["model"]}
            for role, data in selection.items()
            if data.get("provider") and data.get("model")
        },
        "params": {role: data.get("params") or {} for role, data in selection.items()},
    }
    save_config(config)
    return config["profiles"][name]


def apply_profile(name):
    """Restore a saved profile into the environment and the config file."""
    profile = (load_config().get("profiles") or {}).get((name or "").strip())
    if not profile:
        raise ValueError(f"Unknown profile: {name}")
    for role, selection in (profile.get("models") or {}).items():
        if role in ROLE_MODEL_ENV and selection.get("provider") and selection.get("model"):
            apply_model(role, selection["provider"], selection["model"])
    for role, params in (profile.get("params") or {}).items():
        if role in ROLE_MODEL_ENV and isinstance(params, dict):
            kept = {key: value for key, value in params.items() if value is not None}
            if kept:
                apply_params(role, kept)
    return profile


def delete_profile(name):
    config = load_config()
    profiles = config.get("profiles") or {}
    if (name or "").strip() not in profiles:
        raise ValueError(f"Unknown profile: {name}")
    del profiles[(name or "").strip()]
    save_config(config)


def profile_names():
    return sorted((load_config().get("profiles") or {}).keys())


def load_config():
    try:
        data = json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(config):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def apply_saved_config():
    """Re-apply the persisted model/parameter selection over the .env defaults."""
    config = load_config()
    for role, selection in (config.get("models") or {}).items():
        if role in ROLE_MODEL_ENV and isinstance(selection, dict):
            provider_name = selection.get("provider")
            model_id = selection.get("model")
            if provider_name and model_id:
                provider_env, model_env = ROLE_MODEL_ENV[role]
                os.environ.setdefault(provider_env, provider_name)
                os.environ[provider_env] = provider_name
                os.environ[model_env] = model_id
    for role, params in (config.get("params") or {}).items():
        if role in ROLE_MODEL_ENV and isinstance(params, dict):
            for name, value in params.items():
                env_name = ROLE_PARAM_ENV.get((role, name))
                if env_name is not None and value is not None:
                    os.environ[env_name] = str(value)
    return config


def current_selection():
    """What each role resolves to right now, for the sidebar."""
    from . import providers

    selection = {}
    for role, (provider_env, model_env) in ROLE_MODEL_ENV.items():
        provider_name = os.environ.get(provider_env, "")
        model = os.environ.get(model_env, "")
        params = {}
        for name in PARAMETER_SCHEMA["parameters"]:
            env_name = ROLE_PARAM_ENV.get((role, name))
            value = os.environ.get(env_name, "") if env_name else ""
            if value:
                params[name] = value
        selection[role] = {
            "provider": provider_name,
            "model": model,
            "params": params,
            "display": _display_name(model),
            "dialect": providers.PROVIDERS.get(provider_name, {}).get("dialect", "openai"),
        }
    return selection


def _display_name(model_id):
    if not model_id:
        return "—"
    tail = model_id.rsplit("/", 1)[-1].replace("_", "-")
    words = re.split(r"-", tail)  # keep dots: "glm-5.3" → "GLM 5.3"
    acronyms = {"glm", "gpt", "llama", "qwen", "kimi", "oss", "ai", "vl", "nemotron", "devstral"}
    return " ".join(w.upper() if w.lower() in acronyms else (w[:1].upper() + w[1:]) for w in words if w)
