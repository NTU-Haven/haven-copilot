"""Ticket 02 / AC-02: templates are definitions in the Template Registry.

SOAP and DAP moved out of the writer into ``haven.templates``; what is sent to
the model for them is pinned to the strings used before the move. A template
registered in a test drafts a note through the HTTP API with no change to the
writer or the pipeline, and an unknown template id is a 400.
"""

import hashlib
import json
import unittest

from helpers import ServerCase

from haven import config, store, templates
from haven.agents.writer import CHUNK, SYSTEM_PROMPT, NoteWriter, _prompt
from haven.orchestrator import Orchestrator
from haven.templates import Field, Template
from haven.transcript import load_transcript

# The section texts as they were hard-coded in writer.TEMPLATES before ticket 02.
_A = "評估（只寫心理師在會談中說出口的觀察、概念化與介入重點，並引用心理師說的段落；心理師沒說出口的評估不要寫，寧可少寫）"
_P = ("計畫（會談中談定的作業、下次會談安排、轉介、安全措施；個案自己提出的計畫寫「個案計畫…」，心理師提出的才寫「心理師建議…」；"
      "心理師只是提議、逐字稿中沒有完成的事寫「心理師提議…」）")
_R = ("風險與安全（逐字稿中凡是提到自殺、自傷、傷害他人、受暴或受虐、物質使用，都必須寫在這一欄，不受句數限制；"
      "保留否定語、時間和主詞：他人的事寫明是誰，過去的事寫明時間；也寫保護因子與談到的安全措施；沒有這類內容就留空陣列）")
OLD_TEMPLATES = {
    "SOAP": {
        "S": "主觀資料（個案自述的困擾、感受、症狀、睡眠、體重、作業完成情形）",
        "O": ("客觀資料（只寫逐字稿文字本身看得到的行為：逐字稿標註的哭泣、沉默、笑，或心理師說出口的觀察，"
              "例如心理師說「我注意到你講到這裡停了很久」；語氣、語速、表情、神情、肢體動作，"
              "以及從說話內容推測的情緒（例如「語氣中透露出…」）一律不要寫，這些由心理師自己補；"
              "每句都要引用那個段落；量表分數由系統自動填入，不要寫；沒有就留空陣列，寧可少寫）"),
        "A": _A, "P": _P, "R": _R,
    },
    "DAP": {
        "D": ("資料（個案自述，以及逐字稿文字本身看得到的行為：逐字稿標註的哭泣、沉默，或心理師說出口的觀察；"
              "語氣、語速、表情、神情與推測的情緒不要寫；量表分數由系統自動填入，不要寫）"),
        "A": _A, "P": _P, "R": _R,
    },
}
# sha256 of writer.SYSTEM_PROMPT before ticket 02.
OLD_SYSTEM_SHA256 = "9d9084bb94398910590b6d1fff3b3c1e48c54c500b06f5c7f976cfddd726f1d0"


def old_prompt(t, template, segments=None, part=None):
    """writer._prompt as it was before ticket 02."""
    def who(s):
        name = {"therapist": "心理師", "client": "個案"}.get(s.speaker, "說話者")
        return name + ("（推測）" if s.speaker_source == "model" else "")
    segments = t.segments if segments is None else segments
    lines = [json.dumps({"id": s.id, "speaker": who(s), "text": s.text}, ensure_ascii=False) for s in segments]
    sections = "\n".join(f'- "{k}": {v}' for k, v in OLD_TEMPLATES[template].items())
    keys = ", ".join(f'"{k}": [{{"text": "...", "evidence": [段落編號]}}]' for k in OLD_TEMPLATES[template])
    where = f"（全長分成 {part[1]} 段處理，這是第 {part[0]} 段，只寫這一段的內容）" if part else ""
    per = "2 到 5" if not part or part[1] <= 2 else "1 到 3"
    return (
        f"第 {t.session_no} 次會談逐字稿{where}，每行一段（id 是段落編號）：\n" + "\n".join(lines)
        + f"\n\n請撰寫 {template} 格式紀錄，各欄位意義：\n{sections}\n"
        + f"R 以外每欄 {per} 句，每句簡短，只寫這段最重要的內容。只輸出 JSON：{{{keys}}}"
    )


