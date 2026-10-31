import os

import httpx

from jev_ultrafast.demo import load_environment

load_environment()
key = os.environ.get("TEXT_MODEL_API_KEY")
base = os.environ.get("TEXT_MODEL_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

client = httpx.Client(timeout=25)
res = client.get(f"{base}/auth/key", headers={"Authorization": f"Bearer {key}"})
print("Auth status:", res.status_code)
print("Auth response:", res.text)
