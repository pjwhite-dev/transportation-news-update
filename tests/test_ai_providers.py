from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

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
            Response(200, {"message": {"content": '{"wrong": true}'}}),
            Response(
                200,
                {
                    "message": {"content": json.dumps({"ok": True})},
                    "prompt_eval_count": 4,
                    "eval_count": 3,
                },
            ),
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
        first_messages = session.post.call_args_list[0].kwargs["json"]["messages"]
        self.assertEqual(first_messages[0]["role"], "system")

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
