"""APInex LLM client — OpenAI-compatible, v2 with self-healing."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.apinex.bond/v1"
DEFAULT_MODEL = "gpt-4o-mini"


class APInexError(Exception):
    pass


class APInexClient:
    """Async OpenAI-compatible client for APInex with retry."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.getenv("APINEX_API_KEY")
        if not self.api_key:
            raise APInexError("Missing APINEX_API_KEY")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True,
    )
    async def chat(self, messages, model=None, temperature=0.3, max_tokens=600,
                   response_format=None, **kwargs):
        if not self._client:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            )
        payload = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            payload["response_format"] = response_format
        payload.update(kwargs)
        try:
            r = await self._client.post("/chat/completions", json=payload)
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as e:
            body = e.response.text[:500] if e.response else ""
            log.warning("APInex HTTP %d: %s", e.response.status_code, body)
            raise APInexError(f"HTTP {e.response.status_code}: {body}") from e

    async def chat_json(self, messages, model=None, temperature=0.3, max_tokens=600):
        try:
            resp = await self.chat(
                messages, model=model, temperature=temperature, max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except APInexError:
            resp = await self.chat(
                messages, model=model, temperature=temperature, max_tokens=max_tokens,
            )
        content = resp["choices"][0]["message"]["content"]
        usage = resp.get("usage", {})
        try:
            parsed = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            parsed = _extract_json(content)
            if parsed is None:
                raise APInexError(f"Could not parse JSON: {content[:200]}")
        parsed["_usage"] = usage
        parsed["_raw_content"] = content
        return parsed

    async def list_models(self):
        if not self._client:
            self._client = httpx.AsyncClient(
                base_url=self.base_url, timeout=self.timeout,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        r = await self._client.get("/models")
        r.raise_for_status()
        return r.json().get("data", [])


def _extract_json(text):
    if not text: return None
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif "```" in text:
        text = text.split("```", 1)[1].split("```", 1)[0]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(text[start:end+1])
    except json.JSONDecodeError:
        return None
