"""Alignment hybrid ASR (docs/specs/asr-qwen3-evaluation.md §3.1, §0.1–0.2; MVP spec T16c).

With HAVEN_ASR_REFINE=1, Whisper still makes the segments: their ids, start and end
times are what citations, playback and speaker labels point to, and none of them
changes here. Qwen3-ASR (a second local server, vLLM on the B70) transcribes the whole
recording once more; measured on the B70 it is far more accurate in Mandarin (CER 8.1%
vs 17.8% on ASCEND, 1.1% vs 7.4% on the 46-minute L50) but returns text only. That text
is cut to fit Whisper's segments by character alignment (difflib, standard library) and
replaces Whisper's words segment by segment.

Whisper's words are kept:
- for the whole transcript when the Qwen3-ASR request fails or its text does not
  match the recording Whisper heard;
- for one segment when no Qwen3-ASR text falls in it, or what falls in it has too
  little in common with Whisper's words (e.g. Whisper's hallucination over silence).

Each segment records ``asr_engine`` ("whisper" or the Qwen3-ASR model name) and, when
replaced, ``whisper_text``.

Limitation 4 of the evaluation: both models cut the audio every 30 seconds, and
Qwen3-ASR writes an extra 「。」 at such a seam, sometimes in the middle of a word
(「這個方。法很適合」). A full stop near a seam that Whisper does not have at the same
place is dropped, so a clause (and a negation in it) is not split, and no segment
ends up empty, punctuation only, or starting with punctuation.

Qwen3-ASR is asked for ``response_format=json``: it answers 400 to verbose_json.
Whisper keeps verbose_json (its json answer drops words; evaluation §0 finding 2).
"""

from __future__ import annotations

import bisect
import difflib
import json
import re
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import config
from .models import Segment

WHISPER = "whisper"
# How far (in characters) an unsure cut may move to reach Qwen3-ASR's punctuation.
SNAP = 6
# Both models work in 30-second windows; a full stop this close (seconds) to one is suspect.
SEAM = 30.0
SEAM_TOLERANCE = 2.0
# Below this share of matching characters (difflib ratio) a segment keeps Whisper's words.
MIN_SEGMENT_MATCH = 0.3
# Below this share of Whisper's characters found in Qwen3-ASR's text, none of it is used.
MIN_TOTAL_MATCH = 0.5

_PLAIN = re.compile(r"[㐀-鿿豈-﫿A-Za-z0-9]")
_LEADING = re.compile(r"^[\s，。、！？；：,.!?;:…」』）)》〉]+")
_TRAILING_OPEN = re.compile(r"[\s「『（(《〈]+$")
_REPEATED = re.compile(r"([，。、！？；：])\1+")


class RefineError(Exception):
    """The Qwen3-ASR server gave no usable text (Whisper's text is kept)."""


def _warn(message: str) -> None:
    # Counts and reasons only: never transcript text in a log.
    print(f"asr_refine: {message}", file=sys.stderr)


