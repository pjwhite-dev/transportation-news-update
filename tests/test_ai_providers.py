from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

import requests

from ai_providers import OllamaProvider, provider_from_env


class Response:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.text)


class StreamResponse:
    """Minimal streaming /api/chat response: NDJSON chunks ending in a done frame."""

    status_code = 200
    text = ""

    def __init__(self, content: str, prompt_tokens: int = 0, output_tokens: int = 0, done: bool = True):
        half = max(1, len(content) // 2)
        self._lines = [
            json.dumps({"message": {"content": content[:half]}, "done": False}).encode(),
            json.dumps({"message": {"content": content[half:]}, "done": False}).encode(),
        ]
        if done:
            self._lines.append(
                json.dumps(
                    {
                        "message": {"content": ""},
                        "done": True,
                        "prompt_eval_count": prompt_tokens,
                        "eval_count": output_tokens,
                    }
                ).encode()
            )

    def iter_lines(self):
        return iter(self._lines)


class ProviderConfigurationTests(unittest.TestCase):
    def test_ollama_is_default_and_openai_key_is_ignored(self) -> None:
        provider = provider_from_env(
            {
                "OPENAI_API_KEY": "must-not-be-used",
                "OPENAI_FALLBACK_ENABLED": "false",
            }
        )

        self.assertEqual(provider.name, "ollama")
        self.assertNotIn("api_key", vars(provider))

    def test_explicit_fallback_requires_a_key(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires OPENAI_API_KEY"):
            provider_from_env(
                {
                    "AI_PROVIDER": "ollama",
                    "OPENAI_FALLBACK_ENABLED": "true",
                }
            )


class OllamaProviderTests(unittest.TestCase):
    def test_schema_failure_is_retried_and_repaired(self) -> None:
        session = Mock()
        session.post.side_effect = [
            StreamResponse('{"wrong": true}'),
            StreamResponse(json.dumps({"ok": True}), prompt_tokens=4, output_tokens=3),
        ]
        provider = OllamaProvider(model="test", session=session, max_attempts=2)
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }

        with patch("ai_providers.time.sleep"):
            result = provider.generate_structured(
                [{"role": "developer", "content": "Return JSON."}],
                schema_name="test_schema",
                schema=schema,
                max_output_tokens=50,
            )

        self.assertEqual(result.value, {"ok": True})
        self.assertEqual(result.retries, 1)
        self.assertEqual(result.usage["total_tokens"], 7)
        first_call = session.post.call_args_list[0].kwargs
        self.assertEqual(first_call["json"]["messages"][0]["role"], "system")
        self.assertTrue(first_call["json"]["stream"])
        self.assertEqual(first_call["json"]["format"], schema)

    def test_requests_use_a_no_data_stall_timeout_not_a_wall_clock_timeout(self) -> None:
        session = Mock()
        session.post.return_value = StreamResponse("Plain prose.", 5, 2)
        provider = OllamaProvider(model="test", session=session, stall_timeout_seconds=120)

        provider.generate_text([{"role": "user", "content": "Write."}], max_output_tokens=50)

        kwargs = session.post.call_args.kwargs
        self.assertEqual(kwargs["timeout"], (10, 120))
        self.assertTrue(kwargs["stream"])

    def test_text_generation_is_free_text_not_schema_constrained(self) -> None:
        session = Mock()
        session.post.return_value = StreamResponse("The FAA finalized the rule.", 9, 6)
        provider = OllamaProvider(model="test", session=session)

        result = provider.generate_text(
            [{"role": "user", "content": "Write."}], max_output_tokens=50
        )

        self.assertEqual(result.text, "The FAA finalized the rule.")
        self.assertEqual(result.usage["total_tokens"], 15)
        self.assertNotIn("format", session.post.call_args.kwargs["json"])

    def test_stalled_stream_is_retried_then_fails_without_fallback_text(self) -> None:
        session = Mock()
        session.post.side_effect = requests.ReadTimeout("no data for 120s")
        provider = OllamaProvider(model="test", session=session, max_attempts=2)

        with patch("ai_providers.time.sleep"):
            with self.assertRaisesRegex(RuntimeError, "text generation failed after 2 attempts"):
                provider.generate_text([{"role": "user", "content": "Write."}], max_output_tokens=50)
        self.assertEqual(session.post.call_count, 2)

    def test_truncated_stream_is_an_error(self) -> None:
        session = Mock()
        session.post.return_value = StreamResponse("half a sent", done=False)
        provider = OllamaProvider(model="test", session=session, max_attempts=1)

        with self.assertRaisesRegex(RuntimeError, "ended before completion"):
            provider.generate_text([{"role": "user", "content": "Write."}], max_output_tokens=50)

    def test_health_check_fails_when_model_is_not_installed(self) -> None:
        session = Mock()
        session.get.return_value = Response(
            200, {"models": [{"name": "another-model:latest"}]}
        )
        provider = OllamaProvider(model="missing:latest", session=session)

        with self.assertRaisesRegex(RuntimeError, "is not installed"):
            provider.health_check()


if __name__ == "__main__":
    unittest.main()
