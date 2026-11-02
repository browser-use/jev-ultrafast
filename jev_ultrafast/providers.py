"""Any OpenAI-compatible or Anthropic-compatible endpoint can drive the policy and the text helper.

Roles: "policy" (operation + target choice) and "text" (TYPE_TEXT values). Each role reads
POLICY_* / TEXT_MODEL_* variables, falls back to the provider's own key variable
(OPENROUTER_API_KEY, NVIDIA_API_KEY, ...), and auto-detects the API dialect from the base URL.
"""

import json
import os
import re
import urllib.parse

PROVIDERS = {
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "dialect": "openai",
        "key_env": ["OPENAI_API_KEY"],
        "json_mode": True,
        "keys_url": "https://platform.openai.com/api-keys",
    },
    "anthropic": {
        "base_url": "https://api.anthropic.com",
        "dialect": "anthropic",
        "key_env": ["ANTHROPIC_API_KEY"],
        "keys_url": "https://console.anthropic.com/settings/keys",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "dialect": "openai",
        "key_env": ["OPENROUTER_API_KEY"],
        "headers": {"X-Title": "custom-web-ultrafast"},
        "keys_url": "https://openrouter.ai/keys",
    },
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "dialect": "openai",
        "key_env": ["NVIDIA_API_KEY", "NVIDIA_NIM_API_KEY"],
        "keys_url": "https://build.nvidia.com",
    },
    "omniroute": {
        "base_url": "http://localhost:20128/v1",
        "dialect": "openai",
        "key_env": ["OMNIROUTE_API_KEY"],
        "keys_url": "http://localhost:20128",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "dialect": "openai",
        "key_env": ["DEEPSEEK_API_KEY"],
        "json_mode": True,
        "keys_url": "https://platform.deepseek.com/api_keys",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "dialect": "openai",
        "key_env": ["GROQ_API_KEY"],
        "json_mode": True,
        "keys_url": "https://console.groq.com/keys",
    },
    "together": {
        "base_url": "https://api.together.xyz/v1",
        "dialect": "openai",
        "key_env": ["TOGETHER_API_KEY"],
        "json_mode": True,
        "keys_url": "https://api.together.ai/settings/api-keys",
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "dialect": "openai",
        "key_env": ["MISTRAL_API_KEY"],
        "json_mode": True,
        "keys_url": "https://console.mistral.ai/api-keys",
    },
    "xai": {
        "base_url": "https://api.x.ai/v1",
        "dialect": "openai",
        "key_env": ["XAI_API_KEY"],
        "json_mode": True,
        "keys_url": "https://console.x.ai",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "dialect": "openai",
        "key_env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "json_mode": True,
        "keys_url": "https://aistudio.google.com/apikey",
    },
    "custom": {"dialect": "openai"},
    # Local runtimes — OpenAI-compatible servers on 127.0.0.1, no API key
    # needed. Ideal for free/offline runs of the policy or text roles with
    # small quantized models (see docs/modelos-locales.md).
    "ollama": {
        "base_url": "http://127.0.0.1:11434/v1",
        "dialect": "openai",
        "key_env": [],
        "local": True,
        "keys_url": "https://ollama.com",
    },
    "lmstudio": {
        "base_url": "http://127.0.0.1:1234/v1",
        "dialect": "openai",
        "key_env": [],
        "local": True,
        "keys_url": "https://lmstudio.ai",
    },
    "llamacpp": {
        "base_url": "http://127.0.0.1:8080/v1",
        "dialect": "openai",
        "key_env": [],
        "local": True,
        "keys_url": "https://github.com/ggml-org/llama.cpp",
    },
    "jan": {
        "base_url": "http://127.0.0.1:1337/v1",
        "dialect": "openai",
        "key_env": [],
        "local": True,
        "keys_url": "https://jan.ai",
    },
}

