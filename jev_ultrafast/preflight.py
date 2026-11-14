"""Startup checks that report configuration without exposing credentials."""

from __future__ import annotations

import os
import shutil
from typing import Mapping


def selected_backend(backend: str | None = None, environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    value = backend or env.get("ULTRAFAST_BROWSER_BACKEND") or "browser_harness"
    value = value.strip().lower()
    aliases = {"browser": "browser_harness", "harness": "browser_harness"}
    return aliases.get(value, value)


def preflight(backend: str | None = None, *, environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Validate non-secret prerequisites and return safe, displayable statuses."""
    env = os.environ if environ is None else environ
    selected = selected_backend(backend, env)
    if selected not in {"browser_harness", "ego"}:
        raise ValueError(f"Unknown browser backend: {selected!r}")
    if not env.get("TYPESAFE_API_KEY"):
        raise RuntimeError("TYPESAFE_API_KEY is missing; configure it in .env or the environment.")
    if selected == "ego" and shutil.which("ego-browser") is None:
        raise RuntimeError("Ego backend requires the ego-browser executable on PATH.")
    return {
        "jev_api": "configured",
        "text_helper": "configured" if env.get("TEXT_MODEL_API_KEY") else "missing",
        "backend": selected,
        "ego_browser": "available" if selected == "ego" else "n/a",
    }
