"""Model discovery: fetch a provider's catalogue and normalize it into a registry.

The registry combines the provider's /models listing (discovery) with inferred
capabilities and the live connectivity probe (firefox.check_providers) as the
runtime validation, per the catalogue-vs-runtime-schema distinction in the spec.
Registry file: artifacts/model-registry.json (refreshed on demand).
"""

import json
import os
import time
from pathlib import Path

from . import providers

REGISTRY_PATH = Path(os.environ.get("JEV_MODEL_REGISTRY", "artifacts/model-registry.json"))
REGISTRY_TTL_SECONDS = 24 * 3600
DISCOVERABLE = (
    "nvidia", "groq", "openrouter", "deepseek", "together", "mistral", "xai", "gemini", "openai",
    # local runtimes — no API key, listed whenever the server is running
    "ollama", "lmstudio", "llamacpp", "jan",
)

# Non-chat model families commonly listed by catalogues.
_EXCLUDE_MARKERS = (
    "embed", "rerank", "reranker", "guard", "clip", "sdxl", "flux", "stable-diffusion",
    "audio", "whisper", "tts", "stt", "sambert", "canary", "parakeet", "bios",
    "retriever", "nemoretriever", "vista", "esm", "evo", "CAD", "omni",
)
_VISION_MARKERS = ("vl", "vision", "llava", "pixtral", "vila", "image", "multimodal", "gemma-3")
_REASONING_MARKERS = ("r1", "reason", "thinking", "qwq", "o1", "o3", "o4", "glm-5", "deepseek-r", "kimi-k")


def provider_key(provider_name):
    """Resolve the API key for a provider without needing a full role config."""
    preset = providers.PROVIDERS.get(provider_name)
    if not preset:
        return None
    for env_name in preset.get("key_env", []):
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return value
    return None


def _is_chat_model(model_id):
    lowered = model_id.lower()
    return not any(marker in lowered for marker in _EXCLUDE_MARKERS)


def _capabilities(model_id):
    lowered = model_id.lower()
    return {
        "text": True,
        "vision": any(marker in lowered for marker in _VISION_MARKERS),
        "reasoning": any(marker in lowered for marker in _REASONING_MARKERS),
        "inferred": True,  # heuristics from the id; the live probe validates at runtime
    }


def _display_name(model_id):
    from .parameters import _display_name as display

    return display(model_id)


def fetch_models(provider_name):
    """Fetch and normalize one provider's catalogue. Raises RuntimeError on failure."""
    preset = providers.PROVIDERS.get(provider_name)
    if not preset or not preset.get("base_url"):
        raise RuntimeError(f"Provider {provider_name} has no catalogue endpoint.")
    if preset.get("local"):
        key = "local"  # localhost servers need no auth
    else:
        key = provider_key(provider_name)
        if not key:
            env_names = ", ".join(preset.get("key_env", [])) or "its API key variable"
            raise RuntimeError(f"No API key for {provider_name} (set {env_names}).")
    from . import model

    url = preset["base_url"].rstrip("/") + "/models"
    try:
        response = model.CLIENT.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=20)
    except Exception as error:  # noqa: BLE001 - surfaced as a readable message
        raise RuntimeError(f"Could not reach {provider_name}: {error}") from None
    if response.status_code == 401 or response.status_code == 403:
        keys_url = preset.get("keys_url", "")
        raise RuntimeError(
            f"{provider_name} rejected the API key (HTTP {response.status_code})."
            f" Generate a fresh key at {keys_url} and update .env."
        )
    if response.is_error:
        raise RuntimeError(f"{provider_name} returned HTTP {response.status_code}.")
    try:
        entries = response.json().get("data", [])
    except ValueError:
        raise RuntimeError(f"{provider_name} returned a malformed catalogue.") from None
    models = []
    seen = set()
    for entry in entries:
        model_id = entry.get("id") if isinstance(entry, dict) else None
        if not model_id or model_id in seen or not _is_chat_model(model_id):
            continue
        seen.add(model_id)
        models.append(
            {
                "id": model_id,
                "displayName": _display_name(model_id),
                "provider": provider_name,
                "capabilities": _capabilities(model_id),
            }
        )
    models.sort(key=lambda m: m["displayName"].lower())
    return models


def discover(refresh=False):
    """The cached registry, refreshed when missing, stale, or forced."""
    registry = None
    if not refresh:
        registry = _load_registry()
        if registry and time.time() - registry.get("fetchedAt", 0) < REGISTRY_TTL_SECONDS:
            return registry
    providers_report = {}
    for name in DISCOVERABLE:
        preset = providers.PROVIDERS.get(name) or {}
        if not preset.get("local") and not provider_key(name):
            continue
        try:
            providers_report[name] = {"ok": True, "models": fetch_models(name)}
        except RuntimeError as error:
            providers_report[name] = {"ok": False, "error": str(error)[:300]}
    registry = {"fetchedAt": time.time(), "providers": providers_report}
    _save_registry(registry)
    return registry


def _load_registry():
    try:
        data = json.loads(REGISTRY_PATH.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and "providers" in data else None


def _save_registry(registry):
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    REGISTRY_PATH.write_text(json.dumps(registry, indent=2))