def _request(audio_path: Path) -> str:
    """The whole recording to the Qwen3-ASR server, through Haven's local-only opener."""
    from .llm import _OPENER, assert_local

    assert_local(config.ASR_REFINE_URL)  # a misconfigured address is an error, not a fallback
    boundary = uuid.uuid4().hex
    fields = {"model": config.ASR_REFINE_MODEL, "language": "zh", "response_format": "json"}
    body = b"".join(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode() for k, v in fields.items()
    ) + (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio{audio_path.suffix}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + audio_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{config.ASR_REFINE_URL.rstrip('/')}/v1/audio/transcriptions",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with _OPENER.open(req, timeout=config.ASR_TIMEOUT) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:
        raise RefineError(f"HTTP {e.code}") from e
    except OSError as e:  # refused, unreachable, timed out
        raise RefineError(type(e).__name__) from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise RefineError("reply is not JSON") from e
    text = out.get("text") if isinstance(out, dict) else None
    if not isinstance(text, str) or not _PLAIN.search(text):
        raise RefineError("no text in the reply")
    return text


def refine(audio_path: Path, segments: list[Segment]) -> dict:
    """Replaces each segment's text with Qwen3-ASR's words for the same stretch, in
    place. Ids, times and speakers are not touched. Returns counts for reports."""
    from .transcript import _clean

    stats = {"segments": len(segments), "replaced": 0, "kept": len(segments), "failed": ""}
    for s in segments:
        s.asr_engine, s.whisper_text = WHISPER, None
    if not segments:
        return stats
    try:
        text = _clean(_request(audio_path))
    except RefineError as e:
        stats["failed"] = str(e)
        _warn(f"Qwen3-ASR at {config.ASR_REFINE_URL} gave no usable text ({e}); kept Whisper's text for all {len(segments)} segments")
        return stats
    pieces = align([(s.start, s.end, s.text) for s in segments], text, stats)
    if stats.get("mismatch"):
        stats["failed"] = "text does not match"
        _warn(f"Qwen3-ASR's text does not match Whisper's (is opencc installed?); kept Whisper's text for all {len(segments)} segments")
        return stats
    for s, piece in zip(segments, pieces):
        if piece is not None:
            s.whisper_text, s.text, s.asr_engine = s.text, piece, config.ASR_REFINE_MODEL
    stats["replaced"] = sum(p is not None for p in pieces)
    stats["kept"] = len(segments) - stats["replaced"]
    _warn(f"{stats['replaced']} of {len(segments)} segments in Qwen3-ASR's words, {stats['kept']} kept Whisper's; "
          f"{stats.get('snapped', 0)} cuts moved to punctuation, {stats.get('seam_stops', 0)} seam full stops dropped")
    return stats


def _fold(c: str) -> str:
    return c.lower()


def _near_seam(start: float, end: float) -> bool:
    k = max(1, int((start - SEAM_TOLERANCE) // SEAM))
    while k * SEAM <= end + SEAM_TOLERANCE:
        if k * SEAM >= start - SEAM_TOLERANCE:
            return True
        k += 1
    return False


def align(segments: list[tuple[float, float, str]], text: str, stats: dict | None = None) -> list[str | None]:
    """Cuts ``text`` (Qwen3-ASR's whole transcript) into one piece per Whisper segment
    ``(start, end, text)``. None for a segment that keeps Whisper's words.

    Moved from scripts/asr_benchmark.py (evaluated on L50: 18 of 20 sampled cuts right
    against the script, 0.4% of segments empty). Letters, digits and CJK characters of
    both transcripts are aligned with difflib; a cut is sure when the characters on both
    sides of it are the same in both. Inside a replaced stretch the cut falls
    proportionally and then moves to the nearest punctuation of Qwen3-ASR within SNAP
    characters (Whisper's segments end at clause ends, and Qwen3-ASR punctuates them).
    Both texts should already be cleaned the same way (transcript._clean)."""
    stats = {} if stats is None else stats
    n = len(segments)
    whole, w_pos, bounds = "", [], []
    for _, _, t in segments:
        base = len(whole)
        whole += t
        w_pos += [base + k for k, c in enumerate(t) if _PLAIN.fullmatch(c)]
        bounds.append(len(w_pos))
    w_tok = [_fold(whole[p]) for p in w_pos]
    text = _REPEATED.sub(r"\1", text)
    q_tok = [_fold(c) for c in text if _PLAIN.fullmatch(c)]
    if not w_tok or not q_tok:
        stats["mismatch"] = True
        return [None] * n
    ops = difflib.SequenceMatcher(None, w_tok, q_tok, autojunk=False).get_opcodes()
    if sum(i2 - i1 for tag, i1, i2, _, _ in ops if tag == "equal") < MIN_TOTAL_MATCH * len(w_tok):
        stats["mismatch"] = True
        return [None] * n

    # Whisper's punctuation: plain index i is in the set when punctuation stands before it.
    w_punct = {i for i in range(1, len(w_pos)) if whole[w_pos[i - 1] + 1:w_pos[i]].strip()}
    text = _drop_seam_stops(text, segments, ops, w_punct, bounds, stats)
    q_pos = [k for k, c in enumerate(text) if _PLAIN.fullmatch(c)]
    # j is a punctuation gap when something other than spaces stands between plain characters j-1 and j
    gaps = [j for j in range(1, len(q_pos)) if text[q_pos[j - 1] + 1:q_pos[j]].strip()]

    def to_q(b: int) -> int:
        for tag, i1, i2, j1, j2 in ops:
            if i1 < b < i2 and tag == "equal":
                return j1 + b - i1  # sure
        for tag, i1, i2, j1, j2 in ops:
            if i1 <= b <= i2:
                j = j1 + (b - i1 if tag == "equal" else round((b - i1) * (j2 - j1) / max(1, i2 - i1)))
                if j in (0, len(q_pos)):
                    return j  # the start or end of the text is the surest stop there is
                near = min(gaps, key=lambda g: (abs(g - j), g), default=None)
                if near is not None and near != j and abs(near - j) <= SNAP:
                    stats["snapped"] = stats.get("snapped", 0) + 1
                    return near
                return j
        return len(q_pos)

    cuts = [0] + [q_pos[j] if j < len(q_pos) else len(text) for j in (to_q(b) for b in bounds[:-1])] + [len(text)]
    for i in range(1, len(cuts)):
        cuts[i] = max(cuts[i], cuts[i - 1])  # never backwards
    out: list[str | None] = []
    for k in range(n):
        piece = _tidy(text[cuts[k]:cuts[k + 1]])
        mine = [_fold(c) for c in piece if _PLAIN.fullmatch(c)]
        whisper = w_tok[bounds[k - 1] if k else 0:bounds[k]]
        if not mine or difflib.SequenceMatcher(None, whisper, mine, autojunk=False).ratio() < MIN_SEGMENT_MATCH:
            out.append(None)
        else:
            out.append(piece)
    return out


def _to_whisper(j: int, ops) -> int:
    """Whisper's plain index for Qwen3-ASR's plain index j (the inverse of to_q)."""
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal" and j1 < j < j2:
            return i1 + j - j1
    for tag, i1, i2, j1, j2 in ops:
        if j1 <= j <= j2:
            return i1 + (j - j1 if tag == "equal" else round((j - j1) * (i2 - i1) / max(1, j2 - j1)))
    return ops[-1][2]


def _drop_seam_stops(text: str, segments, ops, w_punct: set[int], bounds: list[int], stats: dict) -> str:
    """Drops a 「。」 inside Qwen3-ASR's text that sits in a segment near a 30-second seam
    where Whisper has no punctuation at the same place (evaluation §0.2 limitation 4)."""
    q_pos = [k for k, c in enumerate(text) if _PLAIN.fullmatch(c)]
    last = len(segments) - 1
    drop = set()
    for p, c in enumerate(text):
        if c != "。":
            continue
        j = bisect.bisect_left(q_pos, p)  # plain characters before it
        if j == 0 or j >= len(q_pos):
            continue  # the very start or end of the text: nothing to split
        i = _to_whisper(j, ops)
        if any(k in w_punct for k in (i - 1, i, i + 1)):
            continue
        near = {min(bisect.bisect_right(bounds, x), last) for x in (max(0, i - 1), i)}
        if any(_near_seam(segments[s][0], segments[s][1]) for s in near):
            drop.add(p)
    if drop:
        stats["seam_stops"] = stats.get("seam_stops", 0) + len(drop)
    return "".join(c for p, c in enumerate(text) if p not in drop)


def _tidy(piece: str) -> str:
    """No piece starts with closing punctuation (it belongs to the clause before) or
    ends with an opening bracket (it belongs to the clause after)."""
    piece = _LEADING.sub("", piece.strip())
    return _TRAILING_OPEN.sub("", piece)
