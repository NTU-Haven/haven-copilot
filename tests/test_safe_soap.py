"""Ticket 19: the safe SOAP template (SAFE_SOAP): S and O drafted, A and P human_only."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helpers import ServerCase  # noqa: F401  (also sets HAVEN_LLM=demo)

from haven import human_fields, templates
from haven.agents.writer import NoteWriter, _rule_based
from haven.llm import OpenAICompatLLM
from haven.transcript import load_transcript

SAFE = templates.get("SAFE_SOAP")
HUMAN = [f for f in SAFE.fields if f.ai_mode == templates.HUMAN_ONLY]
SID = "C001-S03"


class FakeChat(BaseHTTPRequestHandler):
    """An OpenAI-style chat server that answers with the rule-engine S/O draft, and
    fills A and P too as a misbehaving model would. Every request body is recorded."""

    bodies: list[dict] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeChat.bodies.append(body)
        if "事實查核員" in body["messages"][0]["content"]:
            reply = {"results": [{"id": i, "supported": True} for i in range(60)]}
        else:
            draft = _rule_based(load_transcript(SID), "SAFE_SOAP")
            reply = {k: [{"text": s.text, "evidence": s.evidence} for s in v] for k, v in draft.items()}
            reply["A"] = [{"text": "個案有憂鬱傾向。", "evidence": [1]}]
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
    out = {}
    for k, items in r["note"]["sections"].items():
        out[k] = []
        for s in items:
            text = s["text"]
            if human_fields.is_placeholder(text) and therapist_text and k in therapist_text:
                text = therapist_text[k]
            out[k].append({"text": text, "evidence": s["evidence"], "source": s["source"]})
    return out


class DefinitionTest(unittest.TestCase):
    def test_definition(self):
        self.assertEqual((SAFE.id, SAFE.version, SAFE.name), ("SAFE_SOAP", 1, "安全版 SOAP"))
        self.assertIsNone(SAFE.risk_field)
        self.assertIsNone(SAFE.measures_field)
        self.assertEqual(SAFE.keys, ("S", "O", "A", "P"))
        self.assertEqual([f.ai_mode for f in SAFE.fields], ["generate", "generate", "human_only", "human_only"])
        for f in SAFE.generated:
            for word in ("診斷", "風險等級", "量表分數", "臨床推論", "引用"):
                self.assertIn(word, f.instructions)

    def test_other_templates_are_unchanged(self):
        self.assertEqual(templates.DEFAULT, "SOAP")
        self.assertEqual(templates.get("SOAP").keys, ("S", "O", "A", "P", "R"))
        self.assertEqual(templates.get("DAP").keys, ("D", "A", "P", "R"))
        self.assertEqual(templates.get("QUAL").keys, ("TOPIC", "INTERACTION", "SAFETY", "FORMULATION", "NEXT"))


class NoHumanOnlyContentSentTest(unittest.TestCase):
    def test_a_and_p_are_in_no_request(self):
        srv, llm = fake_llm()
        self.addCleanup(srv.shutdown)
        FakeChat.bodies.clear()
        note = NoteWriter(llm).run(load_transcript(SID), "SAFE_SOAP")
        self.assertTrue(FakeChat.bodies)
        sent = json.dumps(FakeChat.bodies, ensure_ascii=False)
        for f in HUMAN:
            for text in (f.label, f.instructions, f.hint):
                if text:
                    self.assertNotIn(text, sent)
            self.assertNotIn(f'"{f.key}"', sent)
        self.assertNotIn(human_fields.PLACEHOLDER, sent)
        for f in SAFE.generated:
            self.assertIn(f.instructions, sent)
        for k in ("A", "P"):  # the model filled A anyway; dropped
            self.assertEqual([s.text for s in note.sections[k]], [human_fields.PLACEHOLDER])
        self.assertTrue(note.sections["S"])


class ApiTest(ServerCase, unittest.TestCase):
    def open_safe(self, sid):
        status, r = self.call(f"/api/sessions/{sid}/process?template=SAFE_SOAP", {"discard_edits": True})
        self.assertEqual(status, 200, r)
        return r

    def test_listed_without_instructions(self):
        status, r = self.call("/api/templates")
        self.assertEqual(status, 200)
        t = next(t for t in r if t["id"] == "SAFE_SOAP")
        self.assertEqual(t["name"], "安全版 SOAP")
        self.assertEqual([f["ai_mode"] for f in t["fields"]], ["generate", "generate", "human_only", "human_only"])
        self.assertNotIn("instructions", json.dumps(t, ensure_ascii=False))

    def test_draft_has_cited_verified_s_and_o_and_placeholders(self):
        r = self.open_safe(SID)
        secs = r["note"]["sections"]
        self.assertEqual(list(secs), ["S", "O", "A", "P"])
        self.assertTrue(secs["S"])
        self.assertTrue(secs["O"])
        for k in ("S", "O"):
            for s in secs[k]:
                self.assertTrue(s["evidence"], s)
                self.assertEqual(s["status"], "supported", s)
        for k in ("A", "P"):
            self.assertEqual([s["text"] for s in secs[k]], [human_fields.PLACEHOLDER])
        self.assertFalse(r["verification"]["ready_to_sign"])

    def test_signing_needs_a_and_p_rewritten(self):
        r = self.open_safe(SID)
        note = {"template": "SAFE_SOAP", "sections": sections_for_signing(r), "base_revision": r["revision"]}
        status, body = self.call(f"/api/sessions/{SID}/sign", {"signed_by": "王", "note": note})
        self.assertEqual(status, 409, body)
        self.assertIn("待心理師填寫", body["message_zh"])
        note["sections"] = sections_for_signing(r, {"A": "我的評估。", "P": "我的計畫。"})
        status, v = self.call(f"/api/sessions/{SID}/verify", note)
        self.assertEqual(status, 200, v)
        self.assertTrue(v["verification"]["ready_to_sign"], v["verification"])
        note["base_revision"] = v["revision"]
        status, sig = self.call(f"/api/sessions/{SID}/sign", {"signed_by": "王", "note": note})
        self.assertEqual(status, 200, sig)


if __name__ == "__main__":
    unittest.main()
