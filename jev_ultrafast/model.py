"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import re
import time
import warnings

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)
REASONING_CONTROLS = {}
TOKEN_LIMITS = {}


class ModelHTTPError(RuntimeError):
    def __init__(self, response):
        super().__init__(f"Model provider returned HTTP {response.status_code}; no action executed.")
        self.status = response.status_code
        try:
            self.body = response.json()
        except ValueError:
            self.body = None


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
            raise ModelHTTPError(response)
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
            "page": {k: state[k] for k in ("url", "title", "text")},
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


def reasoning_controls(setting):
    """Try equivalent controls individually, then let the provider use its defaults."""
    if setting not in (None, "none", "low", "medium", "high"):
        raise ValueError("TEXT_MODEL_REASONING must be none, low, medium, or high")
    if setting == "none":
        return [
            {"reasoning": {"enabled": False}},
            {"reasoning_effort": "none"},
            {"thinking": {"type": "disabled"}},
            {},
        ]
    return [{"reasoning": {"effort": setting or "low"}}, {"reasoning_effort": setting or "low"}, {}]


def token_limits():
    """The output cap is spelled differently across model generations; only the server knows."""
    return [{"max_tokens": 1024}, {"max_completion_tokens": 1024}]


def rejected_parameter(error, control):
    """Only negotiate explicit validation rejections of the parameter currently sent."""
    if error.status not in {400, 422} or not control or not isinstance(error.body, dict):
        return False
    detail = error.body.get("error")
    if not isinstance(detail, dict):
        return False
    field = next(iter(control))
    path = rf"{field}(?:\.[a-z_]+)*"
    param = detail.get("param")
    if param is not None and (not isinstance(param, str) or not re.fullmatch(path, param)):
        return False
    if param is not None and detail.get("code") in {
        "unknown_parameter", "unsupported_parameter", "unsupported_value", "invalid_parameter", "invalid_value",
    }:
        return True
    message = detail.get("message")
    if not isinstance(message, str):
        return False
    # A mere mention (e.g. "max_tokens is too small for reasoning") is not a rejection.
    # Accept "Unrecognized request argument supplied: <field>" without swallowing
    # another parameter name or matching the suffix of an unrelated field.
    return bool(re.search(
        rf"\b(?:unknown|unrecognized|unrecognised|unexpected|unsupported|invalid)\s+(?:request\s+)?"
        rf"(?:parameter|argument|field|key|value)(?:\s+supplied)?\s*:?\s*[`'\"]?(?<![\w.]){path}(?![\w.])"
        rf"|(?<![\w.]){path}[`'\"]?\s+(?:is\s+)?(?:not supported|not allowed|not permitted|unsupported)\b",
        message,
        re.IGNORECASE,
    ))


def field_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "deepseek-chat")
    setting = os.environ.get("TEXT_MODEL_REASONING")
    controls = reasoning_controls(setting)
    url = base + "/chat/completions"
    cache_key = (url, model, setting)
    first = REASONING_CONTROLS.get(cache_key, 0)
    limits = token_limits()
    limit_index = TOKEN_LIMITS.get((url, model), 0)
    started = time.perf_counter()
    body = {
        "model": model,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": TEXT_VALUE},
            {"role": "user", "content": json.dumps(context)},
        ],
    }
    index, warned = first, False
    while True:
        control, limit = controls[index], limits[limit_index]
        if not control and not warned:
            warnings.warn(
                f"Text helper could not enforce reasoning={setting or 'low'}; "
                "omitting the control. Provider defaults apply and reasoning may be enabled.",
                RuntimeWarning,
                stacklevel=2,
            )
            warned = True
        try:
            result = post_json(url, key, {**body, **limit, **control})
        except ModelHTTPError as error:
            # Each parameter negotiates on its own; a rejected cap never consumes a reasoning step.
            if rejected_parameter(error, limit) and limit_index + 1 < len(limits):
                limit_index += 1
                continue
            if rejected_parameter(error, control) and index + 1 < len(controls):
                index += 1
                continue
            raise
        break
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    REASONING_CONTROLS[cache_key] = index
    TOKEN_LIMITS[(url, model)] = limit_index
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
        "reasoning_attempts": index - first + 1,
        "reasoning_control": control,
        "token_limit": next(iter(limit)),
    }
