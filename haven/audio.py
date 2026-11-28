"""Original audio: kept in a controlled folder until it has been checked, then removed.

Haven used to delete a recording the moment it was transcribed. Now it keeps the
audio in data/audio/ (0700, files 0600) so the therapist can listen back while
checking the transcript, and removes it when

* the therapist signs the note (``HAVEN_AUDIO_DELETE_ON_CONFIRM``, default 1), or
* ``HAVEN_AUDIO_TTL_HOURS`` have passed (default 72: a test setting that has not
  been checked against any legal retention rule), or
* the therapist deletes it by hand.

Each recording is one row in ``audio_assets`` (store.py). The file name is made
here (``<audio_id><suffix>``), never taken from a client, and the database holds
only that name.

    processing --ASR ok--> available --confirm | expiry | manual--> deleted
        \\--ASR fails--> failed (kept for a retry, until expiry) --same ways--> deleted
    a delete whose unlink fails -> cleanup_failed (attempts + 1), retried by the sweep:
        1 minute, 5 minutes, 30 minutes after the last try, then at every sweep

The sweep (``sweep``) removes what has expired and retries failed deletes. The
server runs it at start-up and every 10 minutes (``start_sweeper``); tests call
``sweep(clock)`` with their own clock. Audit rows carry ids, byte counts, the
reason and the kind of error, never anything from the recording.

For playback (ticket 09): ``for_session(session_id)`` gives the path, mime and
status; ``sweep()`` first if the request should notice an expiry.
"""

from __future__ import annotations

import math
import os
import re
import secrets
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config, files, store

# The kinds of audio Haven accepts, by file suffix (the server only lets these through).
MIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".flac": "audio/flac",
        ".ogg": "audio/ogg", ".opus": "audio/ogg", ".webm": "audio/webm"}
LIVE = ("processing", "available", "failed")  # the file is meant to be there
REASONS = ("confirm", "expiry", "manual", "replaced")
RETRY_AFTER_MINUTES = (1, 5, 30)  # after the 1st, 2nd, 3rd failed delete; later: every sweep
SWEEP_SECONDS = 10 * 60
REF = re.compile(r"^[A-Za-z0-9_-]{16,32}\.[a-z0-9]{2,5}$")
COLUMNS = ("audio_id", "session_id", "storage_ref", "mime", "bytes", "status", "created_at", "expires_at",
           "cleanup_status", "cleanup_attempts", "last_error", "deleted_at", "delete_reason", "last_attempt_at")
_SELECT = f"SELECT {', '.join(COLUMNS)} FROM audio_assets"

_LOCK = threading.RLock()
_TIMER: threading.Timer | None = None


class Busy(Exception):
    """The recording is being transcribed and cannot be deleted by hand yet."""


def now() -> datetime:
    return datetime.now(timezone.utc).astimezone()


def _iso(t: datetime) -> str:
    return t.isoformat(timespec="seconds")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _unlink(path: Path) -> None:
    """The one place a file is removed (tests make it fail to see the retry)."""
    path.unlink()


def _path(row: dict) -> Path | None:
    ref = row["storage_ref"]
    return files.audio_dir() / ref if REF.match(ref) else None  # never a path from outside


def _row(r) -> dict:
    return dict(zip(COLUMNS, r))


def _latest(c, session_id: str) -> dict | None:
    r = c.execute(f"{_SELECT} WHERE session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1", (session_id,)).fetchone()
    return _row(r) if r else None


# ---------- storing ----------

