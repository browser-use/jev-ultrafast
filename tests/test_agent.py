"""Offline contracts for a dynamic operation/target policy. No paid APIs."""

import json
import time
from copy import deepcopy
from unittest.mock import Mock

import pytest

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.browser import StalePage, browser_operation, fingerprint
from jev_ultrafast.questions import MAX_NO_PROGRESS, MAX_STALE_REPLAY, MAX_STEPS


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


def escape_actions():
    return page()["actions"] + [{"id": "escape", "kind": "escape", "label": "Press Escape"}]


def test_escape_is_offered_without_a_target_head():
    elements, targets, controls = model.action_space(escape_actions())
    assert controls["ESCAPE"]["id"] == "escape"
    assert "ESCAPE" not in targets
    assert all(a["id"] != "escape" for head in targets.values() for a in head.values())
    assert len(elements) == 2, "a key press is not an element"


def test_escape_needs_no_target_answer_to_be_executable(monkeypatch):
    def post(_url, _key, body):
        return {
            "model": "test",
            "answers": {"operation": choice(body["questions"]["operation"]["criteria"], "ESCAPE")},
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    state = page()
    state["actions"] = escape_actions()
    d = model.choose(state, "Close the menu", [])
    assert (d["operation"], d["choice"], d["target"]) == ("ESCAPE", "escape", None)


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


def test_an_expanded_control_is_visible_and_not_recommended_for_re_click(monkeypatch):
    """Re-clicking the control that opened a menu closes it, so the policy has to see that it is open."""
    p = page()
    p["actions"].insert(0, {
        "id": "menu", "kind": "click", "label": "Change ticket type", "node": 30,
        "role": "combobox", "expanded": "true",
    })
    asked = []

    def post(_url, _key, body):
        asked.append(body["questions"])
        questions = body["questions"]
        return {
            "model": "test",
            "answers": {
                "operation": choice(questions["operation"]["criteria"], "CLICK"),
                "click_target": choice(questions["click_target"]["criteria"], "1"),
            },
        }

    def rules(question):
        rules = question["instructions"]["rules"]
        return "\n".join(rules) if isinstance(rules, list) else rules

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    model.choose(p, "Choose one way", [])
    questions = asked[0]
    assert questions["click_target"]["criteria"]["1"]["expanded"] == "true"
    for name, question in questions.items():
        assert "expanded" in rules(question), f"{name} asks for a choice without the rule against re-clicking"


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
    a.pending_stale = None
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
        "no_progress": [],
        "stale_replays": 0,
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
    for _ in range(MAX_NO_PROGRESS + 3):
        runner.state["decision"] = decision("wait")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert len(runner.state["history"]) == MAX_NO_PROGRESS + 3
    assert runner.state["status"] == "ready", "waiting on a loading page must not spend the no-op budget"
    assert no_ops(runner.state["history"]) == 0, "a wait is not an action that went nowhere"


def test_action_that_changed_nothing_is_not_offered_again(runner, monkeypatch):
    asked = []
    monkeypatch.setattr(loop, "choose", lambda *a, **k: asked.append(k["skip"]) or decision("e3"))
    runner.state["decision"] = decision("e3")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["no_progress"] == ["e3"]
    runner.command("predict", {})
    assert asked == [["e3"]]
    assert "e3" not in {a["id"] for a in model.action_space(page()["actions"], asked[0])[1]["CLICK"].values()}


def test_page_change_clears_the_exclusion_list(runner):
    runner.state["decision"] = decision("e3")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["no_progress"] == ["e3"]
    runner.state["browser"].observe.return_value = changed = page()
    changed["text"] = "Results"
    changed["fingerprint"] = fingerprint(changed)
    runner.state["decision"] = decision("e3")
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["history"][-1]["page_changed"] is True
    assert runner.state["no_progress"] == []


def drip(runner, monkeypatch, rounds):
    """Act repeatedly, alternating an observation that moved the page with one that did not.

    No two no-ops land next to each other, so the three-in-a-row rule can never fire here; anything
    that stops this run does it through the episode-wide budget.
    """
    monkeypatch.setattr(loop, "choose", lambda *a, **k: decision("e3"))
    runner.state["status"] = "ready"
    seen = 0

    def observe(**_kwargs):
        nonlocal seen
        seen += 1
        current = runner.state["page"]
        if seen % 2:
            return current
        moved = deepcopy(current)
        moved["text"] = f"after {seen}"
        moved["fingerprint"] = fingerprint(moved)
        return moved

    runner.state["browser"].observe.side_effect = observe
    for _ in range(rounds):
        if runner.state["status"] != "ready":
            break
        runner.state["decision"] = decision("e3")
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    return runner.state["history"]


def no_ops(history):
    return sum(1 for h in history if h["page_changed"] is False and h["kind"] != "wait")


def test_no_ops_scattered_across_the_episode_still_stop_it(runner, monkeypatch):
    history = drip(runner, monkeypatch, MAX_STEPS)
    assert runner.state["status"] == "blocked"
    assert no_ops(history) == MAX_NO_PROGRESS, "the budget is what ended this run"
    window = [h["page_changed"] for h in history]
    assert all(window[i : i + 3] != [False, False, False] for i in range(len(window) - 2))
    assert len(history) < MAX_STEPS, len(history)


def test_scattered_no_ops_below_the_budget_keep_running(runner, monkeypatch):
    history = drip(runner, monkeypatch, (MAX_NO_PROGRESS - 1) * 2)
    assert runner.state["status"] == "ready"
    assert no_ops(history) == MAX_NO_PROGRESS - 1


def test_tick_recovery_clears_ids_only_if_the_action_table_moved(runner, monkeypatch):
    monkeypatch.setattr(loop, "choose", lambda *a, **k: decision("e3"))
    runner.state["status"] = "ready"
    runner.state["no_progress"] = ["e1"]
    runner.state["browser"].act.side_effect = StalePage("Page moved during input")
    changed = page()
    changed["text"] = "Results"
    changed["fingerprint"] = fingerprint(changed)
    runner.state["browser"].observe.return_value = changed
    runner.command("tick")
    assert runner.state["no_progress"] == []


def test_a_rejected_click_on_an_unchanged_table_is_replayed_without_the_model(runner, monkeypatch):
    asked = []
    monkeypatch.setattr(loop, "choose", lambda *a, **k: asked.append(1) or decision("e3"))
    runner.state["status"] = "ready"
    runner.state["browser"].act.side_effect = [StalePage("guard churned"), StalePage("again"), None]
    runner.command("tick")
    assert len(asked) == 1, "the action table is what the model saw, and it never moved"
    assert runner.state["browser"].act.call_count == 3
    assert [h["choice"] for h in runner.state["history"]] == ["e3"]
    assert runner.state["stale_replays"] == 0, "a dispatched action closes the streak"


def test_a_table_that_never_settles_loses_the_control_that_refused_it(runner, monkeypatch):
    asked = []
    monkeypatch.setattr(loop, "choose", lambda *a, **k: asked.append(k["skip"]) or decision("e3"))
    runner.state["status"] = "ready"
    # MAX_STALE_REPLAY + 1 attempts: the last one is the rejection that spends the cap.
    runner.state["browser"].act.side_effect = [StalePage("guard churned")] * (MAX_STALE_REPLAY + 1)
    runner.command("tick")
    assert asked == [[]], "the model is asked once, and nothing else during the streak"
    assert len(runner.state["decisions"]) == 1, "no paid call is spent rediscovering the same answer"
    assert runner.state["no_progress"] == ["e3"], "the control that refused is no longer offered"
    assert runner.state["stale_replays"] == 0

    runner.state["browser"].act.side_effect = [None]
    runner.command("tick")
    assert asked == [[], ["e3"]], "the next question is asked with the stuck control dropped"


def test_a_moved_table_ends_the_replay_and_asks_the_model_again(runner, monkeypatch):
    asked = []
    monkeypatch.setattr(loop, "choose", lambda *a, **k: asked.append(1) or decision("e3"))
    runner.state["status"] = "ready"
    moved = page()
    moved["text"] = "Results"
    moved["fingerprint"] = fingerprint(moved)
    runner.state["browser"].observe.return_value = moved
    runner.state["browser"].act.side_effect = [StalePage("table moved"), None]
    runner.command("tick")
    assert len(asked) == 1, "a new table is a new question, so the streak stops here"
    assert runner.state["page"] is moved
    runner.command("tick")
    assert len(asked) == 2
    assert runner.state["browser"].act.call_count == 2


@pytest.mark.parametrize("content", ["identical", "changed"])
def test_predict_keeps_exclusions_across_a_guard_only_churn(runner, monkeypatch, content):
    asked = []
    monkeypatch.setattr(loop, "choose", lambda *a, **k: asked.append(k["skip"]) or decision("e3"))
    fresh = page()
    if content == "changed":
        fresh["text"] = "Results"
        fresh["fingerprint"] = fingerprint(fresh)
    runner.state["browser"].fresh.return_value = False  # The DOM guard churns even when the table does not.
    runner.state["browser"].observe.return_value = fresh
    runner.state["no_progress"] = ["e3"]
    runner.command("predict", {})
    assert asked == [["e3"] if content == "identical" else []]


def test_skipping_one_dropdown_option_leaves_the_rest_selectable():
    options = [
        {"id": f"e{i}", "kind": "select", "node": 5, "role": "combobox", "value": str(n),
         "current_value": "1 room", "label": f"Rooms (1 room) → {n} rooms"}
        for i, n in enumerate((2, 3, 4), start=1)
    ]
    elements, targets, _ = model.action_space(options, skip=("e1",))
    assert {a["id"] for a in targets["SELECT"].values()} == {"e2", "e3"}
    assert targets["SELECT"].keys() == {o["index"] for o in elements[0]["options"]}


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


def reader(monkeypatch, hidden, actions=None, fingerprints=None):
    """A Browser that never touches CDP, reading from a scripted list of observations."""
    import jev_ultrafast.browser as browser

    b = browser.Browser.__new__(browser.Browser)
    b.session = "test"
    b.call = Mock()
    rows = [{"aria_hidden_fallback": value} for value in hidden]
    for i, row in enumerate(rows):
        row["fingerprint"] = (fingerprints or ["fp"] * len(rows))[i]
        row["actions"] = (actions or [[]] * len(rows))[i]
    reads = []
    monkeypatch.setattr(browser, "browser_operation", lambda request: reads.append(request) or dict(rows.pop(0)))
    return b, reads


def test_a_hidden_page_after_an_input_is_read_again_until_the_overlay_lifts(monkeypatch):
    b, reads = reader(monkeypatch, [True, True, False])
    b.after_input = {"id": "e1", "kind": "click", "node": 1}
    page = b.observe(screenshot=False)
    assert page["aria_hidden_fallback"] is False
    assert len(reads) == 3, "the stale table must not be what the agent acts on next"
    assert page["overlay_reads"] == 2, "the trace must say the table only arrived on the second re-read"


def test_a_cold_observation_never_waits_for_an_overlay(monkeypatch):
    b, reads = reader(monkeypatch, [True, True, True, True, True])
    page = b.observe(screenshot=False)
    assert page["aria_hidden_fallback"] is True
    assert len(reads) == 1, "nothing was just opened, so a hidden page is simply this page"
    assert page["overlay_reads"] == 0, "a page nobody just input into spent none of the settle budget"


def test_the_overlay_wait_is_bounded(monkeypatch):
    import jev_ultrafast.browser as browser

    monkeypatch.setattr(browser, "OVERLAY_SETTLE", 0.45)
    b, reads = reader(monkeypatch, [True] * 40)
    b.after_input = {"id": "e1", "kind": "click", "node": 1}
    page = b.observe(screenshot=False)
    assert page["aria_hidden_fallback"] is True
    assert 2 <= len(reads) <= 6, len(reads)
    assert page["overlay_reads"] == len(reads) - 1


OPEN = [{"id": "e1", "kind": "click", "label": "Ticket type", "expanded": "true"}]
CLOSED = [{"id": "e1", "kind": "click", "label": "Ticket type"}]
OFFERED = [{"id": "e1", "kind": "click", "label": "Ticket type"}, {"id": "e7", "kind": "click", "label": "One way"}]
STILL_OPEN = [{**OPEN[0], "label": "Select your ticket type"}, OFFERED[1]]


def test_a_click_that_leaves_its_own_control_expanded_is_read_until_the_table_moves(monkeypatch):
    b, reads = reader(
        monkeypatch,
        [False, False, False],
        actions=[OPEN, OPEN, OFFERED],
        fingerprints=["fp", "fp", "moved"],
    )
    b.after_input = {"id": "e1", "kind": "click", "node": 1}
    page = b.observe(screenshot=False)
    assert len(reads) == 3, "an open control whose options have not arrived is last frame's page"
    assert {a["id"] for a in page["actions"]} == {"e1", "e7"}, "the options must be what the model is asked about"
    assert page["overlay_reads"] == 2, "this is the wait the ticket-type menu needed"


def test_the_expanded_wait_only_answers_for_the_control_that_was_input(monkeypatch):
    b, reads = reader(monkeypatch, [False, False], actions=[OPEN, OPEN], fingerprints=["fp", "fp"])
    b.after_input = {"id": "e9", "kind": "click", "label": "something else", "node": 9}
    page = b.observe(screenshot=False)
    assert len(page["actions"]) == 1
    assert len(reads) == 1, "a menu the agent did not just open is not this input's business"
    assert page["overlay_reads"] == 0


def test_a_table_that_moves_on_its_own_ends_the_expanded_wait(monkeypatch):
    b, reads = reader(
        monkeypatch,
        [False, False, False, False],
        actions=[OPEN, CLOSED, CLOSED, CLOSED],
        fingerprints=["fp", "moved", "moved", "moved"],
    )
    b.after_input = {"id": "e1", "kind": "click", "node": 1}
    assert b.observe(screenshot=False)["fingerprint"] == "moved"
    assert len(reads) == 2, "the overlay offered what it was going to offer, so waiting further is cost"


def test_options_that_arrive_while_the_control_stays_expanded_end_the_wait(monkeypatch):
    b, reads = reader(
        monkeypatch,
        [False, False, False, False],
        actions=[OPEN, STILL_OPEN, STILL_OPEN, STILL_OPEN],
        fingerprints=["fp", "moved", "moved", "moved"],
    )
    b.after_input = {"id": "e1", "kind": "click", "node": 1}
    assert b.observe(screenshot=False)["actions"] == STILL_OPEN
    assert len(reads) == 2, "an autocomplete keeps its field expanded after the suggestions land"


def test_the_expanded_wait_is_bounded(monkeypatch):
    import jev_ultrafast.browser as browser

    monkeypatch.setattr(browser, "OVERLAY_SETTLE", 0.45)
    b, reads = reader(monkeypatch, [False] * 40, actions=[OPEN] * 40)
    b.after_input = {"id": "e1", "kind": "click", "node": 1}
    assert b.observe(screenshot=False)["aria_hidden_fallback"] is False
    assert 2 <= len(reads) <= 6, len(reads)


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


def test_escape_sends_the_key_and_no_dom_evaluation(monkeypatch):
    import jev_ultrafast.browser as browser

    calls = []
    monkeypatch.setattr(browser, "cdp", lambda *a, **k: calls.append((a, k)) or {})
    result = browser_operation({
        "operation": "act", "session": "test", "action": {"id": "escape", "kind": "escape"}
    })
    assert result == {"executed": "escape"}
    assert [a[0] for a, _ in calls] == ["Input.dispatchKeyEvent"] * 2
    assert [k["type"] for _, k in calls] == ["keyDown", "keyUp"]
    assert all(k["key"] == "Escape" and k["code"] == "Escape" for _, k in calls), calls


def test_fingerprint_tracks_values_and_identity_not_screenshots():
    p = page()
    other = deepcopy(p)
    other["screenshot"] = "changed"
    assert fingerprint(p) == fingerprint(other)
    # The exclusion list survives a no-change re-observation, so ids must be inside the fingerprint.
    other["actions"][0]["id"] = "zz"
    assert fingerprint(p) != fingerprint(other)
    other["actions"][0]["node"] = 99
    assert fingerprint(p) != fingerprint(other)


def test_example_target_date_is_still_bookable():
    from datetime import date

    from examples.flights import DAY

    # The page renders no selectable cell for past dates, so a frozen default is a guaranteed miss.
    assert DAY > date.today()


@pytest.mark.parametrize("changed", ["Departure", "Where from?", "Where to?", "year"])
def test_flight_verification_rejects_wrong_trip(changed):
    from datetime import date

    from examples.flights import verify

    # Pinned so the example's rolling default date can never make this fixture stale.
    day = date(2026, 9, 20)
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
    assert verify(actual, day)["passed"]
    if changed == "year":
        actual["text"] = actual["text"].replace("2026", "2027")
    else:
        next(a for a in actual["actions"] if a["label"] == changed)["value"] = "wrong"
    assert not verify(actual, day)["passed"]


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
