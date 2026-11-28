"""ASR agent: turns session audio (or a prepared transcript) into segments."""

from __future__ import annotations

import json
import re
import sys
import urllib.error
import uuid
import urllib.request
from pathlib import Path

from . import config
from .models import Segment, Transcript, to_dict


def load_transcript(session_id: str) -> Transcript:
    path = config.SESSIONS_DIR / f"{session_id}.json"
    from .files import read_private

    raw = json.loads(read_private(path))
    return Transcript(
        session_id=raw["session_id"],
        client_id=raw["client_id"],
        session_no=raw["session_no"],
        date=raw["date"],
        simulated=raw.get("simulated", True),
        input_mode=raw.get("input_mode", "session"),
        timestamps_estimated=bool(raw.get("timestamps_estimated", False)),
        segments=[Segment(**s) for s in raw["segments"]],
    )


_whisper = None


def _whisper_model():
    """Loads the model once per process; loading takes longer than a short clip."""
    global _whisper
    if _whisper is None:
        from faster_whisper import WhisperModel  # optional dependency

        _whisper = WhisperModel(config.ASR_MODEL, device="auto", compute_type="int8")
    return _whisper


def asr_prompt() -> str:
    if config.ASR_PROMPT is not None:
        return config.ASR_PROMPT
    return "" if config.ASR_BACKEND == "openai" else "以下是心理諮商會談的逐字稿，使用繁體中文。"


_HALF = {",": "，", "?": "？", "!": "！", ";": "；", ":": "："}
_HALF_RE = re.compile(r"(?<![0-9A-Za-z])[,?!;:]|[,?!;:](?![0-9A-Za-z])")
_opencc = None


def _clean(text: str) -> str:
    """Whisper may write half-width punctuation or Simplified characters. The risk
    lexicon and the verifier rules are written in Traditional Chinese with full-width
    punctuation, so the transcript is brought to that form before any agent reads it."""
    global _opencc
    text = _HALF_RE.sub(lambda m: _HALF[m.group()], text.strip())
    if _opencc is None:
        try:
            from opencc import OpenCC  # pip install -r requirements-asr.txt
            _opencc = OpenCC("s2twp").convert
        except ImportError:
            _opencc = False
            # Measured in batch 10: Whisper wrote the whole 45-minute session in Simplified
            # characters. Without the converter 「自杀」 does not match the risk lexicon's 「自殺」.
            print("WARNING: opencc is not installed, so Simplified characters from speech-to-text stay "
                  "Simplified and risk words written in Traditional may be missed. "
                  "Install it: pip install -r requirements-asr.txt", file=sys.stderr)
    return _opencc(text) if _opencc else text


def converts_to_traditional() -> bool:
    """Whether transcripts are brought to Traditional characters (opencc installed)."""
    _clean("")
    return bool(_opencc)


# Characters per second of conversational Mandarin, used only to estimate
# timestamps when the transcription server returns text without a duration.
SPEECH_RATE = 4.0


class _Timed(list):
    """The (start, end, text) list from the ASR server; ``estimated`` is True when the
    server gave no timestamps and ``_sentences`` made them up."""
    estimated = False


class ASRError(Exception):
    """The speech-to-text server answered, but not with a usable transcript."""


def _sentences(text: str, duration: float) -> list[tuple[float, float, str]]:
    """Splits a transcript without timestamps at sentence ends, spreading the time
    by length, so a whole session is never one line that every citation points to."""
    parts = [p for p in re.findall(r"[^。！？!?]+[。！？!?]?", text) if p.strip()]
    total = sum(len(p) for p in parts) or 1
    if duration <= 0:
        # The server gave no length. Without one every line would start at 0:00 and
        # "jump to this line" would go nowhere, so estimate from the speaking rate.
        duration = total / SPEECH_RATE
    out, t = [], 0.0
    for p in parts:
        d = duration * len(p) / total
        out.append((t, t + d, p))
        t += d
    return out


def _faster_whisper(audio_path: Path) -> list[tuple[float, float, str]]:
    raw_segments, _info = _whisper_model().transcribe(str(audio_path), language="zh", vad_filter=True, initial_prompt=asr_prompt() or None)
    return [(s.start, s.end, s.text) for s in raw_segments]


def _server_reason(e: urllib.error.HTTPError) -> str:
    """The server's own one-line reason (vLLM puts it in error.message), so a refused
    upload says why, e.g. "Audio exceeds maximum allowed duration of 600s"."""
    try:
        out = json.loads(e.read(4096) or b"{}")
        msg = out.get("error", {}).get("message") if isinstance(out.get("error"), dict) else out.get("error")
    except (ValueError, AttributeError, OSError):
        return ""
    return f": {' '.join(str(msg).split())[:300]}" if msg else ""


