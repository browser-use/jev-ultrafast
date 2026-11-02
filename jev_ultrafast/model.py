"""The policy chooses via TypeSafe Jev or any configured provider; a small model writes field values."""

import json
import math
import os
import time

import httpx

from . import providers
from .questions import MAX_PLAN_STEPS, NEXT_ACTION, PLANNER_SYSTEM, POLICY_SYSTEM, TARGET, TEXT_VALUE
from .redact import redact

CLIENT = httpx.Client(http2=True, timeout=25)


def post_json(url, key, body, headers=None):
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers=headers or {"Authorization": f"Bearer {key}"})
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            # Include the provider's own message so the exact cause is visible in the sidebar.
            detail = redact(" ".join(response.text.split()))[:300]
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}: {detail}; no action executed.")
        try:
            return response.json()
        except ValueError:
            raise RuntimeError(
                "Model provider returned a non-JSON response; check that the base URL ends in /v1."
            ) from None
    raise RuntimeError("Model unavailable")


def post_stream(url, key, body, headers=None, on_delta=None):
    """POST one request and consume the SSE stream; returns (text, usage, model).

    Every text chunk is passed to on_delta as it arrives (reasoning chunks too,
    for the live "thinking" view — but only content builds the returned text).
    Raises the same RuntimeErrors as post_json so error handling stays uniform.
    Retries happen only before the first chunk, never mid-stream.
    """
    request_headers = headers or {"Authorization": f"Bearer {key}"}
    for attempt in range(3):
        parts = []
        usage = {}
        model_id = None
        served = False
        try:
            with CLIENT.stream("POST", url, json=body, headers=request_headers) as response:
                if response.status_code in {429, 529, 503} and attempt < 2:
                    time.sleep(0.5 * 2**attempt)
                    continue
                if response.is_error:
                    detail = redact(" ".join(response.read().decode("utf-8", "replace").split()))[:300]
                    raise RuntimeError(
                        f"Model provider returned HTTP {response.status_code}: {detail}; no action executed."
                    )
                for line in response.iter_lines():
                    content, usage_update, chunk_model, reasoning = _sse_chunk(line)
                    if model_id is None and chunk_model:
                        model_id = chunk_model
                    if usage_update:
                        usage.update(usage_update)
                    if reasoning and on_delta:
                        on_delta(reasoning)
                    if content:
                        served = True
                        parts.append(content)
                        if on_delta:
                            on_delta(content)
                return "".join(parts), usage, model_id
        except httpx.HTTPError:
            if served:
                raise RuntimeError("Model stream failed mid-response; no action executed.") from None
            if attempt < 2:
                time.sleep(0.5 * 2**attempt)
                continue
            raise RuntimeError("Model connection failed; no action executed.") from None
    raise RuntimeError("Model unavailable")


def _sse_chunk(line):
    """Parse one SSE line → (content, usage_update, model_id, reasoning).

    Handles the OpenAI shape (choices[].delta.content / delta.reasoning_content,
    used by Ollama, llama.cpp and LM Studio too) and the Anthropic event shape.
    """
    if not line or not line.startswith("data:"):
        return "", {}, None, ""
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return "", {}, None, ""
    try:
        event = json.loads(payload)
    except ValueError:
        return "", {}, None, ""
    if not isinstance(event, dict):
        return "", {}, None, ""
    model_id = event.get("model") if isinstance(event.get("model"), str) else None
    usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
    kind = event.get("type")
    if kind == "content_block_delta":  # Anthropic
        delta = event.get("delta") or {}
        thinking = delta.get("thinking") if isinstance(delta.get("thinking"), str) else ""
        return delta.get("text") or "", {}, model_id, thinking
    if kind == "message_start":
        message = event.get("message") or {}
        return "", message.get("usage") or {}, model_id, ""
    if kind == "message_delta":
        return "", event.get("usage") or {}, model_id, ""
    choices = event.get("choices") or []
    if choices:
        delta = (choices[0] or {}).get("delta") or {}
        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
        if not isinstance(reasoning, str):
            reasoning = ""
        if not usage:  # some servers nest usage inside the choice instead of the event
            nested = (choices[0] or {}).get("usage")
            if isinstance(nested, dict):
                usage = nested
        return delta.get("content") or "", usage, model_id, reasoning
    return "", usage, model_id, ""


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


