"""Export of a confirmed (signed) note as Markdown or plain text (spec §9.2, E1/E2, AC-09).

The file is built from the signed record (``signed_notes`` and its addenda), never
from the working draft, and holds no transcript text and no segment quotes. Every
download writes one ``exports`` row (status ``downloaded``) and an ``export_note``
audit row with ids only, in one transaction. "Archived" is marked by the therapist
separately; Haven cannot see the other system, so downloading never means filed.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from . import store, templates

FORMATS = {"md": ("text/markdown; charset=utf-8", "md"), "txt": ("text/plain; charset=utf-8", "txt")}
STATEMENT = "本紀錄由 AI 輔助產生草稿，經心理師核對確認"
MAX_REF = 100
COLUMNS = ("export_id", "session_id", "format", "note_sha256", "exported_by", "exported_at", "status",
           "archived_by", "archived_at", "archive_ref")


class BadFormat(ValueError):
    pass


class NotConfirmed(Exception):
    """The session has no signed note; there is nothing to export."""


class BadRef(ValueError):
    pass


class NoSuchExport(KeyError):
    pass


@dataclass
class Built:
    filename: str
    content_type: str
    body: bytes
    export: dict


def _template_of(sid: str, note: dict):
    """(template or None, name, version): the id and version stored with the note's
    draft, falling back to the registry's current definition."""
    tid = note.get("template", "")
    tpl = templates.find(tid)
    result = store.get_result(sid) or {}
    version = result.get("template_version") if result.get("template_id") == tid else None
    if version is None and tpl:
        version = tpl.version
    return tpl, (tpl.name if tpl else str(tid)), version


def render(sid: str, fmt: str) -> str:
    signature, note = store.get_signature(sid), store.get_signed_note(sid)
    if not signature or note is None:
        raise NotConfirmed(sid)
    tpl, name, version = _template_of(sid, note)
    sections = note.get("sections", {})
    keys = list(tpl.keys) if tpl else []
    labels = {f.key: f.label for f in tpl.fields} if tpl else {}
    keys += [k for k in sections if k not in keys]
    md = fmt == "md"
    out = [f"# {name}" if md else name, ""]
    meta = [("會談編號", sid), ("模板", name + (f"（版本 {version}）" if version is not None else "")),
            ("確認者", signature["signed_by"]), ("確認時間", signature["signed_at"]),
            ("紀錄雜湊 (SHA-256)", signature["note_sha256"])]
    out += [f"- {k}：{v}" if md else f"{k}：{v}" for k, v in meta]
    out.append("")
    for key in keys:
        label = labels.get(key, key)
        out.append(f"## {label}" if md else f"【{label}】")
        texts = [s.get("text", "") for s in sections.get(key, []) if s.get("text")]
        out += [f"- {t}" if md else t for t in texts] or ["（無）"]
        out.append("")
    addenda = store.get_addenda(sid)
    if addenda:
        out.append("## 補充說明" if md else "【補充說明】")
        for a in addenda:
            line = f"{a['author']}・{a['created_at']}：{a['text']}"
            out.append(f"- {line}" if md else line)
        out.append("")
    out += ["---" if md else "－－－", STATEMENT, ""]
    return "\n".join(out)


def _row(r) -> dict:
    return dict(zip(COLUMNS, r))


def build(sid: str, fmt: str, by: str) -> Built:
    if fmt not in FORMATS:
        raise BadFormat(fmt)
    text = render(sid, fmt)
    signature = store.get_signature(sid)
    content_type, ext = FORMATS[fmt]
    export_id = "EX" + secrets.token_hex(6).upper()
    row = (export_id, sid, fmt, signature["note_sha256"], by, store.now(), "downloaded", None, None, None)
    with store.connect() as c:
        c.execute(f"INSERT INTO exports ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})", row)
        store._audit(c, "export_note", f"{sid} export_id={export_id} format={fmt} note_sha256={signature['note_sha256'][:16]}")
    filename = f"haven-{re.sub(r'[^A-Za-z0-9_-]', '_', sid)}-note.{ext}"
    return Built(filename, content_type, text.encode("utf-8"), _row(row))


def get(sid: str, export_id: str) -> dict:
    """The export, only if it belongs to this session (another session's id answers as unknown)."""
    with store.connect() as c:
        r = c.execute(f"SELECT {', '.join(COLUMNS)} FROM exports WHERE export_id = ? AND session_id = ?", (export_id, sid)).fetchone()
    if not r:
        raise NoSuchExport(export_id)
    return _row(r)


def for_session(sid: str) -> list[dict]:
    with store.connect() as c:
        rows = c.execute(f"SELECT {', '.join(COLUMNS)} FROM exports WHERE session_id = ? ORDER BY exported_at, rowid", (sid,)).fetchall()
    return [_row(r) for r in rows]


def status_of(exports: list[dict]) -> str:
    if any(e["status"] == "archived" for e in exports):
        return "archived"
    return "downloaded" if exports else "not_exported"


def status(sid: str) -> str:
    return status_of(for_session(sid))


def mark_archived(sid: str, export_id: str, archive_ref, by: str) -> dict:
    if archive_ref is None:
        archive_ref = ""
    if not isinstance(archive_ref, str) or len(archive_ref.strip()) > MAX_REF:
        raise BadRef(f"archive_ref must be text of at most {MAX_REF} characters")
    ref = archive_ref.strip()
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        r = c.execute("SELECT status FROM exports WHERE export_id = ? AND session_id = ?", (export_id, sid)).fetchone()
        if not r:
            raise NoSuchExport(export_id)
        if r[0] != "archived":  # repeating is idempotent: the first mark stands
            c.execute("UPDATE exports SET status = 'archived', archived_by = ?, archived_at = ?, archive_ref = ? WHERE export_id = ?",
                      (by, store.now(), ref or None, export_id))
            store._audit(c, "archive_export", f"{sid} export_id={export_id}")
    return get(sid, export_id)
