"""Ticket 07: regenerate and confirm guards (spec AC-07, AC-08, §9.3 rules 1, 3-6)."""

import json
import sqlite3
import threading
import unittest

from helpers import H, ServerCase

from haven import config, signing, store

LINES = [("client", "最近睡不好，每天大概只睡四個小時。"), ("therapist", "睡不好的時候你都在想什麼？"),
         ("client", "我一直想工作的事情，覺得自己做不好。"), ("therapist", "我們下次一起來看看工作壓力的來源。")]


def plain(view, extra=None):
    """The note as the page sends it back; ``extra`` is a list of (section, sentence dict)."""
    sections = {k: [{"text": s["text"], "evidence": s["evidence"], "source": s["source"]} for s in v]
                for k, v in view["note"]["sections"].items()}
    for key, sentence in extra or []:
        sections.setdefault(key, []).append(sentence)
    return {"template": view["note"]["template"], "sections": sections}


class RegenerateGuardTest(ServerCase, unittest.TestCase):

    def open(self, sid):
        self.data.add_session(sid, LINES, session_no=30)
        status, view = self.call(f"/api/sessions/{sid}/open", {})
        self.assertEqual(status, 200, view)
        return view

    def edit_draft(self, sid, view, text="心理師自己補上的一句。"):
        body = dict(plain(view, [("S", {"text": text, "evidence": [], "origin": "ai"})]), base_revision=view["revision"])
        status, saved = self.call(f"/api/sessions/{sid}/verify", body)
        self.assertEqual(status, 200, saved)
        return saved

    def process(self, sid, base, **extra):
        return self.call(f"/api/sessions/{sid}/process", dict({"base_revision": base}, **extra))

    def test_regenerate_without_edits_needs_no_discard(self):
        view = self.open("C001-S30")
        status, r = self.process("C001-S30", view["revision"])
        self.assertEqual(status, 200, r)
        self.assertEqual(r["superseded_drafts"], [])
        self.assertEqual(r["revision"], view["revision"] + 1)

    def test_base_revision_is_required_and_stale_one_conflicts(self):
        view = self.open("C001-S31")
        self.assertEqual(self.call("/api/sessions/C001-S31/process", {"base_revision": None})[0], 400)
        saved = self.edit_draft("C001-S31", view)
        status, body = self.process("C001-S31", view["revision"], discard_edits=True)  # the page has not seen the save
        self.assertEqual(status, 409, body)
        self.assertTrue(body["conflict"])
        self.assertEqual(store.get_result("C001-S31")["revision"], saved["revision"])

    def test_first_draft_needs_no_base_revision(self):
        self.data.add_session("C001-S32", LINES, session_no=31)
        status, r = self.call("/api/sessions/C001-S32/process", {"base_revision": None})
        self.assertEqual(status, 200, r)

    def test_edits_block_regenerate_until_discard_and_the_old_draft_is_kept(self):  # AC-07
        view = self.open("C001-S33")
        saved = self.edit_draft("C001-S33", view)
        before = store.get_result("C001-S33")["note"]
        status, body = self.process("C001-S33", saved["revision"])
        self.assertEqual(status, 409, body)
        self.assertTrue(body["has_edits"])
        self.assertIn("先前版本", body["message_zh"])
        self.assertEqual(store.get_result("C001-S33")["note"], before)  # nothing changed
        status, r = self.process("C001-S33", saved["revision"], discard_edits=True)
        self.assertEqual(status, 200, r)
        self.assertEqual(len(r["superseded_drafts"]), 1)
        old = r["superseded_drafts"][0]
        self.assertEqual((old["note"], old["revision"]), (before, saved["revision"]))
        self.assertTrue(old["superseded_at"])
        self.assertNotIn("心理師自己補上的一句。", json.dumps(r["note"], ensure_ascii=False))
        with sqlite3.connect(config.DB_FILE) as c:
            rows = [x[0] for x in c.execute("SELECT detail FROM audit_log WHERE action = 'regenerate_discarded_edits'")]
        self.assertTrue(any("C001-S33" in x for x in rows))
        self.assertFalse(any("自己補上" in x for x in rows))  # ids and counts only
        # The new draft has no edits of its own: regenerating it again needs no discard.
        self.assertEqual(self.process("C001-S33", r["revision"])[0], 200)

    def test_only_three_superseded_drafts_are_kept(self):
        view = self.open("C001-S34")
        rev = view["revision"]
        for i in range(5):
            saved = self.edit_draft("C001-S34", {"note": store.get_result("C001-S34")["note"], "revision": rev}, f"第 {i} 版自己寫的句子。")
            status, r = self.process("C001-S34", saved["revision"], discard_edits=True)
            self.assertEqual(status, 200, r)
            rev = r["revision"]
        self.assertEqual(len(r["superseded_drafts"]), 3)
        texts = [json.dumps(d["note"], ensure_ascii=False) for d in r["superseded_drafts"]]
        self.assertIn("第 4 版", texts[-1])
        self.assertNotIn("第 0 版", "".join(texts))

    def test_origin_is_computed_by_the_server(self):
        view = self.open("C001-S35")
        first = view["note"]["sections"]["S"][0]
        sent = plain(view, [("S", {"text": "心理師自己寫的。", "evidence": [], "origin": "ai"})])
        sent["sections"]["S"][0]["origin"] = "human"  # a lie about an untouched AI sentence
        sent["sections"]["S"][1:2] = [dict(sent["sections"]["S"][1], text=sent["sections"]["S"][1]["text"] + "（改過）", origin="ai")] \
            if len(sent["sections"]["S"]) > 1 else []
        status, saved = self.call("/api/sessions/C001-S35/verify", {**sent, "base_revision": view["revision"]})
        self.assertEqual(status, 200, saved)
        got = saved["note"]["sections"]["S"]
        self.assertEqual(got[0]["text"], first["text"])
        self.assertEqual(got[0]["origin"], "ai")
        self.assertEqual(got[-1]["origin"], "human")
        if len(got) > 2:
            self.assertEqual(got[1]["origin"], "ai_edited")

    def test_confirm_refused_while_the_review_is_pending_then_allowed(self):  # AC-08
        view = self.open("C001-S36")
        seg = next(e for items in view["note"]["sections"].values() for t in items for e in t["evidence"])
        status, after = self.call("/api/sessions/C001-S36/transcript", {"segments": {str(seg): LINES[0][1] + "嗯。"}, "base_revision": view["revision"]})
        self.assertEqual(status, 200, after)
        sign = {"signed_by": "王心理師", "note": plain(after), "base_revision": after["revision"]}
        status, body = self.call("/api/sessions/C001-S36/sign", sign)
        self.assertEqual(status, 409, body)
        self.assertTrue(body["needs_review"])
        self.assertIsNone(store.get_signature("C001-S36"))
        self.assertEqual(self.call("/api/sessions/C001-S36/sign", dict(sign, base_revision=None))[0], 400)
        status, done = self.call("/api/sessions/C001-S36/reviewed", {"base_revision": after["revision"]})
        self.assertEqual(status, 200, done)
        status, body = self.call("/api/sessions/C001-S36/sign", dict(sign, base_revision=done["revision"]))
        self.assertEqual(status, 200, body)
        signed = store.get_signed_note("C001-S36")
        self.assertEqual(signed["transcript_revision"], 1)
        self.assertEqual(body["transcript_sha256"], signing.transcript_sha256("C001-S36"))
        self.assertTrue(all(s["origin"] == "ai" for items in signed["sections"].values() for s in items))

    def test_stale_base_revision_is_refused_on_sign(self):
        view = self.open("C001-S37")
        saved = self.edit_draft("C001-S37", view)
        status, body = self.call("/api/sessions/C001-S37/sign", {"signed_by": "王", "note": plain(view), "base_revision": view["revision"]})
        self.assertEqual((status, body.get("conflict")), (409, True))
        self.assertIsNone(store.get_signature("C001-S37"))
        self.assertGreater(saved["revision"], view["revision"])

    def test_sign_and_transcript_edit_at_the_same_time_are_serialized(self):
        for n in range(6):
            sid = f"C001-S4{n}"
            view = self.open(sid)
            seg = view["transcript"][0]["id"]
            sign = {"signed_by": "王心理師", "note": plain(view), "base_revision": view["revision"]}
            edit = {"segments": {str(seg): LINES[0][1] + "嗯嗯。"}, "base_revision": view["revision"]}
            out, gate = {}, threading.Barrier(2)

            def run(name, path, body):
                gate.wait()
                out[name] = self.call(f"/api/sessions/{sid}/{path}", body)

            threads = [threading.Thread(target=run, args=("sign", "sign", sign)), threading.Thread(target=run, args=("edit", "transcript", edit))]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            signature = store.get_signature(sid)
            on_disk = signing.transcript_sha256(sid)
            if signature:
                # Signed first: the correction found a signed note and was refused; the file is as it was signed.
                self.assertEqual(out["sign"][0], 200, out)
                self.assertEqual(out["edit"][0], 409, out)
                self.assertEqual(signature["transcript_sha256"], on_disk)
            else:
                # The correction came first: the draft had changed under the sign request.
                self.assertEqual(out["edit"][0], 200, out)
                self.assertEqual(out["sign"][0], 409, out)


if __name__ == "__main__":
    unittest.main()


class RegenerateIsAJobTest(ServerCase, unittest.TestCase):
    """POST .../process answers 202 with a "regenerate" job (spec §10 G1); guards answer at once."""

    def test_process_answers_202_and_the_job_drafts(self):
        import urllib.request
        from haven import jobs
        self.data.add_session("C001-S40", LINES, session_no=40)
        req = urllib.request.Request(self.base + "/api/sessions/C001-S40/process", data=b"{}", headers=H, method="POST")
        with urllib.request.urlopen(req) as r:
            self.assertEqual(r.status, 202)
            job = json.load(r)
        self.assertEqual((job["kind"], job["session_id"]), ("regenerate", "C001-S40"))
        self.assertEqual(jobs.wait(job["job_id"])["status"], "succeeded")
        self.assertIsNotNone(store.get_result("C001-S40"))
