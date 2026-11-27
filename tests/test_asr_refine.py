"""Alignment hybrid (ticket 12, evaluation §3.1): Whisper's timed segments, Qwen3-ASR's text.

Two fake local servers: one answers like vLLM serving Whisper (verbose_json segments),
the other like vLLM serving Qwen3-ASR (json, text only, 400 for verbose_json)."""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("HAVEN_LLM", "demo")

from haven import asr_refine, config, textutil, transcript  # noqa: E402
from haven.agents.verifier import Verifier  # noqa: E402
from haven.llm import NonLocalEndpointError  # noqa: E402
from haven.models import Note, NoteSentence, to_dict  # noqa: E402

WHISPER = {"segments": [
    {"start": 0.0, "end": 4.0, "text": "有時候會覺得活著好累。"},
    {"start": 4.0, "end": 8.0, "text": "這些想法多長出現？"},  # Whisper's mistake: 長 for 常
    {"start": 8.0, "end": 12.0, "text": "大概每天晚上。"},
]}
QWEN = {"text": "有時候會覺得活著好累。這些想法多常出現？大概每天晚上。"}
OLD_KEYS = ["id", "start", "end", "speaker", "text", "speaker_source"]


def _handler():
    class Fake(BaseHTTPRequestHandler):
        seen: list = []
        reply = (200, {})

        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            type(self).seen.append({"path": self.path, "body": body})
            status, out = type(self).reply
            data = out if isinstance(out, bytes) else json.dumps(out, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass
    return Fake


def _field(body: bytes, name: str) -> str:
    marker = f'name="{name}"\r\n\r\n'.encode()
    return body.split(marker, 1)[1].split(b"\r\n", 1)[0].decode() if marker in body else ""


class HybridTest(unittest.TestCase):
    def setUp(self):
        self.Whisper, self.Qwen = _handler(), _handler()
        self.Whisper.seen, self.Qwen.seen = [], []
        self.Whisper.reply, self.Qwen.reply = (200, WHISPER), (200, QWEN)
        self.servers = [ThreadingHTTPServer(("127.0.0.1", 0), h) for h in (self.Whisper, self.Qwen)]
        for s in self.servers:
            threading.Thread(target=s.serve_forever, daemon=True).start()
        self.saved = (config.ASR_BACKEND, config.ASR_URL, config.ASR_REFINE, config.ASR_REFINE_URL, config.ASR_REFINE_MODEL)
        config.ASR_BACKEND, config.ASR_URL = "openai", f"http://127.0.0.1:{self.servers[0].server_port}"
        config.ASR_REFINE, config.ASR_REFINE_URL = True, f"http://127.0.0.1:{self.servers[1].server_port}"
        config.ASR_REFINE_MODEL = "qwen3-asr"
        self.audio = Path(tempfile.mkdtemp()) / "a.ogg"
        self.audio.write_bytes(b"OggS-fake-audio")

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        (config.ASR_BACKEND, config.ASR_URL, config.ASR_REFINE, config.ASR_REFINE_URL, config.ASR_REFINE_MODEL) = self.saved

    def run_asr(self):
        return transcript.transcribe_audio(self.audio, "T-S01", "C001", 1, "2026-11-08")

    def assert_times_are_whispers(self, t):
        whisper = [(i, s["start"], s["end"]) for i, s in enumerate((s for s in self.Whisper.reply[1]["segments"] if s["text"].strip()), start=1)]
        self.assertEqual([(s.id, s.start, s.end) for s in t.segments], whisper)

    # ---- acceptance: normal case ----

    def test_text_from_qwen_ids_and_times_from_whisper(self):
        t = self.run_asr()
        self.assertEqual([s.text for s in t.segments], ["有時候會覺得活著好累。", "這些想法多常出現？", "大概每天晚上。"])
        self.assert_times_are_whispers(t)
        self.assertEqual([s.asr_engine for s in t.segments], ["qwen3-asr"] * 3)
        self.assertEqual([s.whisper_text for s in t.segments], [s["text"] for s in WHISPER["segments"]])
        self.assertTrue(all(s.speaker == "unknown" and s.speaker_source == "none" for s in t.segments))

    def test_requests_whisper_verbose_json_and_qwen_json(self):
        self.run_asr()
        (w,), (q,) = self.Whisper.seen, self.Qwen.seen
        self.assertEqual(_field(w["body"], "response_format"), "verbose_json")  # evaluation §0 finding 2
        self.assertEqual(q["path"], "/v1/audio/transcriptions")
        self.assertEqual(_field(q["body"], "response_format"), "json")  # verbose_json answers 400
        self.assertEqual(_field(q["body"], "model"), "qwen3-asr")
        self.assertEqual(_field(q["body"], "language"), "zh")
        self.assertIn(b"OggS-fake-audio", q["body"])

    def test_engine_and_whisper_text_survive_save_and_load(self):
        from helpers import IsolatedData
        with IsolatedData():
            t = self.run_asr()
            transcript.save_transcript(t)
            back = transcript.load_transcript("T-S01")
        self.assertEqual([(s.text, s.asr_engine, s.whisper_text) for s in back.segments],
                         [(s.text, s.asr_engine, s.whisper_text) for s in t.segments])
        self.assertIn("asr_engine", to_dict(back.segments[0]))

    def test_half_width_punctuation_from_qwen_is_normalised(self):
        self.Qwen.reply = (200, {"text": "有時候會覺得活著好累,這些想法多常出現?大概每天晚上。"})
        t = self.run_asr()
        self.assertEqual(t.segments[0].text, "有時候會覺得活著好累，")
        self.assertEqual(t.segments[1].text, "這些想法多常出現？")

    # ---- acceptance: Qwen3-ASR fails, the whole transcript keeps Whisper's text ----

    def test_qwen_failure_keeps_whisper_text_everywhere(self):
        for reply in ((500, b"<html>oops</html>"), (400, {"error": {"message": "Currently do not support verbose_json"}}),
                      (200, b"not json"), (200, {"text": ""}), (200, {"text": "。。"}), (200, []), (200, {"text": None})):
            self.Qwen.reply = reply
            t = self.run_asr()
            self.assertEqual([s.text for s in t.segments], [s["text"] for s in WHISPER["segments"]], msg=str(reply))
            self.assertEqual([s.asr_engine for s in t.segments], ["whisper"] * 3, msg=str(reply))
            self.assertEqual([s.whisper_text for s in t.segments], [None] * 3)
            self.assert_times_are_whispers(t)

    def test_qwen_server_down_keeps_whisper_text(self):
        self.servers[1].shutdown()
        self.servers[1].server_close()
        t = self.run_asr()
        self.assertEqual([s.text for s in t.segments], [s["text"] for s in WHISPER["segments"]])
        self.assertEqual([s.asr_engine for s in t.segments], ["whisper"] * 3)

    def test_unrelated_qwen_text_keeps_whisper_text(self):
        self.Qwen.reply = (200, {"text": "今天天氣很好，我們去公園散步吧。"})
        t = self.run_asr()
        self.assertEqual([s.asr_engine for s in t.segments], ["whisper"] * 3)

    def test_refine_server_must_be_local(self):
        config.ASR_REFINE_URL = "https://api.openai.com"
        with self.assertRaises(NonLocalEndpointError):
            self.run_asr()

    # ---- acceptance: a segment that cannot be aligned keeps Whisper's text ----

    def test_segment_qwen_did_not_hear_keeps_whisper_text(self):
        # Whisper's well-known hallucination over silence; Qwen3-ASR has nothing there.
        self.Whisper.reply = (200, {"segments": WHISPER["segments"][:2] + [
            {"start": 8.0, "end": 9.0, "text": "字幕由社群提供"}] + WHISPER["segments"][2:] + [
            {"start": 12.0, "end": 14.0, "text": "謝謝收看。"}]})
        t = self.run_asr()
        self.assertEqual([s.text for s in t.segments],
                         ["有時候會覺得活著好累。", "這些想法多常出現？", "字幕由社群提供", "大概每天晚上。", "謝謝收看。"])
        self.assertEqual([s.asr_engine for s in t.segments], ["qwen3-asr", "qwen3-asr", "whisper", "qwen3-asr", "whisper"])
        self.assertIsNone(t.segments[2].whisper_text)
        self.assert_times_are_whispers(t)

    def test_segment_with_no_common_words_keeps_whisper_text(self):
        self.Whisper.reply = (200, {"segments": WHISPER["segments"][:2] + [
            {"start": 8.0, "end": 9.0, "text": "嗯嗯嗯嗯"}] + WHISPER["segments"][2:]})
        self.Qwen.reply = (200, {"text": "有時候會覺得活著好累。這些想法多常出現？好的好的。大概每天晚上。"})
        t = self.run_asr()
        self.assertEqual(t.segments[2].text, "嗯嗯嗯嗯")
        self.assertEqual(t.segments[2].asr_engine, "whisper")
        self.assertEqual(t.segments[3].text, "大概每天晚上。")

    # ---- acceptance: an extra 「。」 at a 30-second seam (evaluation §0.2 limitation 4) ----

    SEAM = {"segments": [
        {"start": 22.0, "end": 26.0, "text": "我想問你一件事，"},
        {"start": 26.0, "end": 29.6, "text": "我從來沒有想過要傷害自己"},
        {"start": 29.6, "end": 33.0, "text": "那種感覺很難受。"},
    ]}

    def test_stray_seam_full_stops_are_dropped(self):
        self.Whisper.reply = (200, self.SEAM)
        self.Qwen.reply = (200, {"text": "我想問你一件事，我從來沒有。想過要傷害自己。那種感覺。很難受。"})
        t = self.run_asr()
        self.assertEqual([s.text for s in t.segments], ["我想問你一件事，", "我從來沒有想過要傷害自己", "那種感覺很難受。"])
        self.assertEqual([s.asr_engine for s in t.segments], ["qwen3-asr"] * 3)
        # The denial stays in one clause, so the negation rules still see it.
        text = t.segments[1].text
        self.assertTrue(textutil.denied(text, text.index("傷害")))

    def test_full_stops_away_from_a_seam_are_kept(self):
        self.Whisper.reply = (200, {"segments": [{"start": 2.0, "end": 6.0, "text": "我很累我睡不著"}]})
        self.Qwen.reply = (200, {"text": "我很累。我睡不著。"})
        t = self.run_asr()
        self.assertEqual(t.segments[0].text, "我很累。我睡不著。")

    def test_full_stop_whisper_also_has_is_kept_at_a_seam(self):
        self.Whisper.reply = (200, {"segments": [{"start": 28.0, "end": 32.0, "text": "我很累。我睡不著"}]})
        self.Qwen.reply = (200, {"text": "我很累。我睡不著。"})
        t = self.run_asr()
        self.assertEqual(t.segments[0].text, "我很累。我睡不著。")

    def test_no_segment_is_empty_or_only_punctuation(self):
        self.Whisper.reply = (200, {"segments": [
            {"start": 27.0, "end": 29.8, "text": "我覺得"},
            {"start": 29.8, "end": 30.4, "text": "嗯"},
            {"start": 30.4, "end": 33.0, "text": "很難受"},
        ]})
        self.Qwen.reply = (200, {"text": "。我覺得。。很難受。"})
        t = self.run_asr()
        for s in t.segments:
            self.assertRegex(s.text, r"[㐀-鿿A-Za-z0-9]", msg=s.text)
            self.assertNotRegex(s.text, r"^[，。、！？；：]", msg=s.text)
            self.assertNotIn("。。", s.text)
        self.assertEqual([s.text for s in t.segments], ["我覺得", "嗯", "很難受。"])
        self.assertEqual([s.asr_engine for s in t.segments], ["qwen3-asr", "whisper", "qwen3-asr"])

    def test_note_citing_a_seam_segment_verifies(self):
        self.Whisper.reply = (200, self.SEAM)
        self.Qwen.reply = (200, {"text": "我想問你一件事，我從來沒有。想過要傷害自己。那種感覺。很難受。"})
        t = self.run_asr()
        for s in t.segments:
            s.speaker, s.speaker_source = "client", "confirmed"
        note = Note("SOAP", {"S": [NoteSentence("個案表示那種感覺很難受。", [3])]}, "test")
        Verifier().run(note, t)
        self.assertEqual(note.sections["S"][0].status, "supported", note.sections["S"][0].reason)

    # ---- acceptance: flag off behaves exactly as before ----

    def test_flag_off_sends_nothing_to_qwen_and_adds_no_fields(self):
        config.ASR_REFINE = False
        t = self.run_asr()
        self.assertEqual(self.Qwen.seen, [])
        self.assertEqual([s.text for s in t.segments], [s["text"] for s in WHISPER["segments"]])
        for s in t.segments:  # no refine fields; ticket 06's edit fields are not ours to pin
            self.assertEqual([k for k in to_dict(s) if k in ("asr_engine", "whisper_text")], [])
            self.assertEqual(list(to_dict(s))[:len(OLD_KEYS)], OLD_KEYS)
        from helpers import IsolatedData
        with IsolatedData():
            path = transcript.save_transcript(t)
            saved = path.read_text(encoding="utf-8")
        expected = {"session_id": "T-S01", "client_id": "C001", "session_no": 1, "date": "2026-11-08", "simulated": True,
                    "segments": [{"id": i, "start": s["start"], "end": s["end"], "speaker": "unknown", "text": s["text"],
                                  "speaker_source": "none", "asr_text": None, "edited_by": "", "edited_at": ""}  # ticket 06's edit fields
                                 for i, s in enumerate(WHISPER["segments"], start=1)]}
        self.assertEqual(saved, json.dumps(expected, ensure_ascii=False, indent=2))

    def test_flag_is_off_by_default(self):
        import subprocess
        import sys
        env = {k: v for k, v in os.environ.items() if not k.startswith("HAVEN_ASR_REFINE")}
        out = subprocess.run([sys.executable, "-c", "from haven import config as c; print(c.ASR_REFINE, c.ASR_REFINE_URL, c.ASR_REFINE_MODEL)"],
                             cwd=Path(__file__).resolve().parent.parent, env=env, capture_output=True, text=True, check=True)
        self.assertEqual(out.stdout.split(), ["False", "http://127.0.0.1:8002", "qwen3-asr"])


@unittest.skipUnless(transcript.converts_to_traditional(), "opencc not installed")
class SimplifiedQwenTest(unittest.TestCase):
    """Qwen3-ASR writes Simplified characters; the existing opencc step brings them to Traditional."""

    setUp, tearDown, run_asr = HybridTest.setUp, HybridTest.tearDown, HybridTest.run_asr

    def test_simplified_qwen_text_is_converted(self):
        self.Qwen.reply = (200, {"text": "有时候会觉得活着好累。这些想法多常出现？大概每天晚上。"})
        t = self.run_asr()
        self.assertEqual([s.text for s in t.segments], ["有時候會覺得活著好累。", "這些想法多常出現？", "大概每天晚上。"])
        self.assertEqual([s.asr_engine for s in t.segments], ["qwen3-asr"] * 3)


class AlignTest(unittest.TestCase):
    """align() itself, without servers."""

    def test_cut_follows_the_words_not_the_lengths(self):
        segs = [(0.0, 3.0, "我最近睡不好"), (3.0, 6.0, "半夜三點就醒來")]
        self.assertEqual(asr_refine.align(segs, "我最近都睡不好，半夜三點就會醒來。"), ["我最近都睡不好，", "半夜三點就會醒來。"])

    def test_unsure_cut_moves_to_nearby_punctuation(self):
        # Whisper put the boundary word in the wrong segment; Qwen3-ASR's comma says where the clause ends.
        segs = [(0.0, 3.0, "我最近睡不好半"), (3.0, 6.0, "也三點就醒來")]
        self.assertEqual(asr_refine.align(segs, "我最近睡不好，半夜三點就醒來。"), ["我最近睡不好，", "半夜三點就醒來。"])

    def test_whole_l50_script_is_cut_back_into_its_segments(self):
        # The evaluation's local check (§3.1): "Whisper" gets one character in 15 wrong,
        # Qwen3-ASR's text is the script; every segment must get exactly its own line back.
        script = transcript.load_transcript("C002-L50").segments
        segs, n = [], 0
        for s in script:
            chars = []
            for c in s.text:
                if asr_refine._PLAIN.fullmatch(c):
                    n += 1
                    c = "某" if n % 15 == 0 else c
                chars.append(c)
            segs.append((s.id * 5.0, s.id * 5.0 + 4.5, "".join(chars)))
        pieces = asr_refine.align(segs, "".join(s.text for s in script))
        self.assertEqual(pieces, [s.text for s in script])

    def test_nothing_in_common_gives_nothing(self):
        self.assertEqual(asr_refine.align([(0.0, 2.0, "你好")], "再見"), [None])
        self.assertEqual(asr_refine.align([(0.0, 2.0, "你好")], ""), [None])


if __name__ == "__main__":
    unittest.main()
