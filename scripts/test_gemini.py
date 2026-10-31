from jev_ultrafast.demo import load_environment
import os
import json
import httpx

load_environment()
key = os.environ.get("TEXT_MODEL_API_KEY")
base = os.environ.get("TEXT_MODEL_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai").rstrip("/")

client = httpx.Client(timeout=30)
for model in ["gemini-3.6-flash", "gemini-3.8-flash", "gemini-1.5-flash-latest"]:
    try:
        resp = client.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": "Return JSON: {\"text\": \"hello\"}"}
                ],
                "response_format": {"type": "json_object"}
            }
        )
        print(f"Model {model}: status {resp.status_code}")
        print("Response:", resp.text[:200])
    except Exception as e:
        print(f"Model {model} error:", e)
