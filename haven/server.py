"""HTTP server for the therapist UI. Standard library only; binds to localhost by default."""

from __future__ import annotations

import ipaddress
import json
import mimetypes
import os
import re
import secrets
import ssl
import sys
import tempfile
import threading
import time
import traceback
from datetime import date
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import accounts
from . import audio as audio_store
from . import config, crypto, export, files, human_fields, jobs, llm, recap, regenerate, signing, store, templates, transcript_edit
from .agents.verifier import VERIFIER_VERSION, Verifier
from .models import to_dict
from .orchestrator import Orchestrator, get_client, load_clients
from .transcript import ASRError, load_transcript, save_transcript, transcribe_audio

ORCH: Orchestrator | None = None
MAX_UPLOAD = 200 * 1024 * 1024  # a session recording
MAX_JSON = 2 * 1024 * 1024  # a note, a signature, an addendum
MAX_CHUNK = 20 * 1024 * 1024  # one piece of a recording made in the browser (a few seconds)
# A recording in the browser that sends nothing for this long is abandoned and its audio removed.
RECORDING_IDLE = 30 * 60
LOOPBACK = ("127.0.0.1", "localhost", "::1")
# A connection that sends nothing for this long is closed, so half-open
# connections cannot hold threads for ever (slow-loris).
CONNECTION_TIMEOUT = 30
# What the consent box covers, written to the audit log with each upload.
CONSENT_SCOPE = "v1:recording+local-ai-transcription+speaker-guess+transcript-kept"
AUDIO_SUFFIXES = {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".opus", ".webm"}
SESSION_ID = re.compile(r"^[A-Za-z0-9-]{1,40}$")
CLIENT_ID = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
CLIENTS_LOCK = threading.Lock()
# Uploads being transcribed: session id -> (client id, session number). Guarded by
# CLIENTS_LOCK. Transcription takes minutes; without the reservation two uploads
# with the same id both pass the "exists" check and the second replaces the first.
UPLOADING: dict[str, tuple[str, int]] = {}
# Recordings being made in a browser: recording id -> what it is for and where its
# audio is collecting. Guarded by CLIENTS_LOCK. Held in memory only.
RECORDINGS: dict[str, dict] = {}
_LOCKS_GUARD = threading.Lock()
_SESSION_LOCKS: dict[str, threading.Lock] = {}
# Sent by the UI on every API request, GET included. A cross-site page cannot
# add a custom header without a CORS preflight, which this server never
# approves, so another website open in the therapist's browser cannot sign,
# upload, start the model or write to the audit log (not even with an <img>).
CSRF_HEADER = "X-Haven"
# On every response. The UI has no inline script or style and loads nothing
# from another origin, so the policy can be strict; the page cannot be framed.
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _allowed_hosts() -> set[str]:
    names = {config.HOST, "localhost", "127.0.0.1", "[::1]", *config.HOSTNAMES}
    return {f"{n}:{config.PORT}" for n in names} | names


class BadRequest(ValueError):
    pass


class HttpError(Exception):
    """A request answered with a given status and a message for the therapist."""

    def __init__(self, status: HTTPStatus, message: str, zh: str | None = None, code: str | None = None):
        super().__init__(message)
        self.status, self.zh, self.code = status, zh, code  # code: a background job's error_code


def _no_session() -> HttpError:
    return HttpError(HTTPStatus.NOT_FOUND, "no such session", "找不到這次會談。")


def _client(client_id: str) -> dict:
    try:
        return get_client(client_id)
    except KeyError:
        raise HttpError(HTTPStatus.NOT_FOUND, "no such client", "找不到這位個案。") from None


def _user() -> dict | None:
    return accounts.CURRENT.get()


def _my_client(client_id: str) -> dict:
    """The client, if the signed-in therapist may open it. Someone else's client is
    answered as "no such client", so ids cannot be probed."""
    client = _client(client_id)
    if not accounts.may_open(_user(), client):
        raise HttpError(HTTPStatus.NOT_FOUND, "no such client", "找不到這位個案。")
    return client


def _my_session(sid: str) -> None:
    """404 unless the session exists and belongs to a client the signed-in therapist may open."""
    if not accounts.required():
        return
    t = _transcript(sid)
    try:
        client = get_client(t.client_id)
    except KeyError:
        raise _no_session() from None
    if not accounts.may_open(_user(), client):
        raise _no_session()


def _admin_only() -> None:
    user = _user()
    if accounts.required() and (user is None or user["role"] != "admin"):
        raise HttpError(HTTPStatus.FORBIDDEN, "the audit log is for an admin account", "稽核紀錄只有管理者帳號可以查看。")


def _check_revision(result: dict, payload: dict | None) -> None:
    """Two screens on one draft (a therapist and a supervisor, or two tabs): a
    change made from a page that has not seen the latest save is refused, not
    written over it. Requests without ``base_revision`` (scripts) are not checked."""
    base = (payload or {}).get("base_revision")
    if base is None:
        return
    if not isinstance(base, int) or isinstance(base, bool) or base != result.get("revision", 0):
        who = result.get("edited_by") or "另一個畫面"
        when = (result.get("edited_at") or "")[11:16]
        raise Refused("the draft was changed from another page after this one loaded",
                      f"這份草稿在你打開之後，已由{who}{'於 ' + when if when else ''}修改並存檔。為了不覆蓋對方的修改，這次沒有存檔。"
                      "請重新載入這次會談再修改。", conflict=True, revision=result.get("revision", 0))


def _need_base(payload: dict) -> None:
    """Transcript corrections and reviews must say which draft they were made on."""
    if payload.get("base_revision") is None:
        raise BadRequest("base_revision is required")


def _editor() -> str:
    user = _user()
    return user["display_name"] if user else ""


def _bump(result: dict) -> None:
    result["revision"] = result.get("revision", 0) + 1
    user = _user()
    result["edited_by"] = user["display_name"] if user else ""
    result["edited_at"] = store.now()


def _transcript(sid: str):
    """The stored transcript, or 404. Only "the file is not there" is a 404: a
    damaged file or a transcript naming an unknown client is a server fault (500)."""
    try:
        return load_transcript(sid)
    except FileNotFoundError:
        raise _no_session() from None


