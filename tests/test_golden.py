"""Ticket 15: the golden set (data/golden) is well formed, and scripts/golden_eval.py runs
end to end on the rule engine in a throwaway directory without writing anywhere else."""

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import helpers  # noqa: F401  (forces HAVEN_LLM=demo)

from haven import config

ROOT = Path(__file__).resolve().parent.parent
GOLDEN = ROOT / "data" / "golden"
sys.path.insert(0, str(ROOT / "scripts"))
import golden_eval  # noqa: E402

KINDS = {"diagnosis", "risk_level", "score", "inference", "negation", "time", "speaker", "subject", "voice"}


def sessions():
    return sorted(p for p in GOLDEN.glob("G*.json") if not p.name.endswith(".labels.json") and p.name != "clients.json")


class GoldenFilesTest(unittest.TestCase):

    def test_there_are_at_least_ten_sessions_and_two_recaps(self):
        files = sessions()
        self.assertGreaterEqual(len(files), 10)
        modes = [json.loads(p.read_text(encoding="utf-8")).get("input_mode", "session") for p in files]
        self.assertGreaterEqual(modes.count("recap"), 2)

    def test_every_transcript_is_simulated_and_well_formed(self):
        clients = {c["id"] for c in json.loads((GOLDEN / "clients.json").read_text(encoding="utf-8"))}
        demo_clients = {c["id"] for c in json.loads((ROOT / "data" / "clients.json").read_text(encoding="utf-8"))}
        self.assertFalse(clients & demo_clients, "golden client ids must not collide with demo clients")
        for p in sessions():
            with self.subTest(p.name):
                t = json.loads(p.read_text(encoding="utf-8"))
                self.assertTrue(t["simulated"])
                self.assertEqual(t["session_id"], p.stem)
                self.assertTrue(t["session_id"].startswith(t["client_id"]))
                self.assertIn(t["client_id"], clients)
                segs = t["segments"]
                self.assertTrue(15 <= len(segs) <= 40, len(segs))
                self.assertEqual([s["id"] for s in segs], list(range(1, len(segs) + 1)))
                for s in segs:
                    self.assertIn(s["speaker"], ("therapist", "client"))
                    self.assertTrue(s["text"].strip())
                    self.assertLess(s["start"], s["end"])
                if t.get("input_mode") == "recap":
                    self.assertTrue(all(s["speaker"] == "therapist" and s["speaker_source"] == "recap" for s in segs))
                else:
                    self.assertTrue({s["speaker"] for s in segs} == {"therapist", "client"})

    def test_labels_reference_existing_segments_and_valid_patterns(self):
        for p in sessions():
            with self.subTest(p.name):
                t = json.loads(p.read_text(encoding="utf-8"))
                lab = json.loads((GOLDEN / f"{p.stem}.labels.json").read_text(encoding="utf-8"))
                ids = {s["id"] for s in t["segments"]}
                self.assertEqual(lab["session_id"], p.stem)
                self.assertEqual(lab["input_mode"], t.get("input_mode", "session"))
                self.assertTrue(lab["traps"] and lab["notes"])
                self.assertGreaterEqual(len(lab["must_include"]), 3)
                self.assertGreaterEqual(len(lab["must_not_include"]), 2)
                for m in lab["must_include"]:
                    self.assertTrue(m["fact"] and m["segments"])
                    self.assertTrue(set(m["segments"]) <= ids, m)
                for n in lab["must_not_include"]:
                    self.assertIn(n["kind"], KINDS)
                    self.assertTrue(n["claim"])
                    re.compile(n["pattern"])

    def test_the_traps_the_ticket_asks_for_are_covered(self):
        traps = set()
        for p in sessions():
            traps |= set(json.loads((GOLDEN / f"{p.stem}.labels.json").read_text(encoding="utf-8"))["traps"])
        for needed in ("diagnosis", "risk-passive", "risk-explicit", "risk-denial", "scale-guessing",
                       "negation", "time-words", "speaker-paraphrase", "speaker-quote", "recap"):
            self.assertIn(needed, traps)

    def test_verifier_cases_that_cite_golden_sessions_point_at_real_lines(self):
        spec = json.loads((ROOT / "data" / "verifier_eval" / "cases.json").read_text(encoding="utf-8"))
        golden = [c for c in spec["cases"] if c.get("set") == "golden"]
        self.assertGreaterEqual(len(golden), 10)
        self.assertIn("recap_voice", {c["label"] for c in golden})
        for c in golden:
            t = json.loads((GOLDEN / f"{c['session_id']}.json").read_text(encoding="utf-8"))
            self.assertTrue(set(c["evidence"]) <= {s["id"] for s in t["segments"]}, c["id"])
            self.assertIn(c["label"], spec["labels"])


