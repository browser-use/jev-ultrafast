"""TypeSafe AI System 1 decision engine implementation."""

import os
import time
from typing import Any, Dict, List

from ..questions import NEXT_ACTION, TARGET
from .base import BaseEngine, log_debug


class TypeSafeEngine(BaseEngine):
    """Proprietary TypeSafe Jev decision engine via api.typesafe.ai."""

    def __init__(self, api_key: str = None, base_url: str = None, model_name: str = None):
        self.api_key = api_key or os.environ.get("TYPESAFE_API_KEY", "")
        self.base_url = (base_url or os.environ.get("TYPESAFE_BASE_URL", "https://api.typesafe.ai/v1/systemone")).rstrip("/")
        self.model_name = model_name or os.environ.get("TYPESAFE_MODEL", "jev-latest")

    @property
    def name(self) -> str:
        return f"typesafe:{self.model_name}"

    def _validate_choice(self, answer: Dict[str, Any], ids: Any) -> Dict[str, Any]:
        from .base import validate_choice
        try:
            return validate_choice(answer, ids)
        except ValueError:
            raise ValueError("Invalid TypeSafe response; no action executed.")

    def choose(
        self,
        state: Dict[str, Any],
        goal: str,
        history: List[Dict[str, Any]],
        elements: List[Dict[str, Any]],
        targets: Dict[str, Dict[str, Any]],
        controls: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        from .. import model

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
            "model": self.model_name,
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
        log_debug(f"Sending decision request to TypeSafe API: {self.base_url}")
        result = model.post_json(self.base_url, os.environ.get("TYPESAFE_API_KEY", self.api_key), body)
        latency_ms = round((time.perf_counter() - started) * 1000)

        operation_answer = self._validate_choice(result["answers"].get("operation", {}), operations)
        operation = operation_answer["choice"]
        target = None
        target_answer = None
        probabilities = {}
        if operation in targets:
            target_answer = self._validate_choice(result["answers"].get(operation.lower() + "_target", {}), targets[operation])
            target = target_answer["choice"]
            choice = targets[operation][target]["id"]
            probabilities = {a["id"]: target_answer["probabilities"][index] for index, a in targets[operation].items()}
        else:
            choice = controls[operation]["id"] if operation in controls else operation
            probabilities[choice] = operation_answer["probabilities"][operation]

        log_debug(f"TypeSafe decision: op={operation}, target={target}, latency={latency_ms}ms")
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
            "model": result.get("model", self.model_name),
            "usage": result.get("usage", {}),
            "latency_ms": latency_ms,
            "engine": "typesafe",
            "request": body,
        }
