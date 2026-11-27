"""human_only template fields: the placeholder and the therapist-authored rule.

A human_only field (haven.templates) is never sent to the model. The draft
starts it with one placeholder sentence, 「待心理師填寫」. Two server-side rules
follow, both decided from the sentence text and the template, never from a flag
the browser sends:

1. A sentence whose text is the placeholder cannot be signed. The therapist must
   rewrite or delete it (``placeholders``; the sign route refuses with 409, and
   the verifier shows it as unsupported meanwhile).
2. A sentence in a human_only field that cites no transcript line is the
   therapist's own writing: ``source="therapist"``. The verifier does not hold it
   as "no citation" (nothing is claimed about the transcript), it stays out of
   the supported/unsupported counts like a score line, and signing accepts it.
   One that does cite lines is checked like any transcript sentence. In a
   generate field an uncited sentence is still unsupported, as before.
"""

from __future__ import annotations

from . import templates
from .models import Note, NoteSentence

PLACEHOLDER = "待心理師填寫"
THERAPIST = "therapist"
PLACEHOLDER_REASON = "這一欄由心理師填寫：請把「待心理師填寫」改寫成您的內容，或刪除這一句"


def is_placeholder(text: str) -> bool:
    return text.strip().rstrip("。.").strip() == PLACEHOLDER


def placeholder_sentence() -> NoteSentence:
    return NoteSentence(PLACEHOLDER, [])


def placeholders(note: Note) -> list[NoteSentence]:
    """Sentences still holding the placeholder, in any field."""
    return [s for s in note.sentences() if is_placeholder(s.text)]


def mark_therapist_authored(note: Note) -> None:
    """Sets ``source="therapist"`` on uncited, non-placeholder sentences of the
    template's human_only fields (and clears a verdict cached for the text)."""
    tpl = templates.find(note.template)
    if tpl is None:
        return
    for f in tpl.fields:
        if f.ai_mode != templates.HUMAN_ONLY:
            continue
        for s in note.sections.get(f.key, []):
            if s.source == "measure":
                continue
            if not s.evidence and not is_placeholder(s.text):
                s.source, s.status, s.support, s.reason = THERAPIST, "unchecked", 0.0, ""
            elif s.source == THERAPIST:
                s.source = "transcript"  # cites lines (or is the placeholder): checked as a transcript sentence