def _openai_server(audio_path: Path) -> list[tuple[float, float, str]]:
    """Sends the audio to a local server with the OpenAI transcription API (vLLM serving
    Whisper on the B70). The same local-only rule as the note model applies.

    The whole file is read into memory to build the multipart body (the upload
    itself is streamed to disk by the server, but this request is not streamed).
    A 200 MB recording therefore needs roughly twice that in memory while it is
    sent; four rooms uploading at once on a small machine would feel it."""
    from .llm import _OPENER, assert_local

    assert_local(config.ASR_URL)
    boundary = uuid.uuid4().hex
    fields = {"model": config.ASR_MODEL, "language": "zh", "response_format": "verbose_json"}
    if asr_prompt():
        fields["prompt"] = asr_prompt()
    body = b"".join(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode() for k, v in fields.items()
    ) + (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio{audio_path.suffix}\"\r\n"
        f"Content-Type: application/octet-stream\r\n\r\n"
    ).encode() + audio_path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"{config.ASR_URL.rstrip('/')}/v1/audio/transcriptions",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with _OPENER.open(req, timeout=config.ASR_TIMEOUT) as r:
            out = json.load(r)
    except urllib.error.HTTPError as e:  # an answer, not a connection failure
        raise ASRError(f"HTTP {e.code}" + _server_reason(e)) from e
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ASRError("reply is not JSON") from e
    if not isinstance(out, dict):
        raise ASRError("reply is not a JSON object")
    segments = out.get("segments")
    if isinstance(segments, list) and segments:
        try:
            return [(float(s["start"]), float(s["end"]), str(s.get("text") or "")) for s in segments]
        except (KeyError, TypeError, ValueError) as e:
            raise ASRError("segments without start, end or text") from e
    if isinstance(out.get("text"), str) and out["text"].strip():
        try:
            duration = float(out.get("duration") or 0.0)
        except (TypeError, ValueError):
            duration = 0.0
        timed = _Timed(_sentences(out["text"], duration))
        timed.estimated = True
        return timed
    raise ASRError("no transcript in the reply")


# Sign-offs Whisper writes over silence or noise, learned from video captions. Measured on the
# B70 (2026-11-27, L50): 「請不吝點贊 訂閱 轉發 打賞支援明鏡與點點欄目」 at the end of a recording,
# 216 characters of the hybrid's remaining errors. Only whole known phrases are removed, so
# real speech is never cut; a segment left with nothing but punctuation is dropped.
HALLUCINATIONS = [re.compile(p) for p in (
    r"請不吝[點点][贊赞讚]\s*[訂订][閱阅]\s*[轉转][發发]\s*打[賞赏]支[持援]明[鏡镜][與与][點点][點点][欄栏]目",
    r"明[鏡镜][與与][點点][點点][欄栏]目",
    r"字幕由\s*Amara\.org\s*社[群区區]提供",
    r"[優优]{2}[獨独][播]劇場.*?YoYo\s*Television\s*Series\s*Exclusive",
)]


def drop_hallucinations(text: str) -> str:
    """``text`` without known Whisper hallucinations; "" when nothing but punctuation is left."""
    for p in HALLUCINATIONS:
        text = p.sub("", text)
    text = text.strip()
    return text if re.search(r"[^\s\W_]", text) else ""


def transcribe_audio(audio_path: Path, session_id: str, client_id: str, session_no: int, date: str, simulated: bool = True) -> Transcript:
    """Speech to timestamped segments. Speakers are left "unknown" for the speaker agent."""
    raw = _openai_server(audio_path) if config.ASR_BACKEND == "openai" else _faster_whisper(audio_path)
    # Number only the segments with words, so ids stay 1, 2, 3… for citations. Known Whisper
    # hallucinations (video-caption sign-offs it writes over silence) are cut first.
    spoken = [(start, end, t) for start, end, text in raw if (t := drop_hallucinations(_clean(text)))]
    segments = [
        Segment(id=i, start=round(start, 1), end=round(end, 1), speaker="unknown", text=text, speaker_source="none")
        for i, (start, end, text) in enumerate(spoken, start=1)
    ]
    if config.ASR_REFINE:  # Qwen3-ASR's words in Whisper's segments (haven/asr_refine.py)
        from .asr_refine import refine
        refine(audio_path, segments)
    return Transcript(session_id, client_id, session_no, date, segments, simulated=simulated,
                      timestamps_estimated=bool(getattr(raw, "estimated", False)))


def save_transcript(t: Transcript) -> Path:
    path = config.SESSIONS_DIR / f"{t.session_id}.json"
    payload = {
        "session_id": t.session_id,
        "client_id": t.client_id,
        "session_no": t.session_no,
        "date": t.date,
        "simulated": t.simulated,
        "segments": [to_dict(s) for s in t.segments],
    }
    if t.input_mode != "session":  # a file without the key is a session
        payload["input_mode"] = t.input_mode
    if t.timestamps_estimated:  # a file without the key has real timestamps
        payload["timestamps_estimated"] = True
    from .files import write_private

    write_private(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return path