ALIASES = {
    "nim": "nvidia",
    "nvidia-nim": "nvidia",
    "nvidia_nim": "nvidia",
    "openai-compatible": "custom",
    "openai_compatible": "custom",
    "compatible": "custom",
    "self-hosted": "custom",
    "grok": "xai",
    "google": "gemini",
    "omni": "omniroute",
    "lm-studio": "lmstudio",
    "llama.cpp": "llamacpp",
    "llama-server": "llamacpp",
    "llamacpp-server": "llamacpp",
}

ROLE_ENV = {
    "planner": {
        "provider": "PLANNER_PROVIDER",
        "key": "PLANNER_API_KEY",
        "base": "PLANNER_BASE_URL",
        "model": "PLANNER_MODEL",
        "reasoning": "PLANNER_REASONING",
        "json_mode": "PLANNER_JSON_MODE",
        "temperature": "PLANNER_TEMPERATURE",
    },
    "policy": {
        "provider": "POLICY_PROVIDER",
        "key": "POLICY_API_KEY",
        "base": "POLICY_BASE_URL",
        "model": "POLICY_MODEL",
        "reasoning": "POLICY_REASONING",
        "json_mode": "POLICY_JSON_MODE",
        "temperature": "POLICY_TEMPERATURE",
    },
    "text": {
        "provider": "TEXT_MODEL_PROVIDER",
        "key": "TEXT_MODEL_API_KEY",
        "base": "TEXT_MODEL_BASE_URL",
        "model": "TEXT_MODEL",
        "reasoning": "TEXT_MODEL_REASONING",
        "json_mode": "TEXT_MODEL_JSON_MODE",
        "temperature": "TEXT_MODEL_TEMPERATURE",
    },
}

DEFAULT_TEXT_MODEL = "deepseek-chat"
POLICY_HINT = (
    "Set TYPESAFE_API_KEY to use Jev, or configure your own provider: "
    "POLICY_PROVIDER (openai, anthropic, openrouter, nvidia, omniroute, deepseek, groq, "
    "together, mistral, xai, gemini, custom) plus POLICY_MODEL and POLICY_API_KEY "
    "(or the provider's own variable, e.g. OPENROUTER_API_KEY)."
)


def detect_preset(base_url):
    url = base_url.rstrip("/")
    for name, preset in PROVIDERS.items():
        preset_url = preset.get("base_url", "").rstrip("/")
        if preset_url and (url == preset_url or url.startswith(preset_url + "/")):
            return name, preset
    return None


def reasoning_params(setting, name, dialect):
    """Map the reasoning setting to provider-specific body params; omit anything unverified."""
    if dialect == "anthropic":
        return {}
    setting = (setting or "none").strip().lower()
    if setting in {"none", "off", "disabled"}:
        if name == "deepseek":
            return {"thinking": {"type": "disabled"}}
        if name == "openrouter":
            return {"reasoning": {"enabled": False}}
        if name == "groq":
            # gpt-oss style models always reason; low is the fastest budget available.
            return {"reasoning_effort": "low"}
        return {}
    if setting in {"low", "medium", "high", "minimal"}:
        if name == "openai":
            return {"reasoning_effort": setting}
        if name == "openrouter":
            return {"reasoning": {"effort": setting}}
        if name == "groq":
            return {"reasoning_effort": "low" if setting == "minimal" else setting}
    return {}


