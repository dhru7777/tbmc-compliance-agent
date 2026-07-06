"""Ed25519 signing for clearinghouse-issued compliance credentials."""

from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

DEFAULT_SIGNING_KEY_ID = "tbmc-clearinghouse-2026"
DEV_SEED = b"tbmc-demo-clearinghouse-signing-seed-v1"


def generate_clearinghouse_keypair() -> tuple[str, str, str]:
    """
    Generate a new Ed25519 keypair for the clearinghouse.
    Returns (private_key_b64, public_key_b64, signing_key_id).
    Store private_key_b64 in KYB_SIGNING_PRIVATE_KEY — never commit it.
    """
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    priv_b64 = base64.b64encode(
        private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
    ).decode("ascii")
    pub_b64 = base64.b64encode(
        public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    key_id = os.getenv("KYB_SIGNING_KEY_ID", DEFAULT_SIGNING_KEY_ID)
    return priv_b64, pub_b64, key_id


@lru_cache(maxsize=1)
def _load_private_key() -> Ed25519PrivateKey:
    raw_b64 = os.getenv("KYB_SIGNING_PRIVATE_KEY", "").strip()
    if raw_b64:
        raw = base64.b64decode(raw_b64)
        return Ed25519PrivateKey.from_private_bytes(raw)
    # Dev-only deterministic key — set KYB_SIGNING_PRIVATE_KEY in production.
    seed = hashlib.sha256(DEV_SEED).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def _load_public_key() -> Ed25519PublicKey:
    pub_b64 = os.getenv("KYB_SIGNING_PUBLIC_KEY", "").strip()
    if pub_b64:
        return Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64))
    return _load_private_key().public_key()


def signing_key_id() -> str:
    return os.getenv("KYB_SIGNING_KEY_ID", DEFAULT_SIGNING_KEY_ID)


def get_public_key_info() -> dict:
    pub = _load_public_key()
    pub_b64 = base64.b64encode(
        pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    ).decode("ascii")
    return {
        "signing_key_id": signing_key_id(),
        "algorithm": "Ed25519",
        "public_key": pub_b64,
        "issuer": "Better Money Company Clearinghouse Compliance Agent",
    }


def sign_canonical_payload(canonical_json: str) -> str:
    """Sign UTF-8 canonical JSON; return base64 signature."""
    private_key = _load_private_key()
    sig = private_key.sign(canonical_json.encode("utf-8"))
    return base64.b64encode(sig).decode("ascii")


def verify_signature(canonical_json: str, signature_b64: str, public_key: Ed25519PublicKey | None = None) -> bool:
    pub = public_key or _load_public_key()
    try:
        pub.verify(base64.b64decode(signature_b64), canonical_json.encode("utf-8"))
        return True
    except Exception:
        return False
