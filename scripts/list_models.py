import os
import httpx
from jev_ultrafast.demo import load_environment

load_environment()
key = os.environ.get("TEXT_MODEL_API_KEY")

client = httpx.Client(timeout=20)
res = client.get("https://openrouter.ai/api/v1/models", headers={"Authorization": f"Bearer {key}"})
data = res.json()["data"]

# Filter for fast models or models with low latency or free/cheap
fast_candidates = []
for m in data:
    mid = m["id"]
    pricing = m.get("pricing", {})
    prompt_price = float(pricing.get("prompt", 0))
    # look for gemini, groq, cerebras, deepseek, mistral, llama, qwen
    if any(k in mid.lower() for k in ["flash", "groq", "cerebras", "nemotron", "llama-3.1-8b", "llama-3.2-3b", "llama-3.3-70b", "qwen", "mistral-small", "deepseek-chat"]):
        fast_candidates.append({
            "id": mid,
            "name": m.get("name"),
            "prompt_price": prompt_price,
            "completion_price": float(pricing.get("completion", 0)),
            "context_length": m.get("context_length")
        })

print(f"Found {len(fast_candidates)} candidates. Examples:")
for c in sorted(fast_candidates, key=lambda x: x["prompt_price"])[:25]:
    print(f"  {c['id']:<45} prompt: ${c['prompt_price']*1e6:.2f}/M  comp: ${c['completion_price']*1e6:.2f}/M")
