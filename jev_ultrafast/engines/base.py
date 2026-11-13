"""Base decision engine interface and common validation utilities."""

import abc
import math
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple


def is_debug_enabled() -> bool:
    return os.environ.get("JEV_DEBUG", "").lower() in ("1", "true", "yes")


def log_debug(msg: str) -> None:
    if is_debug_enabled():
        print(f"[jev-ultrafast:debug] {msg}", file=sys.stderr)


def validate_choice(answer: Dict[str, Any], ids: Any) -> Dict[str, Any]:
    """Ensures a decision output contains valid choices and finite probabilities."""
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
        raise ValueError("Invalid decision response; no action executed.")
    return answer


class BaseEngine(abc.ABC):
    """
    Abstract interface for browser decision engines.
    Implementations can be cloud APIs (TypeSafe, OpenAI), local open-source models
    (Laya ModernBERT), or remote microservices (Google Colab / self-hosted endpoints).
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Name of the decision engine."""
        pass

    @abc.abstractmethod
    def choose(
        self,
        state: Dict[str, Any],
        goal: str,
        history: List[Dict[str, Any]],
        elements: List[Dict[str, Any]],
        targets: Dict[str, Dict[str, Any]],
        controls: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Executes a typed decision over the browser's current action space.
        Returns a standardized decision dictionary.
        """
        pass
