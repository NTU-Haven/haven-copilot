"""The product drafts with QUAL (單次晤談質化紀錄) unless HAVEN_DEFAULT_TEMPLATE says otherwise.

The rest of the suite runs with SOAP as its default (tests/helpers.py), because it was written
against SOAP's sections; these tests switch the product default back on."""

import os
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import ServerCase

from haven import config, templates
from haven.agents.writer import NoteWriter
from haven.transcript import load_transcript

REPO = Path(__file__).resolve().parent.parent


class ProductDefaultTest(unittest.TestCase):
    def test_config_default_is_qual_when_unset(self):
        env = {k: v for k, v in os.environ.items() if k != "HAVEN_DEFAULT_TEMPLATE"}
        out = subprocess.run([sys.executable, "-c", "from haven import config, templates; print(templates.default_id())"],
                             cwd=REPO, env=env, capture_output=True, text=True)
        self.assertEqual(out.stdout.strip(), "QUAL", out.stderr)

    def test_writer_uses_the_configured_default(self):
        saved = config.DEFAULT_TEMPLATE
        config.DEFAULT_TEMPLATE = "QUAL"
        try:
            note = NoteWriter().run(load_transcript("C001-S03"))
        finally:
            config.DEFAULT_TEMPLATE = saved
        self.assertEqual(note.template, "QUAL")
        self.assertEqual(tuple(note.sections), tuple(templates.get("QUAL").keys))


class ApiDefaultTest(ServerCase, unittest.TestCase):
    def setUp(self):
        self.saved = config.DEFAULT_TEMPLATE
        config.DEFAULT_TEMPLATE = "QUAL"
        self.addCleanup(setattr, config, "DEFAULT_TEMPLATE", self.saved)

    def test_open_and_process_without_a_template_draft_qual(self):
        status, r = self.call("/api/sessions/C002-S05/open", {})
        self.assertEqual(status, 200, r)
        self.assertEqual(r["note"]["template"], "QUAL")
        status, r = self.call("/api/sessions/C002-S05/process", {"discard_edits": True})
        self.assertEqual(status, 200, r)
        self.assertEqual(r["note"]["template"], "QUAL")


if __name__ == "__main__":
    unittest.main()
