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


NO_TARGET = (
    "None of the offered elements is the right target for this operation: the needed element is not in view, "
    "or every offered field already holds its requested value."
)


def choose(state, goal, history):
    # A field the goal has no value for was skipped once. Code removes it from the fill candidates:
    # the model cannot choose an omitted option, which is more reliable than asking it not to.
    no_value = {h.get("action") for h in history if h.get("kind") == "skip"}
    # An element under an overlay (cookie banner, popup) cannot be acted on. It is a fact, not a candidate;
    # the overlay's own button stays offered.
    covered = [a for a in state["actions"] if (a.get("rect") or {}).get("covered")]
    hidden = {a["node"] for a in covered}
    actions = [
        a for a in state["actions"]
        if a.get("node") not in hidden and not (a["kind"] == "fill" and a["label"] in no_value)
    ]
    elements, targets, controls = action_space(actions)
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    outside = state.get("out_of_view") or []

    def reveals(key):
        direction = {"SCROLL_DOWN": "below", "SCROLL_UP": "above"}.get(key)
        fields = [f for f in outside if f["direction"] == direction]
        if not fields:
            return ""
        names = ", ".join(f["label"][:30] + ("" if f["value"] else " (empty)") for f in fields[:6])
        return f" Reveals form fields that are not in view: {names}."

    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] + reveals(key) for key, value in controls.items()})
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
            }
            | {"none": NO_TARGET},
            "instructions": {"goal": goal, "operation": operation, "rules": [NEXT_ACTION, TARGET]},
        }
    body = {
        "model": os.environ.get("TYPESAFE_MODEL", "jev-latest"),
        "state": {
            "page": {k: state[k] for k in ("url", "title", "text")},
            "elements": elements,
            "fields_out_of_view": [
                {k: f[k] for k in ("label", "role", "value", "required", "direction")} for f in outside
            ],
            "elements_covered_by_an_overlay": sorted({a["label"].split(" → ")[0] for a in covered}),
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed", "note")} for h in history[-10:]
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
    composed = None

    seen = {h.get("reveal") for h in history if h.get("reveal") is not None}
    skipped = {h.get("action") for h in history if h.get("kind") == "skip"}

    def reveal(prefer=None):
        """Code owns the workflow: scroll the nearest still-empty out-of-view field to the centre, once each."""
        fresh_fields = [f for f in outside if f["node"] not in seen and f["label"] not in skipped]
        pool = [f for f in fresh_fields if not f["value"]] or fresh_fields
        if prefer:
            pool = [f for f in pool if f["direction"] == prefer] or pool
        for field in pool:
            key = "SCROLL_DOWN" if field["direction"] == "below" else "SCROLL_UP"
            if key in controls:
                controls[key]["reveal"] = field["node"]
                controls[key]["label"] = "Scroll to reveal " + field["label"][:40]
                return key
        return None

    if operation in targets:
        # Unused target heads cannot cause an action. Validate the head selected by the operation.
        offered = targets[operation]
        target_answer = validate_choice(
            result["answers"].get(operation.lower() + "_target", {}), {**offered, "none": None}
        )
        target = target_answer["choice"]
        if target == "none":
            composed = reveal()
            if composed is None:  # nothing to reveal: keep the old behaviour, best real candidate
                target = max(offered, key=lambda index: target_answer["probabilities"][index])
        elif operation == "TYPE_TEXT" and history:
            last, picked = history[-1], offered[target]
            # The exact loop seen on real forms: the operation head (rightly) wants more typing, but the only
            # offered field was filled a moment ago. The two questions cannot see each other; code composes them.
            retyping = (
                last.get("kind") == "fill"
                and last.get("choice") == picked["id"]
                and bool(picked.get("value"))
                and picked.get("value") == last.get("text")
            )
            if retyping:
                composed = reveal()
    elif operation in ("SCROLL_DOWN", "SCROLL_UP"):
        reveal("below" if operation == "SCROLL_DOWN" else "above")

    if composed:
        operation, target = composed, None
        choice = controls[composed]["id"]
        probabilities = {choice: operation_answer["probabilities"].get(composed, 0.0)}
    elif operation in targets:
        choice = targets[operation][target]["id"]
        probabilities = {
            a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()
        }
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
        "composed": composed,
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
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            **reasoning,
            "messages": [
                {"role": "system", "content": TEXT_VALUE},
                {
                    "role": "user",
                    "content": json.dumps(context),
                },
            ],
        },
    )
    try:
        output = json.loads(result["choices"][0]["message"]["content"])
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    return value, {
        "model": model,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": result.get("usage", {}),
    }
