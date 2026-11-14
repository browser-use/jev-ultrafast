"""SemIf makes choices; an optional small OpenAI-compatible model writes field values."""

import hashlib
import json
import math
import os
import time

import httpx

from .questions import NEXT_ACTION, TARGET, TEXT_VALUE

CLIENT = httpx.Client(http2=True, timeout=25)


def compute_question_spec_hash(*specs: str) -> str:
    """Compute a deterministic hash for given question and rule specifications."""
    hasher = hashlib.sha256()
    for s in specs:
        hasher.update(s.encode("utf-8"))
    return hasher.hexdigest()[:16]


QUESTION_SPEC_HASH = compute_question_spec_hash(NEXT_ACTION, TARGET)


def post_json(url, key, body):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers=headers)
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


def post_decide(state, question, options):
    """Call the SemIf /v1/decide endpoint."""
    base = os.environ.get("SEMIF_BASE_URL", "http://127.0.0.1:8765").rstrip("/")
    url = f"{base}/v1/decide"
    key = os.environ.get("SEMIF_API_KEY")
    body = {
        "state": state,
        "question": question,
        "options": options,
    }
    return post_json(url, key, body)


def validate_choice(answer, ids):
    """Validates a SemIf decide or legacy choice response against expected option IDs."""
    if not isinstance(answer, dict):
        raise ValueError("Invalid SemIf response; no action executed.")
    try:
        choice = answer.get("decision") or answer.get("choice")
        answer["choice"] = choice
        probabilities = answer["probabilities"]
        numbers = [*probabilities.values(), answer["confidence"]]
        valid = (
            choice in ids
            and set(probabilities) == set(ids)
            and all(type(n) in (int, float) and math.isfinite(n) and 0 <= n <= 1 for n in numbers)
            and abs(sum(probabilities.values()) - 1) < 0.05
            and probabilities[choice] >= max(probabilities.values()) - 1e-6
        )
    except (KeyError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("Invalid SemIf response; no action executed.")
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
    """Hierarchical two-stage decision powered by SemIf resident server."""
    elements, targets, controls = action_space(state["actions"])
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")

    op_options = [{"id": key, "description": desc} for key, desc in operations.items()]
    op_state = {
        "page": {k: state[k] for k in ("url", "title", "text")},
        "elements": elements,
        "recent_actions": [
            {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-10:]
        ],
    }
    op_question = f"Goal: {goal}\nRules: {NEXT_ACTION}\nWhich operation should be executed next?"

    started = time.perf_counter()
    op_raw = post_decide(op_state, op_question, op_options)
    operation_answer = validate_choice(op_raw, set(operations))
    operation = operation_answer["choice"]

    target = None
    target_answer = None
    probabilities = {}
    is_single_bypass = False
    target_decision_source = None
    target_raw = None

    if operation in targets:
        candidates = targets[operation]
        candidate_keys = list(candidates.keys())
        if len(candidates) == 1:
            # Only one target available: deterministic, bypass model call
            is_single_bypass = True
            target_decision_source = "deterministic"
            target = candidate_keys[0]
            target_answer = {
                "id": f"decide-target-{target}",
                "choice": target,
                "confidence": 1.0,
                "probabilities": {target: 1.0},
                "decision_source": "deterministic",
                "backend": "deterministic",
                "model": "deterministic",
            }
        elif len(candidates) <= 16:
            target_decision_source = "model"
            target_options = [
                {
                    "id": index,
                    "description": (
                        f"[{index}] {candidates[index]['label']}"
                        + (f" value={candidates[index].get('value')}" if candidates[index].get("value") else "")
                    ),
                }
                for index in candidate_keys
            ]
            target_state = {
                "page": {k: state[k] for k in ("url", "title", "text")},
                "operation": operation,
                "candidate_elements": [
                    {
                        "index": index,
                        "label": candidates[index]["label"],
                        "current_value": candidates[index].get("current_value", candidates[index].get("value", "")),
                        **{
                            k: candidates[index][k]
                            for k in ("role", "checked", "selected", "expanded")
                            if k in candidates[index]
                        },
                    }
                    for index in candidate_keys
                ],
            }
            target_question = (
                f"Goal: {goal}\nOperation: {operation}\nRules: {NEXT_ACTION}\n{TARGET}\n"
                f"Which candidate element is the target for operation {operation}?"
            )
            target_raw = post_decide(target_state, target_question, target_options)
            target_answer = validate_choice(target_raw, set(candidate_keys))
            target_answer["decision_source"] = "model"
            target_answer["backend"] = "semif"
            target = target_answer["choice"]
        else:
            # Paged exploration for more than 16 candidates, capped at MAX_TARGET_PAGES to respect budget
            target_decision_source = "model"
            MAX_TARGET_PAGES = 3
            offset = 0
            page_count = 0
            target = None
            target_answer = None
            while offset < len(candidate_keys) and page_count < MAX_TARGET_PAGES:
                page_count += 1
                chunk = candidate_keys[offset : offset + 15]
                has_more = (offset + 15) < len(candidate_keys) and page_count < MAX_TARGET_PAGES
                current_options = [
                    {
                        "id": index,
                        "description": (
                            f"[{index}] {candidates[index]['label']}"
                            + (f" value={candidates[index].get('value')}" if candidates[index].get("value") else "")
                        ),
                    }
                    for index in chunk
                ]
                if has_more:
                    rem = len(candidate_keys) - offset - 15
                    current_options.append({
                        "id": "MORE_CANDIDATES",
                        "description": f"None of these. Show next candidate elements (remaining: {rem}).",
                    })

                target_state = {
                    "page": {k: state[k] for k in ("url", "title", "text")},
                    "operation": operation,
                    "total_candidates": len(candidate_keys),
                    "showing_range": f"{offset + 1} to {offset + len(chunk)}",
                    "candidate_elements": [
                        {
                            "index": index,
                            "label": candidates[index]["label"],
                            "current_value": candidates[index].get("current_value", candidates[index].get("value", "")),
                            **{
                                k: candidates[index][k]
                                for k in ("role", "checked", "selected", "expanded")
                                if k in candidates[index]
                            },
                        }
                        for index in chunk
                    ],
                }
                target_question = (
                    f"Goal: {goal}\nOperation: {operation}\nRules: {NEXT_ACTION}\n{TARGET}\n"
                    f"Which candidate element is the target for operation {operation}?"
                )
                expected_ids = {opt["id"] for opt in current_options}
                target_raw = post_decide(target_state, target_question, current_options)
                target_answer = validate_choice(target_raw, expected_ids)
                target_answer["decision_source"] = "model"
                target_answer["backend"] = "semif"
                selected_choice = target_answer["choice"]
                if selected_choice == "MORE_CANDIDATES" and has_more:
                    offset += 15
                    continue
                target = selected_choice if selected_choice != "MORE_CANDIDATES" else chunk[0]
                break

            if target is None or target not in candidates:
                target = candidate_keys[0]

        choice = candidates[target]["id"]
        # Preserve reported per-candidate probabilities; pad unobserved candidates with 0.0
        probabilities = {
            a["id"]: target_answer["probabilities"].get(index, 0.0)
            for index, a in candidates.items()
        }
    else:
        choice = controls[operation]["id"] if operation in controls else operation
        probabilities = {choice: operation_answer["probabilities"][operation]}

    raw_answers = {
        "operation": operation_answer,
    }
    if target_answer:
        raw_answers[operation.lower() + "_target"] = target_answer

    model_info = op_raw.get("model", "semif")
    model_name = (
        model_info.get("source") or model_info.get("name") or "semif"
        if isinstance(model_info, dict)
        else str(model_info)
    )
    model_version = (
        model_info.get("version")
        if isinstance(model_info, dict)
        else op_raw.get("version")
    )

    decision_source = "mixed" if is_single_bypass else "model"
    op_spec_hash = compute_question_spec_hash(NEXT_ACTION)
    target_spec_hash = compute_question_spec_hash(NEXT_ACTION, TARGET)

    target_model_name = "deterministic" if is_single_bypass else model_name
    target_model_version = None if is_single_bypass else model_version
    if not is_single_bypass and isinstance(target_raw, dict):
        if target_raw.get("model"):
            raw_tm = target_raw["model"]
            target_model_name = (
                raw_tm.get("source") or raw_tm.get("name") or str(raw_tm)
                if isinstance(raw_tm, dict)
                else str(raw_tm)
            )
            if isinstance(raw_tm, dict) and raw_tm.get("version"):
                target_model_version = raw_tm.get("version")
        if target_raw.get("version"):
            target_model_version = target_raw.get("version")

    provenance = {
        "backend": "semif",
        "model": model_name,
        "model_version": model_version,
        "question_spec_hash": QUESTION_SPEC_HASH,
        "decision_source": decision_source,
        "calibration_surface": "semif_local",
        "stages": {
            "operation": {
                "backend": "semif",
                "model": model_name,
                "model_version": model_version,
                "decision_source": "model",
                "question_spec_hash": op_spec_hash,
            },
        },
    }
    if target:
        provenance["stages"]["target"] = {
            "backend": "deterministic" if is_single_bypass else "semif",
            "model": target_model_name,
            "model_version": target_model_version,
            "decision_source": target_decision_source,
            "question_spec_hash": None if is_single_bypass else target_spec_hash,
        }

    return {
        "choice": choice,
        "operation": operation,
        "target": target,
        "confidence": operation_answer["confidence"],
        "probabilities": probabilities,
        "operation_probabilities": operation_answer["probabilities"],
        "target_probabilities": target_answer["probabilities"] if target_answer else {},
        "target_confidence": target_answer["confidence"] if target_answer else None,
        "raw_answers": raw_answers,
        "backend": "semif",
        "model": model_name,
        "model_version": model_version,
        "question_spec_hash": QUESTION_SPEC_HASH,
        "decision_source": decision_source,
        "provenance": provenance,
        "usage": op_raw.get("usage", {}),
        "latency_ms": round((time.perf_counter() - started) * 1000),
        "request": {
            "operation": {"state": op_state, "question": op_question, "options": op_options},
            "target": target,
        },
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
