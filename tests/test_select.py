"""Offline checks for observed native-option identities. No paid APIs."""

from unittest.mock import Mock

import pytest

from jev_ultrafast import browser, model


@pytest.mark.parametrize("option_node", [None, True, "2", 2.5, [], {}])
def test_select_rejects_invalid_option_identity_before_cdp(monkeypatch, option_node):
    cdp = Mock()
    monkeypatch.setattr(browser, "cdp", cdp)
    with pytest.raises(ValueError, match="Invalid observed option node"):
        browser.browser_operation({"operation": "act", "session": "test", "action": {
            "id": "e1", "kind": "select", "node": 1, "option_node": option_node, "value": "same",
        }})
    cdp.assert_not_called()


def test_select_policy_preserves_distinct_options_with_duplicate_values(monkeypatch):
    actions = [
        {"id": f"e{i}", "kind": "select", "node": 1, "option_node": i + 1,
         "label": label, "value": "same", "current_value": "All", "role": "combobox"}
        for i, label in enumerate(["Category → First", "Category → Second"], 1)
    ]
    page = {"url": "https://example.test", "title": "Categories", "text": "", "actions": actions}

    def post(_url, _key, body):
        def answer(ids, selected):
            return {"choice": selected, "confidence": 1, "probabilities": {i: int(i == selected) for i in ids}}

        questions = body["questions"]
        assert set(questions["select_target"]["criteria"]) == {"1:1", "1:2"}
        return {"model": "test", "answers": {
            "operation": answer(questions["operation"]["criteria"], "SELECT"),
            "select_target": answer(questions["select_target"]["criteria"], "1:2"),
        }}

    monkeypatch.setenv("TYPESAFE_API_KEY", "test")
    monkeypatch.setattr(model, "post_json", post)
    decision = model.choose(page, "Choose Second", [])
    selected = next(a for a in actions if a["id"] == decision["choice"])
    assert selected["option_node"] == 3
    assert selected["label"] == "Category → Second"