def operation_catalog(targets, controls):
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")
    return operations


def choose(state, goal, history):
    """TypeSafe Jev when its key is present; otherwise the configured OpenAI-compatible/Anthropic provider."""
    if os.environ.get("TYPESAFE_API_KEY"):
        return typesafe_choose(state, goal, history)
    return provider_choose(state, goal, history)


def policy_description():
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ.get("TYPESAFE_MODEL", "jev-latest")
    try:
        provider = providers.resolve("policy")
    except ValueError:
        return "no policy model configured"
    return f"{provider['name']}:{provider['model']}"


def typesafe_choose(state, goal, history):
    elements, targets, controls = action_space(state["actions"])
    operations = operation_catalog(targets, controls)
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
        "provider": "typesafe",
    }


def _policy_request(goal, state, elements, operations, history):
    lines = [f"GOAL: {goal}", "", f"PAGE: {state['url']} — {state['title']}"]
    if state.get("text"):
        lines.append(f"PAGE TEXT (excerpt): {state['text']}")
    lines.append("")
    lines.append("ELEMENTS (index · role · label · current value · operations):")
    for element in elements:
        line = f"[{element['index']}] {element['role']} · {element['label']}"
        if element.get("value"):
            line += f" · value {json.dumps(element['value'])}"
        for key in ("checked", "selected", "expanded"):
            if element.get(key) is not None:
                line += f" · {key}={json.dumps(element[key])}"
        line += f" · ops: {', '.join(element['operations'])}"
        lines.append(line)
        for option in element.get("options", []):
            lines.append(f"    {option['index']} {json.dumps(option['label'])} → value {json.dumps(option['value'])}")
    lines.append("")
    lines.append("AVAILABLE OPERATIONS (choose exactly one):")
    lines.extend(f"{name}: {description}" for name, description in operations.items())
    lines.append("")
    recent = [
        f"{h['step']}. {h['action']} ({h['kind']}"
        + (f", typed {json.dumps(h['text'])}" if h.get("text") else "")
        + (") — page changed" if h.get("page_changed") else ") — no observed change")
        for h in history[-10:]
    ]
    lines.append("RECENT ACTIONS:" if recent else "RECENT ACTIONS: none")
    lines.extend(recent)
    return "\n".join(lines)


def _validate_llm_choice(answer, operations, targets):
    if not isinstance(answer, dict) or answer.get("operation") not in operations:
        raise ValueError("invalid operation")
    operation = answer["operation"]
    target = None
    if operation in targets:
        target = str(answer.get("target", "")).strip()
        if target not in targets[operation]:
            raise ValueError("invalid target")
    confidence = answer.get("confidence")
    if not (type(confidence) in (int, float) and 0 <= confidence <= 1):
        confidence = 1.0
    return operation, target, float(confidence)


def provider_choose(state, goal, history):
    """One request to the configured provider returns operation and target together."""
    elements, targets, controls = action_space(state["actions"])
    operations = operation_catalog(targets, controls)
    provider = providers.resolve("policy")
    user = _policy_request(goal, state, elements, operations, history)
    started = time.perf_counter()
    content, meta = None, None
    for attempt in range(2):
        message = user
        if attempt:
            message += "\n\nYour previous reply was rejected. Respond again with ONLY the JSON object."
        content, meta = providers.chat(provider, POLICY_SYSTEM, message, max_tokens=512)
        try:
            answer = providers.extract_json(content)
            operation, target, confidence = _validate_llm_choice(answer, operations, targets)
            break
        except ValueError as error:
            if attempt:
                raise ValueError(f"Policy model returned an invalid choice ({error}); no action executed.") from None
    if target is not None:
        choice = targets[operation][target]["id"]
    else:
        choice = controls[operation]["id"] if operation in controls else operation
    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": confidence,
        "probabilities": {choice: confidence},
        "operation_probabilities": {},
        "target_probabilities": {},
        "target_confidence": confidence if target is not None else None,
        "raw_answers": answer,
        "model": meta["model"],
        "usage": meta.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": {
            "provider": provider["name"],
            "model": provider["model"],
            "messages": [
                {"role": "system", "content": POLICY_SYSTEM},
                {"role": "user", "content": user},
            ],
        },
        "provider": "llm",
    }


