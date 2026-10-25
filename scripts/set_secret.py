"""Store one secret in the Windows Credential Manager (never in the repository).

Usage:  python scripts/set_secret.py anthropic_api_key
The value is read with getpass, so it is never echoed, logged or written to disk
by this script. Code reads secrets only through alerts.get_secret / keyring.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alerts import KEYRING_SERVICE, SECRET_NAMES  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name", choices=SECRET_NAMES)
    parser.add_argument("--delete", action="store_true", help="Remove the stored secret.")
    args = parser.parse_args()

    import keyring

    if args.delete:
        keyring.delete_password(KEYRING_SERVICE, args.name)
        print(f"Deleted {args.name}.")
        return 0
    value = getpass.getpass(f"Value for {args.name} (input hidden): ").strip()
    if not value:
        print("Nothing entered; nothing stored.")
        return 1
    keyring.set_password(KEYRING_SERVICE, args.name, value)
    print(f"Stored {args.name} in Windows Credential Manager ({KEYRING_SERVICE}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
