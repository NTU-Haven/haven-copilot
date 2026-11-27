"""Authorized playback of the original audio (ticket 09, spec 10 rows P1/P2, 11.3).

An <audio> element cannot send the X-Haven header, so the page first asks
``POST /api/sessions/<sid>/audio/ticket`` (which has the header and the session
authorization) and gets a short-lived opaque ticket: 128 random bits bound to the signed-in
user, the audio id and an expiry (600 s), held in memory only. The element then loads
``GET /media/audio/<ticket>``. That route checks the ticket, the same user's cookie
(SameSite=Strict; the Host header is checked for every request) and, on every Range
request, that the user may still open the session and the audio is still playable.
One ``audio_play_ticket`` audit row per ticket, ids only.

Every failure of the media route is a 404 (a ticket that is invalid, expired, someone
else's, or for audio that has been deleted), so tickets cannot be probed.

Browser-recorded webm (MediaRecorder, chunked) has no Cues and often no duration, so
seeking over Range is not reliable: ``playback_supported`` is False for audio/webm
(docs/playback-webm-seek.md). Uploaded m4a/mp3/wav/ogg/flac keep playback.
"""

from __future__ import annotations

import os
import re
import secrets
import threading
from datetime import datetime, timedelta
from http import HTTPStatus

from . import audio, store
from .transcript import load_transcript

TICKET_SECONDS = 600
CHUNK = 64 * 1024
_MAX_TICKETS = 2000
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")
UNSUPPORTED_MIMES = ("audio/webm",)  # see docs/playback-webm-seek.md

_LOCK = threading.Lock()
_TICKETS: dict[str, dict] = {}


def now() -> datetime:
    """The time tickets are judged by (tests replace this or pass ``clock``)."""
    return audio.now()


def reset() -> None:
    with _LOCK:
        _TICKETS.clear()


def supported(mime: str | None) -> bool:
    return bool(mime) and mime not in UNSUPPORTED_MIMES


def view(sid: str) -> dict:
    """Fields for the session page. ``playback_supported`` is False when there is audio but
    it is a kind the browser cannot seek (browser-recorded webm); None when never any audio."""
    row = audio.get(sid)
    return {"playback_supported": None if row is None else supported(row["mime"])}


def _estimated(sid: str) -> bool:
    try:
        return bool(load_transcript(sid).timestamps_estimated)
    except (OSError, ValueError, KeyError):
        return False


def _name(user: dict | None) -> str | None:
    return user["username"] if user else None


def _refuse(status: str, zh: str):
    from .server import Refused  # late: server imports this module
    raise Refused(f"the original audio cannot be played ({status})", zh, audio_status=status)


def issue(sid: str, user: dict | None, *, clock: datetime | None = None) -> dict:
    """The body of P1. The caller has already checked that ``user`` may open ``sid``."""
    t = clock or now()
    audio.sweep(t)  # an expiry is noticed now, not at the next timer tick
    row = audio.for_session(sid, clock=t)
    if row is None:
        _refuse("none", "這次會談沒有原始錄音可以回放。")
    if row["status"] == "deleted":
        _refuse("deleted", "原始錄音已清除，無法回放；逐字稿與草稿仍保留。")
    if not row["playable"]:
        _refuse(row["audio_status"], "原始錄音目前無法回放。")
    if not supported(row["mime"]):
        _refuse("unsupported", "瀏覽器錄製的錄音格式不支援拖動回放；請改用上傳的錄音檔。")
    ticket = secrets.token_urlsafe(16)  # 128 bits
    with _LOCK:
        for k in [k for k, v in _TICKETS.items() if v["expires"] <= t]:
            del _TICKETS[k]
        if len(_TICKETS) >= _MAX_TICKETS:
            del _TICKETS[min(_TICKETS, key=lambda k: _TICKETS[k]["expires"])]
        _TICKETS[ticket] = {"user": _name(user), "audio_id": row["audio_id"], "sid": sid,
                            "expires": t + timedelta(seconds=TICKET_SECONDS)}
    store.audit("audio_play_ticket", f"{sid} {row['audio_id']}")
    return {"url": f"/media/audio/{ticket}", "expires_in": TICKET_SECONDS, "timestamps_estimated": _estimated(sid)}


def parse_range(header: str | None, size: int):
    """None: send the whole file (no header, or one that is not a single byte range).
    (start, end) inclusive; or "unsatisfiable"."""
    if not header:
        return None
    m = _RANGE.match(header.strip())
    if not m or (m[1] == "" and m[2] == ""):
        return None
    if m[1] == "":  # suffix: the last n bytes
        n = int(m[2])
        if n == 0 or size == 0:
            return "unsatisfiable"
        return (max(0, size - n), size - 1)
    start = int(m[1])
    end = int(m[2]) if m[2] else size - 1
    if m[2] and end < start:
        return None  # not a valid range: ignored, as the RFC says
    if start >= size:
        return "unsatisfiable"
    return (start, min(end, size - 1))


def serve(h, ticket: str, user: dict | None, range_header: str | None, check_session, *, clock: datetime | None = None) -> None:
    """The body of P2. ``h`` is the request handler; ``check_session(sid)`` raises a 404
    unless ``user`` may open the session."""
    from .server import HttpError  # late: server imports this module

    def gone():
        return HttpError(HTTPStatus.NOT_FOUND, "no such audio", "找不到這段錄音。")

    t = clock or now()
    with _LOCK:
        entry = _TICKETS.get(ticket)
        if entry is not None and entry["expires"] <= t:
            del _TICKETS[ticket]
            entry = None
    if entry is None or entry["user"] != _name(user):
        raise gone()
    try:
        check_session(entry["sid"])  # on every request: access may have been taken away
    except HttpError:
        raise gone() from None
    row = audio.for_session(entry["sid"], clock=t)
    if row is None or not row["playable"] or row["audio_id"] != entry["audio_id"]:
        raise gone()
    try:
        f = open(row["path"], "rb")
    except OSError:
        raise gone() from None
    with f:
        size = os.fstat(f.fileno()).st_size
        rng = parse_range(range_header, size)
        base = {"Content-Type": row["mime"], "Accept-Ranges": "bytes", "Cache-Control": "no-store"}
        if rng == "unsatisfiable":
            h.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            for k, v in base.items():
                h.send_header(k, v)
            h.send_header("Content-Range", f"bytes */{size}")
            h.send_header("Content-Length", "0")
            h.end_headers()
            return
        start, end = rng if rng else (0, size - 1)
        length = max(0, end - start + 1) if size else 0
        h.send_response(HTTPStatus.PARTIAL_CONTENT if rng else HTTPStatus.OK)
        for k, v in base.items():
            h.send_header(k, v)
        if rng:
            h.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        h.send_header("Content-Length", str(length))
        h.end_headers()
        f.seek(start)
        left = length
        while left > 0:
            chunk = f.read(min(CHUNK, left))
            if not chunk:
                break
            h.wfile.write(chunk)
            left -= len(chunk)
