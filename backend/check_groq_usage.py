# backend/check_groq_usage.py
import os
import requests
from dotenv import load_dotenv

load_dotenv()

resp = requests.post(
    "https://api.groq.com/openai/v1/chat/completions",
    headers={
        "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}",
        "Content-Type": "application/json",
    },
    json={
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
    }
)

print("=== All response headers ===")
for key, value in resp.headers.items():
    if "rate" in key.lower() or "limit" in key.lower() or "token" in key.lower() or "remain" in key.lower() or "reset" in key.lower():
        print(f"  {key}: {value}")

print("\n=== All headers (if none above) ===")
for key, value in resp.headers.items():
    print(f"  {key}: {value}")

print(f"\n=== Usage from response body ===")
print(resp.json().get("usage", {}))