def store_file(session_id: str, src: str | Path, *, clock: datetime | None = None) -> dict:
    """Moves a finished recording out of data/tmp (which start-up clears) into
    data/audio and records it as ``processing``. A rename on the same file system.
    An older recording of the same session that is still there is removed first."""
    t = clock or now()
    suffix = Path(src).suffix.lower()
    suffix = suffix if suffix in MIME else ".bin"
    audio_id = secrets.token_urlsafe(16)  # 128 bits
    ref = f"{audio_id}{suffix}"
    dest = files.audio_dir() / ref
    size = os.stat(src).st_size
    os.replace(src, dest)  # same file system: a rename
    os.chmod(dest, files.PRIVATE_FILE)
    expires = t + timedelta(hours=config.AUDIO_TTL_HOURS)
    try:
        with _LOCK:
            with store.connect() as c:
                old = [_row(r) for r in c.execute(f"{_SELECT} WHERE session_id = ? AND status != 'deleted'", (session_id,))]
            for o in old:
                _remove(o, "replaced", t)
            with store.connect() as c:
                c.execute("INSERT INTO audio_assets (audio_id, session_id, storage_ref, mime, bytes, status, created_at, expires_at,"
                          " cleanup_status, cleanup_attempts) VALUES (?, ?, ?, ?, ?, 'processing', ?, ?, 'none', 0)",
                          (audio_id, session_id, ref, MIME.get(suffix, "application/octet-stream"), size, _iso(t), _iso(expires)))
                store._audit(c, "audio_stored", f"{audio_id} {session_id} bytes={size}")
    except BaseException:
        try:
            dest.unlink()
        except OSError:
            pass
        raise
    return get(session_id)


def _set_status(session_id: str, old: tuple[str, ...], new: str) -> None:
    with _LOCK, store.connect() as c:
        row = _latest(c, session_id)
        if row and row["status"] in old:
            c.execute("UPDATE audio_assets SET status = ? WHERE audio_id = ?", (new, row["audio_id"]))


def mark_processing(session_id: str) -> None:
    """A retry of the transcription is starting."""
    _set_status(session_id, ("failed",), "processing")


def mark_available(session_id: str) -> None:
    """The transcription worked: from now the therapist can check it against the audio."""
    _set_status(session_id, ("processing", "failed"), "available")


def mark_failed(session_id: str) -> None:
    """The transcription failed: the file stays for a retry, until it expires."""
    _set_status(session_id, ("processing",), "failed")


# ---------- reading ----------

def get(session_id: str) -> dict | None:
    """The newest recording row of a session (any status) or None."""
    with store.connect() as c:
        return _latest(c, session_id)


def for_session(session_id: str, *, clock: datetime | None = None) -> dict | None:
    """What playback (ticket 09) needs for a session, or None when it never had audio.

    Returns the asset row plus ``path`` (a Path, only when the file is there to be
    read: status available and not past ``expires_at``; otherwise None), ``playable``
    (bool) and ``audio_status``. Check ``playable`` before opening ``path``; when it is
    False, ``status`` says why (deleted, failed, processing, cleanup_failed...)."""
    row = get(session_id)
    if row is None:
        return None
    path = _path(row)
    playable = (row["status"] == "available" and path is not None and path.is_file()
                and _parse(row["expires_at"]) > (clock or now()))
    return dict(row, path=path if playable else None, playable=playable, audio_status=row["status"])


def asr_path(session_id: str) -> Path | None:
    """The file a transcription (or its retry) reads, or None when it is no longer kept."""
    row = get(session_id)
    if row is None or row["status"] not in LIVE:
        return None
    path = _path(row)
    return path if path is not None and path.is_file() else None


def view(session_id: str, *, clock: datetime | None = None) -> dict:
    """What the session page shows. Reads only."""
    row = get(session_id)
    if row is None:
        return {"audio_status": "none", "audio_expires_at": None, "audio_deleted_at": None,
                "audio_delete_reason": None, "audio_hours_left": None}
    left = None
    if row["status"] in LIVE:
        left = max(0, math.ceil((_parse(row["expires_at"]) - (clock or now())).total_seconds() / 3600))
    return {"audio_status": row["status"], "audio_expires_at": row["expires_at"], "audio_deleted_at": row["deleted_at"],
            "audio_delete_reason": row["delete_reason"], "audio_hours_left": left}


def statuses(session_ids: list[str]) -> dict[str, str]:
    """session id -> status of its newest recording, for the client page."""
    if not session_ids:
        return {}
    marks = ", ".join("?" for _ in session_ids)
    with store.connect() as c:
        rows = c.execute(f"SELECT session_id, status FROM audio_assets WHERE session_id IN ({marks}) ORDER BY created_at, rowid",
                         session_ids).fetchall()
    return dict(rows)  # later rows win


# ---------- deleting ----------

