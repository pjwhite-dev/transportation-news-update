from __future__ import annotations

import unittest
from unittest.mock import Mock

import requests

import alerts


def secrets_from(values: dict):
    return lambda name: values.get(name)


class RedactionTests(unittest.TestCase):
    def test_tokens_keys_paths_and_emails_are_removed(self) -> None:
        text = (
            "token 123456789:AAFabcdefghijklmnopqrstuvwxyz0123456 key sk-ant-api03-abcdefghijklmnop "
            "gh ghp_abcdefghijklmnopqrstuv mail me@example.com path C:\\Users\\pjwhi\\OneDrive\\x.json "
            "ping https://hc-ping.com/abcd-1234/fail"
        )
        cleaned = alerts.redact(text)
        for leaked in ("AAFabc", "sk-ant", "ghp_", "me@example.com", "pjwhi", "hc-ping.com/abcd"):
            self.assertNotIn(leaked, cleaned)

    def test_plain_text_is_unchanged(self) -> None:
        self.assertEqual(alerts.redact("Edition for 2026-10-26 failed at gate: links"),
                         "Edition for 2026-10-26 failed at gate: links")


class TelegramTests(unittest.TestCase):
    SECRETS = {"telegram_bot_token": "123456789:AAFabcdefghijklmnopqrstuvwxyz0123456", "telegram_chat_id": "42"}

    def test_critical_message_has_sound_and_is_redacted(self) -> None:
        session = Mock()
        session.post.return_value = Mock(status_code=200)
        ok = alerts.send_telegram(
            "Failed for me@example.com", critical=True, session=session, secrets=secrets_from(self.SECRETS)
        )
        self.assertTrue(ok)
        payload = session.post.call_args.kwargs["json"]
        self.assertFalse(payload["disable_notification"])
        self.assertEqual(payload["chat_id"], "42")
        self.assertNotIn("example.com", payload["text"])

    def test_quiet_message_is_silent(self) -> None:
        session = Mock()
        session.post.return_value = Mock(status_code=200)
        alerts.send_telegram("ok", critical=False, session=session, secrets=secrets_from(self.SECRETS))
        self.assertTrue(session.post.call_args.kwargs["json"]["disable_notification"])

    def test_missing_secrets_or_network_failure_return_false_without_raising(self) -> None:
        self.assertFalse(alerts.send_telegram("x", session=Mock(), secrets=secrets_from({})))
        session = Mock()
        session.post.side_effect = requests.ConnectionError("down")
        self.assertFalse(alerts.send_telegram("x", session=session, secrets=secrets_from(self.SECRETS)))
        session.post.side_effect = None
        session.post.return_value = Mock(status_code=401)
        self.assertFalse(alerts.send_telegram("x", session=session, secrets=secrets_from(self.SECRETS)))


class HealthcheckTests(unittest.TestCase):
    def test_success_and_fail_pings(self) -> None:
        session = Mock()
        session.get.return_value = Mock(status_code=200)
        secrets = secrets_from({"hc_edition_verified": "https://hc-ping.com/abc/"})
        self.assertTrue(alerts.ping_healthcheck("edition-verified", session=session, secrets=secrets))
        self.assertEqual(session.get.call_args.args[0], "https://hc-ping.com/abc")
        alerts.ping_healthcheck("edition-verified", failed=True, session=session, secrets=secrets)
        self.assertEqual(session.get.call_args.args[0], "https://hc-ping.com/abc/fail")

    def test_unknown_name_raises_and_unconfigured_check_is_skipped(self) -> None:
        with self.assertRaises(ValueError):
            alerts.ping_healthcheck("nope")
        self.assertFalse(alerts.ping_healthcheck("pc-alive", session=Mock(), secrets=secrets_from({})))


if __name__ == "__main__":
    unittest.main()
