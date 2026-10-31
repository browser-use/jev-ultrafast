"""TypeSafe makes choices; an optional small OpenAI-compatible model writes field values."""

import atexit
import json
import math
import os
import threading
import time

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

_CLIENT = None
_CLIENT_LOCK = threading.Lock()


def _http():
    """Lazily build the shared client; HTTP/2 needs the optional h2 extra."""
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                timeout = float(os.environ.get("MODEL_TIMEOUT", "25"))
                proxy = _http_proxy()
                try:
                    _CLIENT = httpx.Client(http2=True, timeout=timeout, trust_env=False, proxy=proxy)
                except ImportError:
                    if proxy and proxy.lower().startswith("socks"):
                        proxy = None
                    _CLIENT = httpx.Client(timeout=timeout, trust_env=False, proxy=proxy)
                atexit.register(_CLIENT.close)
    return _CLIENT


def _http_proxy():
    """Pick an HTTP(S) proxy for the API calls, ignoring SOCKS URLs.

    The proxy environment can carry `ALL_PROXY=socks5://…` on CN/corporate machines. httpx turns a
    SOCKS URL into an `ImportError` at client construction unless `socksio` is installed, so each
    request would crash. Only forward http/https proxies; a SOCKS proxy needs `httpx[socks]`.
    """
    for name in ("TYPESAFE_HTTP_PROXY", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        url = os.environ.get(name)
        if url and not url.lower().startswith("socks"):
            return url
    return None


def post_json(url, key, body):
    for attempt in range(3):
        try:
            response = _http().post(url, json=body, headers={"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        try:
            return response.json()
        except ValueError:
            url_host = url.split("/api", 1)[0]
            raise RuntimeError(
                f"Model provider at {url_host} returned HTTP {response.status_code} with a non-JSON body."
            ) from None


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
        element = indices.get(node)
        if element is None:
            element = {k: action[k] for k in ("role", "value", "checked", "selected", "expanded") if k in action}
            element.update(index=str(len(elements) + 1), label=action["label"].split(" → ")[0], operations=[])
            if kind == "select":
                element["value"] = action.get("current_value", "")
                element["options"] = []
            indices[node] = element
            elements.append(element)
        operation = operations[kind]
        group = targets.setdefault(operation, {})
        if operation not in element["operations"]:
            element["operations"].append(operation)
        target = element["index"]
        if kind == "select":
            target = f"{target}:{len(element['options']) + 1}"
            element["options"].append({"index": target, "label": action["label"], "value": action["value"]})
        group[target] = action
    return elements, targets, controls


def choose(state, goal, history, space=None):
    if space is None:
        elements, targets, controls = action_space(state["actions"])
    else:
        elements, targets, controls = space
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
    result = post_json(
        os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai/v1").rstrip("/") + "/systemone",
        os.environ["TYPESAFE_API_KEY"],
        body,
    )
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
        "page": {"title": page["title"], "text": page["text"]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def field_text(context):
    key = os.environ.get("TEXT_MODEL_API_KEY")
    if not key:
        raise ValueError(
            "TYPE_TEXT needs TEXT_MODEL_API_KEY (see .env.example). "
            "No text is hardcoded or guessed by the executor."
        )
    base = os.environ.get("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
    model = os.environ.get("TEXT_MODEL", "inception/mercury-2.5")
    reasoning = {}
    mode = os.environ.get("TEXT_MODEL_REASONING", "").lower()
    if mode == "none":
        reasoning = {"reasoning": {"enabled": False}}
    elif mode in {"low", "medium", "high"}:
        reasoning = {"reasoning": {"effort": mode}}
    elif "api.deepseek.com/" in base:
        reasoning = {"thinking": {"type": "disabled"}}
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