def _remove(row: dict, reason: str, t: datetime) -> bool:
    """Unlinks the file and records the result. True when the file is gone."""
    path = _path(row)
    err = None
    try:
        if path is not None:
            _unlink(path)
    except FileNotFoundError:
        pass
    except OSError as e:
        err = type(e).__name__
    with store.connect() as c:
        if err is None:
            c.execute("UPDATE audio_assets SET status = 'deleted', cleanup_status = 'done', last_error = NULL, deleted_at = ?,"
                      " delete_reason = ?, last_attempt_at = ? WHERE audio_id = ?", (_iso(t), reason, _iso(t), row["audio_id"]))
            store._audit(c, "audio_deleted", f"{row['audio_id']} {row['session_id']} reason={reason}")
        else:
            c.execute("UPDATE audio_assets SET status = 'cleanup_failed', cleanup_status = 'failed',"
                      " cleanup_attempts = cleanup_attempts + 1, last_error = ?, delete_reason = ?, last_attempt_at = ?"
                      " WHERE audio_id = ?", (err, reason, _iso(t), row["audio_id"]))
            store._audit(c, "audio_cleanup_failed", f"{row['audio_id']} {row['session_id']} reason={reason} error={err}")
    return err is None


def delete_for_session(session_id: str, reason: str, *, clock: datetime | None = None) -> dict | None:
    """Deletes a session's recording now (reason: confirm | manual). Returns the
    new row, or None when it never had one. Already deleted: nothing changes.
    A delete that fails on disk leaves status ``cleanup_failed``; the sweep retries.
    Raises ``Busy`` for a manual delete while the transcription is still running."""
    assert reason in REASONS
    t = clock or now()
    with _LOCK:
        with store.connect() as c:
            row = _latest(c, session_id)
        if row is None or row["status"] == "deleted":
            return row
        if row["status"] == "processing" and reason == "manual":
            raise Busy(session_id)
        _remove(row, reason, t)
        return get(session_id)


def sweep(clock: datetime | None = None) -> dict:
    """Removes recordings past ``expires_at`` and retries deletes that failed.
    ``clock`` is the time to judge by (tests pass their own)."""
    t = clock or now()
    done = {"expired": 0, "retried": 0, "failed": 0}
    with _LOCK:
        with store.connect() as c:
            rows = [_row(r) for r in c.execute(f"{_SELECT} WHERE status IN ('processing', 'available', 'failed', 'cleanup_failed')")]
        for row in rows:
            if row["status"] == "cleanup_failed":
                n = row["cleanup_attempts"]
                wait = timedelta(minutes=RETRY_AFTER_MINUTES[n - 1]) if 0 < n <= len(RETRY_AFTER_MINUTES) else timedelta(0)
                if row["last_attempt_at"] and _parse(row["last_attempt_at"]) + wait > t:
                    continue
                ok = _remove(row, row["delete_reason"] or "expiry", t)
                done["retried" if ok else "failed"] += 1
            elif _parse(row["expires_at"]) <= t:
                store.audit("audio_expired", f"{row['audio_id']} {row['session_id']}")
                if _remove(row, "expiry", t):
                    done["expired"] += 1
                else:
                    done["failed"] += 1
    return done


def clean_orphans() -> int:
    """At start-up: files in data/audio that no row names (a crash between the rename
    and the insert, or the job-* files of the first background-jobs version)."""
    with store.connect() as c:
        known = {r[0] for r in c.execute("SELECT storage_ref FROM audio_assets WHERE status != 'deleted'")}
    removed = 0
    for f in files.audio_dir().iterdir():
        # Dotfiles are not recordings (data/audio/.gitkeep is tracked by git).
        if f.is_file() and not f.name.startswith(".") and f.name not in known:
            try:
                f.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def start_sweeper(interval: float = SWEEP_SECONDS) -> None:
    """Sweeps now, then every ``interval`` seconds on a daemon timer thread.
    Only ``serve()`` calls this; tests call ``sweep`` themselves."""
    global _TIMER
    stop_sweeper()

    def tick():
        global _TIMER
        try:
            sweep()
        except Exception as e:  # noqa: BLE001 - a failed sweep must not end the timer
            print(f"Haven: audio sweep failed ({type(e).__name__})")
        with _LOCK:
            _TIMER = threading.Timer(interval, tick)
            _TIMER.daemon = True
            _TIMER.start()

    tick()


def stop_sweeper() -> None:
    global _TIMER
    with _LOCK:
        if _TIMER is not None:
            _TIMER.cancel()
            _TIMER = None
