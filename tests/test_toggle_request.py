"""Offline radio identity contract; no model request is dispatched."""
from jev_ultrafast import model


def test_duplicate_radio_tokens_reach_both_model_target_representations(monkeypatch):
    captured = []
    def post(_url, _key, body):
        captured.append(body)
        def answer(ids, selected):
            return {"choice": selected, "confidence": 1,
                    "probabilities": {i: float(i == selected) for i in ids}}
        questions = body["questions"]
        return {"model": "offline", "answers": {
            "operation": answer(questions["operation"]["criteria"], "CLICK"),
            "click_target": answer(questions["click_target"]["criteria"], "2"),
        }}
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline")
    monkeypatch.setattr(model, "post_json", post)
    page = {"url": "https://example.test", "title": "Options", "text": "Option", "actions": [
        {"id": "a1", "node": 1, "kind": "click", "role": "radio", "label": "Option", "value": "false",
         "checked": "false", "submission_value": "one"},
        {"id": "a2", "node": 2, "kind": "click", "role": "radio", "label": "Option", "value": "false",
         "checked": "false", "submission_value": "two"},
    ]}
    result = model.choose(page, "Select option two", [])
    assert result["choice"] == "a2"
    assert [e["submission_value"] for e in captured[0]["state"]["elements"]] == ["one", "two"]
    choices = captured[0]["questions"]["click_target"]["criteria"]
    assert [choices[i]["submission_value"] for i in ("1", "2")] == ["one", "two"]
