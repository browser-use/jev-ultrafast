"""Injectable model providers. Core code still owns prompts and validation."""

import os
import time
from collections.abc import Mapping
from typing import Any, Protocol

import httpx

JsonObject = Mapping[str, Any]

CLIENT = httpx.Client(http2=True, timeout=25)


def post_json(url, key, body, headers=None):
    request_headers = {"Authorization": f"Bearer {key}", **(headers or {})}
    for attempt in range(3):
        try:
            response = CLIENT.post(url, json=body, headers=request_headers)
        except httpx.HTTPError:
            raise RuntimeError("Model connection failed; no action executed.") from None
        if response.status_code in {429, 529, 503} and attempt < 2:
            time.sleep(0.5 * 2**attempt)
            continue
        if response.is_error:
            raise RuntimeError(f"Model provider returned HTTP {response.status_code}; no action executed.")
        return response.json()
    raise RuntimeError("Model unavailable")


class DecisionProvider(Protocol):
    """Call a decision service and return a raw TypeSafe-compatible response."""

    @property
    def model(self) -> str: ...

    def decide(self, request: JsonObject) -> JsonObject: ...


class TextProvider(Protocol):
    """Call a text service and return a raw OpenAI-compatible response."""

    @property
    def model(self) -> str: ...

    def generate(self, request: JsonObject) -> JsonObject: ...


class TypeSafeProvider:
    """Default decision provider, configured explicitly or through TYPESAFE_* env vars."""

    endpoint = "https://api.typesafe.ai/v1/systemone"

    def __init__(self, *, api_key=None, model=None):
        self._api_key = api_key
        self._model = model
        self._post_json = post_json

    def _use_transport(self, transport):
        self._post_json = transport
        return self

    @property
    def model(self):
        return self._model if self._model is not None else os.environ.get("TYPESAFE_MODEL", "jev-latest")

    def decide(self, request):
        key = self._api_key if self._api_key is not None else os.environ["TYPESAFE_API_KEY"]
        return self._post_json(self.endpoint, key, request)


class OpenAITextProvider:
    """OpenAI-compatible text provider with explicit configuration overriding TEXT_MODEL_* vars."""

    def __init__(
        self,
        *,
        api_key=None,
        base_url=None,
        model=None,
        reasoning=None,
        headers=None,
    ):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._reasoning = reasoning
        self._headers = dict(headers or {})
        self._post_json = post_json

    def _use_transport(self, transport):
        self._post_json = transport
        return self

    @property
    def model(self):
        return self._model if self._model is not None else os.environ.get("TEXT_MODEL", "deepseek-chat")

    @property
    def base_url(self):
        base_url = (
            self._base_url
            if self._base_url is not None
            else os.environ.get("TEXT_MODEL_BASE_URL", "https://api.deepseek.com/v1")
        )
        return base_url.rstrip("/")

    def _reasoning_options(self):
        if isinstance(self._reasoning, Mapping):
            return dict(self._reasoning)
        if self._reasoning is not None:
            return (
                {"reasoning": {"enabled": False}}
                if self._reasoning == "none"
                else {"reasoning": {"effort": self._reasoning}}
            )
        if os.environ.get("TEXT_MODEL_REASONING") == "none":
            return {"reasoning": {"enabled": False}}
        if "api.deepseek.com/" in self.base_url:
            return {"thinking": {"type": "disabled"}}
        return {"reasoning": {"effort": "low"}}

    def generate(self, request):
        key = self._api_key if self._api_key is not None else os.environ.get("TEXT_MODEL_API_KEY")
        if not key:
            raise ValueError("TYPE_TEXT needs TEXT_MODEL_API_KEY; no text is hardcoded or guessed by the executor.")
        body = {**request, **self._reasoning_options()}
        if self._headers:
            return self._post_json(self.base_url + "/chat/completions", key, body, self._headers)
        return self._post_json(self.base_url + "/chat/completions", key, body)
