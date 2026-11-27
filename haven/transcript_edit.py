"""Transcript corrections after the draft is written (spec §9.2, §9.3 rule 2).

The therapist may correct a line's words or its speaker. A correction never
moves a line: id, start and end stay, and the first correction keeps the ASR's
own words in ``asr_text``. Every correction raises the transcript's
``transcript_revision``. Draft sentences that cite a corrected line keep their
words and are marked ``source_changed``; the draft as a whole needs review
until the therapist says it has been read against the current transcript
(``POST .../reviewed``).

Where the numbers live:
- ``transcript_revision`` is in the transcript file (``data/sessions/<SID>.json``),
  0 for files written before this existed.
- ``generated_from_transcript_revision`` and ``reviewed_against_transcript_revision``
  are in the working draft (``results.payload``), with ``source_changed_segments``:
  the lines corrected since the last review.
- ``review_status`` is derived and never stored (see ``review_status``).
"""

from __future__ import annotations

import json

from . import config, files, store
from .models import to_dict
from .transcript import load_transcript

MAX_TEXT = 2000
SPEAKERS = ("therapist", "client", "unknown")


class BadEdit(ValueError):
    """A correction the server cannot read (400)."""


class EditRefused(Exception):
    """A correction the server understands and will not make (409)."""

    def __init__(self, message: str, zh: str):
        super().__init__(message)
        self.zh = zh


def _path(sid: str):
    return config.SESSIONS_DIR / f"{sid}.json"


def _load_raw(sid: str) -> dict:
    return json.loads(files.read_private(_path(sid)))


def _save_raw(sid: str, raw: dict) -> None:
    # The file is rewritten from what was read, so fields this module does not
    # know about (added by other parts of Haven) are kept.
    files.write_private(_path(sid), json.dumps(raw, ensure_ascii=False, indent=2))


def _rev(raw: dict) -> int:
    value = raw.get("transcript_revision", 0)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def revision(sid: str) -> int:
    """The transcript's revision; 0 for a file that was never corrected."""
    try:
        return _rev(_load_raw(sid))
    except FileNotFoundError:
        return 0


def _segment_ids(changes, what: str) -> dict[int, object]:
    if not isinstance(changes, dict) or not changes:
        raise BadEdit(f"{what} must map segment numbers to values")
    out = {}
    for key, value in changes.items():
        try:
            seg_id = int(key)
        except (TypeError, ValueError):
            raise BadEdit("segment numbers must be integers") from None
        out[seg_id] = value
    return out


def _stamp(seg: dict, editor: str, now: str) -> None:
    seg["edited_by"], seg["edited_at"] = editor, now


def edit_text(sid: str, changes, editor: str) -> list[int]:
    """Writes corrected words into the transcript file. Every id must exist and
    every text must be 1..MAX_TEXT characters, or nothing is written. Returns the
    ids whose words actually changed (the revision goes up only when there are any)."""
    wanted = _segment_ids(changes, "segments")
    raw = _load_raw(sid)
    segs = {s["id"]: s for s in raw["segments"]}
    clean = {}
    for seg_id, text in wanted.items():
        if seg_id not in segs:
            raise BadEdit(f"segment {seg_id}: no such segment")
        if not isinstance(text, str) or not text.strip():
            raise BadEdit(f"segment {seg_id}: text must be a non-empty string")
        text = text.strip()
        if len(text) > MAX_TEXT:
            raise BadEdit(f"segment {seg_id}: text longer than {MAX_TEXT} characters")
        clean[seg_id] = text
    changed = [i for i, text in sorted(clean.items()) if segs[i]["text"] != text]
    if not changed:
        return []
    now = store.now()
    for i in changed:
        seg = segs[i]
        if seg.get("asr_text") is None:  # the first correction keeps what the ASR heard, once
            seg["asr_text"] = seg["text"]
        seg["text"] = clean[i]
        _stamp(seg, editor, now)
    raw["transcript_revision"] = _rev(raw) + 1
    _save_raw(sid, raw)
    return changed


