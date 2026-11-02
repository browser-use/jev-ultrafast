"""Contracts for the model/parameter layer (MVP-1)."""

import json

import pytest

from jev_ultrafast import parameters

ALL_ROLE_ENV = [env for pair in parameters.ROLE_MODEL_ENV.values() for env in pair]
ALL_PARAM_ENV = list(parameters.ROLE_PARAM_ENV.values())


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    """Empty role env vars + a temp config file; monkeypatch restores everything."""
    for name in ALL_ROLE_ENV + ALL_PARAM_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(parameters, "CONFIG_PATH", tmp_path / "model-config.json")
    yield


def test_schema_covers_every_role_and_env():
    roles = {r["key"] for r in parameters.PARAMETER_SCHEMA["roles"]}
    assert roles == {"planner", "policy", "text"}
    assert set(parameters.ROLE_MODEL_ENV) == roles
    for role in roles:
        for param in parameters.PARAMETER_SCHEMA["parameters"]:
            assert (role, param) in parameters.ROLE_PARAM_ENV, f"missing env for {role}.{param}"
    assert parameters.PARAMETER_SCHEMA["parameters"]["temperature"]["max"] == 2


def test_presets_only_reference_known_roles_and_params():
    for key, preset in parameters.PRESETS.items():
        assert preset["label"], f"preset {key} needs a label"
        for role, params in preset["params"].items():
            assert role in parameters.ROLE_MODEL_ENV
            for name in params:
                assert name in parameters.PARAMETER_SCHEMA["parameters"]


def test_apply_params_sets_env_and_persists():
    applied = parameters.apply_params("planner", {"reasoning": "high", "temperature": 0.4})
    assert applied == {"reasoning": "high", "temperature": 0.4}
    import os

    assert os.environ["PLANNER_REASONING"] == "high"
    assert os.environ["PLANNER_TEMPERATURE"] == "0.4"
    saved = json.loads(parameters.CONFIG_PATH.read_text())
    assert saved["params"]["planner"] == {"reasoning": "high", "temperature": 0.4}


def test_apply_params_none_clears_the_value():
    import os

    parameters.apply_params("text", {"temperature": 0.9})
    assert os.environ.get("TEXT_MODEL_TEMPERATURE")
    parameters.apply_params("text", {"temperature": None})
    assert "TEXT_MODEL_TEMPERATURE" not in os.environ
    saved = json.loads(parameters.CONFIG_PATH.read_text())
    assert "temperature" not in saved["params"]["text"]


def test_apply_params_rejects_out_of_range_and_unknown():
    with pytest.raises(ValueError, match="between 0 and 2"):
        parameters.apply_params("policy", {"temperature": 3})
    with pytest.raises(ValueError, match="reasoning must be one of"):
        parameters.apply_params("policy", {"reasoning": "ultra"})
    with pytest.raises(ValueError, match="Unknown role"):
        parameters.apply_params("hacker", {"reasoning": "low"})
    with pytest.raises(ValueError, match="Unknown parameter"):
        parameters.apply_params("policy", {"top_p": 0.5})


def test_apply_preset_applies_every_role():
    parameters.apply_preset("coding")
    import os

    assert os.environ["PLANNER_REASONING"] == "high"
    assert os.environ["POLICY_TEMPERATURE"] == "0.2"
    assert os.environ["TEXT_MODEL_TEMPERATURE"] == "0.2"
    with pytest.raises(ValueError, match="Unknown preset"):
        parameters.apply_preset("turbo")


def test_apply_model_switches_provider_and_model():
    import os

    parameters.apply_model("text", "groq", "openai/gpt-oss-20b")
    assert os.environ["TEXT_MODEL_PROVIDER"] == "groq"
    assert os.environ["TEXT_MODEL"] == "openai/gpt-oss-20b"
    saved = json.loads(parameters.CONFIG_PATH.read_text())
    assert saved["models"]["text"] == {"provider": "groq", "model": "openai/gpt-oss-20b"}
    # colons are fine: Ollama tags look like qwen3.5:4b
    parameters.apply_model("text", "ollama", "qwen3.5:4b")
    assert os.environ["TEXT_MODEL"] == "qwen3.5:4b"
    with pytest.raises(ValueError):
        parameters.apply_model("text", "groq", "  ")
    with pytest.raises(ValueError):
        parameters.apply_model("text", "groq", "model with spaces")


def test_apply_saved_config_overrides_env_defaults():
    import os

    parameters.apply_model("planner", "nvidia", "z-ai/glm-5.3")
    parameters.apply_params("planner", {"reasoning": "medium"})
    # simulate a restart: env back to .env defaults, config file kept
    for name in ("PLANNER_PROVIDER", "PLANNER_MODEL", "PLANNER_REASONING"):
        os.environ.pop(name, None)
    os.environ["PLANNER_PROVIDER"] = "groq"
    os.environ["PLANNER_MODEL"] = "openai/gpt-oss-20b"
    parameters.apply_saved_config()
    assert os.environ["PLANNER_PROVIDER"] == "nvidia"
    assert os.environ["PLANNER_MODEL"] == "z-ai/glm-5.3"
    assert os.environ["PLANNER_REASONING"] == "medium"


def test_apply_saved_config_survives_broken_file(tmp_path):
    monkey = pytest.MonkeyPatch()
    monkey.setattr(parameters, "CONFIG_PATH", tmp_path / "broken.json")
    try:
        tmp_path.joinpath("broken.json").write_text("{not json")
        assert parameters.apply_saved_config() == {}
    finally:
        monkey.undo()


def test_current_selection_reports_display_and_params():
    import os

    parameters.apply_model("planner", "nvidia", "z-ai/glm-5.3")
    parameters.apply_params("planner", {"reasoning": "low"})
    selection = parameters.current_selection()
    assert selection["planner"]["provider"] == "nvidia"
    assert selection["planner"]["model"] == "z-ai/glm-5.3"
    assert selection["planner"]["display"] == "GLM 5.3"
    assert selection["planner"]["params"] == {"reasoning": "low"}
    assert selection["planner"]["dialect"] == "openai"
    assert os.environ.get("TEXT_MODEL_PROVIDER", "") == selection["text"]["provider"]
