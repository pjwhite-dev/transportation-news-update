"""Direct alerts and dead-man pings (rebuild plan 7.6 and 7.7).

Telegram alerts go straight to the Bot API so they work when Hermes is down.
Secrets come only from the Windows Credential Manager through ``keyring``; they
are stored with ``scripts/set_secret.py``. Nothing here ever raises into the
caller: an alert that cannot be sent returns False and is logged to stderr.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Any

import requests

KEYRING_SERVICE = "newsroom"
TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_MESSAGE_CHARS = 3500

SECRET_NAMES = (
    "telegram_bot_token",
    "telegram_chat_id",
    "github_token",
    "regulations_gov_api_key",
    "finnhub_api_key",
    "hc_pc_alive",
    "hc_edition_verified",
    "hc_discovery",
    "hc_tracker_verified",
)
HEALTHCHECKS = {
    "pc-alive": "hc_pc_alive",
    "edition-verified": "hc_edition_verified",
    "discovery": "hc_discovery",
    "tracker-verified": "hc_tracker_verified",
}

_REDACTIONS = (
    (re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{30,}\b"), "<telegram-token>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "<api-key>"),
    (re.compile(r"\b(?:ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{16,}"), "<github-token>"),
    (re.compile(r"https://hc-ping\.com/[A-Za-z0-9-]+(?:/[A-Za-z0-9_-]+)?"), "<healthchecks-url>"),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<email>"),
    (re.compile(r"(?i)[A-Z]:\\Users\\[^\\\s\"']+"), r"C:\\Users\\<user>"),
    (re.compile(r"(?i)/(?:c/)?Users/[^/\s\"']+"), "/Users/<user>"),
)


def redact(text: str) -> str:
    """Remove tokens, keys, user-profile paths and email addresses from outbound text."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def get_secret(name: str) -> str | None:
    try:
        import keyring

        value = keyring.get_password(KEYRING_SERVICE, name)
    except Exception as exc:  # keyring backend missing or locked
        print(f"alerts: cannot read secret {name}: {type(exc).__name__}", file=sys.stderr)
        return None
    return value or None


def send_telegram(
    text: str,
    *,
    critical: bool = True,
    session: requests.Session | None = None,
    secrets: Any = get_secret,
) -> bool:
    """Send one message to the owner's direct chat. Critical messages make a sound."""
    token = secrets("telegram_bot_token")
    chat_id = secrets("telegram_chat_id")
    if not token or not chat_id:
        print("alerts: Telegram is not configured; alert not sent.", file=sys.stderr)
        return False
    http = session or requests.Session()
    try:
        response = http.post(
            TELEGRAM_API.format(token=token),
            json={
                "chat_id": chat_id,
                "text": redact(text)[:MAX_MESSAGE_CHARS],
                "disable_notification": not critical,
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        print(f"alerts: Telegram request failed: {type(exc).__name__}", file=sys.stderr)
        return False
    if response.status_code != 200:
        print(f"alerts: Telegram returned HTTP {response.status_code}.", file=sys.stderr)
        return False
    return True


def ping_healthcheck(
    name: str,
    *,
    failed: bool = False,
    session: requests.Session | None = None,
    secrets: Any = get_secret,
) -> bool:
    """Ping a Healthchecks.io check; ``failed`` signals an explicit failure."""
    secret_name = HEALTHCHECKS.get(name)
    if secret_name is None:
        raise ValueError(f"Unknown healthcheck {name!r}; expected one of {sorted(HEALTHCHECKS)}.")
    url = secrets(secret_name)
    if not url:
        print(f"alerts: healthcheck {name} is not configured; ping skipped.", file=sys.stderr)
        return False
    http = session or requests.Session()
    try:
        response = http.get(url.rstrip("/") + ("/fail" if failed else ""), timeout=15)
    except requests.RequestException as exc:
        print(f"alerts: healthcheck {name} ping failed: {type(exc).__name__}", file=sys.stderr)
        return False
    return response.status_code == 200


def main() -> int:
    parser = argparse.ArgumentParser(description="Send an alert or a dead-man ping.")
    sub = parser.add_subparsers(dest="command", required=True)
    critical = sub.add_parser("critical", help="Telegram message with sound.")
    critical.add_argument("text")
    quiet = sub.add_parser("quiet", help="Silent Telegram message.")
    quiet.add_argument("text")
    ping = sub.add_parser("ping", help="Healthchecks.io ping.")
    ping.add_argument("name", choices=sorted(HEALTHCHECKS))
    ping.add_argument("--fail", action="store_true")
    args = parser.parse_args()
    if args.command == "ping":
        ok = ping_healthcheck(args.name, failed=args.fail)
    else:
        ok = send_telegram(args.text, critical=args.command == "critical")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
