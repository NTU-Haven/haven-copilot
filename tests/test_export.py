"""Ticket 11: export of confirmed notes (spec AC-09, E1/E2)."""

import json
import unittest
import urllib.error
import urllib.request

from helpers import H, ServerCase
from test_accounts import AccountCase
from test_regenerate import LINES, plain

from haven import accounts, store


def raw(base, path, headers=H, method=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read(), e.headers


class ExportTest(ServerCase, unittest.TestCase):

    def confirmed(self, sid):
        self.data.add_session(sid, LINES, session_no=50)
        status, view = self.call(f"/api/sessions/{sid}/open", {})
        self.assertEqual(status, 200, view)
        status, sig = self.call(f"/api/sessions/{sid}/sign", {"signed_by": "王心理師", "note": plain(view), "base_revision": view["revision"]})
        self.assertEqual(status, 200, sig)
        return view, sig

    def get(self, sid, fmt="md"):
        return raw(self.base, f"/api/sessions/{sid}/export?format={fmt}")

    def test_unconfirmed_note_is_refused_with_409(self):
        self.data.add_session("C001-S50", LINES, session_no=50)
        self.call("/api/sessions/C001-S50/open", {})
        status, body, _ = self.get("C001-S50")
        self.assertEqual(status, 409)
        self.assertIn("確認", json.loads(body)["message_zh"])
        self.assertEqual([r for r in store.audit_rows("export_note", 1000) if "C001-S50" in r["detail"]], [])

    def test_bad_format_is_400_and_missing_header_is_refused(self):
        self.confirmed("C001-S51")
        self.assertEqual(self.get("C001-S51", "pdf")[0], 400)
        self.assertEqual(raw(self.base, "/api/sessions/C001-S51/export?format=md", headers={})[0], 403)
        self.assertEqual(self.get("C001-S99")[0], 404)

    def test_markdown_and_text_downloads(self):
        view, sig = self.confirmed("C001-S52")
        for fmt, ctype in (("md", "text/markdown"), ("txt", "text/plain")):
            status, body, headers = self.get("C001-S52", fmt)
            self.assertEqual(status, 200)
            self.assertTrue(headers["Content-Type"].startswith(ctype))
            self.assertIn("attachment", headers["Content-Disposition"])
            self.assertIn(f".{fmt}", headers["Content-Disposition"])
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertTrue(headers["X-Export-Id"])
            text = body.decode("utf-8")
            self.assertIn("王心理師", text)
            self.assertIn(sig["note_sha256"], text)
            self.assertIn(sig["signed_at"], text)
            self.assertIn("本紀錄由 AI 輔助產生草稿，經心理師核對確認", text)
            for field in view["template"]["fields"]:
                self.assertIn(field["label"], text)
            for sentence in (s for items in view["note"]["sections"].values() for s in items):
                self.assertIn(sentence["text"], text)

    def test_export_has_no_transcript_beyond_the_note_sentences_and_no_evidence(self):
        view, _ = self.confirmed("C001-S53")
        text = self.get("C001-S53")[1].decode("utf-8")
        sentences = " ".join(s["text"] for items in view["note"]["sections"].values() for s in items)
        for seg in view["transcript"]:
            if seg["text"] not in sentences:
                self.assertNotIn(seg["text"], text)
        self.assertNotIn("evidence", text)

    def test_row_audit_and_view_status(self):
        self.confirmed("C001-S54")
        _, view = self.call("/api/sessions/C001-S54")
        self.assertEqual((view["export_status"], view["exports"]), ("not_exported", []))
        status, _, headers = self.get("C001-S54", "txt")
        eid = headers["X-Export-Id"]
        _, view = self.call("/api/sessions/C001-S54")
        self.assertEqual(view["export_status"], "downloaded")
        row = view["exports"][0]
        self.assertEqual((row["export_id"], row["format"], row["status"]), (eid, "txt", "downloaded"))
        self.assertEqual(row["note_sha256"], view["signature"]["note_sha256"])
        self.assertTrue(any(eid in r["detail"] and "C001-S54" in r["detail"] for r in store.audit_rows("export_note")))
        from haven import server
        meta = server.ORCH.sessions_meta({"sessions": ["C001-S54"]})[0]
        self.assertEqual(meta["export_status"], "downloaded")

    def test_archiving_is_idempotent_and_scoped_to_the_session(self):
        self.confirmed("C001-S55")
        self.confirmed("C001-S56")
        eid = self.get("C001-S55")[2]["X-Export-Id"]
        status, out = self.call(f"/api/sessions/C001-S55/exports/{eid}/archived", {"archive_ref": "院內 A-123"})
        self.assertEqual(status, 200, out)
        first = out["export"]
        self.assertEqual((first["status"], first["archive_ref"]), ("archived", "院內 A-123"))
        self.assertTrue(first["archived_at"])
        status, again = self.call(f"/api/sessions/C001-S55/exports/{eid}/archived", {"archive_ref": "other"})
        self.assertEqual((status, again["export"]), (200, first))
        self.assertEqual(len(store.audit_rows("archive_export")), 1)
        _, view = self.call("/api/sessions/C001-S55")
        self.assertEqual(view["export_status"], "archived")
        # an export id of another session, an unknown one, and a too long reference
        self.assertEqual(self.call(f"/api/sessions/C001-S56/exports/{eid}/archived", {})[0], 404)
        self.assertEqual(self.call("/api/sessions/C001-S55/exports/EXNOPE/archived", {})[0], 404)
        eid2 = self.get("C001-S56")[2]["X-Export-Id"]
        self.assertEqual(self.call(f"/api/sessions/C001-S56/exports/{eid2}/archived", {"archive_ref": "x" * 101})[0], 400)
        self.assertEqual(self.call(f"/api/sessions/C001-S56/exports/{eid2}/archived", {})[1]["export"]["status"], "archived")

    def test_addenda_are_included(self):
        self.confirmed("C001-S57")
        self.call("/api/sessions/C001-S57/addendum", {"text": "補充：下次回診日期確認。", "author": "王心理師"})
        text = self.get("C001-S57")[1].decode("utf-8")
        self.assertIn("補充說明", text)
        self.assertIn("下次回診日期確認", text)


class ExportAccessTest(AccountCase, unittest.TestCase):

    def test_another_therapists_session_is_404_without_side_effects(self):
        self.data.add_session("C001-S58", LINES, session_no=58)
        wang = self.login("wang")
        lin = self.login("lin")
        status, view, _ = self.req("/api/sessions/C001-S58/open", {}, cookie=wang)
        self.assertEqual(status, 200, view)
        status, sig, _ = self.req("/api/sessions/C001-S58/sign", {"note": plain(view), "base_revision": view["revision"], "signed_by": "x"}, cookie=wang)
        self.assertEqual(status, 200, sig)
        status, body, headers = self.req("/api/sessions/C001-S58/export?format=md", cookie=lin)
        self.assertEqual(status, 404)
        self.assertEqual(store.audit_rows("export_note"), [])
        status, body, headers = raw(self.base, "/api/sessions/C001-S58/export?format=md", headers=dict(H, Cookie=f"{accounts.COOKIE}={wang}"))
        self.assertEqual(status, 200)
        self.assertIn("王心理師", body.decode("utf-8"))
        eid = headers["X-Export-Id"]
        self.assertEqual(self.req(f"/api/sessions/C001-S58/exports/{eid}/archived", {}, cookie=lin)[0], 404)
        self.assertEqual(store.audit_rows("archive_export"), [])


if __name__ == "__main__":
    unittest.main()