def edit_speakers(sid: str, changes, editor: str) -> tuple[int, list[int]]:
    """Sets who said each line, including lines already confirmed (AC-06). Lines
    of a prepared script are refused. Labels set here are "confirmed" ("unknown"
    sets the line back to unlabelled). Returns (lines set, ids whose speaker
    changed); the revision goes up only when a speaker changed."""
    wanted = _segment_ids(changes, "speakers")
    raw = _load_raw(sid)
    segs = {s["id"]: s for s in raw["segments"]}
    for seg_id, who in wanted.items():
        if seg_id not in segs or who not in SPEAKERS:
            raise BadEdit(f"segment {seg_id}: unknown segment or speaker")
        if segs[seg_id].get("speaker_source", "script") == "script":
            raise EditRefused("prepared transcripts already carry their speakers", "這份逐字稿的說話者已由原稿標好，不需要更正。")
    changed = sorted(i for i, who in wanted.items() if segs[i]["speaker"] != who)
    now = store.now()
    for i, who in wanted.items():
        seg = segs[i]
        seg["speaker"], seg["speaker_source"] = who, ("none" if who == "unknown" else "confirmed")
        if i in changed:
            _stamp(seg, editor, now)
    if changed:
        raw["transcript_revision"] = _rev(raw) + 1
    _save_raw(sid, raw)
    return len(wanted), changed


def start_draft(result: dict, sid: str) -> None:
    """A newly written draft was written from the transcript as it is now."""
    rev = revision(sid)
    result["generated_from_transcript_revision"] = rev
    result["reviewed_against_transcript_revision"] = rev
    result["source_changed_segments"] = []


def reviewed_against(result: dict) -> int:
    return result.get("reviewed_against_transcript_revision", result.get("generated_from_transcript_revision", 0))


def review_status(result: dict, transcript_revision: int, signed: bool) -> str:
    """editing | needs_review (the transcript changed after the last review) | confirmed (signed)."""
    if signed:
        return "confirmed"
    return "needs_review" if reviewed_against(result) < transcript_revision else "editing"


def mark_sources(result: dict) -> None:
    """Sets ``source_changed`` on every draft sentence citing a line corrected
    since the last review. The sentence's words are not touched."""
    changed = set(result.get("source_changed_segments", []))
    for items in result.get("note", {}).get("sections", {}).values():
        for s in items:
            s["source_changed"] = bool(changed.intersection(s.get("evidence") or []))


def after_change(sid: str, result: dict, changed: list[int]) -> None:
    """Records corrected lines on the draft and the transcript copy it carries.
    Call after the transcript file is written and before the draft is re-checked."""
    result["source_changed_segments"] = sorted(set(result.get("source_changed_segments", [])) | set(changed))
    result["transcript"] = [to_dict(s) for s in load_transcript(sid).segments]
    result.setdefault("generated_from_transcript_revision", 0)
    result.setdefault("reviewed_against_transcript_revision", result["generated_from_transcript_revision"])


def mark_reviewed(sid: str, result: dict) -> None:
    """The therapist has read the draft against the transcript as it is now."""
    result["reviewed_against_transcript_revision"] = revision(sid)
    result["source_changed_segments"] = []
    mark_sources(result)


def annotate(sid: str, result: dict, signed: bool) -> None:
    """Adds the revision fields to a session view."""
    rev = revision(sid)
    result["transcript_revision"] = rev
    result.setdefault("generated_from_transcript_revision", 0)
    result["reviewed_against_transcript_revision"] = reviewed_against(result)
    result["review_status"] = review_status(result, rev, signed)
    if not signed:
        mark_sources(result)


def commit(sid: str, result: dict, changed: list[int], refresh) -> None:
    """After a correction is written: marks the citing sentences, re-checks the
    whole draft with ``refresh`` (Orchestrator.refresh keeps the sentences' words
    and the therapist's confirmations) and saves it."""
    after_change(sid, result, changed)
    refresh(result)
    mark_sources(result)
    store.save_result(sid, result)
