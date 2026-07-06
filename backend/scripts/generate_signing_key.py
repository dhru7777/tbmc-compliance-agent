#!/usr/bin/env python3
"""Generate Ed25519 clearinghouse signing keypair for KYB credentials.

Usage:
  python scripts/generate_signing_key.py

Add the printed KYB_SIGNING_PRIVATE_KEY to backend/.env (never commit).
Publish KYB_SIGNING_PUBLIC_KEY or use GET /.well-known/tbmc-signing-key.json
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.x401.signing import generate_clearinghouse_keypair  # noqa: E402


def main() -> None:
    priv, pub, key_id = generate_clearinghouse_keypair()
    print("Add to backend/.env:\n")
    print(f"KYB_SIGNING_PRIVATE_KEY={priv}")
    print(f"KYB_SIGNING_PUBLIC_KEY={pub}")
    print(f"KYB_SIGNING_KEY_ID={key_id}")
    print("\nKeep KYB_SIGNING_PRIVATE_KEY secret. Never commit it to git.")


if __name__ == "__main__":
    main()