class GoldenEvalTest(unittest.TestCase):

    def test_runs_end_to_end_on_the_rule_engine_and_writes_only_to_the_output_dir(self):
        real = (config.DATA_DIR, config.SESSIONS_DIR, config.CLIENTS_FILE, config.DB_FILE)
        clients_before = (ROOT / "data" / "clients.json").read_bytes()
        with tempfile.TemporaryDirectory() as out:
            out = Path(out) / "golden"
            with mock.patch.object(golden_eval, "OUT_DIR", out), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(golden_eval.main(["--label", "unit test"]), 0)
            files = sorted(p.name for p in out.iterdir())
            self.assertEqual(len(files), 2)
            md = next(p for p in out.iterdir() if p.suffix == ".md").read_text(encoding="utf-8")
            report = json.loads(next(p for p in out.iterdir() if p.suffix == ".json").read_text(encoding="utf-8"))
        self.assertTrue(files[0].startswith("unit-test-"))
        self.assertEqual(report["n_sessions"], len(sessions()))
        for r in report["sessions"]:
            self.assertNotIn("error", r, r)
            self.assertTrue(r["checks"]["sentences"])
        recap = [r for r in report["sessions"] if r["input_mode"] == "recap"]
        self.assertGreaterEqual(len(recap), 2)
        self.assertTrue(all(r["checks"]["recap_voice"] is not None for r in recap))
        self.assertIn("人工審閱表", md)
        self.assertIn("無依據的臨床判斷", md)
        # Nothing of the real data directory moved or changed.
        self.assertEqual((config.DATA_DIR, config.SESSIONS_DIR, config.CLIENTS_FILE, config.DB_FILE), real)
        self.assertEqual((ROOT / "data" / "clients.json").read_bytes(), clients_before)
        self.assertFalse(list((ROOT / "data").glob("haven.db-journal")))

    def test_it_refuses_to_write_outside_docs_golden(self):
        for bad in (ROOT / "data" / "x.json", ROOT / "docs" / "x.md", Path(tempfile.gettempdir()) / "x.md", ROOT / "docs" / "golden" / ".." / "x.md"):
            with self.subTest(bad=str(bad)):
                with self.assertRaises(golden_eval.GoldenError):
                    golden_eval.safe_out(bad)
        golden_eval.safe_out(ROOT / "docs" / "golden" / "ok.md")

    def test_automatic_checks_catch_a_diagnosis_and_a_missed_fact(self):
        item = golden_eval.load_golden(["G001-S01"])[0]
        seg = [{"id": s["id"], "text": s["text"]} for s in item["transcript"]["segments"]]
        sent = {"text": "個案罹患憂鬱症，擔心家人知道。", "evidence": [13], "status": "supported", "support": 1.0, "reason": "", "source": ""}
        result = {"transcript": seg, "note": {"sections": {"TOPIC": [sent]}}}
        ck = golden_eval.check_session(item, result)["counts"]
        self.assertGreaterEqual(ck["pattern_hits"], 1)
        self.assertEqual(ck["pattern_hits_passed_verifier"], ck["pattern_hits"])
        self.assertEqual(ck["facts_covered"], 0)


if __name__ == "__main__":
    unittest.main()
