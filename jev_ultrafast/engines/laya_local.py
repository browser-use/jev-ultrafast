"""Local in-process Laya decision engine using NandhaKishorM/laya."""

import os
import time
from typing import Any, Dict, List

from .base import BaseEngine, log_debug


class LayaLocalEngine(BaseEngine):
    """
    Runs the open-source Laya model locally in Python.
    Suitable for machines with >= 2GB free RAM or GPU acceleration.
    """

    def __init__(self):
        self._router = None

    @property
    def name(self) -> str:
        return "laya-local"

    def _get_router(self):
        if self._router is None:
            try:
                from laya import Router
            except ImportError:
                raise RuntimeError(
                    "The 'laya' package is not installed. Run 'pip install laya' "
                    "or configure a remote endpoint via LAYA_ENDPOINT to use Google Colab."
                ) from None
            try:
                log_debug("Initializing local Laya Router...")
                self._router = Router()
            except OSError as err:
                if "1455" in str(err) or "paging file" in str(err).lower():
                    raise RuntimeError(
                        "Insufficient system memory/pagefile to load local Laya weights into RAM.\n"
                        "Solution: Set LAYA_ENDPOINT to run on Google Colab (Free GPU/16GB RAM) "
                        "or increase Windows virtual memory."
                    ) from err
                raise
        return self._router

    def choose(
        self,
        state: Dict[str, Any],
        goal: str,
        history: List[Dict[str, Any]],
        elements: List[Dict[str, Any]],
        targets: Dict[str, Dict[str, Any]],
        controls: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        router = self._get_router()

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
                "criteria": operations,
            }
        }
        for operation, candidates in targets.items():
            criteria = {}
            for index, a in candidates.items():
                elem_label = a.get("label", "")
                criteria[index] = f"Element [{index}]: {elem_label}"
            questions[operation.lower() + "_target"] = {
                "type": "choice",
                "instructions": f"Goal: {goal}. Select the exact element index to {operation}.",
                "criteria": criteria,
            }

        page_text = state.get("text", "")[:1800]
        context = (
            f"Goal: {goal}\n"
            f"Page URL: {state.get('url', '')}\n"
            f"Page Title: {state.get('title', '')}\n"
            f"Page Content Snippet: {page_text}\n"
        )

        started = time.perf_counter()
        laya_result = router.predict(context, questions)
        latency_ms = round((time.perf_counter() - started) * 1000)

        answers = laya_result.get("answers", {})
        op_answer = answers.get("operation", {})
        operation = op_answer.get("choice", "DONE")
        confidence = op_answer.get("confidence", 0.9)

        target = None
        target_answer = None
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
                first_idx = next(iter(targets[operation]))
                target = first_idx
                choice_id = targets[operation][first_idx]["id"]
                probabilities = {choice_id: 1.0}
        elif operation in controls:
            choice_id = controls[operation]["id"]
            probabilities = {choice_id: 1.0}
        else:
            choice_id = operation
            probabilities = {choice_id: 1.0}

        log_debug(f"Local Laya decision: op={operation}, target={target}, latency={latency_ms}ms")
        return {
            "choice": choice_id,
            "operation": operation,
            "target": target,
            "confidence": confidence,
            "probabilities": probabilities,
            "operation_probabilities": op_answer.get("probabilities", {operation: 1.0}),
            "target_probabilities": target_answer.get("probabilities", {}) if target_answer else {},
            "target_confidence": target_answer.get("confidence") if target_answer else None,
            "raw_answers": answers,
            "model": laya_result.get("routing", {}).get("model", "laya-local"),
            "usage": {},
            "latency_ms": latency_ms,
            "engine": "laya-local",
        }
