"""Regenerate and confirm guards (spec §9.2, §9.3 rules 1 and 3-6, §10 G1/C1).

- Every sentence of the working draft has an ``origin``: "ai", "ai_edited" or
  "human". The server sets it when the draft is saved, by comparing with
  ``ai_note`` (the AI's own draft); the browser's value is never read.
- Regenerating a draft that has human edits is refused (409 ``has_edits``) unless
  the request says ``discard_edits: true``. The old working draft is then kept in
  ``superseded_drafts`` (at most MAX_SUPERSEDED, newest last), never deleted.
- ``regenerate_draft`` is the one function the synchronous route calls today and a
  background job (ticket 05) can call later: the caller holds the session lock.
"""

from __future__ import annotations

from . import store

MAX_SUPERSEDED = 3
ORIGINS = ("ai", "ai_edited", "human")


def stamp_origins(note: dict, ai_note: dict | None) -> None:
    """Sets ``origin`` on every sentence of ``note`` (a note dict) from ``ai_note``.
    Same words and same citations in the same section: "ai". Same words with other
    citations, or other words citing a line an AI sentence of that section cites:
    "ai_edited". Anything else: "human". Score lines the server writes are "ai"."""
    ai_sections = (ai_note or {}).get("sections", {})
    for key, items in note.get("sections", {}).items():
        theirs = ai_sections.get(key, [])
        exact = {(s["text"], tuple(s.get("evidence") or [])) for s in theirs}
        texts = {s["text"] for s in theirs}
        cited = {e for s in theirs for e in (s.get("evidence") or [])}
        for s in items:
            evidence = s.get("evidence") or []
            if s.get("source") == "measure" or (s["text"], tuple(evidence)) in exact:
                s["origin"] = "ai"
            elif s["text"] in texts or cited.intersection(evidence):
                s["origin"] = "ai_edited"
            else:
                s["origin"] = "human"


def edited_sentences(result: dict) -> int:
    return sum(1 for items in result.get("note", {}).get("sections", {}).values() for s in items
               if s.get("origin", "ai") != "ai" or s.get("confirmed_by_therapist") or s.get("keep_reason"))


def has_edits(result: dict) -> bool:
    return bool(edited_sentences(result) or result.get("edited_by"))


def regenerate_draft(sid: str, template: str, payload: dict, *, orch, check_revision, need_base, bump, refuse) -> dict:
    """Writes a new draft for ``sid``. The caller holds the session lock.
    ``check_revision(result, payload)`` and ``need_base(payload)`` raise on a stale or
    missing base_revision, ``refuse(message, zh, **extra)`` builds the 409, ``bump(result)``
    raises the draft's revision. Returns the new stored result."""
    old = store.get_result(sid)
    edits = False
    if old is not None:
        if store.get_signature(sid):
            raise store.AlreadySignedError(sid)
        need_base(payload)
        check_revision(old, payload)
        edits = has_edits(old)
        if edits and payload.get("discard_edits") is not True:
            raise refuse("the draft has edits; send discard_edits to regenerate it (the old draft is kept under superseded_drafts)",
                         "這份草稿有您修改過的內容。重新產生後，目前的草稿會保留在「先前版本」，不會刪除；請確認要重新產生。",
                         has_edits=True, edited_sentences=edited_sentences(old), revision=old.get("revision", 0))
    orch.process_session(sid, template)  # refuses a signed session
    result = store.get_result(sid)
    kept = list((old or {}).get("superseded_drafts", []))
    if old is not None and edits:
        kept.append({"superseded_at": store.now(), "revision": old.get("revision", 0), "saved_at": old.get("draft_saved_at"),
                     "edited_by": old.get("edited_by", ""), "template_id": old.get("template_id"),
                     "note": old["note"], "ai_note": old.get("ai_note")})
    result["superseded_drafts"] = kept[-MAX_SUPERSEDED:]
    result["revision"] = (old or {}).get("revision", 0)
    bump(result)
    result["edited_by"] = ""  # a new AI draft has no human edits yet
    store.save_result(sid, result)
    if old is not None and edits:
        store.audit("regenerate_discarded_edits", f"{sid}: {edited_sentences(old)} edited sentence(s) moved to superseded_drafts "
                    f"({len(result['superseded_drafts'])} kept), revision={old.get('revision', 0)}")
    return result