def field_context(goal, action, page, history):
    return {
        "goal": goal,
        "field": {k: action.get(k) for k in ("label", "role", "value")},
        "page": {"title": page["title"], "text": page["text"][:6000]},
        "recent_actions": [{k: h.get(k) for k in ("action", "text")} for h in history[-6:]],
    }


def field_text(context):
    provider = providers.resolve("text")
    started = time.perf_counter()
    content, meta = providers.chat(provider, TEXT_VALUE, json.dumps(context), max_tokens=1024)
    try:
        output = providers.extract_json(content)
        value = output["text"]
        if set(output) != {"text"} or not isinstance(value, str) or not value.strip() or len(value) > 2000:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise ValueError("Text helper returned no valid field value; nothing typed.") from None
    return value, {
        "model": provider["model"],
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "usage": meta.get("usage", {}),
    }


def planning_config():
    """The planner provider config when the user configured one; None keeps the original single-goal loop."""
    if not any(os.environ.get(name, "").strip() for name in ("PLANNER_PROVIDER", "PLANNER_BASE_URL")):
        return None
    return providers.resolve("planner")


def _validate_steps(answer):
    steps = answer.get("steps") if isinstance(answer, dict) else None
    if not isinstance(steps, list) or not steps or len(steps) > MAX_PLAN_STEPS:
        raise ValueError("invalid steps")
    cleaned = []
    for step in steps:
        if not isinstance(step, str) or not step.strip() or len(step) > 500:
            raise ValueError("invalid step")
        cleaned.append(step.strip())
    return cleaned


def _planner_request(mission, page, *, reason=None, plan=None, plan_index=0, history=None):
    lines = ["MISSION: " + mission, "", f"PAGE: {page.get('url', '')} — {page.get('title', '')}"]
    if reason:
        done = ["  ✓ " + step for step in (plan or [])[:plan_index]]
        remaining = ["  ✗ " + step for step in (plan or [])[plan_index:]]
        lines += ["", "PREVIOUS PLAN (✓ completed, ✗ not completed):", *(done or ["  none"]), *remaining]
        lines += ["", "PROBLEM: " + reason]
        if history:
            lines += ["", "RECENT ACTIONS:"]
            lines += [f"{h.get('step', '')}. {h.get('action', '')} ({h.get('kind', '')})" for h in history[-6:]]
        lines += ["", "Produce a corrected plan for the REMAINING work only. Reply with the JSON steps list."]
    else:
        lines += ["", "Produce the ordered checklist of browser steps for the mission. Reply with the JSON steps list."]
    return "\n".join(lines)


def plan_steps(mission, page):
    """The planner decomposes the mission into a short ordered checklist of browser steps."""
    provider = providers.resolve("planner")
    content, _meta = providers.chat(provider, PLANNER_SYSTEM, _planner_request(mission, page), max_tokens=1024)
    return _validate_steps(providers.extract_json(content))


def replan_steps(mission, plan, plan_index, reason, page, history):
    """Replacement steps for the remaining plan after a blocked or stalled step."""
    provider = providers.resolve("planner")
    user = _planner_request(mission, page, reason=reason, plan=plan, plan_index=plan_index, history=history)
    content, _meta = providers.chat(provider, PLANNER_SYSTEM, user, max_tokens=1024)
    return _validate_steps(providers.extract_json(content))
