"""Ticket 04: post-session recap mode (input_mode=recap): no speaker agent, every line the
therapist's, drafted in the dictation voice, checked by a recap rule in the verifier."""

import json
import unittest
from unittest import mock

from helpers import IsolatedData, ServerCase

from haven import recap, store
from haven.agents.verifier import VERIFIER_VERSION, Verifier
from haven.agents.writer import NoteWriter, _rule_based
from haven.models import Note, NoteSentence, Segment, Transcript
from haven.transcript import load_transcript
from test_server_robust import UploadMixin, fake_transcribe

DICTATION = ("這次是第五次會談。\n個案表示這週睡得很差，常常半夜醒來。\n我先帶個案做了呼吸練習。\n"
             "下週請個案繼續記錄睡眠，我會建議下次再討論。")


def recap_transcript(*lines):
    segs = [Segment(i, float(i), float(i + 1), "therapist", t, speaker_source="recap") for i, t in enumerate(lines, start=1)]
    return Transcript("C001-S60", "C001", 60, "2026-11-12", segs, input_mode="recap")


class RecapUploadTest(UploadMixin, ServerCase, unittest.TestCase):

    def run_upload(self, sid, extra="&simulated=1&input_mode=recap"):
        with mock.patch("haven.server.transcribe_audio", fake_transcribe()):
            return self.upload(sid, DICTATION, extra=extra)

    def test_recap_upload_skips_the_speaker_agent_and_labels_every_line(self):
        with mock.patch.object(self.__class__.__mro__[0], "_unused", create=True), \
                mock.patch("haven.server.ORCH.speakers.run", side_effect=AssertionError("SpeakerAgent was called")) as speakers:
            status, page = self.run_upload("C001-S61")
        self.assertEqual(status, 200, page)
        speakers.assert_not_called()
        self.assertEqual(page["input_mode"], "recap")
        self.assertTrue(page["transcript"])
        for seg in page["transcript"]:
            self.assertEqual((seg["speaker"], seg["speaker_source"]), ("therapist", "recap"))
        stored = json.loads((self.data.dir / "sessions" / "C001-S61.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["input_mode"], "recap")
        self.assertEqual(page["note"]["template"], "QUAL")  # a recap is drafted as QUAL by default

    def test_recap_draft_is_in_dictation_voice_and_verifies(self):
        status, page = self.run_upload("C001-S62")
        self.assertEqual(status, 200, page)
        sentences = [s for items in page["note"]["sections"].values() for s in items if s["source"] == "transcript"
                     and s["text"] != "待心理師填寫"]
        self.assertTrue(sentences)
        for s in sentences:
            self.assertTrue(s["text"].startswith("心理師"), s["text"])
            self.assertFalse(recap.attributes_to_client(s["text"]), s["text"])
            self.assertNotIn(s["status"], ("unsupported",), s)
        self.assertTrue(any(s["text"].startswith("心理師口述：") for s in sentences))

    def test_normal_upload_still_calls_the_speaker_agent(self):
        with mock.patch("haven.server.transcribe_audio", fake_transcribe()), \
                mock.patch("haven.server.ORCH.speakers.run", return_value=0) as speakers:
            self.upload("C001-S63", DICTATION)
        speakers.assert_called_once()
        self.assertEqual(load_transcript("C001-S63").input_mode, "session")

    def test_input_mode_must_be_session_or_recap(self):
        status, body = self.upload("C001-S64", DICTATION, extra="&simulated=1&input_mode=podcast")
        self.assertEqual(status, 400, body)
        self.assertIn("message_zh", body)
        status, _ = self.call("/api/recordings", {"client": "C001", "session_id": "C001-S65", "consent": True, "input_mode": "x"})
        self.assertEqual(status, 400)

    def test_recording_carries_input_mode_to_the_transcript(self):
        status, started = self.call("/api/recordings", {"client": "C001", "session_id": "C001-S66", "consent": True,
                                                        "simulated": True, "input_mode": "recap"})
        self.assertEqual(status, 200, started)
        from haven import server
        self.assertEqual(server.RECORDINGS[started["recording_id"]]["input_mode"], "recap")
        self.call(f"/api/recordings/{started['recording_id']}/cancel", {})

    def test_speakers_route_refuses_a_recap_with_409(self):
        status, page = self.run_upload("C001-S67")
        self.assertEqual(status, 200, page)
        status, body = self.call("/api/sessions/C001-S67/speakers",
                                 {"speakers": {"1": "client"}, "base_revision": page.get("revision", 0)})
        self.assertEqual(status, 409, body)
        self.assertTrue(body["message_zh"])
        self.assertEqual(load_transcript("C001-S67").segments[0].speaker, "therapist")

    def test_transcript_text_can_still_be_edited_in_a_recap(self):
        status, page = self.run_upload("C001-S68")
        status, body = self.call("/api/sessions/C001-S68/transcript",
                                 {"segments": {"1": "這次是第六次會談。"}, "base_revision": page.get("revision", 0)})
        self.assertEqual(status, 200, body)


class RecapRulesTest(unittest.TestCase):

    def test_label_sets_every_segment_without_a_model(self):
        t = Transcript("X-S1", "C001", 1, "2026-11-12", [Segment(1, 0, 1, "unknown", "a", speaker_source="none")])
        self.assertEqual(recap.label(t), 1)
        self.assertEqual((t.segments[0].speaker, t.segments[0].speaker_source), ("therapist", "recap"))

    def test_parse(self):
        self.assertEqual(recap.parse(None), "session")
        self.assertEqual(recap.parse(""), "session")
        self.assertEqual(recap.parse("recap"), "recap")
        with self.assertRaises(ValueError):
            recap.parse("dictation")

    def test_rule_engine_writes_recap_voice_and_never_attributes_to_the_client(self):
        t = recap_transcript("個案表示這週睡得很差，常常半夜醒來。", "個案說最近很焦慮，工作壓力很大。", "下週請個案繼續記錄睡眠。")
        draft = _rule_based(t, "QUAL")
        texts = [s.text for items in draft.values() for s in items]
        self.assertTrue(texts)
        for text in texts:
            self.assertTrue(text.startswith("心理師"), text)
            self.assertFalse(recap.attributes_to_client(text), text)
        self.assertTrue(any(x.startswith("心理師口述：個案這週睡得很差") for x in texts), texts)

    def test_writer_sends_the_recap_system_prompt_and_voice_instructions(self):
        seen = []

        class Spy:
            label = "spy"

            def generate_json(self, system, prompt, max_tokens=0):
                seen.append((system, prompt))
                return {"TOPIC": [{"text": "心理師口述：個案睡眠變差。", "evidence": [1]}]}

        t = recap_transcript("個案這週睡得很差，常常半夜醒來。")
        note = NoteWriter(Spy()).run(t, "QUAL")
        system, prompt = seen[0]
        self.assertIn("心理師口述", system)
        self.assertNotIn("寫「個案表示」的句子", system)
        self.assertIn("以「心理師口述…」開頭", prompt)
        self.assertNotIn("以「個案表示…」開頭", prompt)
        self.assertEqual(note.sections["TOPIC"][0].text, "心理師口述：個案睡眠變差。")

    def test_verifier_blocks_client_attribution_in_a_recap_only(self):
        sentences = lambda: [NoteSentence("個案表示這週睡得很差。", [1]), NoteSentence("心理師口述：個案這週睡得很差。", [1]),
                             NoteSentence("心理師口述：個案提到這週睡得很差。", [1])]  # the therapist reporting it: allowed
        t = recap_transcript("個案這週睡得很差，常常半夜醒來。")
        note = Note("QUAL", {"TOPIC": sentences()}, "test")
        Verifier(None).run(note, t)
        bad, good, reported = note.sections["TOPIC"]
        self.assertNotEqual(reported.status, "unsupported", reported.reason)
        self.assertEqual(bad.status, "unsupported")
        self.assertIn("口述", bad.reason)
        self.assertNotEqual(good.status, "unsupported", good.reason)
        # the same words in a full-session transcript with a client line are fine
        t2 = Transcript("C001-S60", "C001", 60, "2026-11-12", [Segment(1, 0, 1, "client", "我這週睡得很差，常常半夜醒來。")])
        note2 = Note("QUAL", {"TOPIC": [NoteSentence("個案表示這週睡得很差。", [1])]}, "test")
        Verifier(None).run(note2, t2)
        self.assertEqual(note2.sections["TOPIC"][0].status, "supported")

    def test_recap_lines_are_not_reported_as_guessed_speakers(self):
        t = recap_transcript("個案這週睡得很差，常常半夜醒來。")
        note = Note("QUAL", {"TOPIC": [NoteSentence("心理師口述：個案這週睡得很差，常常半夜醒來。", [1])]}, "test")
        Verifier(None).run(note, t)
        s = note.sections["TOPIC"][0]
        self.assertEqual(s.status, "supported", s.reason)

    def test_verifier_version_was_bumped(self):
        self.assertNotEqual(VERIFIER_VERSION, "2026.11-6")

    def test_old_transcript_without_the_key_is_a_session(self):
        with IsolatedData():
            self.assertEqual(load_transcript("C001-S03").input_mode, "session")


if __name__ == "__main__":
    unittest.main()
