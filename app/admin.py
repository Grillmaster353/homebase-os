"""Offline administrator setup. Stop the dashboard before changing its account.

Passwords are prompted without echo or generated using the operating system's
cryptographic random source. They are never accepted in arguments or env vars.
"""
from __future__ import annotations

import argparse
import getpass
import json
import re
import secrets
import sys
import time

from .security import Vault, password_hash


def generate_password() -> str:
    """Forty copy-friendly characters carrying 240 bits of random input."""
    while True:
        password = secrets.token_urlsafe(30)
        if any(c.isupper() for c in password) and any(c.islower() for c in password) and any(c.isdigit() for c in password):
            return password


def configure_account(vault: Vault, username: str, password: str, *, reset: bool = False, if_missing: bool = False) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username):
        raise ValueError("Username must be 1–64 letters, numbers, dots, underscores or dashes.")
    with vault.lock:
        current = vault.data.get("account")
        if current and not reset:
            if if_missing:
                return False
            raise ValueError("An account already exists. Stop the dashboard and use the explicit reset command to change it.")
        if reset and not current:
            raise ValueError("No account exists. Use init first.")
        record = password_hash(password)
        # Preserve an existing second factor, integrations, and all encrypted data.
        account = dict(current or {})
        account.update(username=username, password=record, updatedAt=time.time())
        account.setdefault("createdAt", time.time())
        vault.data["account"] = account
        vault.event("Administrator password changed" if current else "Local administrator initialized", "Offline account maintenance; no password stored in the audit log.", "security")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "reset"))
    parser.add_argument("--username", default="admin")
    parser.add_argument("--data-dir", default=".data")
    parser.add_argument("--if-missing", action="store_true", help="Leave an existing account unchanged during init")
    parser.add_argument("--generate", action="store_true", help="Generate and display a new password once; save it in your password manager")
    args = parser.parse_args(argv)
    vault = Vault(args.data_dir)
    if args.command == "init" and args.if_missing and vault.data.get("account"):
        from .lan import get_lan_name
        get_lan_name(args.data_dir)
        print("Existing administrator account preserved. No password was changed.")
        return
    try:
        if args.generate:
            password = generate_password()
        else:
            if not sys.stdin.isatty():
                parser.error("Run this command in an interactive terminal so the password can be entered without echo. Password arguments and environment variables are not supported.")
            password = getpass.getpass("New dashboard password (at least 14 characters): ")
            confirmation = getpass.getpass("Confirm dashboard password: ")
            if password != confirmation:
                raise ValueError("The passwords do not match. Nothing was changed.")
        changed = configure_account(vault, args.username, password, reset=args.command == "reset", if_missing=args.if_missing)
        from .lan import get_lan_name
        get_lan_name(args.data_dir)
    except ValueError as exc:
        parser.error(str(exc))
    print(f"Dashboard account {'updated' if changed else 'preserved'}: {args.username}")
    if changed and args.generate:
        print(json.dumps({"username": args.username, "password": password}))
        print("Save this generated password now. It is stored only as a salted hash.")


if __name__ == "__main__":
    main()
