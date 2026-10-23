"""Thin wrapper over any OpenAI-compatible host (open models via credits, Together, Groq, Ollama...).

Skills compute their findings deterministically and only ask the model to phrase them.
If no model is configured (or the call fails) the fallback text is used, so mock mode and
demo mode never depend on a network call.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from chief.config import Config

log = logging.getLogger("chief.llm")

REQUEST_TIMEOUT = 20  # seconds: a slow model must not stall the stage; the fallback text answers instead
VISION_TIMEOUT = 45  # reading a photo takes longer than phrasing a sentence


def _message(r):
    """The assistant message of a completion. Cline's API wraps a successful reply as {"data": {"choices": [...]}},
    which the OpenAI SDK parses as choices=None with the payload in model_extra, so unwrap that shape too."""
    if getattr(r, "choices", None):
        return r.choices[0].message
    data = (getattr(r, "model_extra", None) or {}).get("data") or {}
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("model reply has no choices")
    m = choices[0].get("message") or {}
    calls = [
        SimpleNamespace(id=c.get("id"), function=SimpleNamespace(**(c.get("function") or {"name": "", "arguments": ""})))
        for c in (m.get("tool_calls") or [])
    ]
    return SimpleNamespace(content=m.get("content"), tool_calls=calls)


class LLM:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.mode != "mock" and self.cfg.llm_api_key and self.cfg.llm_model)

    def complete(self, system: str, user: str, fallback: str) -> str:
        if not self.enabled:
            return fallback
        try:
            if self._client is None:
                from openai import OpenAI  # imported lazily: not needed in mock mode

                self._client = OpenAI(base_url=self.cfg.llm_base_url, api_key=self.cfg.llm_api_key)
            r = self._client.chat.completions.create(
                model=self.cfg.llm_model,
                temperature=0.3,
                stream=False,  # Cline's API streams by default; we parse one JSON reply
                timeout=REQUEST_TIMEOUT,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
            return (_message(r).content or "").strip() or fallback
        except Exception as exc:  # network, quota, model errors: never break the loop
            log.warning("LLM call failed, using fallback: %s", exc)
            return fallback

    def vision(self, system: str, user: str, image: bytes, mime: str) -> str | None:
        """Ask the model about one image (an OpenAI-style image_url data URL). Returns its text, or None when no model is
        configured or the call fails, so the caller can say plainly that it could not read the picture. The image and the
        text in it are untrusted data: callers must treat the answer as data too."""
        if not self.enabled:
            return None
        try:
            import base64

            if self._client is None:
                from openai import OpenAI  # imported lazily: not needed in mock mode

                self._client = OpenAI(base_url=self.cfg.llm_base_url, api_key=self.cfg.llm_api_key)
            url = f"data:{mime};base64," + base64.b64encode(image).decode("ascii")
            r = self._client.chat.completions.create(
                model=self.cfg.llm_model, temperature=0, stream=False, timeout=VISION_TIMEOUT, max_tokens=900,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": [{"type": "text", "text": user}, {"type": "image_url", "image_url": {"url": url}}]},
                ],
            )
            return (_message(r).content or "").strip() or None
        except Exception as exc:  # network, quota, a model without vision: never break the loop
            log.warning("LLM vision call failed: %s", type(exc).__name__)
            return None

    def chat_with_tools(self, messages: list[dict], tool_schemas: list[dict]) -> dict | None:
        """One OpenAI-style tool-calling turn. Returns the assistant message as a plain dict:
        {"role": "assistant", "content": str | None, "tool_calls": [{"id", "type": "function",
        "function": {"name", "arguments": <JSON string>}}]}  (no "tool_calls" key when there are none).
        Returns None when no model is configured or the call fails, so callers can fall back."""
        if not self.enabled:
            return None
        try:
            if self._client is None:
                from openai import OpenAI  # imported lazily: not needed in mock mode

                self._client = OpenAI(base_url=self.cfg.llm_base_url, api_key=self.cfg.llm_api_key)
            kwargs = {"tools": tool_schemas} if tool_schemas else {}
            r = self._client.chat.completions.create(
                model=self.cfg.llm_model, temperature=0.2, stream=False, timeout=REQUEST_TIMEOUT, messages=messages, **kwargs
            )
            msg = _message(r)
            out: dict = {"role": "assistant", "content": msg.content}
            calls = [
                {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in (getattr(msg, "tool_calls", None) or [])
            ]
            if calls:
                out["tool_calls"] = calls
            return out
        except Exception as exc:  # network, quota, model errors: never break the loop
            log.warning("LLM tool-calling call failed: %s", exc)
            return None