def _is_loopback_url(base_url):
    host = (urllib.parse.urlparse(base_url or "").hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def resolve(role):
    """Build one provider config from the environment for the given role."""
    env = ROLE_ENV[role]
    raw = (os.environ.get(env["provider"]) or "").strip()
    base = (os.environ.get(env["base"]) or "").strip()
    key = (os.environ.get(env["key"]) or "").strip()
    name, preset = "", None
    if raw.startswith(("http://", "https://")):
        base, name, preset = (base or raw), "custom", PROVIDERS["custom"]
        detected = detect_preset(base)
        if detected:  # e.g. http://127.0.0.1:1234/v1 → lmstudio (no key needed)
            name, preset = detected
    elif raw:
        name = ALIASES.get(raw.lower(), raw.lower())
        preset = PROVIDERS.get(name)
        if preset is None:
            known = ", ".join(sorted(PROVIDERS))
            raise ValueError(f"Unknown provider '{raw}' in {env['provider']}. Use: {known}, or a http(s) base URL.")
    if preset and not base:
        base = preset.get("base_url", "")
    elif not preset and base:
        name, preset = detect_preset(base) or ("", None)
    if not base and role == "text":
        name, preset, base = "deepseek", PROVIDERS["deepseek"], PROVIDERS["deepseek"]["base_url"]
    if not base:
        hint = POLICY_HINT if role == "policy" else ""
        raise ValueError(f"{env['base']} is required for a custom {role} provider. {hint}".strip())
    if not key and preset:
        for key_name in preset.get("key_env", []):
            if (os.environ.get(key_name) or "").strip():
                key = os.environ[key_name].strip()
                break
    if not key and preset and preset.get("local"):
        key = "local"  # localhost servers ignore auth; the header keeps the request path uniform
    if not key and _is_loopback_url(base):
        key = "local"  # any server on this machine (custom port, llama-server, ...) needs no key
    if not key:
        if preset and preset.get("key_env"):
            extra = f" or one of {', '.join(preset['key_env'])}"
        else:
            extra = f" or set {env['provider']} to a named provider"
        raise ValueError(f"{env['key']} is not set{extra}. No request was sent.")
    model = (os.environ.get(env["model"]) or "").strip()
    if not model and role == "text" and name == "deepseek":
        model = DEFAULT_TEXT_MODEL
    if not model:
        hint = POLICY_HINT if role == "policy" else ""
        raise ValueError(f"{env['model']} is not set; no request was sent. {hint}".strip())
    dialect = "anthropic" if "api.anthropic.com" in base else (preset or {}).get("dialect", "openai")
    json_mode = bool((preset or {}).get("json_mode"))
    flag = (os.environ.get(env["json_mode"]) or "").strip().lower()
    if flag in {"on", "true", "1", "yes"}:
        json_mode = True
    elif flag in {"off", "false", "0", "no"}:
        json_mode = False
    temperature = None
    raw_temperature = (os.environ.get(env["temperature"]) or "").strip()
    if raw_temperature:
        try:
            temperature = round(min(2.0, max(0.0, float(raw_temperature))), 2)
        except ValueError:
            raise ValueError(f"{env['temperature']} must be a number between 0 and 2.") from None
    return {
        "name": name or "custom",
        "dialect": dialect,
        "base_url": base,
        "key": key,
        "model": model,
        "reasoning": (os.environ.get(env["reasoning"]) or "none").strip().lower(),
        "temperature": temperature,
        "json_mode": json_mode,
        "headers": (preset or {}).get("headers", {}),
    }


def _openai_url(base_url):
    url = base_url.rstrip("/")
    return url if url.endswith("/chat/completions") else url + "/chat/completions"


def _anthropic_url(base_url):
    url = base_url.rstrip("/")
    if url.endswith("/messages"):
        return url
    if url.endswith("/v1"):
        url = url[: -len("/v1")]
    return url + "/v1/messages"


def build_request(provider, system, user, max_tokens, omit=()):
    body = {"model": provider["model"]}
    if "max_tokens" in omit:
        body["max_completion_tokens"] = max_tokens  # newer OpenAI-compatible endpoints
    else:
        body["max_tokens"] = max_tokens
    if "reasoning" not in omit:
        body.update(reasoning_params(provider["reasoning"], provider["name"], provider["dialect"]))
    if provider.get("temperature") is not None and "temperature" not in omit:
        body["temperature"] = provider["temperature"]
    if provider["dialect"] == "anthropic":
        headers = {"x-api-key": provider["key"], "anthropic-version": "2023-06-01"}
        body["system"] = system
        body["messages"] = [{"role": "user", "content": user}]
        return _anthropic_url(provider["base_url"]), headers, body
    if provider["json_mode"] and "response_format" not in omit:
        body["response_format"] = {"type": "json_object"}
    body["messages"] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if provider["headers"]:
        headers = {"Authorization": f"Bearer {provider['key']}", **provider["headers"]}
    else:
        headers = None
    return _openai_url(provider["base_url"]), headers, body


def parse_response(provider, result):
    if provider["dialect"] == "anthropic":
        blocks = result.get("content") or []
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
    else:
        choices = result.get("choices") or []
        message = (choices[0] or {}).get("message", {}) if choices else {}
        text = message.get("content") or ""
        if isinstance(text, list):  # a few gateways return OpenAI content parts
            text = "".join(part.get("text", "") for part in text if isinstance(part, dict))
    meta = {
        "model": result.get("model") or provider["model"],
        "usage": result.get("usage") or {},
        "provider": provider["name"],
    }
    return text, meta


def chat(provider, system, user, max_tokens=1024, on_delta=None):
    """Send one chat request; adaptively drop params a strict endpoint rejects.

    OpenAI-compatible providers disagree on response_format, reasoning controls,
    temperature, and max_tokens vs max_completion_tokens. Instead of failing,
    retry without the rejected parameter so a model change never breaks a run.

    With on_delta, the reply is streamed over SSE and every text chunk is passed
    to the callback as it arrives; if the endpoint rejects streaming, the call
    falls back to a single request and on_delta simply fires once.
    """
    from . import model  # one shared HTTP seam; tests patch model.post_json / model.post_stream

    dropped = set()
    while True:
        url, headers, body = build_request(provider, system, user, max_tokens, omit=dropped)
        if on_delta is not None and "stream" not in dropped:
            body = {**body, "stream": True}
            try:
                text, usage, model_id = model.post_stream(
                    url, provider["key"], body, headers=headers, on_delta=on_delta
                )
                return text, {"model": model_id or provider["model"], "usage": usage, "provider": provider["name"]}
            except RuntimeError as error:
                drop = _droppable_param(str(error), dropped, provider["dialect"], allow_stream_drop=True)
                if drop is None:
                    raise
                dropped.add(drop)
                continue
        try:
            if headers is None:
                result = model.post_json(url, provider["key"], body)
            else:
                result = model.post_json(url, provider["key"], body, headers=headers)
        except RuntimeError as error:
            drop = _droppable_param(str(error), dropped, provider["dialect"])
            if drop is None:
                raise
            dropped.add(drop)
            continue
        return parse_response(provider, result)


def _droppable_param(error, dropped, dialect, allow_stream_drop=False):
    """The canonical parameter to drop next for a rejected request, or None."""
    if dialect == "anthropic":
        return None
    lowered = error.lower()
    if allow_stream_drop and "stream" in lowered and "stream" not in dropped:
        return "stream"
    if "response_format" in lowered and "response_format" not in dropped:
        return "response_format"
    if ("reasoning_effort" in lowered or "'reasoning'" in lowered) and "reasoning" not in dropped:
        return "reasoning"
    if "temperature" in lowered and "temperature" not in dropped:
        return "temperature"
    if "max_tokens" in lowered and "max_tokens" not in dropped:
        return "max_tokens"
    if "unsupported parameter" in lowered or "unexpected keyword" in lowered:
        for param in ("response_format", "reasoning", "temperature", "max_tokens"):
            if param not in dropped:
                return param
    return None


def extract_json(text):
    """Parse the first JSON object from a model reply, tolerating fences and surrounding prose."""
    stripped = (text or "").strip()
    if not stripped:
        raise ValueError("Model returned an empty response.")
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped).strip()
    try:
        parsed = json.loads(stripped)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    start = stripped.find("{")
    if start >= 0:
        depth = 0
        for position, character in enumerate(stripped[start:], start):
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    try:
                        candidate = json.loads(stripped[start : position + 1])
                    except ValueError:
                        break
                    if isinstance(candidate, dict):
                        return candidate
    raise ValueError("Model response contained no JSON object.")
