"""The policy receives measured page position and the scroll operations actually available."""

import pytest

from jev_ultrafast import model
from jev_ultrafast.browser import fingerprint


@pytest.mark.parametrize(
    ("scroll_y", "directions"),
    [
        (0, ["scroll_down"]),
        (2360, ["scroll_up", "scroll_down"]),
        (10549, ["scroll_up"]),
        (0, []),
    ],
)
def test_scroll_position_and_available_directions_reach_the_model(monkeypatch, scroll_y, directions):
    requests = []

    def post(_url, _key, body):
        requests.append(body)
        operations = body["questions"]["operation"]["criteria"]
        return {
            "model": "test",
            "answers": {
                "operation": {
                    "choice": "BLOCKED",
                    "confidence": 1.0,
                    "probabilities": {operation: float(operation == "BLOCKED") for operation in operations},
                },
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    state = {
        "url": "https://example.test/product",
        "title": "Product details",
        "text": "Details visible in this viewport",
        "h": 780,
        "scroll": {"y": scroll_y, "height": 11329 if directions else 780},
        "actions": [
            {"id": direction, "kind": "scroll", "label": direction.replace("_", " ")}
            for direction in directions
        ],
    }
    history = [{"action": "Scroll down", "kind": "scroll", "page_changed": True}]

    model.choose(state, "Find the required control.", history)

    assert len(requests) == 1
    observed = requests[0]["state"]
    assert observed["page"] == {
        "url": state["url"],
        "title": state["title"],
        "text": state["text"],
        "scroll_y": scroll_y,
        "viewport_height": 780,
        "page_height": state["scroll"]["height"],
        "can_scroll_up": "scroll_up" in directions,
        "can_scroll_down": "scroll_down" in directions,
        "offscreen_controls": [],
        "omitted_offscreen_controls": {"above": 0, "below": 0},
    }
    offered = requests[0]["questions"]["operation"]["criteria"]
    assert observed["page"]["can_scroll_up"] == ("SCROLL_UP" in offered)
    assert observed["page"]["can_scroll_down"] == ("SCROLL_DOWN" in offered)
    assert observed["recent_actions"][0]["action"] == "Scroll down"
    assert observed["elements"] == []


@pytest.mark.parametrize(("direction", "operation"), [("above", "SCROLL_UP"), ("below", "SCROLL_DOWN")])
def test_offscreen_hints_reach_policy_but_cannot_be_action_targets(monkeypatch, direction, operation):
    requests = []

    def post(_url, _key, body):
        requests.append(body)
        operations = body["questions"]["operation"]["criteria"]
        return {
            "model": "test",
            "answers": {
                "operation": {
                    "choice": operation,
                    "confidence": 1.0,
                    "probabilities": {key: float(key == operation) for key in operations},
                },
            },
        }

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    state = {
        "url": "https://example.test",
        "title": "Long page",
        "text": "Details",
        "scroll": {"y": 2000, "height": 8000},
        "h": 780,
        "offscreen_controls": [{"role": "button", "label": "Save changes", "direction": direction}],
        "omitted_offscreen_controls": {"above": 5, "below": 7},
        "actions": [
            {"id": "e1", "node": 1, "kind": "click", "role": "link", "label": "Help", "value": ""},
            {"id": operation.lower(), "kind": "scroll", "label": operation},
        ],
    }

    decision = model.choose(state, "Save my changes.", [])

    assert decision["choice"] == operation.lower()
    assert decision["target"] is None
    assert len(requests) == 1
    body = requests[0]
    assert body["state"]["page"]["offscreen_controls"] == state["offscreen_controls"]
    assert body["state"]["page"]["omitted_offscreen_controls"] == state["omitted_offscreen_controls"]
    assert [element["label"] for element in body["state"]["elements"]] == ["Help"]
    assert set(body["questions"]["click_target"]["criteria"]) == {"1"}
    assert body["questions"]["click_target"]["criteria"]["1"]["element"] == "[1] Help"


def test_new_offscreen_control_counts_as_changed_observation():
    state = {"url": "https://example.test", "text": "Details", "actions": [], "scroll": {"y": 2000}}
    before = fingerprint(state)
    state["offscreen_controls"] = [{"role": "button", "label": "Save changes", "direction": "above"}]
    assert fingerprint(state) != before
