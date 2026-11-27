"""Background jobs: transcription and drafting run off the request thread.

An upload or a finished recording is answered at once with a job id (202); a
worker thread does the slow part and the page polls ``GET /api/jobs/<id>``.

    queued -> running -> succeeded
                     \\-> failed      --retry--> queued (attempt + 1)
    queued/running when Haven starts -> interrupted --retry--> queued

The state lives in the ``jobs`` table (store.py). A job writes there only when
it changes state, in short transactions; the model runs with no transaction
open. One session has at most one active (queued or running) job of a kind: a
partial unique index refuses the second, so two retries at once cannot both run.

Each status change is conditional on the attempt number and the old status, so
a worker left over from before a restart (or a retry) cannot overwrite the
state of a newer attempt.

Audio: a job's recording is moved out of data/tmp (which start-up clears) into
data/audio/ so a failed or interrupted job can be retried. It is removed as
soon as transcription succeeds. ``keep_audio``/``audio_of``/``drop_audio`` are
the only code that knows where it is (the full audio lifecycle replaces them).
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import traceback
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from . import config, store

ACTIVE = ("queued", "running")
RETRYABLE = ("failed", "interrupted")
JOB_ID = re.compile(r"^[A-Za-z0-9_-]{8,40}$")
INTERRUPTED_ZH = "處理途中 Haven 被關閉或重新啟動，這次處理沒有完成。請按「重試」。"
INTERNAL_ZH = "處理時發生錯誤，沒有完成。請按「重試」；一直失敗時，請看主機終端機的訊息。"
PUBLIC = ("job_id", "session_id", "kind", "status", "attempt", "error_code", "message_zh", "stage", "created_at", "updated_at")


class Busy(Exception):
    """The session already has an active job of this kind."""


class NotRetryable(Exception):
    """Only a failed or interrupted job can be retried."""


class AudioGone(Exception):
    """The job needs its recording again and the recording is no longer kept."""


# kind -> (run(job, audio_path_or_None), needs_audio(job) -> bool)
_HANDLERS: dict[str, tuple[Callable, Callable]] = {}
_POOL: ThreadPoolExecutor | None = None
_POOL_LOCK = threading.Lock()
_FUTURES: dict[str, Future] = {}


def register(kind: str, run: Callable, needs_audio: Callable | None = None) -> None:
    """``run(job, audio)`` does the work; it raises to fail the job. An exception
    with a ``zh`` attribute (and optionally ``code``) gives the therapist's message.
    ``needs_audio(job)`` says whether a retry needs the recording (409 audio_gone)."""
    _HANDLERS[kind] = (run, needs_audio or (lambda job: False))


def _pool() -> ThreadPoolExecutor:
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=config.JOB_WORKERS, thread_name_prefix="haven-job")
        return _POOL


# ---------- the job's recording (minimal; replaced by the audio lifecycle) ----------

def _audio_dir() -> Path:
    path = config.DATA_DIR / "audio"
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def keep_audio(job_id: str, src: str | Path) -> Path:
    """Moves the recording out of data/tmp (cleared at start-up) so the job can be retried."""
    dest = _audio_dir() / f"job-{job_id}{Path(src).suffix.lower()}"
    os.replace(src, dest)  # same file system: a rename
    return dest


def audio_of(job_id: str) -> Path | None:
    return next(iter(sorted((config.DATA_DIR / "audio").glob(f"job-{job_id}.*"))), None) \
        if (config.DATA_DIR / "audio").is_dir() else None


def drop_audio(job_id: str) -> None:
    path = audio_of(job_id)
    if path is not None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


# ---------- table access ----------

def _row(r) -> dict:
    keys = ("job_id", "session_id", "kind", "status", "attempt", "error_code", "message_zh", "stage", "params", "created_at", "updated_at")
    job = dict(zip(keys, r))
    job["params"] = json.loads(job["params"] or "{}")
    return job


_SELECT = "SELECT job_id, session_id, kind, status, attempt, error_code, message_zh, stage, params, created_at, updated_at FROM jobs"


def get(job_id: str) -> dict | None:
    if not isinstance(job_id, str) or not JOB_ID.match(job_id):
        return None
    with store.connect() as c:
        r = c.execute(f"{_SELECT} WHERE job_id = ?", (job_id,)).fetchone()
    return _row(r) if r else None


def public(job: dict) -> dict:
    """What the API shows: no parameters."""
    return {k: job.get(k) for k in PUBLIC}


def active(session_id: str, kind: str) -> bool:
    with store.connect() as c:
        return c.execute("SELECT 1 FROM jobs WHERE session_id = ? AND kind = ? AND status IN ('queued', 'running')",
                         (session_id, kind)).fetchone() is not None


def for_client(client_id: str) -> list[dict]:
    """The newest job of each of a client's sessions (ids start with the client id)."""
    with store.connect() as c:
        rows = c.execute(f"{_SELECT} WHERE session_id LIKE ? ORDER BY created_at, rowid", (f"{client_id}-%",)).fetchall()
    latest: dict[str, dict] = {}
    for r in rows:
        job = _row(r)
        latest[job["session_id"]] = job
    return [public(j) for j in latest.values()]


def _set(job_id: str, attempt: int, old: tuple[str, ...], new: str, **fields) -> bool:
    """One state change, only if the job is still at ``attempt`` in one of ``old``."""
    cols = {"status": new, "updated_at": store.now(), **fields}
    sets = ", ".join(f"{k} = ?" for k in cols)
    marks = ", ".join("?" for _ in old)
    with store.connect() as c:
        cur = c.execute(f"UPDATE jobs SET {sets} WHERE job_id = ? AND attempt = ? AND status IN ({marks})",
                        (*cols.values(), job_id, attempt, *old))
        return cur.rowcount == 1


