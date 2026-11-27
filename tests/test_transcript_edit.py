"""Ticket 06: transcript corrections and the transcript revision (spec AC-05, AC-06)."""

import json
import sqlite3
import unittest

from helpers import ServerCase
from test_accounts import AccountCase

from haven import config


LINES = [("client", "最近睡不好，每天大概只睡四個小時。"), ("therapist", "睡不好的時候你都在想什麼？"),
         ("client", "我一直想工作的事情，覺得自己做不好。"), ("therapist", "我們下次一起來看看工作壓力的來源。")]


def file_of(sid):
    return json.loads((config.SESSIONS_DIR / f"{sid}.json").read_text(encoding="utf-8"))


def citing(view, seg_id):
    return [s for items in view["note"]["sections"].values() for s in items if seg_id in (s.get("evidence") or [])]


class TranscriptEditTest(ServerCase, unittest.TestCase):

    def open(self, sid, source="script"):
        self.data.add_session(sid, LINES, session_no=20, source=source)
        status, view = self.call(f"/api/sessions/{sid}/open", {})
        self.assertEqual(status, 200, view)
        return view

    def edit(self, sid, segments, base):
        return self.call(f"/api/sessions/{sid}/transcript", {"segments": segments, "base_revision": base})

    def test_edit_keeps_ids_and_times_and_the_asr_text(self):  # AC-05 happy path
        view = self.open("C001-S20")
        self.assertEqual(view["transcript_revision"], 0)
        self.assertEqual(view["review_status"], "editing")
        self.assertEqual(view["generated_from_transcript_revision"], 0)
        seg = next(s for s in view["transcript"] if citing(view, s["id"]))
        before = {s["text"] for s in citing(view, seg["id"])}
        status, after = self.edit("C001-S20", {str(seg["id"]): "最近睡不好，每天大概只睡三個小時。"}, view["revision"])
        self.assertEqual(status, 200, after)
        self.assertEqual(after["transcript_revision"], 1)
        self.assertEqual(after["revision"], view["revision"] + 1)
        self.assertEqual(after["review_status"], "needs_review")
        new = next(s for s in after["transcript"] if s["id"] == seg["id"])
        self.assertEqual((new["start"], new["end"]), (seg["start"], seg["end"]))
        self.assertEqual(new["text"], "最近睡不好，每天大概只睡三個小時。")
        self.assertEqual(new["asr_text"], seg["text"])
        self.assertTrue(new["edited_at"])
        # Sentences citing the line keep their words and are marked; the others are not.
        marked = citing(after, seg["id"])
        self.assertTrue(marked and all(s["source_changed"] for s in marked))
        self.assertEqual({s["text"] for s in marked}, before)
        others = [s for items in after["note"]["sections"].values() for s in items if s not in marked]
        self.assertFalse(any(s.get("source_changed") for s in others))
        # A second correction keeps the first ASR text.
        status, again = self.edit("C001-S20", {str(seg["id"]): "每天只睡兩個小時。"}, after["revision"])
        self.assertEqual(status, 200, again)
        self.assertEqual(again["transcript_revision"], 2)
        stored = {s["id"]: s for s in file_of("C001-S20")["segments"]}[seg["id"]]
        self.assertEqual(stored["asr_text"], seg["text"])
        self.assertEqual(file_of("C001-S20")["transcript_revision"], 2)
        with sqlite3.connect(config.DB_FILE) as c:
            rows = [r[0] for r in c.execute("SELECT detail FROM audit_log WHERE action = 'transcript_edited'")]
        self.assertTrue(rows)
        self.assertFalse(any("睡" in r for r in rows))  # ids and counts only

    def test_reviewed_clears_the_marks(self):
        view = self.open("C001-S21")
        seg_id = next(s["id"] for s in view["transcript"] if citing(view, s["id"]))
        _, after = self.edit("C001-S21", {str(seg_id): "改過的句子。"}, view["revision"])
        self.assertEqual(self.call("/api/sessions/C001-S21/reviewed", {})[0], 400)  # base_revision is required
        self.assertEqual(self.call("/api/sessions/C001-S21/reviewed", {"base_revision": view["revision"]})[0], 409)
        status, done = self.call("/api/sessions/C001-S21/reviewed", {"base_revision": after["revision"]})
        self.assertEqual(status, 200, done)
        self.assertEqual(done["review_status"], "editing")
        self.assertEqual(done["reviewed_against_transcript_revision"], 1)
        self.assertFalse(any(s["source_changed"] for items in done["note"]["sections"].values() for s in items))
        # Saving the draft afterwards does not bring the marks back.
        note = {"template": done["note"]["template"], "base_revision": done["revision"], "sections": {
            k: [{"text": s["text"], "evidence": s["evidence"], "source": s["source"]} for s in v]
            for k, v in done["note"]["sections"].items()}}
        status, saved = self.call("/api/sessions/C001-S21/verify", note)
        self.assertEqual(status, 200, saved)
        self.assertFalse(any(s["source_changed"] for items in saved["note"]["sections"].values() for s in items))

    def test_marks_survive_a_draft_save(self):
        view = self.open("C001-S22")
        seg_id = next(s["id"] for s in view["transcript"] if citing(view, s["id"]))
        _, after = self.edit("C001-S22", {str(seg_id): "改過的句子。"}, view["revision"])
        note = {"template": after["note"]["template"], "base_revision": after["revision"], "sections": {
            k: [{"text": s["text"], "evidence": s["evidence"], "source": s["source"]} for s in v]
            for k, v in after["note"]["sections"].items()}}
        status, saved = self.call("/api/sessions/C001-S22/verify", note)
        self.assertEqual(status, 200, saved)
        self.assertTrue(any(s["source_changed"] for s in citing(saved, seg_id)))
        _, reopened = self.call("/api/sessions/C001-S22/open", {})
        self.assertEqual(reopened["review_status"], "needs_review")
        self.assertTrue(all(s["source_changed"] for s in citing(reopened, seg_id)))

    def test_two_tabs_with_one_base_revision(self):  # AC-05 concurrency
        view = self.open("C001-S23")
        first = self.edit("C001-S23", {"1": "第一個分頁改的。"}, view["revision"])
        second = self.edit("C001-S23", {"2": "第二個分頁改的。"}, view["revision"])
        self.assertEqual(first[0], 200)
        self.assertEqual(second[0], 409)
        self.assertTrue(second[1]["conflict"])
        self.assertTrue(second[1]["message_zh"])
        self.assertEqual(file_of("C001-S23")["segments"][1]["text"], LINES[1][1])

    def test_bad_requests(self):  # AC-05 failure
        view = self.open("C001-S24")
        base = view["revision"]
        for body in ({"segments": {"99": "沒有這段"}, "base_revision": base},
                     {"segments": {"x": "不是數字"}, "base_revision": base},
                     {"segments": {"1": ""}, "base_revision": base},
                     {"segments": {"1": "長" * 2001}, "base_revision": base},
                     {"segments": {"1": {"text": "a", "start": 9}}, "base_revision": base},
                     {"segments": {}, "base_revision": base},
                     {"segments": {"1": "沒有 base_revision"}}):
            status, r = self.call("/api/sessions/C001-S24/transcript", body)
            self.assertEqual(status, 400, body)
            self.assertIn("message_zh", r)
        self.assertEqual(file_of("C001-S24").get("transcript_revision", 0), 0)
        self.assertEqual(self.edit("C001-S99", {"1": "x"}, 0)[0], 404)
        # 2000 characters is accepted.
        self.assertEqual(self.edit("C001-S24", {"1": "長" * 2000}, base)[0], 200)

    def test_signed_note_is_refused(self):  # AC-05 failure: confirmed
        r, note = self.draft("C002-S05")
        self.call("/api/sessions/C002-S05/verify", note)
        _, r = self.call("/api/sessions/C002-S05/open", {})
        self.assertEqual(self.call("/api/sessions/C002-S05/sign", {"signed_by": "王", "note": note})[0], 200)
        _, signed = self.call("/api/sessions/C002-S05/open", {})
        self.assertEqual(signed["review_status"], "confirmed")
        status, body = self.edit("C002-S05", {"1": "簽核後改"}, signed["revision"])
        self.assertEqual(status, 409, body)
        self.assertEqual(self.call("/api/sessions/C002-S05/reviewed", {"base_revision": signed["revision"]})[0], 409)

    def test_confirmed_speaker_can_be_changed_back(self):  # AC-06
        view = self.open("C001-S25", source="model")
        status, one = self.call("/api/sessions/C001-S25/speakers",
                                {"speakers": {"2": "client"}, "base_revision": view["revision"]})
        self.assertEqual(status, 200, one)
        seg = {s["id"]: s for s in one["transcript"]}[2]
        self.assertEqual((seg["speaker"], seg["speaker_source"]), ("client", "confirmed"))
        self.assertEqual(one["transcript_revision"], 1)
        self.assertEqual(one["review_status"], "needs_review")
        status, two = self.call("/api/sessions/C001-S25/speakers",
                                {"speakers": {"2": "therapist"}, "base_revision": one["revision"]})
        self.assertEqual(status, 200, two)
        seg = {s["id"]: s for s in two["transcript"]}[2]
        self.assertEqual((seg["speaker"], seg["speaker_source"]), ("therapist", "confirmed"))
        self.assertEqual(two["transcript_revision"], 2)
        self.assertEqual(two["verification"]["verifier_version"], view["verification"]["verifier_version"])
        self.assertTrue(all(s["source_changed"] for s in citing(two, 2)))
        # Confirming a label without changing it is not a transcript change.
        status, three = self.call("/api/sessions/C001-S25/speakers",
                                  {"speakers": {"1": "client"}, "base_revision": two["revision"]})
        self.assertEqual(status, 200, three)
        self.assertEqual(three["transcript_revision"], 2)
        # A stale page is refused here too.
        self.assertEqual(self.call("/api/sessions/C001-S25/speakers",
                                   {"speakers": {"2": "client"}, "base_revision": one["revision"]})[0], 409)

    def test_old_files_without_a_revision(self):
        view = self.open("C001-S26")
        self.assertNotIn("transcript_revision", file_of("C001-S26"))
        self.assertEqual(view["transcript_revision"], 0)
        self.assertEqual(view["reviewed_against_transcript_revision"], 0)