# A template made up for these tests. Registering it is all it takes to use it.
GOALS = "目標（個案在這次會談中說出的改變目標）"
WORK = "工作重點（心理師在會談中說出口的介入）"
PRIVATE = "心理師私人筆記：不可送給模型"
TEST_TEMPLATE = Template(
    id="TESTGOALS", version=3, name="測試用目標紀錄",
    fields=(
        Field("G", "G 目標", GOALS, fallback="subjective"),
        Field("W", "W 工作重點", WORK, fallback="assessment"),
        Field("H", "H 心理師筆記", PRIVATE, ai_mode=templates.HUMAN_ONLY),
        Field("K", "K 安全", "安全（風險線索與安全措施）", fallback="risk"),
    ),
    risk_field="K",
)


class Recorder:
    """A model stub: records every prompt and answers with one G sentence (and an H one it must not keep)."""

    label = "recorder"

    def __init__(self):
        self.prompts = []

    def generate_json(self, system, prompt, max_tokens=None):
        self.prompts.append((system, prompt))
        first = json.loads(prompt.split("\n")[1])["id"]
        return {"G": [{"text": "個案表示想要改善睡眠。", "evidence": [first]}],
                "H": [{"text": "模型不該寫這一欄。", "evidence": [first]}]}


class PinnedPromptTest(unittest.TestCase):
    def test_soap_and_dap_prompts_are_byte_identical_to_before(self):
        self.assertEqual(hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(), OLD_SYSTEM_SHA256)
        checked = 0
        for path in sorted(config.SESSIONS_DIR.glob("*.json")):
            t = load_transcript(path.stem)
            parts = [t.segments[i:i + CHUNK] for i in range(0, len(t.segments), CHUNK)]
            for template in ("SOAP", "DAP"):
                self.assertEqual(_prompt(t, template), old_prompt(t, template))
                for n, segments in enumerate(parts, start=1):
                    self.assertEqual(_prompt(t, template, segments, (n, 3)), old_prompt(t, template, segments, (n, 3)))
                    self.assertEqual(_prompt(t, template, segments, (n, 2)), old_prompt(t, template, segments, (n, 2)))
                checked += 1
        self.assertGreater(checked, 4)

    def test_registry_holds_soap_and_dap(self):
        self.assertEqual(templates.get("SOAP").keys, ("S", "O", "A", "P", "R"))
        self.assertEqual(templates.get("DAP").keys, ("D", "A", "P", "R"))
        for tpl in (templates.get("SOAP"), templates.get("DAP")):
            self.assertTrue(all(f.ai_mode == templates.GENERATE for f in tpl.fields))
            self.assertEqual({f.key: f.instructions for f in tpl.fields}, OLD_TEMPLATES[tpl.id])
            self.assertEqual(tpl.risk_field, "R")
        self.assertEqual((templates.get("SOAP").measures_field, templates.get("DAP").measures_field), ("O", "D"))

    def test_bad_definitions_are_refused(self):
        with self.assertRaises(ValueError):
            templates.register(Template("SOAP", 9, "dup", (Field("X", "X", "x"),)))
        with self.assertRaises(ValueError):
            templates.register(Template("BAD1", 1, "bad", (Field("X", "X", "x", ai_mode="maybe"),)))
        with self.assertRaises(ValueError):
            templates.register(Template("BAD2", 1, "bad", (Field("X", "X", "x"), Field("X", "X", "y"))))
        with self.assertRaises(ValueError):
            templates.register(Template("BAD3", 1, "bad", (Field("X", "X", "x", ai_mode=templates.HUMAN_ONLY),), risk_field="X"))
        self.assertIsNone(templates.find("BAD1"))


class WriterWithNewTemplateTest(unittest.TestCase):
    def setUp(self):
        templates.register(TEST_TEMPLATE)
        self.addCleanup(templates.unregister, TEST_TEMPLATE.id)

    def test_prompt_comes_from_the_definition_and_human_only_is_not_sent(self):
        llm = Recorder()
        note = NoteWriter(llm).run(load_transcript("C001-S03"), "TESTGOALS")
        self.assertEqual(list(note.sections), ["G", "W", "H", "K"])
        self.assertEqual(note.template, "TESTGOALS")
        _, prompt = llm.prompts[0]
        self.assertIn(GOALS, prompt)
        self.assertIn(WORK, prompt)
        self.assertNotIn(PRIVATE, prompt)
        self.assertNotIn('"H"', prompt)
        self.assertNotIn("主觀資料", prompt)
        self.assertEqual(note.sections["H"], [])  # the model's H line is not kept
        self.assertEqual([s.text for s in note.sections["G"]], ["個案表示想要改善睡眠。"])

    def test_rule_engine_fills_fields_by_their_fallback(self):
        note = NoteWriter().run(load_transcript("C001-S04"), "TESTGOALS")
        self.assertEqual(list(note.sections), ["G", "W", "H", "K"])
        self.assertTrue(note.sections["G"])
        self.assertTrue(note.sections["K"])  # risk lines go to the template's risk field
        self.assertEqual(note.sections["H"], [])

    def test_unknown_template_raises(self):
        with self.assertRaises(templates.UnknownTemplate):
            NoteWriter().run(load_transcript("C001-S03"), "NOPE")
        with self.assertRaises(templates.UnknownTemplate):
            Orchestrator(use_llm=False).process_session("C001-S03", "NOPE")


