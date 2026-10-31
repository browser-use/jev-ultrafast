import os
import time
import json
import httpx
from jev_ultrafast.demo import load_environment

load_environment()
key = os.environ.get("TEXT_MODEL_API_KEY")
base = os.environ.get("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
model = os.environ.get("TEXT_MODEL")

print(f"Testing model: {model}")
print(f"Base URL: {base}")

client = httpx.Client(timeout=30)
t0 = time.perf_counter()
try:
    resp = client.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={
            "model": model,
            "max_tokens": 512,
            "response_format": {"type": "json_object"},
            "reasoning": {"enabled": False},
            "messages": [
                {"role": "system", "content": "You are a helpful JSON assistant. Return JSON with key status."},
                {"role": "user", "content": "Hello"}
            ]
        }
    )
    latency = (time.perf_counter() - t0) * 1000
    print(f"Status: {resp.status_code} in {latency:.1f}ms")
    print(f"Body: {resp.text[:500]}")
except Exception as e:
    print(f"Error after {(time.perf_counter() - t0)*1000:.1f}ms: {e}")
