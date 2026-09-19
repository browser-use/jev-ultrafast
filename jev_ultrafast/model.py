"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import json
import math
import os
import time

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)


def provider_error_detail(response):
    """The provider's own explanation, when it sends one.

    Unknown-field rejections are only actionable with this text: Fireworks answers an unsupported
    `reasoning` key with "Extra inputs are not permitted", which reads nothing like the bare
    "HTTP 400" that used to be all the caller saw.
    """
    try:
        payload = response.json()
    except ValueError:
        return ""
    if isinstance(payload, list) and payload:  # the Gemini shim wraps errors in a list
        payload = payload[0]
    if not isinstance(payload, dict):
        return ""
    error = payload.get("error", payload)
    if not isinstance(error, dict):
        return ""
    message = error.get("message")
    if not isinstance(message, str) or not message.strip():
        return ""
    return ": " + " ".join(message.split())[:200]


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
            raise RuntimeError(
                f"Model provider returned HTTP {response.status_code}"
                f"{provider_error_detail(response)}; no action executed."
            )
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


# OpenAI-compatible endpoints disagree about the request fields this helper sends, and several
# reject an unknown key outright instead of ignoring it. Verified live, one field at a time:
#
#   Fireworks  (deepseek-v4p1-flash)   reasoning -> 400 "Extra inputs are not permitted"
#                                      thinking  -> 200
#   Gemini     (openai shim)           reasoning -> 400 'Unknown name "reasoning"'
#                                      thinking  -> 400
#   Groq       (openai/gpt-oss-20b)    reasoning -> 400 "property 'reasoning' is unsupported"
#                                      thinking  -> 400 "property 'thinking' is unsupported"
#   OpenRouter (inception/mercury-2.5) reasoning -> 200, thinking -> 200
#   OpenAI     (o4-mini, o3-mini)      reasoning -> 400 "Unknown parameter: 'reasoning'"
#                                      reasoning_effort -> 200, max_tokens -> 400
#                                      "Use 'max_completion_tokens' instead"
#   OpenAI     (gpt-4o-mini)           reasoning_effort -> 400 unrecognized, max_tokens -> 200
#
# So no single spelling is safe everywhere, and on OpenAI the right answer depends on the model, not
# the host. `omit` is the only option every endpoint accepted, so it is the fallback. Driving this
# off a provider table (rather than one hardcoded DeepSeek substring) is what stops Fireworks 400ing
# on `reasoning` while OpenRouter keeps the payload it documents.
REASONING_DIALECTS = {
    "reasoning": {"reasoning": {"effort": "low"}},
    "effort": {"reasoning_effort": "low"},
    "thinking": {"thinking": {"type": "disabled"}},
    "omit": {},
}
REASONING_OFF = {
    "reasoning": {"reasoning": {"enabled": False}},
    "effort": {"reasoning_effort": "low"},  # o-series has no "off"; low is its floor
    "thinking": {"thinking": {"type": "disabled"}},
    "omit": {},
}
PROVIDER_DIALECTS = (
    ("openrouter.ai", "reasoning"),
    ("api.deepseek.com", "thinking"),
    ("fireworks.ai", "thinking"),
    ("api.groq.com", "omit"),
    ("generativelanguage.googleapis.com", "omit"),
)
OPENAI_HOST = "api.openai.com"
OPENAI_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")
DEEPSEEK_BASE = "https://api.deepseek.com/v1"


def is_first_party_deepseek(model):
    """Models that legitimately pair with the DeepSeek default endpoint."""
    return model == "deepseek-chat" or model.startswith("deepseek-")


def is_openai_reasoning_model(model):
    """OpenAI's reasoning families: they take `max_completion_tokens` and a flat `reasoning_effort`
    where gpt-4o/gpt-4.1 take `max_tokens` and reject `reasoning_effort` outright."""
    return model.startswith(OPENAI_REASONING_PREFIXES)


def reasoning_dialect(base, model):
    if OPENAI_HOST in base:
        return "effort" if is_openai_reasoning_model(model) else "omit"
    for host, dialect in PROVIDER_DIALECTS:
        if host in base:
            return dialect
    return "omit"


def token_limit_field(base, model):
    """o-series and gpt-5 400 on `max_tokens` ("Use 'max_completion_tokens' instead")."""
    if OPENAI_HOST in base and is_openai_reasoning_model(model):
        return "max_completion_tokens"
    return "max_tokens"


def field_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
    base = os.environ.get("TEXT_MODEL_BASE_URL", "").strip().rstrip("/")
    model = os.environ.get("TEXT_MODEL", "").strip()
    if not base:
        # The DeepSeek fallbacks only make sense together. With TEXT_MODEL set but no endpoint,
        # the old default sent the configured key to api.deepseek.com — and README.md tells you
        # that key is an OpenRouter key, so it left for a vendor it does not belong to. Paired
        # defaults are kept for a DeepSeek setup (any first-party deepseek-* model, not just
        # deepseek-chat); the ambiguous half-configured case fails loudly, like the missing key.
        if model and not is_first_party_deepseek(model):
            raise ValueError(
                "TEXT_MODEL is set but TEXT_MODEL_BASE_URL is not, so the key would be sent to the "
                "default endpoint. Set TEXT_MODEL_BASE_URL to the endpoint that issued the key."
            )
        base = DEEPSEEK_BASE
    model = model or "deepseek-chat"
    dialect = reasoning_dialect(base, model)
    setting = os.environ.get("TEXT_MODEL_REASONING", "").strip().lower()
    if setting == "omit":
        # Some OpenAI-compatible endpoints reject an unknown `reasoning` key outright: Fireworks
        # ("Extra inputs are not permitted"), Groq ("property 'reasoning' is unsupported"), and
        # Gemini's OpenAI-compatible shim ("Unknown name \"reasoning\""). For those, leave the
        # field out entirely — `none` still sends the key, and 400s.
        reasoning = {}
    elif setting == "none":
        # Disable reasoning in whichever dialect this endpoint speaks. Sending the OpenRouter
        # spelling at a `thinking` endpoint (DeepSeek, Fireworks) is itself an unknown key.
        reasoning = REASONING_OFF[dialect]
    elif setting:
        raise ValueError(
            "TEXT_MODEL_REASONING=%r is not a recognised setting; use 'none' (disable reasoning in "
            "the endpoint's own dialect) or 'omit' (send no reasoning field at all)." % setting
        )
    else:
        reasoning = REASONING_DIALECTS[dialect]
    started = time.perf_counter()
    result = post_json(
        base + "/chat/completions",
        key,
        {
            "model": model,
            token_limit_field(base, model): 1024,
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
