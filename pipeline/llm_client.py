"""Shared LLM client used by every generation stage. Supports Gemini (free
tier, hits a 20 requests/day wall at real volume — see build log) and
OpenRouter (pay-per-token, no restrictive daily cap) via LLM_PROVIDER.
"""

import json
import re
import time

import requests

from pipeline.config import env

LLM_PROVIDER = env("LLM_PROVIDER", "openrouter")

GEMINI_API_KEY = env("GEMINI_API_KEY", "")
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-flash-latest")

OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = env("OPENROUTER_MODEL", "deepseek/deepseek-v4-flash")
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

MAX_RETRIES = 4
RETRY_BACKOFF_SECONDS = 10

_gemini_client = None


def _get_gemini_client():
    global _gemini_client
    if _gemini_client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY not set in .env")
        from google import genai

        _gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    return _gemini_client


def _call_gemini(prompt: str, temperature: float) -> str:
    from google.genai import errors, types

    client = _get_gemini_client()
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(temperature=temperature),
            )
            return response.text.strip()
        except (errors.ServerError, errors.ClientError) as e:
            transient = isinstance(e, errors.ServerError) or getattr(e, "code", None) == 429
            if not transient or attempt == MAX_RETRIES:
                raise
            wait = RETRY_BACKOFF_SECONDS * attempt
            print(f"    (Gemini transient error, retrying in {wait}s: {e})")
            time.sleep(wait)
            last_error = e
    raise last_error


def _call_openrouter(prompt: str, temperature: float) -> str:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY not set in .env")

    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                OPENROUTER_URL,
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": temperature,
                },
                timeout=180,
            )
            if response.status_code == 429 or response.status_code >= 500:
                raise requests.HTTPError(f"{response.status_code}: {response.text[:300]}", response=response)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()
        except requests.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            transient = status == 429 or (status is not None and status >= 500)
            if not transient or attempt == MAX_RETRIES:
                raise
            wait = RETRY_BACKOFF_SECONDS * attempt
            print(f"    (OpenRouter transient error, retrying in {wait}s: {e})")
            time.sleep(wait)
            last_error = e
    raise last_error


def call_llm(prompt: str, temperature: float = 0.85) -> str:
    if LLM_PROVIDER == "gemini":
        return _call_gemini(prompt, temperature)
    if LLM_PROVIDER == "openrouter":
        return _call_openrouter(prompt, temperature)
    raise ValueError(f"Unknown LLM_PROVIDER: {LLM_PROVIDER!r}")


def extract_json(text: str) -> dict:
    """Models sometimes wrap JSON in ```json fences or add stray text around it."""
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            text = text[start : end + 1]
    return json.loads(text)
