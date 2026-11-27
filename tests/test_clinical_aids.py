"""AC-01 (first half): with the default settings (HAVEN_CLINICAL_AIDS off) a draft has no
score lines and no forced quotes in R, the session page and the client brief carry no
risk flags, checklist or outcome alerts, signing does not check risk cues, and Case
Memory is not offered. The features themselves are tested with the flag on elsewhere."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import ServerCase
from haven import config, store
from haven.agents.writer import NoteWriter
from haven.transcript import load_transcript

ROOT = Path(__file__).resolve().parent.parent

class FakeLLM:
    label = "fake:model"
    url = "http://127.0.0.1:1"

    def __init__(self, answer):
        self.answer = answer

    def generate_json(self, system, prompt, max_tokens=None):
        return self.answer


def sentences(note):
    return [s for items in note["sections"].values() for s in items]


class DefaultIsOffTest(unittest.TestCase):
    def test_flag_is_off_unless_set_to_1(self):
        def read(value):
            env = {k: v for k, v in os.environ.items() if k != "HAVEN_CLINICAL_AIDS"}
            if value is not None:
                env["HAVEN_CLINICAL_AIDS"] = value
            return subprocess.run([sys.executable, "-c", "from haven import config; print(config.CLINICAL_AIDS)"],
                                  cwd=ROOT, env=env, capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(read(None), "False")
        self.assertEqual(read("0"), "False")
        self.assertEqual(read("1"), "True")


class WriterWithAidsOffTest(unittest.TestCase):
    def setUp(self):
        self.saved, config.CLINICAL_AIDS = config.CLINICAL_AIDS, False

    def tearDown(self):
        config.CLINICAL_AIDS = self.saved

    def test_model_note_that_skips_the_disclosure_gets_nothing_added_to_r(self):
        t = load_transcript("C001-S04")  # #10 is a high-level risk line
        note = NoteWriter(FakeLLM({"S": [{"text": "個案表示上週被主管責罵後請假。", "evidence": [2]}]})).run(t)
        self.assertEqual(note.sections["R"], [])
        self.assertNotIn(10, {e for s in note.sentences() for e in s.evidence})

    def test_rule_engine_does_not_route_risk_lines_into_r(self):
        note = NoteWriter().run(load_transcript("C001-S04"))
        self.assertEqual(note.sections["R"], [])


class ServerWithAidsOffTest(ServerCase, unittest.TestCase):
    def setUp(self):
        self.saved, config.CLINICAL_AIDS = config.CLINICAL_AIDS, False

    def tearDown(self):
        config.CLINICAL_AIDS = self.saved

    def test_draft_has_no_score_lines_and_no_risk_or_outcome(self):
        for sid in ("C001-S03", "C001-S04", "C002-S05"):
            r, _ = self.draft(sid)
            self.assertEqual([s for s in sentences(r["note"]) if s["source"] == "measure"], [], sid)
            self.assertIsNone(r["risk"], sid)
            self.assertIsNone(r["outcome"], sid)
            self.assertEqual(r["risk_uncovered"], [], sid)
            self.assertNotIn("risk", r["timings_ms"])
            self.assertNotIn("outcome", r["timings_ms"])
            status, view = self.call(f"/api/sessions/{sid}")
            self.assertEqual(status, 200)
            self.assertIsNone(view["risk"])  # no flags, no checklist
            self.assertIsNone(view["outcome"])

    def test_brief_and_client_list_have_no_alerts_or_risk_level(self):
        self.draft("C001-S04")
        _, brief = self.call("/api/clients/C001/brief")
        self.assertIsNone(brief["outcome"])
        self.assertIsNone(brief["last_session"]["risk_level"])
        self.assertTrue(all(m.get("risk_level") is None for m in brief["sessions_meta"]))
        _, clients = self.call("/api/clients")
        self.assertTrue(all(c["alert_count"] == 0 for c in clients))

    def test_signing_does_not_check_risk_cues(self):
        self.data.add_session("C001-S81", [("client", "最近睡不好，每天只睡四小時。"), ("therapist", "我們下週再談睡眠。"),
                                          ("client", "有時候會想說乾脆死掉算了。")])
        r, note = self.draft("C001-S81")
        self.assertNotIn(3, {e for s in sentences(r["note"]) for e in s["evidence"]})
        status, body = self.call("/api/sessions/C001-S81/verify", note)
        self.assertEqual(status, 200, body)
        self.assertEqual(body["risk_uncovered"], [])
        status, body = self.call("/api/sessions/C001-S81/sign", {"signed_by": "王", "note": note})
        self.assertEqual(status, 200, body)
        self.assertEqual(store.get_signed_note("C001-S81")["risk_dismissals"], [])

    def test_case_memory_endpoint_is_not_there(self):
        status, body = self.call("/api/clients/C001/ask", {"q": "父親"})
        self.assertEqual(status, 404)
        self.assertNotIn("ask", [a["action"] for a in store.recent_audit()])

    def test_ui_is_told_the_flag(self):
        self.assertIs(self.call("/api/me")[1]["clinical_aids"], False)
        self.assertIs(self.call("/api/status")[1]["clinical_aids"], False)
        config.CLINICAL_AIDS = True
        self.assertIs(self.call("/api/me")[1]["clinical_aids"], True)
        self.assertIs(self.call("/api/status")[1]["clinical_aids"], True)

    def test_a_draft_made_with_the_aids_on_shows_none_of_them_when_off(self):
        self.data.add_session("C001-S82", [("client", "有時候會想說乾脆死掉算了。"), ("therapist", "謝謝你願意說。")])
        config.CLINICAL_AIDS = True
        r, _ = self.draft("C001-S82")
        self.assertEqual(r["risk"]["level"], "high")
        config.CLINICAL_AIDS = False
        _, view = self.call("/api/sessions/C001-S82")
        self.assertIsNone(view["risk"])
        self.assertIsNone(view["outcome"])
        self.assertEqual(view["risk_uncovered"], [])


class UiHidesPanelsTest(unittest.TestCase):
    def test_app_hides_the_panels_without_the_data(self):
        js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        self.assertIn("r.risk ? riskPanel(r.risk) : null", js)
        self.assertIn("if (brief.outcome) {", js)
        self.assertIn('askForm.closest("section").hidden = !me.clinical_aids', js)


if __name__ == "__main__":
    unittest.main()
