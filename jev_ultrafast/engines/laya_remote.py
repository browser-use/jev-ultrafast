"""Remote Laya / Open-Source Decision Engine client.

Connects to a remote Laya inference microservice hosted on Google Colab, RunPod,
vLLM, or self-hosted GPU/CPU servers. Offloads all memory and model weights
from the local client, achieving sub-40ms decision speed over HTTP.
"""

import json
import os
import time
from typing import Any, Dict, List

import httpx

from .base import BaseEngine, log_debug, validate_choice


class LayaRemoteEngine(BaseEngine):
    """
    Connects to an external Laya service (e.g. Google Colab with GPU or Docker container).
    Uses HTTP/2 with keep-alive connections for near-instant latency.
    """

    def __init__(self, endpoint_url: str = None, api_key: str = None, timeout: float = None):
        url = endpoint_url or os.environ.get("LAYA_ENDPOINT") or os.environ.get("DECISION_API_URL") or "http://localhost:8000"
        self.endpoint_url = url.rstrip("/")
        self.api_key = api_key or os.environ.get("LAYA_API_KEY", "")
        self.timeout = timeout or float(os.environ.get("LAYA_TIMEOUT", 45.0))
        self._client = httpx.Client(http2=True, timeout=self.timeout)

    @property
    def name(self) -> str:
        return f"laya-remote:{self.endpoint_url}"

    def _post(self, path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.endpoint_url}{path}" if path.startswith("/") else f"{self.endpoint_url}/{path}"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        for attempt in range(3):
            try:
                resp = self._client.post(url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                if attempt < 2:
                    time.sleep(0.3 * (2 ** attempt))
                    continue
                raise RuntimeError(f"Failed to connect to remote Laya engine at {url}: {exc}") from None

            if resp.status_code in (429, 502, 503, 504) and attempt < 2:
                time.sleep(0.5 * (2 ** attempt))
                continue
            if resp.is_error:
                raise RuntimeError(f"Remote Laya server returned HTTP {resp.status_code}: {resp.text}")
            return resp.json()
        raise RuntimeError(f"Remote Laya engine at {url} unreachable after 3 attempts.")

    def choose(
        self,
        state: Dict[str, Any],
        goal: str,
        history: List[Dict[str, Any]],
        elements: List[Dict[str, Any]],
        targets: Dict[str, Dict[str, Any]],
        controls: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        labels = {
            "CLICK": "Click an element, button, menu option, autocomplete suggestion, or calendar day.",
            "TYPE_TEXT": "Enter or replace text in an editable field. A small LLM will supply the value from the goal.",
            "SELECT": "Select an observed dropdown value.",
        }
        operations = {key: labels[key] for key in targets}
        operations.update({key: value["label"] for key, value in controls.items()})
        operations.update(DONE="Every requirement is visibly satisfied.", BLOCKED="No supported operation can progress.")

        # Prepare questions
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

        page_text = state.get("text", "")[:2000]
        context = (
            f"Goal: {goal}\n"
            f"Page URL: {state.get('url', '')}\n"
            f"Page Title: {state.get('title', '')}\n"
            f"Page Content Snippet: {page_text}\n"
        )

        payload = {
            "context": context,
            "questions": questions,
            "state": {
                "url": state.get("url"),
                "title": state.get("title"),
            },
            "recent_actions": [
                {k: h.get(k) for k in ("action", "kind", "text", "page_changed")} for h in history[-6:]
            ],
        }

        started = time.perf_counter()
        log_debug(f"Calling remote Laya endpoint: {self.endpoint_url}/predict")
        # Supports both /predict and /v1/predict
        try:
            res = self._post("/predict", payload)
        except RuntimeError as e:
            if "404" in str(e):
                res = self._post("/v1/predict", payload)
            else:
                raise

        latency_ms = round((time.perf_counter() - started) * 1000)
        answers = res.get("answers", {})

        op_answer = answers.get("operation", {})
        operation = op_answer.get("choice", "DONE")
        confidence = op_answer.get("confidence", 0.95)

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

        log_debug(f"Remote Laya decision: op={operation}, target={target}, latency={latency_ms}ms")
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
            "model": res.get("model", "laya-remote"),
            "usage": res.get("usage", {}),
            "latency_ms": latency_ms,
            "engine": "laya-remote",
            "remote_url": self.endpoint_url,
        }
