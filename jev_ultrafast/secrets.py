"""Values for secret fields come from the local environment, never from a model.

JEV_SECRETS is a JSON object mapping a field's label to its value:

    JEV_SECRETS='{"Password": "..."}'

A key matches a password field whose label is the same text, ignoring case, surrounding whitespace and a
trailing required-marker ("*") or colon. It is an exact match on purpose: "Password" never fills "Confirm
password", and "user" never fills "Username". The value is read in-process and typed straight into the page.
It is never placed in a prompt, a model request, a decision trace, or the run's history.
"""

import json
import os
import re

MASK = "(secret)"


def normalize(label):
    return re.sub(r"\s+", " ", re.sub(r"[\s*:]+$", "", label or "")).strip().lower()


def secret_for(label):
    """The stored value for this exact label, or None when the vault has no entry for it."""
    try:
        mapping = json.loads(os.environ.get("JEV_SECRETS") or "{}")
    except ValueError:
        raise ValueError("JEV_SECRETS is not valid JSON") from None
    if not isinstance(mapping, dict):
        raise ValueError("JEV_SECRETS must be a JSON object of {field label: value}")
    wanted = normalize(label)
    for key, value in mapping.items():
        if normalize(key) == wanted and isinstance(value, str) and value:
            return value
    return None