def stage(job: dict, name: str) -> None:
    """What a running job is doing now, for the processing screen."""
    with store.connect() as c:
        c.execute("UPDATE jobs SET stage = ?, updated_at = ? WHERE job_id = ? AND attempt = ? AND status = 'running'",
                  (name, store.now(), job["job_id"], job["attempt"]))


# ---------- running ----------

def create(session_id: str, kind: str, params: dict, audio: str | Path | None = None) -> dict:
    """Queues a job. Raises Busy when the session already has an active job of
    this kind. With ``audio``, the file is moved where a retry can find it."""
    if kind not in _HANDLERS:
        raise ValueError(f"unknown job kind {kind}")
    job_id, now = secrets.token_urlsafe(12), store.now()
    try:
        with store.connect() as c:
            c.execute("INSERT INTO jobs (job_id, session_id, kind, status, attempt, params, created_at, updated_at)"
                      " VALUES (?, ?, ?, 'queued', 1, ?, ?, ?)",
                      (job_id, session_id, kind, json.dumps(params, ensure_ascii=False), now, now))
            store._audit(c, "job_queued", f"{job_id} {session_id} {kind}")
    except sqlite3.IntegrityError:
        raise Busy(session_id) from None
    if audio is not None:
        try:
            keep_audio(job_id, audio)
        except OSError:
            _set(job_id, 1, ACTIVE, "failed", error_code="internal", message_zh=INTERNAL_ZH)
            raise
    job = get(job_id)  # read before the worker starts, so the answer shows "queued"
    _submit(job_id, 1)
    return job


def retry(job_id: str) -> dict:
    """A new attempt of a failed or interrupted job (same id, attempt + 1)."""
    job = get(job_id)
    if job is None or job["status"] not in RETRYABLE:
        raise NotRetryable(job_id)
    _, needs_audio = _HANDLERS[job["kind"]]
    if needs_audio(job) and audio_of(job_id) is None:
        raise AudioGone(job_id)
    attempt = job["attempt"] + 1
    try:
        with store.connect() as c:
            cur = c.execute("UPDATE jobs SET status = 'queued', attempt = ?, error_code = NULL, message_zh = NULL, stage = NULL,"
                            " updated_at = ? WHERE job_id = ? AND attempt = ? AND status IN ('failed', 'interrupted')",
                            (attempt, store.now(), job_id, job["attempt"]))
            if cur.rowcount != 1:  # another retry got there first
                raise NotRetryable(job_id)
            store._audit(c, "job_retry", f"{job_id} {job['session_id']} {job['kind']} attempt={attempt}")
    except sqlite3.IntegrityError:
        raise Busy(job["session_id"]) from None
    job = get(job_id)
    _submit(job_id, attempt)
    return job


def _submit(job_id: str, attempt: int) -> None:
    # The worker runs as the account that started the job (audit rows name it).
    ctx = contextvars.copy_context()
    _FUTURES[job_id] = _pool().submit(ctx.run, _run, job_id, attempt)


def _run(job_id: str, attempt: int) -> None:
    if not _set(job_id, attempt, ("queued",), "running"):
        return  # interrupted or superseded before it started
    job = get(job_id)
    run, _ = _HANDLERS[job["kind"]]
    try:
        run(job, audio_of(job_id))
    except Exception as e:  # noqa: BLE001 - every failure becomes the job's state
        zh = getattr(e, "zh", None)
        code = getattr(e, "code", None) or ("failed" if zh else "internal")
        if _set(job_id, attempt, ("running",), "failed", error_code=code, message_zh=zh or INTERNAL_ZH):
            store.audit("job_failed", f"{job_id} {job['session_id']} {job['kind']} attempt={attempt} code={code}")
            if not zh:  # where it happened, for the terminal; the message is left out (it can quote content)
                frames = "".join(traceback.format_tb(e.__traceback__)[-4:])
                print(f"Haven: job {job['kind']} failed with {type(e).__name__}\n{frames}", file=sys.stderr, end="")
        return
    if _set(job_id, attempt, ("running",), "succeeded", stage=None):
        drop_audio(job_id)
        store.audit("job_succeeded", f"{job_id} {job['session_id']} {job['kind']} attempt={attempt}")


def wait(job_id: str, timeout: float = 60) -> dict | None:
    """Blocks until the job's current attempt has finished (tests use this as the
    synchronous mode), then returns the job as the API shows it."""
    future = _FUTURES.get(job_id)
    if future is not None:
        future.result(timeout)
    job = get(job_id)
    return public(job) if job else None


def startup() -> int:
    """At start-up nothing is running yet: a job left queued or running by the
    previous process is marked interrupted, to be retried by the therapist."""
    global _POOL
    with _POOL_LOCK:
        old, _POOL = _POOL, None
    if old is not None:
        old.shutdown(wait=False)
    with store.connect() as c:
        rows = c.execute("SELECT job_id, session_id, kind FROM jobs WHERE status IN ('queued', 'running')").fetchall()
        c.execute("UPDATE jobs SET status = 'interrupted', error_code = 'interrupted', message_zh = ?, updated_at = ?"
                  " WHERE status IN ('queued', 'running')", (INTERRUPTED_ZH, store.now()))
        for job_id, session_id, kind in rows:
            store._audit(c, "job_interrupted", f"{job_id} {session_id} {kind}")
    return len(rows)
