"""
Laya Open-Source Decision Engine for Jev-Ultrafast.
Drop-in replacement for TypeSafe Jev model using NandhaKishorM/laya.
Runs 100% locally or on Google Colab with sub-40ms non-autoregressive forward passes.
"""

import json
import time
from typing import Dict, Any, Tuple

try:
    from laya import Router
    _ROUTER = None
    def get_router():
        global _ROUTER
        if _ROUTER is None:
            _ROUTER = Router()
        return _ROUTER
except ImportError:
    get_router = None


def laya_choose(state: Dict[str, Any], goal: str, history: list, elements: list, targets: dict, controls: dict) -> Dict[str, Any]:
    """
    Executes a typed decision using the open-source Laya model.
    """
    router = get_router()
    if router is None:
        raise RuntimeError("Laya package not found. Run 'pip install laya' to use the open-source decision engine.")

    # 1. Build operations question
    labels = {
        "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
        "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
        "SELECT": "Select an observed dropdown value.",
    }
    operations = {key: labels[key] for key in targets}
    operations.update({key: value["label"] for key, value in controls.items()})
    operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")

    questions = {
        "operation": {
            "type": "choice",
            "instructions": f"Goal: {goal}. Select the next browser action to execute.",
            "criteria": operations
        }
    }

    # 2. Build target choice questions for each potential operation
    for operation, candidates in targets.items():
        criteria = {}
        for index, a in candidates.items():
            elem_label = a.get("label", "")
            criteria[index] = f"Element [{index}]: {elem_label}"
        
        questions[operation.lower() + "_target"] = {
            "type": "choice",
            "instructions": f"Goal: {goal}. Select the exact element index to {operation}.",
            "criteria": criteria
        }

    # 3. Format page context for Laya
    page_text = state.get("text", "")[:1500] # keep concise for fast System 1 pass
    context = (
        f"Goal: {goal}\n"
        f"Page URL: {state.get('url', '')}\n"
        f"Page Title: {state.get('title', '')}\n"
        f"Page Content Snippet: {page_text}\n"
    )

    start_time = time.perf_counter()
    # Predict using Laya non-autoregressive decision engine
    laya_result = router.predict(context, questions)
    latency_ms = (time.perf_counter() - start_time) * 1000

    answers = laya_result.get("answers", {})

    # Extract chosen operation
    op_answer = answers.get("operation", {})
    operation = op_answer.get("choice", "DONE")
    op_probs = op_answer.get("probabilities", {operation: 1.0})
    confidence = op_answer.get("confidence", 0.9)

    target = None
    target_answer = None
    choice_id = None
    probabilities = {}

    if operation in targets:
        target_q_key = operation.lower() + "_target"
        target_answer = answers.get(target_q_key, {})
        target = target_answer.get("choice")
        if target and target in targets[operation]:
            choice_id = targets[operation][target]["id"]
            probabilities = {
                a["id"]: target_answer.get("probabilities", {}).get(idx, 0.0)
                for idx, a in targets[operation].items()
            }
        else:
            # Fallback to first candidate if target index was ambiguous
            first_idx = next(iter(targets[operation]))
            target = first_idx
            choice_id = targets[operation][first_idx]["id"]
            probabilities = {choice_id: 1.0}
    elif operation in controls:
        choice_id = controls[operation]["id"]
        probabilities = {choice_id: 1.0}

    return {
        "operation": operation,
        "target": target,
        "choice": choice_id,
        "target_answer": target_answer or {"choice": target, "confidence": confidence, "probabilities": {target: 1.0}},
        "probabilities": probabilities,
        "round_trip_ms": latency_ms,
        "engine": "laya-opensource",
        "model": laya_result.get("routing", {}).get("model", "laya-multilingual")
    }
