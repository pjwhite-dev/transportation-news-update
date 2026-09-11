from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Protocol

import requests
from jsonschema import ValidationError, validate

DEFAULT_OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen3.6:27b-q4_K_M"
DEFAULT_OPENAI_MODEL = "gpt-5.4-mini"
OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"
TRANSIENT_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class StructuredGeneration:
    value: dict[str, Any]
    usage: dict[str, int]
    estimated_cost: float | None
    retries: int = 0


class AIProvider(Protocol):
    name: str
    model: str

    def generate_structured(
        self,
        messages: list[dict[str, str]],
        *,
        schema_name: str,
        schema: dict[str, Any],
        max_output_tokens: int,
    ) -> StructuredGeneration: ...

    def health_check(self) -> dict[str, Any]: ...


def _clean_error(value: str, maximum: int = 900) -> str:
    return " ".join((value or "").split())[:maximum]


def _positive_int(value: str | None, default: int) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _float(value: str | None, default: float) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _enabled(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _validate_payload(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValidationError("The structured response must be a JSON object.")
    validate(instance=value, schema=schema)
    return value


class OllamaProvider:
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str = DEFAULT_OLLAMA_BASE_URL,
        model: str = DEFAULT_OLLAMA_MODEL,
        timeout_seconds: int = 600,
        num_ctx: int = 32768,
        temperature: float = 0.1,
        num_predict: int | None = None,
        max_attempts: int = 3,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.num_predict = num_predict
        self.max_attempts = max(1, max_attempts)
        self.session = session or requests.Session()
        if not self.model:
            raise ValueError("OLLAMA_MODEL must not be empty.")

    @staticmethod
    def _messages(messages: list[dict[str, str]]) -> list[dict[str, str]]:
        return [
            {
                "role": "system" if item.get("role") == "developer" else item["role"],
                "content": item["content"],
            }
            for item in messages
        ]

    def generate_structured(
        self,
        messages: list[dict[str, str]],
        *,
        schema_name: str,
        schema: dict[str, Any],
        max_output_tokens: int,
    ) -> StructuredGeneration:
        request_messages = self._messages(messages)
        errors: list[str] = []
        for attempt in range(self.max_attempts):
            payload = {
                "model": self.model,
                "messages": request_messages,
                "stream": False,
                "think": False,
                "format": schema,
                "options": {
                    "temperature": self.temperature,
                    "num_ctx": self.num_ctx,
                    "num_predict": self.num_predict or max_output_tokens,
                },
            }
            try:
                response = self.session.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                if response.status_code >= 400:
                    detail = _clean_error(response.text)
                    if response.status_code not in TRANSIENT_STATUS_CODES:
                        raise RuntimeError(
                            f"Ollama returned HTTP {response.status_code}: {detail}"
                        )
                    raise requests.HTTPError(f"HTTP {response.status_code}: {detail}")
                data = response.json()
                content = str((data.get("message") or {}).get("content", "")).strip()
                if not content:
                    raise ValueError("Ollama returned no structured content.")
                value = _validate_payload(json.loads(content), schema)
                usage = {
                    "input_tokens": int(data.get("prompt_eval_count", 0) or 0),
                    "output_tokens": int(data.get("eval_count", 0) or 0),
                }
                usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
                return StructuredGeneration(value, usage, 0.0, attempt)
            except RuntimeError:
                raise
            except (requests.RequestException, ValueError, json.JSONDecodeError, ValidationError) as exc:
                errors.append(_clean_error(str(exc)))
                if attempt + 1 >= self.max_attempts:
                    break
                request_messages = [
                    *self._messages(messages),
                    {
                        "role": "system",
                        "content": (
                            "The previous response was invalid. Return only one JSON object "
                            f"that conforms exactly to the schema named {schema_name}. "
                            f"Validation error: {_clean_error(str(exc), 350)}"
                        ),
                    },
                ]
                time.sleep(min(2 ** attempt, 4) + random.uniform(0.05, 0.25))
        raise RuntimeError(
            f"Ollama structured generation failed after {self.max_attempts} attempts: "
            + " | ".join(errors[-self.max_attempts :])
        )

    def health_check(self) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}/api/tags", timeout=min(self.timeout_seconds, 15)
            )
            response.raise_for_status()
            names = {
                str(item.get("name", ""))
                for item in response.json().get("models", [])
            }
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeError(f"Ollama is not healthy at {self.base_url}: {exc}") from exc
        if self.model not in names:
            raise RuntimeError(
                f"Ollama model {self.model!r} is not installed. Available: "
                + (", ".join(sorted(names)) or "none")
            )
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean", "const": True}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        result = self.generate_structured(
            [{"role": "user", "content": 'Return exactly {"ok": true}.'}],
            schema_name="health_check",
            schema=schema,
            max_output_tokens=64,
        )
        return {
            "provider": self.name,
            "model": self.model,
            "base_url": self.base_url,
            "structured_generation": result.value.get("ok") is True,
            "retries": result.retries,
        }


