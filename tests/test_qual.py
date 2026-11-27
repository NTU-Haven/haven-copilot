"""Ticket 03: the single-session qualitative template (QUAL) and human_only fields."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helpers import ServerCase  # noqa: F401  (also sets HAVEN_LLM=demo)

from haven import human_fields, store, templates
from haven.agents.writer import SYSTEM_PROMPT, NoteWriter, _rule_based
from haven.llm import OpenAICompatLLM
from haven.transcript import load_transcript

QUAL = templates.get("QUAL")
HUMAN = [f for f in QUAL.fields if f.ai_mode == templates.HUMAN_ONLY]


class FakeChat(BaseHTTPRequestHandler):
    """An OpenAI-style chat server. It answers a note request with the draft of the
    rule engine (so the sentences verify), and fills a human_only key too, as a
    misbehaving model would. Every request body is recorded."""

    bodies: list[dict] = []
    session = "C001-S03"

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeChat.bodies.append(body)
        system = body["messages"][0]["content"]
        if "事實查核員" in system:  # the verifier paraphrase check
            reply = {"results": [{"id": i, "supported": True} for i in range(60)]}
        else:
            draft = _rule_based(load_transcript(FakeChat.session), "QUAL")
            reply = {k: [{"text": s.text, "evidence": s.evidence} for s in v] for k, v in draft.items()}
            reply["FORMULATION"] = [{"text": "個案有憂鬱傾向。", "evidence": [1]}]
        data = json.dumps({"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}]}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def fake_llm():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeChat)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, OpenAICompatLLM(url=f"http://127.0.0.1:{srv.server_port}", model="qwen3-32b-awq")


def sections_for_signing(r, therapist_text=None):
    """The note as the UI sends it, with placeholders rewritten when therapist_text has the field."""
    out = {}
    for k, items in r["note"]["sections"].items():
        out[k] = []
        for s in items:
            text = s["text"]
            if human_fields.is_placeholder(text) and therapist_text and k in therapist_text:
                text = therapist_text[k]
            out[k].append({"text": text, "evidence": s["evidence"], "source": s["source"]})
    return out


class QualDefinitionTest(unittest.TestCase):
    def test_definition(self):
        self.assertEqual((QUAL.id, QUAL.version, QUAL.name), ("QUAL", 1, "單次晤談質化紀錄"))
        self.assertIsNone(QUAL.risk_field)
        self.assertIsNone(QUAL.measures_field)
        self.assertEqual([f.ai_mode for f in QUAL.fields], ["generate"] * 3 + ["human_only"] * 2)
        self.assertEqual(templates.DEFAULT, "SOAP")
        for f in QUAL.generated:  # no diagnosis, risk grading, scale guessing or inference
            for word in ("診斷", "風險等級", "量表分數", "臨床推論"):
                self.assertIn(word, f.instructions)


class RequestsCarryNoHumanOnlyContentTest(unittest.TestCase):
    def test_human_only_fields_are_not_in_any_request(self):
        srv, llm = fake_llm()
        self.addCleanup(srv.shutdown)
        FakeChat.bodies.clear()
        FakeChat.session = "C001-S04"
        note = NoteWriter(llm).run(load_transcript("C001-S04"), "QUAL")
        self.assertTrue(FakeChat.bodies)
        sent = json.dumps(FakeChat.bodies, ensure_ascii=False)
        for f in HUMAN:
            for text in (f.key, f.label, f.instructions, f.hint):
                if text:
                    self.assertNotIn(text, sent)
        self.assertNotIn(human_fields.PLACEHOLDER, sent)
        for f in QUAL.generated:
            self.assertIn(f.instructions, sent)
        self.assertNotIn("心理師評估與概念化", SYSTEM_PROMPT)
        # The model filled FORMULATION anyway; that was dropped and the placeholder is there.
        self.assertEqual([s.text for s in note.sections["FORMULATION"]], [human_fields.PLACEHOLDER])
        self.assertEqual([s.text for s in note.sections["NEXT"]], [human_fields.PLACEHOLDER])
        self.assertTrue(note.sections["TOPIC"])


class QualApiTest(ServerCase, unittest.TestCase):
    """The rule engine (no model): a QUAL draft verifies and signs once the placeholders are replaced."""

    def open_qual(self, sid):
        status, r = self.call(f"/api/sessions/{sid}/process?template=QUAL", {})
        self.assertEqual(status, 200, r)
        return r

    def test_picker_list_and_view(self):
        status, r = self.call("/api/templates")
        self.assertEqual(status, 200)
        q = next(t for t in r if t["id"] == "QUAL")
        self.assertEqual(q["name"], "單次晤談質化紀錄")
        self.assertNotIn("instructions", json.dumps(q, ensure_ascii=False))
        v = self.open_qual("C001-S03")
        self.assertEqual(v["template"]["id"], "QUAL")
        self.assertEqual(list(v["note"]["sections"]), ["TOPIC", "INTERACTION", "SAFETY", "FORMULATION", "NEXT"])
        self.assertEqual(store.get_result("C001-S03")["template_id"], "QUAL")

    def test_placeholder_cannot_be_signed_and_a_flag_does_not_help(self):
        sid = "C001-S03"
        r = self.open_qual(sid)
        for k in ("FORMULATION", "NEXT"):
            self.assertEqual([s["text"] for s in r["note"]["sections"][k]], [human_fields.PLACEHOLDER])
            self.assertEqual(r["note"]["sections"][k][0]["status"], "unsupported")
        self.assertFalse(r["verification"]["ready_to_sign"])
        sections = sections_for_signing(r)
        for s in sections["FORMULATION"] + sections["NEXT"]:  # forged confirmations
            s.update(confirmed_by_therapist=True, keep_reason="已看過", status="supported", source="therapist")
        status, body = self.call(f"/api/sessions/{sid}/sign", {"signed_by": "王", "note": {"template": "QUAL", "sections": sections, "base_revision": r["revision"]}})
        self.assertEqual(status, 409, body)
        self.assertIn("待心理師填寫", body["message_zh"])
        self.assertIsNone(store.get_signature(sid))

    def test_rewritten_placeholders_verify_and_sign(self):
        sid = "C001-S04"
        r = self.open_qual(sid)
        mine = {"FORMULATION": "個案睡眠與情緒的關聯，待下次會談再討論。", "NEXT": "下週回診追蹤睡眠紀錄。"}
        note = {"template": "QUAL", "sections": sections_for_signing(r, mine), "base_revision": r["revision"]}
        status, v = self.call(f"/api/sessions/{sid}/verify", note)
        self.assertEqual(status, 200, v)
        for k in mine:
            s = v["note"]["sections"][k][0]
            self.assertEqual((s["source"], s["status"]), ("therapist", "supported"))
        self.assertTrue(v["verification"]["ready_to_sign"], v["verification"])
        note["base_revision"] = v["revision"]
        status, sig = self.call(f"/api/sessions/{sid}/sign", {"signed_by": "王", "note": note})
        self.assertEqual(status, 200, sig)
        signed = store.get_signed_note(sid)
        self.assertEqual(signed["template"], "QUAL")
        self.assertEqual(signed["sections"]["NEXT"][0]["text"], mine["NEXT"])

    def test_deleting_a_placeholder_is_allowed(self):
        sid = "C002-S05"
        r = self.open_qual(sid)
        sections = sections_for_signing(r, {"FORMULATION": "心理師的評估。"})
        sections["NEXT"] = []  # deleted
        status, v = self.call(f"/api/sessions/{sid}/verify", {"template": "QUAL", "sections": sections, "base_revision": r["revision"]})
        self.assertEqual(status, 200, v)
        self.assertEqual(v["note"]["sections"]["NEXT"], [])
        texts = [s["text"] for items in r["note"]["sections"].values() for s in items]
        self.assertNotIn("未提及", texts)  # the UI shows it for an empty field; it is never a sentence

    def test_a_cited_sentence_in_a_human_only_field_is_checked_like_any_other(self):
        sid = "C001-S03"
        r = self.open_qual(sid)
        sections = sections_for_signing(r, {"FORMULATION": "x評估", "NEXT": "y計畫"})
        sections["NEXT"] = [{"text": "個案表示下週要去月球旅行。", "evidence": [1], "source": "transcript"}]
        status, v = self.call(f"/api/sessions/{sid}/verify", {"template": "QUAL", "sections": sections, "base_revision": r["revision"]})
        self.assertEqual(status, 200, v)
        s = v["note"]["sections"]["NEXT"][0]
        self.assertEqual(s["source"], "transcript")
        self.assertNotEqual(s["status"], "supported")

    def test_an_uncited_sentence_in_a_generate_field_is_still_unsupported(self):
        sid = "C001-S03"
        r = self.open_qual(sid)
        sections = sections_for_signing(r, {"FORMULATION": "x評估", "NEXT": "y計畫"})
        sections["TOPIC"].append({"text": "心理師自己加的一句。", "evidence": [], "source": "therapist"})
        status, v = self.call(f"/api/sessions/{sid}/verify", {"template": "QUAL", "sections": sections, "base_revision": r["revision"]})
        added = v["note"]["sections"]["TOPIC"][-1]
        self.assertEqual((added["source"], added["status"]), ("transcript", "unsupported"))

    def test_soap_is_unchanged(self):
        status, r = self.call("/api/sessions/C002-S05/process", {})
        self.assertEqual(r["note"]["template"], "SOAP")
        for items in r["note"]["sections"].values():
            self.assertFalse(any(human_fields.is_placeholder(s["text"]) for s in items))


class QualWithModelTest(ServerCase, unittest.TestCase):
    """Through the real OpenAI-compatible transport: the draft verifies and signs."""

    @classmethod
    def setUpClass(cls):
        cls.srv, cls.llm = fake_llm()
        FakeChat.bodies.clear()
        FakeChat.session = "C001-S04"
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.srv.shutdown()

    def test_model_draft_signs_after_placeholders_are_replaced(self):
        sid = "C001-S04"
        status, r = self.call(f"/api/sessions/{sid}/process?template=QUAL", {})
        self.assertEqual(status, 200, r)
        self.assertNotIn(human_fields.PLACEHOLDER, json.dumps(FakeChat.bodies, ensure_ascii=False))
        self.assertEqual([s["text"] for s in r["note"]["sections"]["FORMULATION"]], [human_fields.PLACEHOLDER])
        note = {"template": "QUAL", "sections": sections_for_signing(r, {"FORMULATION": "我的評估。", "NEXT": "我的計畫。"}), "base_revision": r["revision"]}
        status, v = self.call(f"/api/sessions/{sid}/verify", note)
        self.assertEqual(status, 200, v)
        self.assertTrue(v["verification"]["ready_to_sign"], v["verification"])
        note["base_revision"] = v["revision"]
        status, sig = self.call(f"/api/sessions/{sid}/sign", {"signed_by": "王", "note": note})
        self.assertEqual(status, 200, sig)


if __name__ == "__main__":
    unittest.main()