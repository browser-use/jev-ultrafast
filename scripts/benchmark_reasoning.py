import os
import time
import httpx
from jev_ultrafast.demo import load_environment

load_environment()
key = os.environ.get("TEXT_MODEL_API_KEY")

test_models = [
    "mistralai/mistral-small-24b-instruct-2501",
    "meta-llama/llama-3.2-3b-instruct",
    "meta-llama/llama-3.1-8b-instruct",
    "deepseek/deepseek-chat",
    "liquid/lfm-2.5-2.6b:free",
]

reasoning_configs = [
    ("none (enabled: False)", {"reasoning": {"enabled": False}}),
    ("effort: none", {"reasoning": {"effort": "none"}}),
    ("effort: low", {"reasoning": {"effort": "low"}}),
    ("no reasoning param", {}),
]

client = httpx.Client(timeout=20)
print(f"{'Model':<42} {'Reasoning':<25} {'Status':<7} {'Latency':<9}")
print("-" * 85)

for m in test_models:
    for label, r_cfg in reasoning_configs:
        t0 = time.perf_counter()
        try:
            payload = {
                "model": m,
                "messages": [{"role": "user", "content": 'Return JSON: {"decision": "ok"}'}],
                "response_format": {"type": "json_object"},
                **r_cfg
            }
            r = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {key}"},
                json=payload
            )
            dt = (time.perf_counter() - t0) * 1000
            print(f"{m:<42} {label:<25} {r.status_code:<7} {dt:7.1f}ms")
        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000
            print(f"{m:<42} {label:<25} ERROR   {dt:7.1f}ms ({e})")