class TemplateApiTest(ServerCase, unittest.TestCase):
    def setUp(self):
        templates.register(TEST_TEMPLATE)
        self.addCleanup(templates.unregister, TEST_TEMPLATE.id)

    def test_registered_template_drafts_through_the_api(self):
        status, r = self.call("/api/sessions/C001-S03/process?template=TESTGOALS", {})
        self.assertEqual(status, 200, r)
        self.assertEqual(r["note"]["template"], "TESTGOALS")
        self.assertEqual(list(r["note"]["sections"]), ["G", "W", "H", "K"])
        self.assertTrue(r["note"]["sections"]["G"])
        self.assertEqual([f["key"] for f in r["template"]["fields"]], ["G", "W", "H", "K"])
        self.assertEqual(r["template"]["fields"][2]["ai_mode"], "human_only")
        self.assertNotIn("instructions", r["template"]["fields"][0])
        stored = store.get_result("C001-S03")
        self.assertEqual((stored["template_id"], stored["template_version"]), ("TESTGOALS", 3))
        # The edited draft goes back through verify with the new template's fields.
        note = {"template": "TESTGOALS", "sections": {"G": [{"text": s["text"], "evidence": s["evidence"]} for s in r["note"]["sections"]["G"]],
                                                     "H": [{"text": "心理師自己寫的筆記。", "evidence": []}]},
                "base_revision": r["revision"]}
        status, v = self.call("/api/sessions/C001-S03/verify", note)
        self.assertEqual(status, 200, v)
        self.assertEqual(set(v["note"]["sections"]), {"G", "W", "H", "K"})
        status, v = self.call("/api/sessions/C001-S03/verify", {"template": "TESTGOALS", "sections": {"S": []}, "base_revision": v["revision"]})
        self.assertEqual(status, 400)  # S is not a field of this template

    def test_soap_draft_records_template_id_and_version(self):
        status, r = self.call("/api/sessions/C001-S04/process", {})
        self.assertEqual(status, 200, r)
        stored = store.get_result("C001-S04")
        self.assertEqual((stored["template_id"], stored["template_version"]), ("SOAP", 1))
        self.assertEqual(r["template"]["id"], "SOAP")
        self.assertEqual(r["template"]["fields"][1]["label"], "O 客觀資料")

    def test_unknown_template_is_400_on_every_route(self):
        _, note = self.draft("C002-S05")
        before = store.get_result("C002-S05")
        status, body = self.call("/api/sessions/C002-S05/process?template=NOPE", {})
        self.assertEqual(status, 400)
        self.assertIn("模板", body["message_zh"])
        status, body = self.call("/api/sessions/C002-S05/verify", dict(note, template="NOPE"))
        self.assertEqual(status, 400)
        self.assertIn("模板", body["message_zh"])
        status, body = self.call("/api/sessions/C002-S05/sign", {"signed_by": "王心理師", "note": dict(note, template="NOPE")})
        self.assertEqual(status, 400)
        self.assertIn("模板", body["message_zh"])
        self.assertIsNone(store.get_signature("C002-S05"))
        self.assertEqual(store.get_result("C002-S05")["note"], before["note"])

    def test_templates_are_listed(self):
        status, body = self.call("/api/templates")
        self.assertEqual(status, 200)
        ids = [t["id"] for t in body]
        self.assertIn("SOAP", ids)
        self.assertIn("DAP", ids)
        self.assertIn("TESTGOALS", ids)
        self.assertTrue(all("instructions" not in f for t in body for f in t["fields"]))


if __name__ == "__main__":
    unittest.main()
