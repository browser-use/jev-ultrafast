"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import os
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint


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


def choice(ids, selected):
    return {"choice": selected, "confidence": 1.0, "probabilities": {i: float(i == selected) for i in ids}}


def decision(action="e1"):
    return {
        "choice": action,
        "operation": "TYPE_TEXT",
        "target": "1",
        "confidence": 1.0,
        "probabilities": {action: 1.0},
        "latency_ms": 10,
        "usage": {},
    }


@pytest.mark.parametrize("mutation", ["unknown", "nan", "missing", "negative", "non_max", "confidence"])
def test_invalid_choice_is_rejected(mutation):
    a = choice(["a", "b"], "a")
    if mutation == "unknown":
        a["choice"] = "invented"
    elif mutation == "nan":
        a["probabilities"]["a"] = float("nan")
    elif mutation == "missing":
        del a["probabilities"]["b"]
    elif mutation == "negative":
        a["probabilities"]["b"] = -1
    elif mutation == "non_max":
        a["choice"] = "b"
    else:
        a["confidence"] = 5
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.validate_choice(a, {"a", "b"})


def test_one_index_per_node_with_operation_specific_targets():
    elements, targets, controls = model.action_space(page()["actions"])
    assert len(elements) == 2
    assert elements[0]["operations"] == ["TYPE_TEXT", "CLICK"]
    assert targets["TYPE_TEXT"]["1"]["id"] == "e1"
    assert targets["CLICK"]["1"]["id"] == "e2"
    assert targets["CLICK"]["2"]["id"] == "e3"
    assert "WAIT" in controls


def test_all_heads_are_one_request_and_only_matching_head_executes(monkeypatch):
    calls = []

    def post(_url, _key, body):
        calls.append(body)
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "TYPE_TEXT"),
                "type_text_target": choice(["1"], "1"),
                "click_target": {"choice": "invented"},
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(page(), "Find a book", [])
    assert len(calls) == 1
    assert d["operation"] == "TYPE_TEXT" and d["target"] == "1" and d["choice"] == "e1"
    assert set(calls[0]["questions"]) == {"operation", "click_target", "type_text_target"}


