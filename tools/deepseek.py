"""A tiny DeepSeek chat client (OpenAI-compatible, no SDK needed)."""
from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from .config import Settings, get_settings

# The machine may have a proxy configured for the harness, but the local tools
# run direct.  Set RADIO_HTTP_PROXY to use one explicitly.
DIRECT = os.environ.get("RADIO_HTTP_PROXY", "").strip()


def http_session() -> requests.Session:
    s = requests.Session()
    if DIRECT:
        s.proxies = {"http": DIRECT, "https": DIRECT}
        s.trust_env = False
    else:
        s.trust_env = False
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) myradio-studio/1.0",
        "Accept": "application/json, text/plain, */*",
    })
    return s


class DeepSeekError(RuntimeError):
    pass


class DeepSeekClient:
    def __init__(self, settings: Settings | None = None, timeout: int = 300):
        self.settings = settings or get_settings()
        self.timeout = timeout
        self.session = http_session()

    @property
    def configured(self) -> bool:
        return self.settings.configured

    def _headers(self) -> dict[str, str]:
        if not self.settings.api_key:
            raise DeepSeekError("DeepSeek API key is not configured")
        return {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool = False,
        temperature: float = 0.7,
        max_tokens: int = 6000,
        model: str | None = None,
    ) -> str:
        url = f"{self.settings.base_url}/chat/completions"
        body: dict[str, Any] = {
            "model": model or self.settings.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        last: Exception | None = None
        for attempt in range(3):
            try:
                r = self.session.post(url, headers=self._headers(), json=body, timeout=self.timeout)
                if r.status_code == 400 and json_mode and "response_format" in r.text:
                    # A compatible gateway that does not implement JSON mode.
                    body.pop("response_format", None)
                    json_mode = False
                    continue
                if r.status_code in (429, 500, 502, 503, 504):
                    raise DeepSeekError(f"DeepSeek returned HTTP {r.status_code}: {r.text[:300]}")
                if r.status_code >= 400:
                    detail = r.text[:500]
                    try:
                        detail = r.json().get("error", {}).get("message") or detail
                    except Exception:
                        pass
                    raise DeepSeekError(f"DeepSeek HTTP {r.status_code}: {detail}")
                data = r.json()
                choices = data.get("choices") or []
                if not choices:
                    raise DeepSeekError(f"DeepSeek returned no choices: {str(data)[:300]}")
                content = choices[0].get("message", {}).get("content") or ""
                if not content.strip():
                    raise DeepSeekError("DeepSeek returned an empty message")
                return content
            except (requests.RequestException, DeepSeekError, ValueError) as exc:
                last = exc
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
        raise DeepSeekError(str(last) if last else "DeepSeek request failed")

    def chat_json(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.65,
        max_tokens: int = 6000,
    ) -> dict:
        text = self.chat(messages, json_mode=True, temperature=temperature, max_tokens=max_tokens)
        return parse_json_object(text)

    def verify(self) -> dict:
        started = time.time()
        text = self.chat(
            [{"role": "user", "content": "Reply with exactly: OK"}],
            temperature=0,
            max_tokens=8,
        )
        return {
            "ok": "OK" in text.upper(),
            "reply": text.strip()[:40],
            "model": self.settings.model,
            "latency_ms": round((time.time() - started) * 1000),
        }


def parse_json_object(text: str) -> dict:
    """Parse the first JSON object in a model reply, tolerating code fences."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1] if "\n" in raw else raw
        raw = raw.rsplit("```", 1)[0]
    raw = raw.strip()
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise DeepSeekError(f"DeepSeek did not return JSON: {text[:300]}")
        try:
            obj = json.loads(raw[start:end + 1])
        except json.JSONDecodeError as exc:
            raise DeepSeekError(f"DeepSeek returned invalid JSON: {exc}; {text[:300]}")
    if not isinstance(obj, dict):
        raise DeepSeekError("DeepSeek returned JSON that is not an object")
    return obj
