"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import time

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)


def post_json(url, key, body):
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


def validate_choice(answer, ids):
    try:
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            answer["choice"] in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.02
            and probabilities[answer["choice"]] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid TypeSafe response; no action executed.")
    return answer


def action_space(actions):
    """One index per observed element; each operation has its own valid target choices."""
    elements, indices, targets, controls = [], {}, {}, {}
    operations = {"click": "CLICK", "fill": "TYPE_TEXT", "select": "SELECT"}
    for action in actions:
        kind = action["kind"]
        if kind not in operations:
            controls[action["id"].upper()] = action
            continue
        node = action["node"]
        if node not in indices:
            index = str(len(elements) + 1)
            indices[node] = index
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=index, label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            elements.append(element)
        index = indices[node]
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        element = elements[int(index) - 1]
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = index
        if kind == "select":
            target = f"{index}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    questions = {
        "operation": {"type": "choice", "criteria": operations, "instructions": {"goal": goal, "rules": NEXT_ACTION}}
    }
    for operation, candidates in targets.items():
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "criteria": {
                index: {
                    "element": f"[{index}] {a['label']}",
                    "current_value": a.get("current_value", a.get("value", "")),
                    **{k: a[k] for k in ("role", "checked", "selected", "expanded") if k in a},
                }
                for index, a in candidates.items()
            },
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {
                **{k: state[k] for k in ("url", "title", "text")},
                "scroll_y": state.get("scroll", {}).get("y"),
                "viewport_height": state.get("h"),
                "page_height": state.get("scroll", {}).get("height"),
                "can_scroll_up": "SCROLL_UP" in controls,
                "can_scroll_down": "SCROLL_DOWN" in controls,
                "offscreen_controls": state.get("offscreen_controls", []),
                "omitted_offscreen_controls": state.get("omitted_offscreen_controls", {"above": 0, "below": 0}),
            },
            "elements": elements,
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
            ],
        },
        "questions": questions,
    }
    started = time.perf_counter()
    result = post_json("https://api.typesafe.ai/v1/systemone", os.environ["TYPESAFE_API_KEY"], body)
    operation_answer = validate_choice(result["answers"].get("operation", {}), operations)
    operation = operation_answer["choice"]
    target = None
    target_answer = None
    probabilities = {}
    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        target_answer = validate_choice(result["answers"].get(operation.lower() + "_target", {}), targets[operation])
        target = target_answer["choice"]
        choice = targets[operation][target]["id"]
        probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities[choice] = operation_answer["probabilities"][operation]
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": result["answers"],
        "model": result["model"],
        "usage": result.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": body,
    }


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


class TextHelperError(ValueError):
    def __init__(self, reason, detail):
        super().__init__(f"Text helper {detail}; nothing typed.")
        self.reason = reason
        self.metadata = {}


def parse_field_value(result):
    choices = result.get("choices") if isinstance(result, dict) else None
    choice = choices[0] if isinstance(choices, list) and choices else None
    message = choice.get("message") if isinstance(choice, dict) else None
    if not isinstance(message, dict):
        raise TextHelperError("invalid_response", "returned an invalid response structure")
    content, finish, refused = message.get("content"), choice.get("finish_reason"), message.get("refusal")
    if finish == "length":
        raise TextHelperError("truncated", "output was cut off at the token limit")
    if refused or finish == "content_filter":
        raise TextHelperError("refused", "declined to provide a field value")
    if not isinstance(content, str) or not content.strip():
        raise TextHelperError("empty_content", "returned no text content")
    try:
        output = json.loads(content)
    except ValueError:
        raise TextHelperError("invalid_json", "returned malformed JSON") from None
    if not isinstance(output, dict) or set(output) != {"text"}:
        raise TextHelperError("invalid_schema", 'must return exactly {"text": "value"}')
    value = output["text"]
    if value is None:
        raise TextHelperError("missing_value", "could not infer a value from the supplied task and page")
    if not isinstance(value, str) or not value.strip() or len(value) > 2000:
        raise TextHelperError("invalid_value", "returned an empty, non-string, or overlong field value")
    return value


def field_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-chat")
    reasoning = {"thinking": {"type": "disabled"}} if "api.deepseek.com/" in base else {"reasoning": {"effort": "low"}}
    if os.environ.get("TEXT_MODEL_REASONING") == "none":
        reasoning = {"reasoning": {"enabled": False}}
    started = time.perf_counter()
    body = {
        "model": model,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
        **reasoning,
        "messages": [
            {"role": "system", "content": TEXT_VALUE},
            {"role": "user", "content": json.dumps(context)},
        ],
    }
    metadata = {"model": model, "attempts": [], "usage": {}}
    for attempt in range(2):
        result = post_json(base + "/chat/completions", key, body)
        # Keep diagnostics and token counts, never failed response text or reasoning.
        choices = result.get("choices") if isinstance(result, dict) else None
        choice = choices[0] if isinstance(choices, list) and choices else None
        choice = choice if isinstance(choice, dict) else {}
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        usage = result.get("usage", {}) if isinstance(result, dict) else {}
        usage = {k: usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                 if isinstance(usage, dict) and type(usage.get(k)) is int}
        record = {
            "attempt": attempt + 1,
            "finish_reason": choice.get("finish_reason"),
            "content_length": len(content) if isinstance(content, str) else None,
            "usage": usage,
        }
        metadata["attempts"].append(record)
        for key_name, count in usage.items():
            metadata["usage"][key_name] = metadata["usage"].get(key_name, 0) + count
        metadata["latency_ms"] = round((time.perf_counter() - started) * 1000)
        try:
            value = parse_field_value(result)
        except TextHelperError as error:
            record["status"] = error.reason
            if attempt == 1 or error.reason in {"missing_value", "refused"}:
                error.metadata = metadata
                raise
            if error.reason == "truncated":
                body = {**body, "max_tokens": 2048}
            # Regenerate from the same context; do not feed malformed output back as instructions.
            body = {
                **body,
                "messages": [
                    {"role": "system", "content": TEXT_VALUE + "\nBe concise. Return only the requested JSON object."},
                    body["messages"][1],
                ],
            }
        else:
            record["status"] = "ok"
            return value, metadata
