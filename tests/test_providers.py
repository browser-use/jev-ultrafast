"""Offline contracts for the pluggable provider layer. No paid APIs."""

import json
from unittest.mock import Mock

import pytest

from jev_ultrafast import model, providers
from jev_ultrafast.browser import fingerprint

ENV_NAMES = [
    "TYPESAFE_API_KEY",
    "TYPESAFE_MODEL",
    "POLICY_PROVIDER",
    "POLICY_API_KEY",
    "POLICY_BASE_URL",
    "POLICY_MODEL",
    "POLICY_REASONING",
    "POLICY_JSON_MODE",
    "PLANNER_PROVIDER",
    "PLANNER_API_KEY",
    "PLANNER_BASE_URL",
    "PLANNER_MODEL",
    "PLANNER_REASONING",
    "PLANNER_JSON_MODE",
    "TEXT_MODEL_PROVIDER",
    "TEXT_MODEL_API_KEY",
    "TEXT_MODEL_BASE_URL",
    "TEXT_MODEL",
    "TEXT_MODEL_REASONING",
    "TEXT_MODEL_JSON_MODE",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "NVIDIA_API_KEY",
    "NVIDIA_NIM_API_KEY",
    "OMNIROUTE_API_KEY",
    "DEEPSEEK_API_KEY",
    "GROQ_API_KEY",
    "TOGETHER_API_KEY",
    "MISTRAL_API_KEY",
    "XAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def page():
    state = {
        "url": "https://example.test/",
        "title": "Search",
        "text": "Search",
        "scroll": {"y": 0},
        "actions": [
            {"id": "e1", "kind": "fill", "label": "Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e2", "kind": "click", "label": "Open Search", "role": "textbox", "value": "", "node": 10},
            {"id": "e3", "kind": "click", "label": "Go", "role": "button", "value": "", "node": 20},
            {"id": "wait", "kind": "wait", "label": "Wait"},
        ],
    }
    state["fingerprint"] = fingerprint(state)
    return state


def custom_policy(monkeypatch):
    monkeypatch.setenv("POLICY_PROVIDER", "custom")
    monkeypatch.setenv("POLICY_BASE_URL", "https://gateway.test/v1")
    monkeypatch.setenv("POLICY_API_KEY", "test-key")
    monkeypatch.setenv("POLICY_MODEL", "test-model")


def reply(content):
    return Mock(return_value={"model": "test-model", "choices": [{"message": {"content": content}}], "usage": {}})


def test_documented_providers_have_presets():
    names = (
        "openai", "anthropic", "openrouter", "nvidia", "omniroute",
        "deepseek", "groq", "together", "mistral", "xai", "gemini", "custom",
    )
    for name in names:
        assert name in providers.PROVIDERS
    assert providers.PROVIDERS["nvidia"]["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert providers.PROVIDERS["openrouter"]["base_url"] == "https://openrouter.ai/api/v1"
    assert providers.PROVIDERS["omniroute"]["base_url"] == "http://localhost:20128/v1"
    assert providers.PROVIDERS["anthropic"]["dialect"] == "anthropic"


def test_policy_resolves_a_preset_with_its_own_key_variable(monkeypatch):
    monkeypatch.setenv("POLICY_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or")
    monkeypatch.setenv("POLICY_MODEL", "anthropic/claude-sonnet-4.5")
    provider = providers.resolve("policy")
    assert (provider["name"], provider["dialect"], provider["key"]) == ("openrouter", "openai", "sk-or")
    assert provider["base_url"] == "https://openrouter.ai/api/v1"
    assert provider["model"] == "anthropic/claude-sonnet-4.5"
    assert provider["json_mode"] is False  # model-dependent on OpenRouter; robust parsing is used instead


def test_explicit_api_key_wins_over_the_provider_variable(monkeypatch):
    monkeypatch.setenv("POLICY_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi")
    monkeypatch.setenv("POLICY_API_KEY", "direct")
    monkeypatch.setenv("POLICY_MODEL", "meta/llama-3.3-70b-instruct")
    assert providers.resolve("policy")["key"] == "direct"


def test_custom_provider_requires_a_base_url(monkeypatch):
    monkeypatch.setenv("POLICY_PROVIDER", "custom")
    monkeypatch.setenv("POLICY_API_KEY", "k")
    monkeypatch.setenv("POLICY_MODEL", "m")
    with pytest.raises(ValueError, match="POLICY_BASE_URL"):
        providers.resolve("policy")


def test_provider_accepts_a_base_url_directly(monkeypatch):
    monkeypatch.setenv("POLICY_PROVIDER", "https://my-gateway.internal/v1")
    monkeypatch.setenv("POLICY_API_KEY", "k")
    monkeypatch.setenv("POLICY_MODEL", "m")
    provider = providers.resolve("policy")
    assert provider["base_url"] == "https://my-gateway.internal/v1"
    assert provider["dialect"] == "openai"


def test_anthropic_dialect_is_detected_from_the_base_url(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_BASE_URL", "https://api.anthropic.com")
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "k")
    monkeypatch.setenv("TEXT_MODEL", "claude-sonnet-4-5")
    assert providers.resolve("text")["dialect"] == "anthropic"


def test_text_role_keeps_the_deepseek_defaults(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "k")
    provider = providers.resolve("text")
    assert (provider["name"], provider["model"], provider["json_mode"]) == ("deepseek", "deepseek-chat", True)


def test_unknown_provider_is_rejected_with_the_known_list(monkeypatch):
    monkeypatch.setenv("POLICY_PROVIDER", "not-a-provider")
    with pytest.raises(ValueError, match="openrouter"):
        providers.resolve("policy")


def test_missing_key_names_the_variable(monkeypatch):
    monkeypatch.setenv("POLICY_PROVIDER", "openai")
    monkeypatch.setenv("POLICY_MODEL", "gpt-4.1")
    with pytest.raises(ValueError, match="POLICY_API_KEY"):
        providers.resolve("policy")


def test_policy_model_is_required(monkeypatch):
    monkeypatch.setenv("POLICY_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    with pytest.raises(ValueError, match="POLICY_MODEL"):
        providers.resolve("policy")


def test_chat_builds_an_openai_request(monkeypatch):
    monkeypatch.setenv("POLICY_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    monkeypatch.setenv("POLICY_MODEL", "gpt-4.1")
    result = {"model": "gpt-4.1", "choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 1}}
    post = Mock(return_value=result)
    monkeypatch.setattr(model, "post_json", post)
    text, meta = providers.chat(providers.resolve("policy"), "system text", "user text", max_tokens=99)
    assert text == "hi" and meta["model"] == "gpt-4.1"
    url, key, body = post.call_args.args
    assert url == "https://api.openai.com/v1/chat/completions"
    assert key == "sk"
    assert body["messages"] == [{"role": "system", "content": "system text"}, {"role": "user", "content": "user text"}]
    assert body["max_tokens"] == 99
    assert body["response_format"] == {"type": "json_object"}


def test_chat_builds_an_anthropic_request(monkeypatch):
    monkeypatch.setenv("POLICY_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("POLICY_MODEL", "claude-sonnet-4-5")
    result = {
        "model": "claude-sonnet-4-5",
        "content": [{"type": "text", "text": '{"text": "Zurich"}'}],
        "usage": {"input_tokens": 3},
    }
    post = Mock(return_value=result)
    monkeypatch.setattr(model, "post_json", post)
    text, meta = providers.chat(providers.resolve("policy"), "sys", "usr", max_tokens=64)
    assert json.loads(text)["text"] == "Zurich"
    url, key, body = post.call_args.args
    headers = post.call_args.kwargs["headers"]
    assert url == "https://api.anthropic.com/v1/messages"
    assert headers["x-api-key"] == "sk-ant" and headers["anthropic-version"] == "2023-06-01"
    assert body["system"] == "sys" and body["messages"] == [{"role": "user", "content": "usr"}]
    assert "response_format" not in body


def test_openrouter_reasoning_defaults_to_disabled(monkeypatch):
    monkeypatch.setenv("POLICY_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("POLICY_MODEL", "m")
    provider = providers.resolve("policy")
    _, _, body = providers.build_request(provider, "s", "u", 10)
    assert body["reasoning"] == {"enabled": False}


def test_reasoning_effort_maps_per_provider():
    assert providers.reasoning_params("none", "deepseek", "openai") == {"thinking": {"type": "disabled"}}
    assert providers.reasoning_params("none", "openai", "openai") == {}
    assert providers.reasoning_params("high", "openai", "openai") == {"reasoning_effort": "high"}
    assert providers.reasoning_params("high", "nvidia", "openai") == {}
    assert providers.reasoning_params("high", "anthropic", "anthropic") == {}


@pytest.mark.parametrize(
    "content",
    ['{"a": 1}', '```json\n{"a": 1}\n```', 'Sure!\n{"a": 1}\nDone.', 'prefix {"a": {"b": 2}} suffix'],
)
def test_extract_json_finds_the_first_object(content):
    assert providers.extract_json(content)["a"]


@pytest.mark.parametrize("content", ["no json here", "", "[1, 2]", '"text"'])
def test_extract_json_rejects_non_objects(content):
    with pytest.raises(ValueError):
        providers.extract_json(content)


def test_choose_uses_the_provider_when_typesafe_is_unset(monkeypatch):
    custom_policy(monkeypatch)
    post = reply('{"operation": "CLICK", "target": "2", "confidence": 0.9}')
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert post.call_count == 1
    assert d["provider"] == "llm"
    assert (d["operation"], d["target"], d["choice"]) == ("CLICK", "2", "e3")
    assert d["probabilities"] == {"e3": 0.9}
    assert d["target_confidence"] == 0.9 and d["target_probabilities"] == {}
    body = post.call_args.args[2]
    assert body["model"] == "test-model" and body["max_tokens"] == 512
    user = body["messages"][1]["content"]
    assert "GOAL: Find a book" in user
    assert "[1] textbox · Search" in user and "[2] button · Go" in user
    assert "CLICK:" in user and "DONE:" in user and "RECENT ACTIONS: none" in user


def test_provider_choose_maps_select_option_targets(monkeypatch):
    custom_policy(monkeypatch)
    p = page()
    p["actions"].insert(
        3,
        {
            "id": "s1",
            "kind": "select",
            "label": "Category → Design",
            "value": "design",
            "current_value": "All",
            "role": "combobox",
            "node": 30,
        },
    )
    post = reply('{"operation": "SELECT", "target": "3:1"}')
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Filter by Design", [])
    assert (d["operation"], d["target"], d["choice"]) == ("SELECT", "3:1", "s1")
    user = post.call_args.args[2]["messages"][1]["content"]
    assert "3:1" in user and "SELECT:" in user


def test_provider_choose_accepts_control_operations(monkeypatch):
    custom_policy(monkeypatch)
    post = reply('{"operation": "WAIT"}')
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert (d["operation"], d["choice"], d["target"]) == ("WAIT", "wait", None)
    assert d["target_confidence"] is None


def test_provider_choose_retries_invalid_json_once(monkeypatch):
    custom_policy(monkeypatch)
    post = Mock(side_effect=[
        {"model": "m", "choices": [{"message": {"content": "I would click..."}}]},
        {"model": "m", "choices": [{"message": {"content": '{"operation": "DONE"}'}}]},
    ])
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert post.call_count == 2
    assert d["operation"] == "DONE" and d["choice"] == "DONE"
    assert "rejected" in post.call_args.args[2]["messages"][1]["content"]


def test_provider_choose_rejects_an_invented_operation(monkeypatch):
    custom_policy(monkeypatch)
    monkeypatch.setattr(model, "post_json", reply('{"operation": "PRESS_KEYS", "target": "1"}'))
    with pytest.raises(ValueError, match="invalid operation"):
        model.choose(page(), "Find a book", [])


def test_provider_choose_rejects_a_target_from_another_operation(monkeypatch):
    custom_policy(monkeypatch)
    monkeypatch.setattr(model, "post_json", reply('{"operation": "TYPE_TEXT", "target": "2"}'))
    with pytest.raises(ValueError, match="invalid target"):
        model.choose(page(), "Find a book", [])


def test_provider_choose_reports_history_and_never_leaks_nodes(monkeypatch):
    custom_policy(monkeypatch)
    post = reply('{"operation": "DONE"}')
    monkeypatch.setattr(model, "post_json", post)
    history = [{"step": 1, "action": "Search", "kind": "fill", "text": "books", "page_changed": True}]
    model.choose(page(), "Find a book", history)
    user = post.call_args.args[2]["messages"][1]["content"]
    assert "1. Search (fill, typed \"books\") — page changed" in user
    assert "node" not in user and '"10"' not in user and '"20"' not in user  # internal ids never reach the model


def test_choose_still_prefers_typesafe_when_its_key_is_present(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    custom_policy(monkeypatch)
    operation = {
        "choice": "CLICK",
        "confidence": 1.0,
        "probabilities": {"CLICK": 1.0, "TYPE_TEXT": 0.0, "WAIT": 0.0, "DONE": 0.0, "BLOCKED": 0.0},
    }
    post = Mock(return_value={
        "model": "test",
        "answers": {
            "operation": operation,
            "click_target": {"choice": "1", "confidence": 1.0, "probabilities": {"1": 1.0, "2": 0.0}},
        },
    })
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert d["provider"] == "typesafe" and d["choice"] == "e2"
    assert post.call_args.args[0] == "https://api.typesafe.ai/v1/systemone"


def test_field_text_works_through_an_anthropic_provider(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("TEXT_MODEL", "claude-sonnet-4-5")
    result = {
        "model": "claude-sonnet-4-5",
        "content": [{"type": "text", "text": '{"text": "Zurich"}'}],
        "usage": {},
    }
    post = Mock(return_value=result)
    monkeypatch.setattr(model, "post_json", post)
    value, helper = model.field_text({"goal": "Fly from Zurich"})
    assert value == "Zurich" and helper["model"] == "claude-sonnet-4-5"
    assert post.call_args.args[0] == "https://api.anthropic.com/v1/messages"


def test_policy_description_reports_the_active_backend(monkeypatch):
    assert model.policy_description() == "no policy model configured"
    custom_policy(monkeypatch)
    assert model.policy_description() == "custom:test-model"
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    assert model.policy_description() == "jev-latest"


# ── Planner role: plan once, execute fast, replan bounded ────────────────────


def planner_env(monkeypatch):
    monkeypatch.setenv("PLANNER_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi")
    monkeypatch.setenv("PLANNER_MODEL", "z-ai/glm-5.3")


def planned_agent(plan, plan_index=0, planner=True):
    from jev_ultrafast import agent as loop

    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.planner = {"model": "z-ai/glm-5.3"} if planner else None
    a.pending_text = None
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=page())),
        "page": page(),
        "decision": None,
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "plan": plan,
        "plan_index": plan_index,
        "replans": 0,
        "started_at": __import__("time").perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def terminal_decision(operation):
    return {
        "choice": operation,
        "operation": operation,
        "target": None,
        "confidence": 1.0,
        "probabilities": {operation: 1.0},
        "latency_ms": 5,
        "usage": {},
    }


def act(a, operation):
    a.state["decision"] = terminal_decision(operation)
    return a.command("act", {"fingerprint": a.state["page"]["fingerprint"]})


def test_planner_role_resolves_with_provider_key_fallback(monkeypatch):
    planner_env(monkeypatch)
    provider = providers.resolve("planner")
    assert (provider["name"], provider["model"], provider["key"]) == ("nvidia", "z-ai/glm-5.3", "nvapi")


def test_planning_is_off_without_configuration(monkeypatch):
    assert model.planning_config() is None


def test_planning_is_on_when_configured(monkeypatch):
    planner_env(monkeypatch)
    assert model.planning_config()["model"] == "z-ai/glm-5.3"


def test_plan_steps_returns_a_validated_checklist(monkeypatch):
    planner_env(monkeypatch)
    payload = '{"steps": ["Type Zurich into Where from?", "Click Search"]}'
    post = Mock(return_value={"model": "glm", "choices": [{"message": {"content": payload}}]})
    monkeypatch.setattr(model, "post_json", post)
    steps = model.plan_steps("Find a flight", page())
    assert steps == ["Type Zurich into Where from?", "Click Search"]
    user = post.call_args.args[2]["messages"][1]["content"]
    assert "MISSION: Find a flight" in user and "steps list" in user
@pytest.mark.parametrize(
    "content",
    [
        '{"steps": []}',
        '{"steps": "x"}',
        '{"other": 1}',
        '{"steps": [1, 2]}',
        '{"steps": ["a", ""]}',
        '{"steps": ["x"] * 13}',
    ],
)
def test_plan_steps_rejects_invalid_checklists(monkeypatch, content):
    planner_env(monkeypatch)
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError):
        model.plan_steps("Find a flight", page())


def test_replan_request_carries_the_failure_context(monkeypatch):
    planner_env(monkeypatch)
    payload = '{"steps": ["Try the search icon"]}'
    post = Mock(return_value={"model": "glm", "choices": [{"message": {"content": payload}}]})
    monkeypatch.setattr(model, "post_json", post)
    history = [{"step": 1, "action": "Go", "kind": "click"}]
    steps = model.replan_steps("Find a book", ["Click Go", "Open result"], 0, "blocked", page(), history)
    assert steps == ["Try the search icon"]
    user = post.call_args.args[2]["messages"][1]["content"]
    assert "PROBLEM: blocked" in user
    assert "✓ Click Go" not in user and "✗ Click Go" in user
    assert "1. Go (click)" in user


def test_done_advances_to_the_next_plan_step():
    a = planned_agent(["Click Go", "Open the first result"])
    result = act(a, "DONE")
    assert result["status"] == "ready" and result["plan_index"] == 1


def test_last_done_ends_the_run():
    a = planned_agent(["Click Go", "Open the first result"], plan_index=1)
    result = act(a, "DONE")
    assert result["status"] == "done" and result["plan_index"] == 2


def test_single_goal_done_still_completes_the_run():
    a = planned_agent(["Find a book"])
    result = act(a, "DONE")
    assert result["status"] == "done" and result["plan_index"] == 1


def test_blocked_without_planner_stops():
    a = planned_agent(["Click Go"], planner=False)
    result = act(a, "BLOCKED")
    assert result["status"] == "blocked" and result["plan_index"] == 0


def test_blocked_with_planner_replans_the_remaining_steps(monkeypatch):
    from jev_ultrafast import agent as loop

    a = planned_agent(["Click Go", "Open the first result"])
    replan = Mock(return_value=["Use the search icon", "Open the first result"])
    monkeypatch.setattr(loop, "replan_steps", replan)
    result = act(a, "BLOCKED")
    assert result["status"] == "ready" and result["replans"] == 1
    assert result["plan"] == ["Use the search icon", "Open the first result"]
    assert replan.call_args.args[3]  # the failure reason reaches the planner


def test_replan_budget_is_bounded(monkeypatch):
    from jev_ultrafast import agent as loop

    a = planned_agent(["Click Go"])
    a.state["replans"] = 2
    replan = Mock(return_value=["Try again"])
    monkeypatch.setattr(loop, "replan_steps", replan)
    result = act(a, "BLOCKED")
    assert result["status"] == "blocked"
    replan.assert_not_called()


def test_stalled_loop_replans_once_before_blocking(monkeypatch):
    from jev_ultrafast import agent as loop

    a = planned_agent(["Click Go"])
    a.state["history"] = [
        {"step": 1, "action": "Open Search", "kind": "click", "page_changed": False},
        {"step": 2, "action": "Open Search", "kind": "click", "page_changed": False},
    ]
    replan = Mock(return_value=["Click Go instead"])
    monkeypatch.setattr(loop, "replan_steps", replan)
    a.state["decision"] = {
        "choice": "e3", "operation": "CLICK", "target": "2", "confidence": 1.0,
        "probabilities": {"e3": 1.0}, "latency_ms": 5, "usage": {},
    }
    result = a.command("act", {"fingerprint": a.state["page"]["fingerprint"]})
    assert result["status"] == "ready" and result["plan"] == ["Click Go instead"]


def test_predict_passes_the_current_step_to_the_executor(monkeypatch):
    from jev_ultrafast import agent as loop

    a = planned_agent(["Click Go", "Open the first result"], plan_index=1)
    captured = {}

    def fake_choose(_page, goal, _history):
        captured["goal"] = goal
        return terminal_decision("DONE")

    monkeypatch.setattr(loop, "choose", fake_choose)
    a.command("predict", {})
    assert "CURRENT STEP 2/2: Open the first result" in captured["goal"]
    assert "✓ 1. Click Go" in captured["goal"]


def test_directive_matches_the_goal_for_single_step_runs():
    a = planned_agent(["Find a book"])
    assert a.directive() == "Find a book"


# ── Adaptive requests and readable errors ────────────────────────────────────


def test_post_json_includes_the_provider_error_detail(monkeypatch):
    class FakeResponse:
        status_code = 400
        is_error = True
        text = '{"error": {"message": "Model z-ai/glm-5.3 does not exist"}}'

    monkeypatch.setattr(model, "CLIENT", Mock(post=Mock(return_value=FakeResponse())))
    with pytest.raises(RuntimeError, match="does not exist"):
        model.post_json("https://api.test/v1/chat/completions", "k", {})


def test_chat_retries_without_a_rejected_response_format(monkeypatch):
    monkeypatch.setenv("POLICY_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    monkeypatch.setenv("POLICY_MODEL", "gpt-4.1")
    calls = []

    def fake_post(_url, _key, body, headers=None):
        calls.append(body)
        if "response_format" in body:
            raise RuntimeError("HTTP 400: 'response_format' is not supported for this model")
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(model, "post_json", fake_post)
    text, _meta = providers.chat(providers.resolve("policy"), "s", "u")
    assert text == "{}"
    assert len(calls) == 2
    assert "response_format" not in calls[1]
    assert calls[1]["messages"][0]["content"] == "s"


def test_chat_falls_back_to_max_completion_tokens(monkeypatch):
    monkeypatch.setenv("POLICY_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    monkeypatch.setenv("POLICY_MODEL", "gpt-4.1")
    monkeypatch.setenv("POLICY_REASONING", "high")
    calls = []

    def fake_post(_url, _key, body, headers=None):
        calls.append(body)
        if "response_format" in body:
            raise RuntimeError("HTTP 400: response_format unsupported")
        if "reasoning_effort" in body:
            raise RuntimeError("HTTP 400: Unsupported parameter: 'reasoning_effort'")
        if "max_tokens" in body:
            raise RuntimeError("HTTP 400: Use 'max_completion_tokens' instead of 'max_tokens'")
        return {"choices": [{"message": {"content": "{}"}}]}

    monkeypatch.setattr(model, "post_json", fake_post)
    providers.chat(providers.resolve("policy"), "s", "u")
    assert len(calls) == 4
    assert "max_completion_tokens" in calls[3] and "max_tokens" not in calls[3]


def test_chat_gives_up_when_the_error_is_not_a_parameter_issue(monkeypatch):
    monkeypatch.setenv("POLICY_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk")
    monkeypatch.setenv("POLICY_MODEL", "gpt-4.1")
    monkeypatch.setattr(model, "post_json", Mock(side_effect=RuntimeError("HTTP 401: invalid api key")))
    with pytest.raises(RuntimeError, match="401"):
        providers.chat(providers.resolve("policy"), "s", "u")