class OpenAIProvider:
    name = "openai"

    PRICES = {
        "gpt-5.4-mini": {"input": 0.75, "output": 4.50},
        "gpt-5-mini": {"input": 0.25, "output": 2.00},
    }

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_OPENAI_MODEL,
        timeout_seconds: int = 300,
        max_attempts: int = 4,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError(
                "OPENAI_API_KEY is required only when OpenAI is explicitly enabled."
            )
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)
        self.session = session or requests.Session()

    @staticmethod
    def _text(data: dict[str, Any]) -> str:
        parts: list[str] = []
        refusals: list[str] = []
        for output in data.get("output", []):
            if output.get("type") != "message":
                continue
            for item in output.get("content", []):
                if item.get("type") == "output_text":
                    parts.append(str(item.get("text", "")))
                elif item.get("type") == "refusal":
                    refusals.append(str(item.get("refusal", "Request refused.")))
        if refusals:
            raise RuntimeError("OpenAI declined the request: " + " ".join(refusals))
        text = "".join(parts).strip()
        if not text:
            raise ValueError("OpenAI returned no usable output.")
        return text

    def _cost(self, usage: dict[str, Any]) -> float | None:
        prices = self.PRICES.get(self.model)
        if not prices:
            return None
        return (
            int(usage.get("input_tokens", 0) or 0) * prices["input"] / 1_000_000
            + int(usage.get("output_tokens", 0) or 0) * prices["output"] / 1_000_000
        )

    def generate_structured(
        self,
        messages: list[dict[str, str]],
        *,
        schema_name: str,
        schema: dict[str, Any],
        max_output_tokens: int,
    ) -> StructuredGeneration:
        payload = {
            "model": self.model,
            "input": messages,
            "reasoning": {"effort": "none"},
            "max_output_tokens": max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        errors: list[str] = []
        for attempt in range(self.max_attempts):
            try:
                response = self.session.post(
                    OPENAI_RESPONSES_ENDPOINT,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout_seconds,
                )
                if response.status_code >= 400:
                    detail = _clean_error(response.text)
                    if response.status_code not in TRANSIENT_STATUS_CODES:
                        raise RuntimeError(
                            f"OpenAI API returned HTTP {response.status_code}: {detail}"
                        )
                    raise requests.HTTPError(f"HTTP {response.status_code}: {detail}")
                data = response.json()
                value = _validate_payload(json.loads(self._text(data)), schema)
                usage = data.get("usage") or {}
                return StructuredGeneration(value, usage, self._cost(usage), attempt)
            except RuntimeError:
                raise
            except (requests.RequestException, ValueError, json.JSONDecodeError, ValidationError) as exc:
                errors.append(_clean_error(str(exc)))
                if attempt + 1 >= self.max_attempts:
                    break
                time.sleep(min(2 ** attempt, 8) + random.uniform(0.1, 0.5))
        raise RuntimeError(
            f"OpenAI structured generation failed after {self.max_attempts} attempts: "
            + " | ".join(errors[-self.max_attempts :])
        )

    def health_check(self) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean", "const": True}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        result = self.generate_structured(
            [{"role": "user", "content": 'Return exactly {"ok": true}.'}],
            schema_name="health_check",
            schema=schema,
            max_output_tokens=64,
        )
        return {
            "provider": self.name,
            "model": self.model,
            "structured_generation": result.value.get("ok") is True,
            "retries": result.retries,
        }


class FallbackProvider:
    """Use OpenAI only when the operator explicitly enabled fallback."""

    name = "ollama-with-explicit-openai-fallback"

    def __init__(self, primary: OllamaProvider, fallback: OpenAIProvider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.model = primary.model

    def generate_structured(self, *args: Any, **kwargs: Any) -> StructuredGeneration:
        try:
            return self.primary.generate_structured(*args, **kwargs)
        except RuntimeError as primary_error:
            try:
                return self.fallback.generate_structured(*args, **kwargs)
            except RuntimeError as fallback_error:
                raise RuntimeError(
                    f"Ollama failed ({primary_error}); explicitly enabled OpenAI fallback "
                    f"also failed ({fallback_error})."
                ) from fallback_error

    def health_check(self) -> dict[str, Any]:
        return self.primary.health_check()


def provider_from_env(environ: dict[str, str] | None = None) -> AIProvider:
    env = os.environ if environ is None else environ
    provider_name = env.get("AI_PROVIDER", "ollama").strip().casefold()
    fallback_enabled = _enabled(env.get("OPENAI_FALLBACK_ENABLED", "false"))
    if provider_name == "ollama":
        num_predict_value = env.get("OLLAMA_NUM_PREDICT", "").strip()
        primary = OllamaProvider(
            base_url=env.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_BASE_URL),
            model=env.get("OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
            timeout_seconds=_positive_int(env.get("OLLAMA_TIMEOUT_SECONDS"), 600),
            num_ctx=_positive_int(env.get("OLLAMA_NUM_CTX"), 32768),
            temperature=_float(env.get("OLLAMA_TEMPERATURE"), 0.1),
            num_predict=(
                _positive_int(num_predict_value, 20000) if num_predict_value else None
            ),
            max_attempts=_positive_int(env.get("AI_MAX_ATTEMPTS"), 3),
        )
        if not fallback_enabled:
            return primary
        key = env.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ValueError(
                "OPENAI_FALLBACK_ENABLED=true requires OPENAI_API_KEY. "
                "Set fallback to false for local-only production."
            )
        return FallbackProvider(
            primary,
            OpenAIProvider(
                api_key=key,
                model=env.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
            ),
        )
    if provider_name == "openai":
        return OpenAIProvider(
            api_key=env.get("OPENAI_API_KEY", ""),
            model=env.get("OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
        )
    raise ValueError("AI_PROVIDER must be either 'ollama' or 'openai'.")
