"""Mask credentials before anything is logged, broadcast, or persisted.

Provider errors can echo request material; this module is the single choke
point that guarantees no API key survives into error messages, runs.jsonl,
the audit trail, or the sidebar. Over-redacting is safe; leaking is not.
"""

import re

# Known key formats (value kept only as its recognisable prefix).
_TOKENS = [
    (re.compile(r"\bgsk_[A-Za-z0-9]{8,}"), "gsk_***"),
    (re.compile(r"\bnvapi-[A-Za-z0-9_\-]{8,}"), "nvapi-***"),
    (re.compile(r"\bsk-(?:ant-|or-v1-)?[A-Za-z0-9_\-]{8,}"), "sk-***"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"), "Bearer ***"),
]

# KEY=value / KEY: value leftovers in any casing (unknown formats included).
_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Z0-9_]{2,}(?:API_KEY|API_TOKEN|ACCESS_TOKEN|SECRET|PASSWORD|TOKEN))(\s*[=:]\s*)([^\s,;\"'&]{4,})"
)


def redact(text):
    """The text with every credential-looking token masked."""
    text = str(text)
    for pattern, replacement in _TOKENS:
        text = pattern.sub(replacement, text)
    return _ASSIGNMENT.sub(r"\1\2***", text)