class EditSurvivesSignInTest(AccountCase, unittest.TestCase):
    """AC-05 persistence: sign out, sign in again, the correction is still there."""

    def test_edit_survives_signing_out_and_in(self):
        self.data.add_session("C001-S30", LINES, session_no=30)
        cookie = self.login("wang")
        status, view, _ = self.req("/api/sessions/C001-S30/open", {}, cookie=cookie)
        self.assertEqual(status, 200, view)
        status, after, _ = self.req("/api/sessions/C001-S30/transcript",
                                    {"segments": {"3": "我一直想工作的事。"}, "base_revision": view["revision"]}, cookie=cookie)
        self.assertEqual(status, 200, after)
        self.assertEqual({s["id"]: s for s in after["transcript"]}[3]["edited_by"], "王心理師")
        self.req("/api/logout", {}, cookie=cookie)
        cookie = self.login("wang")
        status, again, _ = self.req("/api/sessions/C001-S30/open", {}, cookie=cookie)
        self.assertEqual(status, 200, again)
        seg = {s["id"]: s for s in again["transcript"]}[3]
        self.assertEqual(seg["text"], "我一直想工作的事。")
        self.assertEqual(seg["asr_text"], LINES[2][1])
        self.assertEqual(again["transcript_revision"], 1)
        self.assertEqual(again["review_status"], "needs_review")
        # Someone else's session is not found.
        lin = self.login("lin")
        self.assertEqual(self.req("/api/sessions/C001-S30/transcript",
                                  {"segments": {"3": "x"}, "base_revision": again["revision"]}, cookie=lin)[0], 404)


if __name__ == "__main__":
    unittest.main()
