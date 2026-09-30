"""Decision engine package and engine factory."""

import os
from typing import Optional

from .base import BaseEngine, log_debug
from .laya_local import LayaLocalEngine
from .laya_remote import LayaRemoteEngine
from .typesafe import TypeSafeEngine

__all__ = [
    "BaseEngine",
    "TypeSafeEngine",
    "LayaRemoteEngine",
    "LayaLocalEngine",
    "get_decision_engine",
]

_CACHED_ENGINE: Optional[BaseEngine] = None


def get_decision_engine(name: Optional[str] = None) -> BaseEngine:
    """
    Returns an instantiated decision engine based on explicit name or environment.

    Resolution:
    1. Explicit 'name' argument or 'DECISION_ENGINE' env var:
       - 'typesafe' -> TypeSafeEngine
       - 'laya-remote', 'remote' -> LayaRemoteEngine
       - 'laya-local', 'local' -> LayaLocalEngine
    2. Auto-detection:
       - If LAYA_ENDPOINT or DECISION_API_URL is set -> LayaRemoteEngine
       - Else if TYPESAFE_API_KEY is present -> TypeSafeEngine
       - Else if USE_LAYA == '1' -> LayaLocalEngine
       - Default -> TypeSafeEngine
    """
    global _CACHED_ENGINE
    engine_name = (name or os.environ.get("DECISION_ENGINE", "")).lower().strip()

    if not engine_name:
        if os.environ.get("LAYA_ENDPOINT") or os.environ.get("DECISION_API_URL"):
            engine_name = "laya-remote"
        elif "TYPESAFE_API_KEY" in os.environ:
            engine_name = "typesafe"
        elif os.environ.get("USE_LAYA") == "1":
            engine_name = "laya-local"
        else:
            engine_name = "typesafe"

    log_debug(f"Resolved decision engine: '{engine_name}'")

    if engine_name == "typesafe":
        return TypeSafeEngine()
    elif engine_name in ("laya-remote", "remote"):
        return LayaRemoteEngine()
    elif engine_name in ("laya-local", "local", "laya"):
        return LayaLocalEngine()
    else:
        raise ValueError(
            f"Unknown decision engine '{engine_name}'. "
            "Supported options: 'typesafe', 'laya-remote', 'laya-local'."
        )