def test_click_cannot_consume_a_text_target(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {
                "operation": choice(body["questions"]["operation"]["criteria"], "CLICK"),
                "type_text_target": choice(["1"], "1"),
                "click_target": choice(["1", "2", "999"], "999"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    with pytest.raises(ValueError, match="Invalid TypeSafe"):
        model.choose(page(), "Find a book", [])


def test_target_head_receives_control_state_and_full_next_step_rules(monkeypatch):
    p = page()
    p["actions"].insert(0, {
        "id": "toggle", "kind": "click", "label": "Free cancellation", "node": 30,
        "role": "checkbox", "checked": "true", "selected": False,
    })

    def post(_url, _key, body):
        questions = body["questions"]
        target = questions["click_target"]
        assert target["criteria"]["1"]["checked"] == "true"
        assert target["criteria"]["1"]["selected"] is False
        assert questions["operation"]["instructions"]["rules"] in target["instructions"]["rules"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(target["criteria"], "3"),
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    d = model.choose(p, "Search with free cancellation", [])
    assert d["choice"] == "e3"


def test_quoted_task_text_still_uses_the_llm(monkeypatch):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    post = Mock(return_value={"choices": [{"message": {"content": '{"text":"Zurich"}'}}]})
    monkeypatch.setattr(model, "post_json", post)
    context = model.field_context('Fly from "Zurich" to London', page()["actions"][0], page(), [])
    assert model.field_text(context)[0] == "Zurich"
    assert post.call_count == 1
    sent = json.loads(post.call_args.args[2]["messages"][1]["content"])
    assert sent["goal"] == 'Fly from "Zurich" to London'


def test_missing_text_credential_stops_before_guessing(monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        model.field_text({"goal": 'Enter "Zurich"'})


@pytest.fixture
def runner():
    a = loop.Agent.__new__(loop.Agent)
    a.screenshots = False
    a.pending_text = None
    p = page()
    a.state = {
        "browser": Mock(fresh=Mock(return_value=True), observe=Mock(return_value=p)),
        "page": p,
        "decision": decision(),
        "goal": "Find a book",
        "history": [],
        "decisions": [],
        "status": "predicted",
        "started_at": time.perf_counter(),
        "record": False,
        "text_calls": [],
    }
    return a


def test_stale_decision_is_consumed_before_any_mutation(runner):
    runner.state["browser"].fresh.return_value = False
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


def test_generated_text_reused_only_for_identical_retry_context(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 1
    assert runner.state["browser"].act.call_count == 2  # The first call rejects before any browser input.
    assert runner.pending_text is None


def test_changed_field_context_does_not_reuse_generated_text(runner, monkeypatch):
    helper = Mock(return_value=("book", {"model": "test", "latency_ms": 10}))
    monkeypatch.setattr(loop, "field_text", helper)
    runner.state["browser"].act.side_effect = [StalePage("Changed before input"), None]
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["page"]["text"] = "Different page context"
    runner.state["decision"] = decision()
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert helper.call_count == 2


def test_loading_waits_do_not_trigger_no_progress_stop(runner):
    for _ in range(5):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == 5 and runner.state["status"] == "ready"


def test_stale_observation_preserves_executed_action(runner):
    runner.state["decision"] = decision("e3")
    runner.state["browser"].observe.side_effect = StalePage("changed")
    with pytest.raises(StalePage):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["action"] == "Go"
    runner.state["browser"].act.assert_called_once()


def test_observation_is_one_atomic_browser_read(monkeypatch):
    import jev_ultrafast.browser as browser

    p = page()
    cdp = Mock(return_value={"result": {"value": p}})
    monkeypatch.setattr(browser, "cdp", cdp)
    actual = browser_operation({"operation": "observe", "session": "test", "screenshot": False})
    assert actual["actions"] == p["actions"]
    assert cdp.call_count == 1
    assert cdp.call_args.args[0] == "Runtime.evaluate"


def test_executor_rejects_a_stale_page_before_browser_input(monkeypatch):
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.fresh = Mock(return_value=False)
    operation = Mock()
    monkeypatch.setattr(browser, "browser_operation", operation)
    with pytest.raises(StalePage):
        b.act(page()["actions"][0], page(), "book")
    operation.assert_not_called()


@pytest.mark.parametrize("response", [{"exceptionDetails": {}}, {"result": {}}])
def test_interrupted_dropdown_mutation_cannot_be_retried_as_stale(monkeypatch, response):
    import jev_ultrafast.browser as browser

    # A navigation can destroy the evaluation result after the change event already fired.
    if "exceptionDetails" in response:
        response["exceptionDetails"] = {"text": "Execution context destroyed"}
    cdp = Mock(return_value=response)
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(RuntimeError, match="Dropdown execution"):
        browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "value": "Design",
        }})
    assert cdp.call_count == 1


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from examples.flights import verify

    actual = {
        "url": "https://www.google.com/travel/flights/search?tfs=example",
        "text": "Track prices from Zürich to London departing 2026-09-20",
        "actions": [
            {"label": k, "value": v}
            for k, v in [
                ("Change ticket type. One way", "One way"),
                ("Where from?", "Zürich"),
                ("Where to?", "London"),
                ("Departure", "Sun, Sep 20"),
                ("Nonstop flight on Sunday, September 20. Select flight", ""),
            ]
        ],
    }
    assert verify(actual)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual)["passed"]


@pytest.mark.parametrize(
    "content", ["Thinking: Zurich", '{"text":null}', '{"text":"Zurich","extra":true}', '{"text":123}']
)
def test_text_helper_rejects_invalid_values(monkeypatch, content):
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", Mock(return_value={"choices": [{"message": {"content": content}}]}))
    with pytest.raises(ValueError, match="nothing typed"):
        model.field_text({"goal": "Find a flight"})


def test_navigation_during_prediction_reobserves_without_action(runner):
    runner.state["browser"].fresh.side_effect = StalePage("Document navigating")
    runner.command("tick")
    assert runner.state["status"] == "ready"
    assert runner.state["decision"] is None
    runner.state["browser"].act.assert_not_called()


def test_secret_value_matches_its_exact_label_only(monkeypatch):
    from jev_ultrafast.secrets import secret_for

    monkeypatch.setenv("JEV_SECRETS", '{"Password": "hunter2", "user": "ada"}')
    assert secret_for("password") == secret_for("Password *") == secret_for(" Password: ") == "hunter2"
    assert secret_for("Confirm password") is None  # Never a substring match.
    assert secret_for("Username") is None
    assert secret_for("Email") is None
    monkeypatch.setenv("JEV_SECRETS", "not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        secret_for("Password")


def test_secret_field_is_typed_from_the_vault_and_masked_in_history(runner, monkeypatch):
    monkeypatch.setenv("JEV_SECRETS", '{"Password": "hunter2"}')
    helper = Mock()
    monkeypatch.setattr(loop, "field_text", helper)
    p = runner.state["page"]
    p["actions"].insert(0, {"id": "pw", "kind": "fill", "label": "Password", "role": "textbox", "value": "",
                            "node": 40, "secret": True})
    runner.state["decision"] = decision("pw")
    runner.command("act", {"fingerprint": p["fingerprint"]})
    helper.assert_not_called()  # No model ever sees or writes the secret.
    assert runner.state["browser"].act.call_args.kwargs["text"] == "hunter2"
    assert runner.state["history"][-1]["text"] == "(secret)"
    assert "hunter2" not in json.dumps(runner.state["history"])


def test_policy_is_told_which_fields_are_secret(monkeypatch):
    p = page()
    p["actions"].insert(0, {"id": "pw", "kind": "fill", "label": "Password", "role": "textbox", "value": "",
                            "node": 40, "secret": True})
    seen = {}

    def post(_url, _key, body):
        seen.update(body)
        return {"model": "test", "answers": {"operation": choice(body["questions"]["operation"]["criteria"], "WAIT")}}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    model.choose(p, "Log in", [])
    assert seen["state"]["elements"][0]["secret"] is True
    assert seen["questions"]["type_text_target"]["criteria"]["1"]["secret"] is True
    assert "secret" in seen["questions"]["operation"]["criteria"]["TYPE_TEXT"]


def secret_field(runner):
    p = runner.state["page"]
    p["actions"].insert(0, {"id": "pw", "kind": "fill", "label": "New password", "role": "textbox", "value": "",
                            "node": 40, "secret": True})
    p["actions"].insert(0, {"id": "pwc", "kind": "click", "label": "Open New password", "role": "textbox",
                            "value": "", "node": 40, "secret": True})
    p["fingerprint"] = fingerprint(p)
    return p


def test_a_secret_field_without_a_stored_entry_takes_its_value_from_the_goal(runner, monkeypatch):
    monkeypatch.setenv("JEV_SECRETS", '{"Password": "hunter2"}')
    # A stand-in helper that can only answer from the goal it is given.
    helper = Mock(side_effect=lambda context: (context["goal"].split()[-1], {"model": "t", "latency_ms": 1}))
    monkeypatch.setattr(loop, "field_text", helper)
    p = secret_field(runner)
    runner.state["goal"] = "Register with the password S3t-by-goal"
    runner.state["decision"] = decision("pw")
    runner.command("act", {"fingerprint": p["fingerprint"]})
    assert helper.call_args.args[0]["goal"] == "Register with the password S3t-by-goal"
    assert runner.state["browser"].act.call_args.kwargs["text"] == "S3t-by-goal"
    assert "S3t-by-goal" not in json.dumps(runner.state["history"] + runner.state["text_calls"])


def test_a_field_with_no_value_anywhere_stops_the_run_cleanly(runner, monkeypatch):
    monkeypatch.setenv("JEV_SECRETS", "{}")
    no_value = model.NoFieldValue("Text helper returned no valid field value")
    monkeypatch.setattr(loop, "field_text", Mock(side_effect=no_value))
    p = secret_field(runner)
    runner.state["decision"] = decision("pw")
    runner.command("act", {"fingerprint": p["fingerprint"]})
    assert runner.state["status"] == "blocked" and "No secret stored" in runner.state["error"]
    runner.state["browser"].act.assert_not_called()


def test_clicking_a_password_field_is_not_recorded_as_a_secret(runner, monkeypatch):
    p = secret_field(runner)
    runner.state["decision"] = decision("pwc")
    runner.command("act", {"fingerprint": p["fingerprint"]})
    assert runner.state["history"][-1]["text"] is None


def test_a_missing_text_model_key_is_not_mistaken_for_a_missing_value(runner, monkeypatch):
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    p = runner.state["page"]
    runner.state["decision"] = decision("e1")
    with pytest.raises(ValueError, match="TEXT_MODEL_API_KEY"):
        runner.command("act", {"fingerprint": p["fingerprint"]})


def test_the_inspector_loads_a_quoted_json_secret_as_json(tmp_path, monkeypatch):
    from jev_ultrafast import demo
    from jev_ultrafast.secrets import secret_for

    (tmp_path / ".env").write_text("# comment\nJEV_SECRETS='{\"Password\": \"p#ss word\"}'\n TEXT_MODEL = \"m\" \n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JEV_SECRETS", raising=False)
    monkeypatch.delenv("TEXT_MODEL", raising=False)
    demo.load_environment()
    assert secret_for("Password") == "p#ss word"
    assert os.environ["TEXT_MODEL"] == "m"
    # load_environment() writes os.environ directly, which monkeypatch does not track: remove what it loaded.
    monkeypatch.delenv("JEV_SECRETS")
    monkeypatch.delenv("TEXT_MODEL")


@pytest.mark.parametrize(("raw", "value"), [
    ("'{\"Note\": \"a#b\"}'  # stored for the demo", '{"Note": "a#b"}'),
    ('"mercury"   # cheap', "mercury"),
    ("mercury # cheap", "mercury"),
    ("p#ss", "p#ss"),
    ("", ""),
    ("'{\"Password\": \"it's a secret\"}'", '{"Password": "it\'s a secret"}'),
    ("'plain' # don't", "plain"),
    ('"{\\"Password\\": \\"hunter2\\"}"', '{"Password": "hunter2"}'),
    ('"back\\\\slash"', "back\\slash"),
    ('"ends in a backslash\\\\"', "ends in a backslash\\"),
    ("'{\\\"x\\\"}'", '{\\"x\\"}'),
])
def test_inspector_env_values_follow_dotenv(raw, value):
    from jev_ultrafast.demo import env_value

    assert env_value(raw) == value


def test_inspector_ignores_a_line_without_a_key(tmp_path, monkeypatch):
    from jev_ultrafast import demo

    (tmp_path / ".env").write_text("= orphan\n = also\nJEV_DEMO_CHECK=1\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("JEV_DEMO_CHECK", raising=False)
    demo.load_environment()
    assert os.environ["JEV_DEMO_CHECK"] == "1"
    monkeypatch.delenv("JEV_DEMO_CHECK")


def test_inspector_rejects_an_unclosed_quote(tmp_path, monkeypatch):
    from jev_ultrafast import demo

    (tmp_path / ".env").write_text("TEXT_MODEL='gpt-4o\n")
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="TEXT_MODEL: Unclosed ' quote"):
        demo.load_environment()
