"""Therapist accounts, sign-in sessions and who may open which client.

Accounts live in ``data/users.json`` (0600, encrypted with the rest when
HAVEN_ENCRYPT=1). Passwords are stored as scrypt hashes. Sign-in sessions are
held in memory only: restarting Haven signs everyone out.

Sign-in is required whenever an account exists, and always when Haven listens
on anything but loopback (serve() refuses to start without accounts then). On a
laptop with no accounts Haven keeps working as before, for one person at the
machine.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from contextvars import ContextVar

from . import config, files

USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
ROLES = ("therapist", "admin")
# A signed-in browser left alone this long is signed out (a laptop left open in a counselling room).
IDLE_SECONDS = int(float(os.environ.get("HAVEN_IDLE_MINUTES", "15")) * 60)
MAX_SECONDS = 12 * 3600  # signed out after a working day whatever happens
# Failed sign-ins: after this many for one account or one address within the window, wait.
MAX_FAILURES, FAILURE_WINDOW, LOCKOUT = 5, 15 * 60, 5 * 60
SCRYPT = {"n": 2**14, "r": 8, "p": 1}
COOKIE = "haven_session"

# The account acting in this request, for the audit log (store.audit reads it).
CURRENT: ContextVar[dict | None] = ContextVar("haven_user", default=None)

_LOCK = threading.Lock()
_SESSIONS: dict[str, dict] = {}  # token -> {"username", "created", "seen"}
_FAILURES: dict[str, list[float]] = {}
# Compared against when the account does not exist, so a wrong name takes as long as a wrong password.
_DUMMY = {"salt": "00" * 16, "hash": "00" * 32, **SCRYPT}


class AccountError(ValueError):
    pass


def _hash(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32)


def users_file():
    return config.DATA_DIR / "users.json"


def load_users() -> list[dict]:
    if not users_file().exists():
        return []
    return json.loads(files.read_private(users_file()))


def _save_users(users: list[dict]) -> None:
    files.write_private(users_file(), json.dumps(users, ensure_ascii=False, indent=2) + "\n")


def required() -> bool:
    """Is sign-in needed? Yes once any account exists."""
    return bool(load_users())


def get_user(username: str) -> dict | None:
    return next((u for u in load_users() if u["username"] == username), None)


def public(user: dict) -> dict:
    return {"username": user["username"], "display_name": user["display_name"], "role": user["role"]}


def set_user(username: str, display_name: str, password: str, role: str = "therapist") -> dict:
    """Adds an account or replaces its name, role and password."""
    username = username.strip().lower()
    if not USERNAME.match(username):
        raise AccountError("帳號只能用小寫英文字母、數字、. _ -，2–32 個字")
    if not display_name.strip() or len(display_name) > 40:
        raise AccountError("請填寫顯示名稱（40 字以內），例如「王心理師」")
    if len(password) < 10:
        raise AccountError("密碼至少 10 個字")
    if role not in ROLES:
        raise AccountError(f"角色只能是 {', '.join(ROLES)}")
    salt = secrets.token_bytes(16)
    record = {"username": username, "display_name": display_name.strip(), "role": role,
              "salt": salt.hex(), "hash": _hash(password, salt, **SCRYPT).hex(), **SCRYPT}
    with _LOCK:
        users = [u for u in load_users() if u["username"] != username] + [record]
        _save_users(users)
        # A new password ends that account's sign-ins everywhere.
        for token in [t for t, s in _SESSIONS.items() if s["username"] == username]:
            del _SESSIONS[token]
    return public(record)


def remove_user(username: str) -> bool:
    with _LOCK:
        users = load_users()
        kept = [u for u in users if u["username"] != username]
        if len(kept) == len(users):
            return False
        _save_users(kept)
        for token in [t for t, s in _SESSIONS.items() if s["username"] == username]:
            del _SESSIONS[token]
    return True


def _locked(key: str, now: float) -> bool:
    recent = [t for t in _FAILURES.get(key, []) if now - t < FAILURE_WINDOW]
    _FAILURES[key] = recent
    return len(recent) >= MAX_FAILURES and now - recent[-1] < LOCKOUT


def sign_in(username: str, password: str, address: str) -> tuple[str, dict]:
    """A new session token for a correct name and password. Raises AccountError
    with a message for the therapist otherwise; it never says which of the two was wrong."""
    username = (username or "").strip().lower()
    now = time.time()
    with _LOCK:
        if _locked("u:" + username, now) or _locked("a:" + address, now):
            raise AccountError(f"登入失敗太多次，請 {LOCKOUT // 60} 分鐘後再試。")
    user = get_user(username) if USERNAME.match(username) else None
    rec = user or _DUMMY
    ok = hmac.compare_digest(_hash(password or "", bytes.fromhex(rec["salt"]), rec["n"], rec["r"], rec["p"]), bytes.fromhex(rec["hash"]))
    with _LOCK:
        if not (ok and user):
            for key in ("u:" + username, "a:" + address):
                _FAILURES.setdefault(key, []).append(now)
            raise AccountError("帳號或密碼不正確。")
        _FAILURES.pop("u:" + username, None)
        token = secrets.token_urlsafe(32)
        _SESSIONS[token] = {"username": username, "created": now, "seen": now}
    return token, public(user)


def session_user(token: str | None) -> dict | None:
    """The account behind a session token, or None when it is unknown, idle too
    long or too old. A valid token counts as activity."""
    if not token:
        return None
    now = time.time()
    with _LOCK:
        s = _SESSIONS.get(token)
        if not s:
            return None
        if now - s["seen"] > IDLE_SECONDS or now - s["created"] > MAX_SECONDS:
            del _SESSIONS[token]
            return None
        s["seen"] = now
    user = get_user(s["username"])
    if user is None:  # the account was removed
        sign_out(token)
        return None
    return public(user)


def sign_out(token: str | None) -> None:
    with _LOCK:
        _SESSIONS.pop(token or "", None)


def reset() -> None:
    """Forget every session and failure count (tests)."""
    with _LOCK:
        _SESSIONS.clear()
        _FAILURES.clear()


def may_open(user: dict | None, client: dict) -> bool:
    """A therapist opens only the clients assigned to them (``therapist_user`` in
    clients.json). Without sign-in (one person at the machine) everyone may.
    An admin manages accounts and reads the audit log; an admin does not read
    clinical records unless the client is assigned to them as well."""
    if user is None:
        return not required()
    return client.get("therapist_user") == user["username"]