def _session_lock(sid: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(sid, threading.Lock())


def _log_fault(e: BaseException) -> None:
    """Where an unexpected error happened, for the terminal. The exception's
    message is left out: it can quote the request (a KeyError names the key)."""
    frames = "".join(traceback.format_tb(e.__traceback__)[-4:])
    print(f"Haven: unexpected {type(e).__name__}\n{frames}", file=sys.stderr, end="")


def _session_id(value: str) -> str:
    """Session ids are upper case everywhere. On a case-insensitive file system
    (macOS) "c001-s04" opens the same file as "C001-S04", and without this the
    one session could be signed twice under two ids."""
    if not SESSION_ID.match(value):
        raise BadRequest("session_id must be letters, digits or '-'")
    return value.upper()


class Refused(Exception):
    """A request the server understands and will not carry out (409)."""

    def __init__(self, message: str, zh: str, **extra):
        super().__init__(message)
        self.zh, self.extra = zh, extra


def _draft(sid: str) -> tuple[dict, object, list[str]]:
    """The stored draft, the transcript and the server's score lines for a session
    that can still be changed."""
    transcript = _transcript(sid)
    if store.get_signature(sid):
        raise Refused("this session is already signed; signed notes are not overwritten",
                      "這次會談已經簽核，簽核後的紀錄不能再修改。需要更正時，請新增「補充說明」。")
    result = store.get_result(sid)
    if result is None:
        raise Refused("no draft has been produced for this session yet", "這次會談還沒有產生紀錄草稿，請先開啟會談。")
    client = get_client(transcript.client_id)
    transcript.client_age = client.get("age")
    measures = ORCH.measure_sentences(client, transcript.session_no)
    return result, transcript, measures


def _stale(result: dict) -> bool:
    return result.get("verification", {}).get("verifier_version") != VERIFIER_VERSION


def _open_session(sid: str) -> dict:
    """Drafts the note when the session has none, re-checks a draft made under
    older verifier rules, and returns the session page. This writes, so it is
    reached by POST only."""
    if not (config.SESSIONS_DIR / f"{sid}.json").is_file():
        raise _no_session()
    signed = bool(store.get_signature(sid))
    result = store.get_result(sid)
    if result is None or (not signed and _stale(result)):
        # One pipeline run per session: of several requests opening a session for
        # the first time, one drafts the note and the others wait and read it.
        with _session_lock(sid):
            result = store.get_result(sid)
            if result is None:
                ORCH.process_session(sid)
            elif not signed and _stale(result):
                ORCH.refresh(result)  # a draft checked under older rules
    return _session_view(sid)


def _session_view(sid: str) -> dict:
    """What the session page shows, read from the store and nothing else. Once
    signed, that is the signed note, not the draft."""
    if not (config.SESSIONS_DIR / f"{sid}.json").is_file():
        raise _no_session()
    result = store.get_result(sid)
    if result is None:
        raise Refused("no draft has been produced for this session yet; POST /api/sessions/<id>/open drafts it",
                      "這次會談還沒有產生紀錄草稿。", needs_processing=True)
    signature = store.get_signature(sid)
    result.setdefault("revision", 0)  # what a page sends back as base_revision when it saves
    store.audit("read_session", f"{sid}{' (signed)' if signature else ''}")
    result["signature"] = signature
    result["signed"] = bool(signature)
    try:  # for the page heading and the signing dialog; the record itself does not depend on it
        client = get_client(result["client_id"])
        result["client_alias"], result["therapist"] = client.get("alias", ""), client.get("therapist", "")
    except KeyError:
        result["client_alias"], result["therapist"] = "", ""
    result["addenda"] = store.get_addenda(sid) if signature else []
    result["exports"] = export.for_session(sid)
    result["export_status"] = export.status_of(result["exports"])
    result.update(audio_store.view(sid))  # audio_status, audio_expires_at, audio_deleted_at, audio_delete_reason, audio_hours_left
    result["input_mode"] = recap.mode_of(sid)
    if signature:
        result["note"] = store.get_signed_note(sid)
    # Field labels and hints for the page; None for a template no longer registered.
    tpl = templates.find(result["note"].get("template"))
    result["template"] = tpl.public() if tpl else None
    if signature:
        note = result["note"]
        result["verification"] = signing.summarize(note)
        result["risk_dismissals"] = note.get("risk_dismissals", [])
        result["risk_uncovered"] = []
    else:
        result["needs_refresh"] = _stale(result)
        result.setdefault("risk_uncovered", signing.uncovered_risk(result.get("risk"), result["note"]))
    if not config.CLINICAL_AIDS:  # also for a draft made while the aids were on
        result["risk"], result["outcome"], result["risk_uncovered"] = None, None, []
    transcript_edit.annotate(sid, result, bool(signature))  # transcript_revision, review_status, source_changed
    return result


def _status() -> dict:
    """What this process is set up to talk to, looked up now. No figure here is a
    constant: the addresses are what the endpoint names resolve to at this moment,
    and the counter is the number of connections Haven's own check refused."""
    asr = None
    if config.ASR_BACKEND == "openai":
        asr = dict(llm.describe(config.ASR_URL), model=config.ASR_MODEL)
    return {
        "engine": ORCH.engine,
        "bind": f"{config.HOST}:{config.PORT}",
        "bind_loopback": config.HOST in LOOPBACK,
        "login_required": accounts.required(),
        "tls": bool(config.TLS_CERT),
        "llm": dict(llm.describe(ORCH.llm.url), model=ORCH.llm.model) if ORCH.llm else None,
        "asr_backend": config.ASR_BACKEND,
        "asr": asr,  # None: Whisper runs inside this process
        "clinical_aids": config.CLINICAL_AIDS,
        "refused_connections": len(llm.REFUSED),
        # Said plainly so nobody reads the page as a measurement of network traffic.
        "note": "addresses are resolved now; refused_connections counts refusals by Haven's endpoint check since start; this is not a packet capture, and it cannot see what the model server itself connects to",
    }


def _client_summary(c: dict) -> dict:
    outcome = ORCH.outcome_report(c)
    alerts = outcome["alerts"] if outcome else []
    return {
        "id": c["id"],
        "alias": c["alias"],
        "presenting_concern": c["presenting_concern"],
        "sessions": c.get("sessions", []),
        "alert_count": len(alerts),
    }


# Why an admin may open a client's signed records. Following clinical practice an
# admin keeps the records (心理師法 §15: kept by the practice) but does not read them;
# opening them is an exception with a stated reason, read only, logged, and shown to
# the client's therapist. Drafts and transcripts are the therapist's working material
# and are not shown.
ACCESS_REASONS = {
    "client_request": "個案本人申請閱覽或複製紀錄",
    "legal": "法院、檢察機關或主管機關依法調閱",
    "handover": "原心理師離職、長假或無法聯繫，需要交接",
    "safety": "個案或他人有立即安全危險，需要緊急了解",
    "quality": "督導或品質查核（已取得負責心理師同意）",
}
ACCESS_ROW = re.compile(r"^(?:\[(?P<by>[^\]]+)\] )?(?P<client>[A-Z][A-Z0-9]{1,15}) reason=(?P<reason>[a-z_]+); (?P<note>.*)$", re.S)


def record_access_log(client_id: str) -> list[dict]:
    """Every time an admin opened this client's records, newest first."""
    out = []
    for row in store.audit_rows("record_access"):
        m = ACCESS_ROW.match(row["detail"])
        if m and m["client"] == client_id:
            out.append({"ts": row["ts"], "by": m["by"] or "", "reason": ACCESS_REASONS.get(m["reason"], m["reason"]), "note": m["note"]})
    return out


def _signed_records(client: dict) -> dict:
    """A client's signed notes and addenda, for an admin with a grant. Nothing else."""
    sessions = []
    for sid in client.get("sessions", []):
        signature = store.get_signature(sid)
        if not signature:
            sessions.append({"session_id": sid, "signed": False})
            continue
        note = store.get_signed_note(sid) or {}
        sessions.append({"session_id": sid, "signed": True, "signed_by": signature["signed_by"],
                         "signed_at": signature["signed_at"], "template": note.get("template", ""),
                         "sections": {k: [s.get("text", "") for s in v] for k, v in note.get("sections", {}).items()},
                         "addenda": store.get_addenda(sid)})
    return {"client": {"id": client["id"], "alias": client.get("alias", ""), "therapist": client.get("therapist", "")},
            "sessions": sessions}


class Handler(BaseHTTPRequestHandler):
    server_version = "Haven/0.1"
    timeout = CONNECTION_TIMEOUT

    def log_message(self, fmt, *args):  # keep transcripts out of console logs
        pass

    def end_headers(self):
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        super().end_headers()

    def _json(self, payload, status=HTTPStatus.OK, cookie: str | None = None):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status, message, zh=None, **extra):
        # "error" is for logs and scripts; "message_zh" is what the UI shows the therapist.
        self._json({"error": message, **({"message_zh": zh} if zh else {}), **extra}, status)

    def _host_ok(self) -> bool:
        # Rejects DNS-rebinding: a hostile domain pointed at 127.0.0.1 still
        # sends its own name in the Host header.
        return self.headers.get("Host", "") in _allowed_hosts()

    def _address_ok(self) -> bool:
        """Only this machine and the clinic's private network: an address from the
        internet is refused even if a router forwards the port by mistake."""
        try:
            ip = ipaddress.ip_address(self.client_address[0].split("%")[0])
        except ValueError:
            return False
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        return ip.is_loopback or ip.is_private

    def _token(self) -> str | None:
        try:
            jar = SimpleCookie(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        morsel = jar.get(accounts.COOKIE)
        return morsel.value if morsel else None

    def _cookie(self, token: str, max_age: int | None) -> str:
        """``max_age`` None: a browser-session cookie, gone when the browser closes
        (the page also asks for the password each time it is opened)."""
        secure = "; Secure" if isinstance(self.connection, ssl.SSLSocket) else ""
        age = "" if max_age is None else f"; Max-Age={max_age}"
        return f"{accounts.COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict{age}{secure}"

    def _begin(self) -> bool:
        """Checks that every request passes before its route runs, and sets the
        signed-in account for this request. False when an answer was already sent."""
        if not self._address_ok():
            self._error(HTTPStatus.FORBIDDEN, "only this machine and the clinic's private network may connect")
            return False
        if not self._host_ok():
            self._error(HTTPStatus.FORBIDDEN, "unexpected Host header")
            return False
        self._ctx = accounts.CURRENT.set(accounts.session_user(self._token()))
        return True

    def _end(self) -> None:
        if getattr(self, "_ctx", None) is not None:
            accounts.CURRENT.reset(self._ctx)
            self._ctx = None

    def _signed_in_or_401(self, parts: list[str]) -> bool:
        """With accounts, every API request but signing in needs a signed-in account."""
        if parts[:2] == ["api", "me"] or parts == ["api", "login"] or parts == ["api", "logout"]:
            return True
        if accounts.required() and _user() is None:
            self._error(HTTPStatus.UNAUTHORIZED, "sign in first", "請先登入。閒置超過一段時間會自動登出。", login=True)
            return False
        return True

    def _me(self):
        user = _user()
        return self._json({"login_required": accounts.required(), "user": user, "idle_minutes": accounts.IDLE_SECONDS // 60,
                           "tls": isinstance(self.connection, ssl.SSLSocket), "clinical_aids": config.CLINICAL_AIDS})

    def _login(self):
        p = self._payload()
        username, password = p.get("username"), p.get("password")
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64 or len(password) > 256:
            raise BadRequest("username and password are required")
        if not accounts.required():
            raise Refused("there are no accounts on this machine", "這台電腦沒有設定帳號，不需要登入。")
        try:
            token, user = accounts.sign_in(username, password, self.client_address[0])
        except accounts.AccountError as e:
            name = username.strip().lower()
            store.audit("sign_in_failed", name if accounts.USERNAME.match(name) else "(invalid name)")
            return self._error(HTTPStatus.UNAUTHORIZED, "sign-in refused", str(e))
        accounts.CURRENT.set(user)
        store.audit("sign_in", f"from {self.client_address[0]}")
        return self._json({"user": user, "idle_minutes": accounts.IDLE_SECONDS // 60}, cookie=self._cookie(token, None))

    def _logout(self):
        if _user():
            store.audit("sign_out", "")
        accounts.sign_out(self._token())
        return self._json({"signed_out": True}, cookie=self._cookie("", 0))

    def _content_length(self, limit: int) -> int:
        raw = self.headers.get("Content-Length")
        if raw is None:
            raise HttpError(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
        # int() would accept "-1", "+5" and " 5 "; read(-1) reads until the connection closes.
        if not re.fullmatch(r"[0-9]{1,15}", raw.strip()):
            raise BadRequest("Content-Length must be a non-negative number")
        length = int(raw)
        if length > limit:
            raise HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, f"the request body is larger than {limit} bytes",
                            "送出的內容太大。")
        return length

    def _body(self) -> bytes:
        length = self._content_length(MAX_JSON)
        body = self.rfile.read(length)
        if len(body) < length:
            raise BadRequest("the request body ended early")
        return body

    def _guard(self, route) -> None:
        """Runs one request. Whatever goes wrong, the client gets a JSON answer:
        an unexpected error is a 500 with no detail, never a dropped connection."""
        try:
            return route()
        except templates.UnknownTemplate as e:
            self._error(HTTPStatus.BAD_REQUEST, str(e), "沒有這個紀錄模板，請重新選擇模板。")
        except (BadRequest, signing.BadNote, transcript_edit.BadEdit) as e:
            self._error(HTTPStatus.BAD_REQUEST, str(e), "送出的資料格式不正確。")
        except transcript_edit.EditRefused as e:
            self._error(HTTPStatus.CONFLICT, str(e), e.zh)
        except HttpError as e:
            self._error(e.status, str(e), e.zh)
        except Refused as e:
            self._error(HTTPStatus.CONFLICT, str(e), e.zh, **e.extra)
        except store.AlreadySignedError:
            self._error(HTTPStatus.CONFLICT, "this session is already signed; signed notes are not overwritten",
                        "這次會談已經簽核，簽核後的紀錄不能再修改。需要更正時，請新增「補充說明」。")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True  # the browser went away; nobody to answer
        except TimeoutError:
            self.close_connection = True
            self._error(HTTPStatus.REQUEST_TIMEOUT, "the request body did not arrive in time", "上傳逾時，請再試一次。")
        except Exception as e:  # noqa: BLE001 - the last line of defence
            _log_fault(e)
            self.close_connection = True
            try:
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal error", "伺服器發生錯誤，這個操作沒有完成。請看終端機的訊息。")
            except OSError:
                pass

    def _static(self, path: str):
        rel = "index.html" if path in ("", "/") else path.lstrip("/")
        file = (config.WEB_DIR / rel).resolve()
        if config.WEB_DIR.resolve() not in file.parents or not file.is_file():
            return self._error(HTTPStatus.NOT_FOUND, "not found")
        data = file.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", (mimetypes.guess_type(file.name)[0] or "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._begin():
            return
        try:
            self._guard(self._get)
        finally:
            self._end()

    def _get(self):
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        query = parse_qs(url.query)
        if parts[:1] != ["api"]:
            return self._static(url.path)
        # Every API request carries the header, reads included: a read of a clinical
        # record is written to the audit log, and a cross-site <img> must not be
        # able to do that.
        if self.headers.get(CSRF_HEADER) != "1":
            return self._error(HTTPStatus.FORBIDDEN, f"missing {CSRF_HEADER} header")
        if parts == ["api", "me"]:
            return self._me()
        if not self._signed_in_or_401(parts):
            return
        # GET only reads. Drafting a note, re-checking one and asking the model are POSTs.
        if parts == ["api", "status"]:
            return self._json(_status())
        if parts == ["api", "templates"]:
            return self._json([t.public() for t in templates.all_templates()])
        if parts == ["api", "clients"]:
            store.audit("list_clients", "")
            return self._json([_client_summary(c) for c in load_clients() if accounts.may_open(_user(), c)])
        if len(parts) == 4 and parts[1] == "clients" and parts[3] == "brief":
            client = _my_client(parts[2])
            store.audit("read_brief", client["id"])
            # The therapist sees every time an admin opened this client's records.
            return self._json(dict(ORCH.brief(client["id"]), record_access=record_access_log(client["id"]),
                                   jobs=jobs.for_client(client["id"]),
                                   audio=audio_store.statuses(client.get("sessions", []))))
        if len(parts) == 4 and parts[1] == "sessions" and parts[3] == "export":
            sid = _session_id(parts[2])
            _my_session(sid)
            return self._export(sid, query)
        if len(parts) == 3 and parts[1] == "sessions":
            sid = _session_id(parts[2])
            _my_session(sid)
            return self._json(_session_view(sid))
        if len(parts) == 3 and parts[1] == "jobs":
            return self._json(jobs.public(_my_job(parts[2])))
        if parts == ["api", "admin"]:
            self._admin()
            return self._json(_admin_overview())
        if len(parts) == 5 and parts[:3] == ["api", "admin", "clients"] and parts[4] == "records":
            admin = self._admin()
            client = _client(parts[3])
            if accounts.access_grant(admin, client["id"]) is None:
                raise HttpError(HTTPStatus.FORBIDDEN, "state a reason before opening a client's records",
                                "調閱紀錄前要先選擇事由；調閱有效 30 分鐘，逾時請重新申請。")
            store.audit("read_records", client["id"])
            return self._json(_signed_records(client))
        if parts == ["api", "audit"]:
            _admin_only()
            store.audit("read_audit", "")
            return self._json(store.recent_audit())
        if parts == ["api", "audit", "verify"]:
            _admin_only()
            return self._json(store.verify_audit())
        return self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def do_POST(self):
        if not self._begin():
            return
        try:
            if self.headers.get(CSRF_HEADER) != "1":
                return self._error(HTTPStatus.FORBIDDEN, f"missing {CSRF_HEADER} header")
            # An error may leave part of the body unread; it must not be parsed as the next request.
            self.close_connection = True
            self._guard(self._post)
        finally:
            self._end()

    def _post(self):
        url = urlparse(self.path)
        parts = [p for p in url.path.split("/") if p]
        query = parse_qs(url.query)
        if parts == ["api", "login"]:
            return self._login()
        if parts == ["api", "logout"]:
            return self._logout()
        if not self._signed_in_or_401(parts):
            return
        if parts[:2] == ["api", "admin"]:
            return self._admin_post(parts[2:])
        if parts == ["api", "recordings"]:
            return self._recording_start()
        if len(parts) == 4 and parts[1] == "recordings":
            if parts[3] == "chunk":
                return self._recording_chunk(parts[2], query)
            if parts[3] == "finish":
                return self._recording_finish(parts[2])
            if parts[3] == "cancel":
                return self._recording_cancel(parts[2])
        if len(parts) == 4 and parts[1] == "sessions":
            sid = _session_id(parts[2])
            _my_session(sid)
            if parts[3] == "open":
                return self._json(_open_session(sid))
            if parts[3] == "process":
                return self._process(sid, query)
            if parts[3] == "verify":
                return self._verify(sid)
            if parts[3] == "restore":
                return self._restore(sid)
            if parts[3] == "speakers":
                return self._speakers(sid)
            if parts[3] == "transcript":
                return self._transcript_edit(sid)
            if parts[3] == "reviewed":
                return self._reviewed(sid)
            if parts[3] == "sign":
                return self._sign(sid)
            if parts[3] == "addendum":
                return self._addendum(sid)
        if len(parts) == 6 and parts[1] == "sessions" and parts[3] == "exports" and parts[5] == "archived":
            sid = _session_id(parts[2])
            _my_session(sid)
            return self._archived(sid, parts[4])
        if len(parts) == 4 and parts[1] == "clients" and parts[3] == "ask" and config.CLINICAL_AIDS:
            return self._ask(parts[2])  # Case Memory; with the clinical aids off the endpoint does not exist
        if parts == ["api", "upload"]:
            return self._upload(query)
        if len(parts) == 5 and parts[1] == "sessions" and parts[3:] == ["audio", "delete"]:
            sid = _session_id(parts[2])
            _my_session(sid)
            return self._json(_delete_audio(sid))
        if len(parts) == 4 and parts[1] == "jobs" and parts[3] == "retry":
            return self._json(_retry_job(parts[2]), HTTPStatus.ACCEPTED)
        return self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def _payload(self) -> dict:
        try:
            payload = json.loads(self._body() or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            raise BadRequest("the request body is not JSON") from None
        if not isinstance(payload, dict):
            raise BadRequest("the request body must be a JSON object")
        return payload

    def _ask(self, client_id):
        """The question travels in the body, not the URL: it can name what a client said."""
        q = self._payload().get("q")
        if not isinstance(q, str) or not q.strip() or len(q) > 500:
            raise BadRequest("q is required")
        client = _my_client(client_id)  # an unknown client is a 404 and leaves no audit row
        store.audit("ask", client["id"])
        return self._json(ORCH.memory.ask(client, q.strip()))

    def _process(self, sid, query):
        default = recap.DEFAULT_TEMPLATE if recap.mode_of(sid) == recap.RECAP else templates.DEFAULT  # QUAL for a recap
        template = templates.get(query.get("template", [default])[0]).id  # unknown id: 400
        if not (config.SESSIONS_DIR / f"{sid}.json").is_file():
            raise _no_session()
        payload = self._payload() if self.headers.get("Content-Length") else {}
        params = {"template": template, "payload": {k: payload[k] for k in ("base_revision", "discard_edits") if k in payload}}
        with _session_lock(sid):
            regenerate.precheck(sid, params["payload"], **_REGENERATE_CHECKS)  # 409s now, not in the job
            try:
                job = jobs.create(sid, "regenerate", params)
            except jobs.Busy:
                raise Refused("this session is already being processed", "這次會談已經在處理中，請等它完成。") from None
        return self._json(jobs.public(job), HTTPStatus.ACCEPTED)

    def _verify(self, sid):
        """Re-checks the note as the therapist has edited it. Only the sentence
        texts, their citations and the confirmations are read from the request.
        The checked note is also saved as the working draft (N10: closing the page
        used to lose every edit); the AI's own draft is kept beside it."""
        payload = self._payload()
        dismissals = signing.parse_dismissals(payload.get("risk_dismissals") if isinstance(payload, dict) else None)
        with _session_lock(sid):
            result, transcript, measures = _draft(sid)
            _check_revision(result, payload)
            note, verification, _ = signing.check(sid, transcript, payload, result["note"]["template"], result["note"].get("engine", ""), measures, ORCH.verifier)
            signing.remember(sid, note, verification.get("llm_failed", False), by_model=ORCH.verifier.llm is not None)
            risk = result.get("risk") if config.CLINICAL_AIDS else None
            known = {f["segment_id"] for f in (risk or {}).get("flags", [])}
            uncovered = signing.uncovered_risk(risk, note)
            result.setdefault("ai_note", result["note"])
            result["note"] = to_dict(note)
            regenerate.stamp_origins(result["note"], result["ai_note"])  # never the browser's word
            result["verification"] = verification
            result["risk_uncovered"] = uncovered
            result["draft_dismissals"] = [d for d in dismissals if d["segment_id"] in known]
            result["draft_saved_at"] = store.now()
            transcript_edit.mark_sources(result)
            _bump(result)
            store.save_result(sid, result)
        store.audit("verify", f"{sid} {verification['supported']}/{verification['total']} supported; draft saved"
                    + ("; local model did not answer" if verification.get("llm_failed") else ""))
        return self._json({"note": result["note"], "verification": verification, "risk_uncovered": uncovered,
                           "draft_saved_at": result["draft_saved_at"], "revision": result["revision"]})

    def _speakers(self, sid):
        """The therapist sets who said each line of an uploaded recording, from
        listening to it, also for lines already confirmed (AC-06). Labels become
        "confirmed", and the risk scan, the checklist and every sentence of the
        draft are checked again under them. Logic: haven/transcript_edit.py."""
        payload = self._payload()
        _need_base(payload)
        with _session_lock(sid):
            result, transcript, _ = _draft(sid)
            if recap.is_recap(transcript):  # a dictation has one voice and nothing to confirm
                raise Refused(recap.SPEAKERS_REFUSED, recap.SPEAKERS_REFUSED_ZH)
            _check_revision(result, payload)
            count, changed = transcript_edit.edit_speakers(sid, payload.get("speakers"), _editor())
            _bump(result)
            transcript_edit.commit(sid, result, changed, ORCH.refresh)
            store.audit("speakers_confirmed", f"{sid}: {count} line(s) confirmed, {len(changed)} changed, "
                        f"transcript_revision={transcript_edit.revision(sid)}")
        return self._json(_session_view(sid))

    def _transcript_edit(self, sid):
        """Corrects the words of transcript lines; ids and times stay (AC-05)."""
        payload = self._payload()
        _need_base(payload)
        with _session_lock(sid):
            result, _, _ = _draft(sid)
            _check_revision(result, payload)
            changed = transcript_edit.edit_text(sid, payload.get("segments"), _editor())
            if changed:
                _bump(result)
                transcript_edit.commit(sid, result, changed, ORCH.refresh)
            store.audit("transcript_edited", f"{sid}: {len(changed)} line(s) corrected, "
                        f"transcript_revision={transcript_edit.revision(sid)}")
        return self._json(_session_view(sid))

    def _reviewed(self, sid):
        """The therapist has read the draft against the transcript as it is now."""
        payload = self._payload()
        _need_base(payload)
        with _session_lock(sid):
            result, _, _ = _draft(sid)
            _check_revision(result, payload)
            transcript_edit.mark_reviewed(sid, result)
            _bump(result)
            store.save_result(sid, result)
            store.audit("transcript_reviewed", f"{sid}: transcript_revision={transcript_edit.revision(sid)}")
        return self._json(_session_view(sid))

    def _restore(self, sid):
        """Throws the working draft away and goes back to the note the AI wrote."""
        payload = self._payload() if self.headers.get("Content-Length") else {}
        with _session_lock(sid):
            result, _, _ = _draft(sid)
            _check_revision(result, payload)
            if "ai_note" in result:
                result["note"] = result.pop("ai_note")
                for k in ("draft_dismissals", "draft_saved_at"):
                    result.pop(k, None)
                _bump(result)
                ORCH.refresh(result)  # verifies the AI draft again and saves it
                store.audit("restore_ai_draft", sid)
        return self._json(_session_view(sid))

    def _sign(self, sid):
        payload = self._payload()
        signed_by = payload.get("signed_by")
        user = _user()
        if user:  # with accounts the signer is the account, not a name typed into the dialog
            signed_by = user["display_name"]
        if not isinstance(signed_by, str) or not signed_by.strip() or len(signed_by) > 80 or "note" not in payload:
            return self._error(HTTPStatus.BAD_REQUEST, "signed_by and note are required", "請填寫簽核心理師姓名。")
        # One at a time with transcript corrections, so the hash recorded below is the
        # hash of the transcript the draft was reviewed against (spec §9.3 rule 6).
        with _session_lock(sid):
            return self._sign_locked(sid, payload, signed_by)

    def _sign_locked(self, sid, payload, signed_by):
        dismissals = signing.parse_dismissals(payload.get("risk_dismissals"))
        result, transcript, measures = _draft(sid)
        _need_base(payload)
        _check_revision(result, payload)
        t_rev = transcript_edit.revision(sid)
        if transcript_edit.review_status(result, t_rev, False) == "needs_review":
            raise Refused("the transcript was corrected after the draft was last reviewed; mark it reviewed first",
                          "逐字稿在您上次審閱之後有修改。請先對照逐字稿檢查紀錄，按「已審閱」後再簽核。",
                          needs_review=True, transcript_revision=t_rev, reviewed_against=transcript_edit.reviewed_against(result))
        # The rule lives here, not in the UI. Sentences the server has not judged
        # before are checked by the rule layers only: fast, and never more lenient
        # than the full check the therapist saw.
        note, verification, forged = signing.check(sid, transcript, payload["note"], result["note"]["template"], result["note"].get("engine", ""), measures, Verifier(None))
        if forged:
            raise Refused(f"{len(forged)} score line(s) do not match the questionnaire data",
                          f"有 {len(forged)} 句量表句和量表資料不一致。量表句由系統依量表資料產生，不能修改；請還原後再簽核。", blocking=forged)
        if verification["total"] == 0:
            raise Refused("the note has no sentences", "紀錄是空的，不能簽核。請至少寫下一句引用逐字稿的紀錄。")
        left = human_fields.placeholders(note)
        if left:  # decided from the text, so a flag from the browser cannot clear it
            raise Refused(f"{len(left)} placeholder sentence(s) are still in the note",
                          f"還有 {len(left)} 個欄位保留著「{human_fields.PLACEHOLDER}」。請改寫成您的內容，或刪除這一句，再簽核。",
                          blocking=[s.text for s in left])
        blocking = signing.blocking_sentences(note)
        if blocking:
            raise Refused(f"{len(blocking)} sentence(s) unverified, unsupported, or held for confirmation; re-verify, edit or confirm them first",
                          f"還有 {len(blocking)} 句沒有通過驗證（無支持證據、與風險有關而支持不足，或被本機模型否決）。請先修改、刪除，或逐句按「保留此句」"
                          "（沒有引用逐字稿的句子要寫下依據），再重新驗證。",
                          blocking=[s.text for s in blocking])
        risk = ORCH.scan_risk(transcript)  # None with the clinical aids off: no risk gate
        known = {f["segment_id"] for f in (risk or {}).get("flags", [])}
        dismissals = [d for d in dismissals if d["segment_id"] in known]
        uncovered = signing.uncovered_risk(risk, note, dismissals)
        if uncovered:
            where = "、".join(f"#{u['segment_id']}" for u in uncovered)
            raise Refused(f"{len(uncovered)} high-level risk cue(s) are not in the note and not left out with a reason",
                          f"逐字稿 {where} 的風險線索還沒有寫進紀錄。請補寫並引用該段，或在風險面板按「不納入紀錄」並填寫理由。",
                          risk_uncovered=uncovered)
        kept = sum(1 for s in note.sentences() if s.status != "supported" and s.confirmed_by_therapist)
        signed = signing.signed_form(note, dismissals)
        regenerate.stamp_origins(signed, result.get("ai_note") or result["note"])
        signed["transcript_revision"] = t_rev
        signature = store.sign_note(
            sid, signed, signed_by.strip(), kept,
            transcript_sha256=signing.transcript_sha256(sid), note_sha256=signing.note_sha256(signed), verifier_version=VERIFIER_VERSION,
            audit_rows=[("risk_cue_left_out", f"{sid} #{d['segment_id']}: {d['reason']}") for d in dismissals]
            + [("uncited_sentence_kept", f"{sid} 「{s.text[:40]}」: {s.keep_reason}") for s in note.sentences() if s.keep_reason],
        )
        if config.AUDIO_DELETE_ON_CONFIRM:  # the recording has done its job; a failure here is retried by the sweep
            try:
                audio_store.delete_for_session(sid, "confirm")
            except Exception as e:  # noqa: BLE001 - the note is signed either way
                print(f"Haven: could not remove the audio of {sid} ({type(e).__name__}); the sweep will retry", file=sys.stderr)
        return self._json(signature)

    def _export(self, sid, query):
        """E1: the confirmed note as a file. Only a signed note; the draft is never exported."""
        fmt = (query.get("format") or [""])[0]
        if fmt not in export.FORMATS:
            raise BadRequest("format must be md or txt")
        _transcript(sid)  # 404 for a session that does not exist
        user = _user()
        try:
            built = export.build(sid, fmt, user["display_name"] if user else "")
        except export.NotConfirmed:
            raise Refused("only a confirmed note can be exported", "這次會談的紀錄還沒有簽核確認，不能匯出。請先完成簽核。") from None
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", built.content_type)
        self.send_header("Content-Length", str(len(built.body)))
        self.send_header("Content-Disposition", f'attachment; filename="{built.filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Export-Id", built.export["export_id"])
        self.end_headers()
        self.wfile.write(built.body)

    def _archived(self, sid, export_id):
        """E2: the therapist marks an export as filed in the practice's own system."""
        payload = self._payload()
        user = _user()
        try:
            row = export.mark_archived(sid, export_id, payload.get("archive_ref"), user["display_name"] if user else "")
        except export.NoSuchExport:
            raise HttpError(HTTPStatus.NOT_FOUND, "no such export", "找不到這個匯出紀錄。") from None
        except export.BadRef:
            return self._error(HTTPStatus.BAD_REQUEST, "archive_ref is at most 100 characters", "歸檔編號最多 100 個字。")
        return self._json({"export": row})

    def _addendum(self, sid):
        payload = self._payload()
        text, author = payload.get("text"), payload.get("author")
        if _user():
            author = _user()["display_name"]
        if not isinstance(text, str) or not text.strip() or len(text) > 4000 or not isinstance(author, str) or not author.strip() or len(author) > 80:
            return self._error(HTTPStatus.BAD_REQUEST, "text and author are required", "請填寫補充說明的內容和姓名。")
        _transcript(sid)
        try:
            return self._json({"addenda": store.add_addendum(sid, text.strip(), author.strip())})
        except store.NotSignedError:
            raise Refused("an addendum can only be added to a signed note", "這次會談還沒有簽核，草稿可以直接修改，不需要補充說明。") from None

    def _upload(self, query):
        client_id = query.get("client", [""])[0]
        session_id = _session_id(query.get("session_id", [""])[0])  # upper case
        if query.get("consent", [""])[0] != "1":
            return self._error(HTTPStatus.BAD_REQUEST, "the client's written consent to recording is required",
                               "需要先取得個案對錄音的書面同意。")
        # Absent means a real client: the safer reading. A recording wrongly marked
        # as simulated would lose the care real data gets (and pass the pre-commit check).
        simulated = query.get("simulated", ["0"])[0] == "1"
        input_mode = _input_mode(query.get("input_mode", [None])[0])
        _my_client(client_id)
        if not session_id.startswith(f"{client_id.upper()}-"):
            raise HttpError(HTTPStatus.BAD_REQUEST, "the session id must start with the client id",
                            f"會談編號要以個案編號開頭，例如 {client_id}-S05。")
        suffix = Path(self.headers.get("X-Filename", "audio.wav")).suffix.lower()
        if suffix not in AUDIO_SUFFIXES:
            suffix = ".wav"
        length = self._content_length(MAX_UPLOAD)
        if length == 0:
            raise HttpError(HTTPStatus.BAD_REQUEST, "the upload is empty", "沒有收到錄音：檔案是空的。請重新選擇錄音檔。")
        session_no = _reserve_upload(session_id, client_id)  # 409 when the id is taken or being uploaded
        # In the data directory (0700), not the shared system temp directory. The
        # file is removed whatever happens below; what a crash or a power cut leaves
        # behind is removed at the next start (files.clear_temp).
        fd, tmp_name = tempfile.mkstemp(suffix=suffix, prefix="upload-", dir=files.temp_dir())
        try:
            with os.fdopen(fd, "wb") as tmp:
                # Streamed to the temp file in 1 MB pieces, so the upload is not held in memory
                # here. (The faster-whisper backend reads the file from disk; the "openai"
                # backend then loads it whole to send it on: see transcript._openai_server.)
                left = length
                while left > 0:
                    chunk = self.rfile.read(min(left, 1 << 20))
                    if not chunk:
                        break
                    tmp.write(chunk)
                    left -= len(chunk)
            if left:
                raise HttpError(HTTPStatus.BAD_REQUEST, "the upload ended before the whole file arrived",
                                "上傳中斷，錄音沒有完整送達。請再上傳一次。")
        except BaseException as e:
            _abandon_audio(tmp_name, session_id, e)
            raise
        return self._json(_start_transcription(tmp_name, session_id, client_id, session_no, simulated, "transcribe_upload", input_mode),
                          HTTPStatus.ACCEPTED)

    # ---------- accounts and client assignments (admin, on the main PC) ----------

    def _admin(self) -> dict:
        user = _user()
        if not accounts.required() or user is None or user["role"] != "admin":
            raise HttpError(HTTPStatus.FORBIDDEN, "an admin account is required",
                            "這裡只有管理者帳號可以使用。第一個管理者帳號請在主機上用 scripts/add_user.py 建立。")
        if not config.ADMIN_FROM_NETWORK and not self._from_this_machine():
            raise HttpError(HTTPStatus.FORBIDDEN, "accounts and assignments are managed on the main PC",
                            f"帳號與個案指派只能在主機上操作：請在主機上開 https://localhost:{config.PORT}。")
        return user

    def _from_this_machine(self) -> bool:
        try:
            ip = ipaddress.ip_address(self.client_address[0].split("%")[0])
        except ValueError:
            return False
        return (getattr(ip, "ipv4_mapped", None) or ip).is_loopback

    def _admin_post(self, rest: list[str]):
        admin = self._admin()
        p = self._payload()
        try:
            if rest == ["users"]:
                return self._json(_save_account(p))
            if len(rest) == 3 and rest[0] == "users" and rest[2] == "remove":
                if rest[1] == admin["username"]:
                    raise Refused("an admin cannot remove their own account", "不能刪除自己正在使用的帳號。")
                return self._json(_remove_account(rest[1]))
            if rest == ["clients"]:
                return self._json(_new_client(p))
            if len(rest) == 3 and rest[0] == "clients" and rest[2] == "assign":
                return self._json(_assign_client(rest[1], p.get("username")))
            if len(rest) == 3 and rest[0] == "clients" and rest[2] == "access":
                return self._json(_open_records(admin, rest[1], p))
            if len(rest) == 4 and rest[0] == "clients" and rest[2:] == ["access", "end"]:
                accounts.end_access(admin, _client(rest[1])["id"])
                store.audit("record_access_ended", rest[1])
                return self._json(_admin_overview())
        except accounts.AccountError as e:
            raise Refused(str(e), str(e)) from None
        raise HttpError(HTTPStatus.NOT_FOUND, "unknown endpoint")

    # ---------- recording in the browser ----------
    # The page records with the device's microphone and sends the audio here every few
    # seconds while the session goes on. Nothing is written on the therapist's device:
    # pieces not yet sent are held in the page's memory only. The pieces collect in one
    # temp file in the data directory, which is transcribed and removed at the end.

    def _recording(self, rid: str) -> dict:
        _sweep_recordings()
        with CLIENTS_LOCK:
            rec = RECORDINGS.get(rid)
        user = _user()
        if rec is None or rec["user"] != (user["username"] if user else None):
            raise HttpError(HTTPStatus.NOT_FOUND, "no such recording", "找不到這段錄音：可能已逾時被刪除，或已經完成。")
        return rec

    def _recording_start(self):
        _sweep_recordings()
        p = self._payload()
        client_id = p.get("client") if isinstance(p.get("client"), str) else ""
        session_id = _session_id(p.get("session_id") if isinstance(p.get("session_id"), str) else "")
        if p.get("consent") is not True:
            return self._error(HTTPStatus.BAD_REQUEST, "the client's written consent to recording is required",
                               "需要先取得個案對錄音的書面同意。")
        input_mode = _input_mode(p.get("input_mode"))
        _my_client(client_id)
        if not session_id.startswith(f"{client_id.upper()}-"):
            raise HttpError(HTTPStatus.BAD_REQUEST, "the session id must start with the client id",
                            f"會談編號要以個案編號開頭，例如 {client_id}-S05。")
        mime = p.get("mime") if isinstance(p.get("mime"), str) else ""
        suffix = ".ogg" if "ogg" in mime else ".m4a" if "mp4" in mime else ".webm"
        session_no = _reserve_upload(session_id, client_id)
        fd, tmp_name = tempfile.mkstemp(suffix=suffix, prefix="record-", dir=files.temp_dir())
        os.close(fd)
        rid = secrets.token_urlsafe(16)
        user = _user()
        with CLIENTS_LOCK:
            RECORDINGS[rid] = {"path": tmp_name, "session_id": session_id, "client_id": client_id, "session_no": session_no,
                               "simulated": p.get("simulated") is True, "input_mode": input_mode, "next": 0, "bytes": 0, "seen": time.time(),
                               "user": user["username"] if user else None, "lock": threading.Lock()}
        store.audit("recording_started", f"{session_id} consent={CONSENT_SCOPE}")
        return self._json({"recording_id": rid, "session_id": session_id})

    def _recording_chunk(self, rid: str, query: dict):
        rec = self._recording(rid)
        try:
            seq = int(query.get("seq", [""])[0])
        except ValueError:
            raise BadRequest("seq must be a number") from None
        length = self._content_length(MAX_CHUNK)
        data = self.rfile.read(length)
        if len(data) < length:
            raise BadRequest("the piece of audio ended early")
        with rec["lock"]:
            with CLIENTS_LOCK:
                alive = RECORDINGS.get(rid) is rec
            if not alive:  # finished, cancelled or swept while this piece was on its way
                raise HttpError(HTTPStatus.NOT_FOUND, "no such recording", "這段錄音已經結束，這一段沒有寫入。")
            rec["seen"] = time.time()
            if seq < rec["next"]:  # sent again after a dropped answer: already here
                return self._json({"received": rec["next"], "bytes": rec["bytes"]})
            if seq > rec["next"]:
                raise Refused(f"piece {rec['next']} has not arrived", "錄音有一段沒有送到主機，正在重送。", expected=rec["next"])
            if rec["bytes"] + len(data) > MAX_UPLOAD:
                raise HttpError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "the recording is too long", "錄音太長，已超過上限。請先結束這段錄音。")
            with open(rec["path"], "ab") as f:
                f.write(data)
            rec["next"] += 1
            rec["bytes"] += len(data)
            return self._json({"received": rec["next"], "bytes": rec["bytes"]})

    def _recording_finish(self, rid: str):
        rec = self._recording(rid)
        with rec["lock"], CLIENTS_LOCK:
            if RECORDINGS.pop(rid, None) is None:
                raise HttpError(HTTPStatus.NOT_FOUND, "no such recording", "找不到這段錄音：可能已經完成。")
        if rec["bytes"] == 0:
            _abandon_audio(rec["path"], rec["session_id"], HttpError(HTTPStatus.BAD_REQUEST, "empty"))
            raise HttpError(HTTPStatus.BAD_REQUEST, "nothing was recorded", "沒有收到任何錄音，沒有建立會談。")
        return self._json(_start_transcription(rec["path"], rec["session_id"], rec["client_id"], rec["session_no"],
                                               rec["simulated"], "transcribe_recording", rec["input_mode"]), HTTPStatus.ACCEPTED)

    def _recording_cancel(self, rid: str):
        rec = self._recording(rid)
        with rec["lock"], CLIENTS_LOCK:
            RECORDINGS.pop(rid, None)
        _remove(rec["path"])
        with CLIENTS_LOCK:
            UPLOADING.pop(rec["session_id"], None)
        store.audit("recording_cancelled", f"{rec['session_id']}; audio removed")
        return self._json({"cancelled": True})


def _write_clients(clients: list[dict]) -> None:
    files.write_private(config.CLIENTS_FILE, json.dumps(clients, ensure_ascii=False, indent=2) + "\n")


def _open_records(admin: dict, client_id: str, p: dict) -> dict:
    client = _client(client_id)
    reason = p.get("reason")
    if reason not in ACCESS_REASONS:
        raise BadRequest(f"reason must be one of {', '.join(ACCESS_REASONS)}")
    note = _text(p, "note", 200)
    accounts.grant_access(admin, client["id"], reason)
    store.audit("record_access", f"{client['id']} reason={reason}; {' '.join(note.split())}")
    return {"until_seconds": accounts.ACCESS_SECONDS, **_signed_records(client)}


def _admin_overview() -> dict:
    """Accounts and who each client is assigned to: ids, names and counts only,
    no clinical content (an admin reads records only through _open_records)."""
    admin = _user()
    clients = load_clients()
    users = [dict(accounts.public(u), clients=[c["id"] for c in clients if c.get("therapist_user") == u["username"]])
             for u in accounts.load_users()]
    return {
        "users": users,
        "clients": [{"id": c["id"], "alias": c.get("alias", ""), "therapist_user": c.get("therapist_user") or "",
                     "therapist": c.get("therapist", ""), "sessions": len(c.get("sessions", [])),
                     "access_open": accounts.access_grant(admin, c["id"]) is not None} for c in clients],
        "password_min": 10,
        "access_reasons": ACCESS_REASONS,
        "access_minutes": accounts.ACCESS_SECONDS // 60,
    }


def _text(p: dict, key: str, limit: int, required: bool = True) -> str:
    v = p.get(key)
    if v is None and not required:
        return ""
    if not isinstance(v, str) or (required and not v.strip()) or len(v) > limit:
        raise BadRequest(f"{key} is required (at most {limit} characters)")
    return v.strip()


def _save_account(p: dict) -> dict:
    """Adds an account, or changes an existing one's name, role or password."""
    username = _text(p, "username", 32).lower()
    display_name = _text(p, "display_name", 40)
    role = p.get("role", "therapist")
    password = p.get("password") or ""
    if not isinstance(password, str) or len(password) > 256:
        raise BadRequest("password must be text")
    existing = accounts.get_user(username)
    if existing is None:
        if not password:
            raise Refused("a new account needs a password", "新帳號要設定初始密碼。")
        user = accounts.set_user(username, display_name, password, role)
        store.audit("account_added", f"{username} role={role}")
    else:
        user = accounts.update_user(username, display_name, role)
        if password:
            accounts.set_password(username, password)
        store.audit("account_changed", f"{username} role={role}" + ("; password reset, signed out everywhere" if password else ""))
    with CLIENTS_LOCK:  # the name shown on that therapist's clients follows the account
        clients = load_clients()
        changed = False
        for c in clients:
            if c.get("therapist_user") == username and c.get("therapist") != user["display_name"]:
                c["therapist"], changed = user["display_name"], True
        if changed:
            _write_clients(clients)
    return _admin_overview()


def _remove_account(username: str) -> dict:
    mine = [c["id"] for c in load_clients() if c.get("therapist_user") == username]
    if mine:
        raise Refused("the account still has clients assigned",
                      f"這個帳號還負責 {len(mine)} 位個案（{'、'.join(mine)}），請先把他們指派給其他心理師再刪除帳號。", clients=mine)
    if not accounts.remove_user(username):
        raise HttpError(HTTPStatus.NOT_FOUND, "no such account", "沒有這個帳號。")
    store.audit("account_removed", username)
    return _admin_overview()


def _assign_client(client_id: str, username) -> dict:
    """Gives a client to a therapist's account, moves it to another, or leaves it
    unassigned (nobody can open it then). The previous therapist loses access at once."""
    if username not in (None, "") and (not isinstance(username, str) or accounts.get_user(username) is None):
        raise Refused("no such account", "沒有這個帳號。")
    with CLIENTS_LOCK:
        clients = load_clients()
        client = next((c for c in clients if c["id"] == client_id), None)
        if client is None:
            raise HttpError(HTTPStatus.NOT_FOUND, "no such client", "找不到這位個案。")
        before = client.get("therapist_user") or "-"
        if username:
            client["therapist_user"] = username
            client["therapist"] = accounts.get_user(username)["display_name"]
        else:
            client.pop("therapist_user", None)
        _write_clients(clients)
    store.audit("client_assigned", f"{client_id}: {before} -> {username or '-'}")
    return _admin_overview()


def _new_client(p: dict) -> dict:
    client_id = _text(p, "id", 16).upper()
    if not CLIENT_ID.match(client_id):
        raise BadRequest("id must be a letter followed by letters or digits")
    alias = _text(p, "alias", 40)
    concern = _text(p, "presenting_concern", 200, required=False)
    age = p.get("age")
    if age in ("", None):
        age = None
    elif not isinstance(age, int) or isinstance(age, bool) or not 0 < age < 120:
        raise BadRequest("age must be a whole number")
    username = p.get("username") or ""
    if username and (not isinstance(username, str) or accounts.get_user(username) is None):
        raise Refused("no such account", "沒有這個帳號。")
    with CLIENTS_LOCK:
        clients = load_clients()
        if any(c["id"] == client_id for c in clients):
            raise Refused("a client with this id exists", "這個個案編號已經有人使用。")
        client = {"id": client_id, "alias": alias, "age": age, "presenting_concern": concern,
                  "therapist": accounts.get_user(username)["display_name"] if username else "",
                  "consent": {"recording": False}, "scores": {}, "sessions": []}
        if username:
            client["therapist_user"] = username
        clients.append(client)
        _write_clients(clients)
    store.audit("client_added", f"{client_id} -> {username or '-'}")  # the alias is not written to the log
    return _admin_overview()


def _abandon_audio(path: str, session_id: str, e: BaseException) -> None:
    """A recording that will not be transcribed: removed, its id released, and the
    failure recorded (the id and the kind of failure, never content)."""
    _remove(path)
    with CLIENTS_LOCK:
        UPLOADING.pop(session_id, None)
    kind = f"HTTP {int(e.status)}" if isinstance(e, HttpError) else type(e).__name__
    store.audit("upload_failed", f"{session_id} {kind}; temp audio removed")


def _sweep_recordings() -> None:
    """Removes the audio of recordings that stopped sending (a closed laptop, a lost connection)."""
    now = time.time()
    with CLIENTS_LOCK:
        gone = [rid for rid, r in RECORDINGS.items() if now - r["seen"] > RECORDING_IDLE]
        recs = [RECORDINGS.pop(rid) for rid in gone]
    for r in recs:
        _remove(r["path"])
        with CLIENTS_LOCK:
            UPLOADING.pop(r["session_id"], None)
        store.audit("recording_abandoned", f"{r['session_id']}: nothing arrived for {RECORDING_IDLE // 60} minutes; audio removed")


def _input_mode(value) -> str:
    """``input_mode`` of an upload or recording (haven/recap.py); anything but session or recap is a 400."""
    try:
        return recap.parse(value)
    except ValueError as e:
        raise BadRequest(str(e)) from None


def _start_transcription(tmp_name: str, session_id: str, client_id: str, session_no: int, simulated: bool, action: str,
                         input_mode: str = recap.SESSION) -> dict:
    """Queues transcription and drafting of a complete recording and answers at
    once (202). The recording moves into data/audio (haven/audio.py), where a retry
    can find it and where it stays until it is signed off, expires or is deleted."""
    params = {"client_id": client_id, "session_no": session_no, "simulated": simulated, "action": action,
              "date": date.today().isoformat(), "input_mode": input_mode}
    try:
        job = jobs.create(session_id, "transcribe", params, audio=tmp_name)
    except jobs.Busy:
        e = Refused("this session is already being processed", "這次會談已經在處理中，請等它完成。")
        _abandon_audio(tmp_name, session_id, e)
        raise e from None
    except BaseException as e:
        _abandon_audio(tmp_name, session_id, e)
        raise
    return jobs.public(job)


def _transcribe_job(job: dict, audio: Path | None) -> None:
    """The "transcribe" job: speech to text, speaker labels, the transcript filed
    and the client's session list updated, then the note drafted. A retry after the
    transcript was stored skips the speech-to-text step and only drafts. On a
    failure the recording is kept for a retry; once transcribed it is removed."""
    session_id, p = job["session_id"], job["params"]
    client_id = p["client_id"]
    asr_done = False
    try:
        if not (config.SESSIONS_DIR / f"{session_id}.json").is_file():
            if audio is None:
                raise HttpError(HTTPStatus.GONE, "the recording is no longer kept",
                                "原始錄音已不在主機上，無法重新轉寫。請重新上傳錄音檔。", code="audio_gone")
            jobs.stage(job, "transcribe")
            audio_store.mark_processing(session_id)  # a retry after a failure
            try:
                t = transcribe_audio(audio, session_id, client_id, p["session_no"], p["date"], p["simulated"])
            except ImportError:
                raise HttpError(HTTPStatus.NOT_IMPLEMENTED, "faster-whisper is not installed",
                                "這台電腦還沒有安裝語音轉寫模組（faster-whisper）。", code="asr_not_installed") from None
            except ASRError as e:
                too_long = "maximum allowed duration" in str(e)
                raise HttpError(HTTPStatus.BAD_GATEWAY, f"speech-to-text server at {config.ASR_URL}: {e}",
                                "錄音超過本機語音轉寫服務允許的長度。請調高伺服器的 VLLM_MAX_AUDIO_DECODE_DURATION_S，或把錄音分段上傳。"
                                if too_long else "本機語音轉寫服務沒有回傳可用的逐字稿，請確認錄音檔格式後再試。",
                                code="asr_too_long" if too_long else "asr_failed") from None
            except llm.NonLocalEndpointError:
                raise HttpError(HTTPStatus.BAD_GATEWAY, "the speech-to-text endpoint is not local; nothing was sent",
                                "語音轉寫服務的位址不是本機或診所區域網路，錄音沒有送出。請檢查設定。", code="asr_not_local") from None
            except OSError:
                raise HttpError(HTTPStatus.SERVICE_UNAVAILABLE, f"speech-to-text server not reachable at {config.ASR_URL}",
                                "連不上本機語音轉寫服務，請確認它已經啟動，再按「重試」。", code="asr_unreachable") from None
            if not t.segments:
                raise HttpError(HTTPStatus.UNPROCESSABLE_ENTITY, "no speech was recognised in the recording",
                                "錄音裡沒有辨識到任何語音，沒有建立會談。請確認錄音檔是否正確、有沒有聲音。", code="no_speech")
            jobs.stage(job, "speakers")
            t.input_mode = recap.parse(p.get("input_mode"))
            labelled = recap.label(t) if recap.is_recap(t) else ORCH.speakers.run(t)  # a recap has one voice: no guessing
            with CLIENTS_LOCK:  # two uploads at once must not drop each other's session
                if not (config.SESSIONS_DIR / f"{session_id}.json").exists():
                    save_transcript(t)
                clients = load_clients()
                for c in clients:
                    if c["id"] == client_id and session_id not in c.setdefault("sessions", []):
                        c["sessions"].append(session_id)
                files.write_private(config.CLIENTS_FILE, json.dumps(clients, ensure_ascii=False, indent=2) + "\n")
            audio_store.mark_available(session_id)  # kept for listening back until signed off, expired or deleted
            asr_done = True
            store.audit(p["action"], f"{session_id} consent={CONSENT_SCOPE} simulated={int(p['simulated'])} asr={config.ASR_BACKEND} "
                                     f"speakers_guessed={labelled}/{len(t.segments)}; audio kept until signed off or expired")
    except BaseException as e:
        if not asr_done:
            audio_store.mark_failed(session_id)  # the file stays for a retry, until it expires
        kind = f"HTTP {int(e.status)}" if isinstance(e, HttpError) else type(e).__name__
        store.audit("upload_failed", f"{session_id} {kind}; audio kept for a retry")
        raise
    finally:
        with CLIENTS_LOCK:
            UPLOADING.pop(session_id, None)
    jobs.stage(job, "draft")
    _open_session(session_id)


_REGENERATE_CHECKS = {"check_revision": _check_revision, "need_base": _need_base,
                      "refuse": lambda m, zh, **x: Refused(m, zh, **x)}


def _regenerate_job(job: dict, audio: Path | None) -> None:
    """The "regenerate" job (POST .../process): a new draft, guarded again under the session lock."""
    sid, p = job["session_id"], job["params"]
    jobs.stage(job, "draft")
    with _session_lock(sid):
        regenerate.regenerate_draft(sid, p["template"], p["payload"], orch=ORCH, bump=_bump, **_REGENERATE_CHECKS)


jobs.register("regenerate", _regenerate_job)
jobs.register("transcribe", _transcribe_job,
              needs_audio=lambda job: not (config.SESSIONS_DIR / f"{job['session_id']}.json").is_file())


def _my_job(job_id: str) -> dict:
    """The job, if the signed-in therapist may open its session's client; 404 otherwise."""
    job = jobs.get(job_id)
    missing = HttpError(HTTPStatus.NOT_FOUND, "no such job", "找不到這項處理工作。")
    if job is None:
        raise missing
    if accounts.required():
        try:
            client = get_client(job["params"].get("client_id") or load_transcript(job["session_id"]).client_id)
        except (KeyError, FileNotFoundError):
            raise missing from None
        if not accounts.may_open(_user(), client):
            raise missing
    return job


def _retry_job(job_id: str) -> dict:
    job = _my_job(job_id)
    try:
        job = jobs.retry(job["job_id"])
    except jobs.NotRetryable:
        raise Refused("only a failed or interrupted job can be retried", "這項處理正在進行或已經完成，不需要重試。",
                      job_status=(jobs.get(job_id) or {}).get("status")) from None
    except jobs.AudioGone:
        raise Refused("the recording of this job is no longer kept", "原始錄音已不在主機上，無法重新轉寫。請重新上傳錄音檔。",
                      error_code="audio_gone") from None
    except jobs.Busy:
        raise Refused("this session is already being processed", "這次會談已經在處理中，請等它完成。") from None
    return jobs.public(job)


def _delete_audio(sid: str) -> dict:
    """The therapist deletes the original audio now (X1). The transcript and the draft stay."""
    try:
        row = audio_store.delete_for_session(sid, "manual")
    except audio_store.Busy:
        raise Refused("the recording is still being transcribed", "錄音還在轉寫中，請等處理完成後再刪除。") from None
    if row is None:
        raise HttpError(HTTPStatus.NOT_FOUND, "this session has no recording", "這次會談沒有保留的原始錄音。")
    return audio_store.view(sid)


def _remove(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def _reserve_upload(session_id: str, client_id: str) -> int:
    """Claims ``session_id`` for one upload and gives it the client's next session
    number. Both happen under one lock, so two uploads at once can neither share
    an id (the second is refused) nor a number."""
    with CLIENTS_LOCK:
        client = _client(client_id)
        taken = (config.SESSIONS_DIR / f"{session_id}.json").exists() or session_id in UPLOADING \
            or any(session_id in c.get("sessions", []) for c in load_clients()) or jobs.active(session_id, "transcribe")
        if taken:
            raise Refused("a session with this id already exists or is being uploaded",
                          "這個會談編號已經存在，或正在上傳中。請換一個編號。")
        numbers = [no for cid, no in UPLOADING.values() if cid == client_id]
        for sid in client.get("sessions", []):
            try:
                numbers.append(int(json.loads(files.read_private(config.SESSIONS_DIR / f"{sid}.json"))["session_no"]))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        session_no = max(numbers, default=0) + 1
        UPLOADING[session_id] = (client_id, session_no)
        return session_no


def lan_problems(host: str) -> list[str]:
    """What is missing before Haven may listen on the clinic network. On loopback
    nothing is: only this machine can connect."""
    if host in LOOPBACK:
        return []
    problems = []
    if not accounts.required():
        problems.append("no accounts: anyone who could reach this port could read every transcript and sign notes. "
                        "Add one per therapist with scripts/add_user.py")
    if not (config.TLS_CERT and config.TLS_KEY):
        problems.append("no certificate: set HAVEN_TLS_CERT and HAVEN_TLS_KEY (scripts/make_cert.py makes them). "
                        "Without HTTPS passwords and transcripts cross the network readable, and browsers do not allow the microphone")
    if not config.HOSTNAMES:
        problems.append("HAVEN_HOSTNAMES is not set: list the names or addresses the devices use for this machine, "
                        "for example 192.168.1.20,haven.local")
    return problems


def lan_warning(host: str) -> str:
    problems = lan_problems(host)
    if not problems:
        return ""
    return f"Haven: HAVEN_HOST={host} is not loopback and Haven will not start:\n" + "\n".join(f"  - {p}" for p in problems)


def tls_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(config.TLS_CERT, config.TLS_KEY)
    return ctx


def serve() -> None:
    global ORCH
    os.umask(0o077)  # whatever Haven creates is for this account only
    files.secure_data_dir()
    line = crypto.start()
    if line:
        print(line)
    leftover = files.clear_temp()
    orphans = audio_store.clean_orphans()
    if orphans:
        print(f"Haven: removed {orphans} file(s) in data/audio that no record names")
    stopped = jobs.startup()
    if stopped:
        print(f"Haven: {stopped} background job(s) were cut off by the last shutdown; they are marked interrupted and can be retried")
    if leftover:
        print(f"Haven: removed {leftover} recording(s) left in {files.temp_dir()} by an earlier run that did not finish")
    chain = store.verify_audit()
    if not chain["ok"]:
        print(f"Haven: WARNING: the audit log does not verify (first bad row id {chain['first_bad_id']}). "
              "A row was changed, inserted or removed outside Haven. Run scripts/verify_audit.py.", file=sys.stderr)
    warning = lan_warning(config.HOST)
    if warning:
        print(warning, file=sys.stderr)
        sys.exit(2)
    ORCH = Orchestrator()
    audio_store.start_sweeper()  # removes expired audio now and every 10 minutes
    httpd = ThreadingHTTPServer((config.HOST, config.PORT), Handler)
    scheme = "http"
    if config.TLS_CERT and config.TLS_KEY:
        # The handshake runs in each connection's own thread (on its first read), so a
        # slow or broken client cannot hold up the others.
        httpd.socket = tls_context().wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
        scheme = "https"
    if accounts.required():
        print(f"Haven: sign-in required ({len(accounts.load_users())} account(s)); idle sign-out after {accounts.IDLE_SECONDS // 60} minutes")
    shown = config.HOSTNAMES[0] if config.HOSTNAMES and config.HOST not in LOOPBACK else config.HOST
    print(f"Haven running at {scheme}://{shown}:{config.PORT}  (engine: {ORCH.engine})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
