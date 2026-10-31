import os
import time
import httpx
from jev_ultrafast.demo import load_environment

load_environment()
key = os.environ.get("TEXT_MODEL_API_KEY")

models = [
    "meta-llama/llama-3.2-3b-instruct",
    "meta-llama/llama-3.1-8b-instruct",
    "mistralai/mistral-small-24b-instruct-2501",
    "google/gemini-2.5-flash",
    "google/gemini-2.5-flash-lite",
    "deepseek/deepseek-chat",
    "nvidia/nemotron-3.5-lightning:free",
    "qwen/qwen3.8-27b:free",
    "liquid/lfm-2.5-2.6b:free",
    "google/gemma-4-26b-a4b-it:free",
    "google/gemma-4-31b-it:free",
]

client = httpx.Client(timeout=20)
for m in models:
    t0 = time.perf_counter()
    try:
        r = client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": m,
                "messages": [{"role": "user", "content": 'Return JSON: {"status": "ok"}'}],
                "response_format": {"type": "json_object"}
            }
        )
        dt = (time.perf_counter() - t0) * 1000
        if r.status_code == 200:
            content = r.json()["choices"][0]["message"]["content"]
            print(f"{m:<45} Status: {r.status_code} Latency: {dt:6.1f}ms  Response: {content.strip()}")
        else:
            print(f"{m:<45} Status: {r.status_code} Latency: {dt:6.1f}ms  Error: {r.text[:100]}")
    except Exception as e:
        dt = (time.perf_counter() - t0) * 1000
        print(f"{m:<45} ERROR: {e} ({dt:.1f}ms)")
