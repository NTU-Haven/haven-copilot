"""Encryption at rest for session content (HAVEN_ENCRYPT=1).

What is encrypted: transcripts in data/sessions/, the client list, and in the
database the pipeline results, signed notes, addenda and cached verdicts.
What is not: session ids, timestamps, who signed, hashes, and the audit log
(which holds only those), so lists and the hash chain work without the key.

AES-256-GCM (the ``cryptography`` package). The key is derived from a
passphrase with scrypt each time Haven starts and is kept in memory only; the
data directory holds the salt and a check value, never the key. Losing the
passphrase means losing the data: there is no recovery.

The threat this answers is a copied disk, backup or data folder. It does not
protect against someone using the running machine under the therapist's
account, which can read what the server reads.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets

from . import config

PREFIX = "enc1:"
_CHECK = b"haven-key-check"
_SCRYPT = {"n": 2**15, "r": 8, "p": 1, "dklen": 32, "maxmem": 64 * 1024 * 1024}
_key: bytes | None = None


class LockedError(RuntimeError):
    """Encrypted data was met but no key is loaded."""


class WrongPassphrase(ValueError):
    pass


def enabled() -> bool:
    return os.environ.get("HAVEN_ENCRYPT", "") == "1"


def unlocked() -> bool:
    return _key is not None


def _aead():
    AESGCM = _aead_class()
    if _key is None:
        raise LockedError("encrypted data, but no key is loaded (start Haven with HAVEN_ENCRYPT=1 and the passphrase)")
    return AESGCM(_key)


def _key_file():
    return config.DATA_DIR / "key.json"


def unlock(passphrase: str) -> bool:
    """Loads the key. The first time, creates the salt and check value and
    returns True (a new key); afterwards a wrong passphrase raises WrongPassphrase."""
    global _key
    if not passphrase:
        raise WrongPassphrase("empty passphrase")
    path = _key_file()
    new = not path.exists()
    if new:
        salt = secrets.token_bytes(16)
    else:
        saved = json.loads(path.read_text(encoding="utf-8"))
        salt = base64.b64decode(saved["salt"])
    key = hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, **_SCRYPT)
    previous, _key = _key, key
    try:
        if new:
            from .files import write_private
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            write_private(path, json.dumps({"kdf": "scrypt", "n": _SCRYPT["n"], "r": _SCRYPT["r"], "p": _SCRYPT["p"],
                                            "salt": base64.b64encode(salt).decode(), "check": seal_bytes(_CHECK)}) + "\n", encrypt=False)
        elif open_bytes(saved["check"]) != _CHECK:
            raise WrongPassphrase("wrong passphrase")
    except WrongPassphrase:
        _key = previous
        raise
    except Exception as e:  # authentication failure from AES-GCM
        _key = previous
        if type(e).__name__ == "InvalidTag":
            raise WrongPassphrase("wrong passphrase") from None
        raise
    return new


def start(prompt=None) -> str:
    """Run at start-up. With HAVEN_ENCRYPT=1, loads the key from HAVEN_PASSPHRASE
    or by asking on the terminal. Returns a line for the console, or raises
    SystemExit with a message the therapist can act on."""
    if not enabled():
        if _key_file().exists():
            raise SystemExit("Haven: this data folder is encrypted (data/key.json). Start with HAVEN_ENCRYPT=1.")
        return ""
    try:
        _aead_class()
    except RuntimeError as e:
        raise SystemExit(f"Haven: {e}") from None
    if unlocked():
        return "Haven: encryption on"
    passphrase = os.environ.get("HAVEN_PASSPHRASE")
    if passphrase is None:
        import getpass
        ask = prompt or getpass.getpass
        first = not _key_file().exists()
        passphrase = ask("Haven passphrase (new: choose one; it cannot be recovered): " if first else "Haven passphrase: ")
        if first and ask("Type it again: ") != passphrase:
            raise SystemExit("Haven: the two passphrases differ; nothing was created.")
    try:
        new = unlock(passphrase)
    except WrongPassphrase:
        raise SystemExit("Haven: wrong passphrase.") from None
    return "Haven: encryption on (new key created; keep the passphrase safe)" if new else "Haven: encryption on"


def _aead_class():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise RuntimeError("HAVEN_ENCRYPT=1 needs the cryptography package: pip install cryptography") from None
    return AESGCM


def lock() -> None:
    global _key
    _key = None


def seal_bytes(data: bytes) -> str:
    nonce = secrets.token_bytes(12)
    return PREFIX + base64.b64encode(nonce + _aead().encrypt(nonce, data, None)).decode()


def open_bytes(text: str) -> bytes:
    raw = base64.b64decode(text[len(PREFIX):])
    return _aead().decrypt(raw[:12], raw[12:], None)


def is_sealed(text: str) -> bool:
    return text.startswith(PREFIX)


def seal(text: str) -> str:
    """Encrypts when encryption is on; otherwise returns the text unchanged."""
    if not enabled():
        return text
    return seal_bytes(text.encode("utf-8"))


def open_(text: str) -> str:
    """Decrypts a sealed value; plain text (written before encryption was on,
    or a tracked demo file) is returned as it is."""
    if not is_sealed(text):
        return text
    return open_bytes(text).decode("utf-8")


def lookup(text: str) -> str:
    """A stable stand-in for a value used as a database key, so equal values
    still match while the value itself is not stored."""
    if not enabled():
        return text
    if _key is None:
        raise LockedError("no key loaded")
    subkey = hmac.new(_key, b"haven-lookup", hashlib.sha256).digest()
    return "h1:" + hmac.new(subkey, text.encode("utf-8"), hashlib.sha256).hexdigest()
