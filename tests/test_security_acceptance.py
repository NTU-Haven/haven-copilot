"""Ticket 14: security acceptance (spec 13.2, 10 common rules). Therapist A (lin) knows every
id of therapist B (wang) and tries each operation: all answer 404, exactly like an id that does
not exist, and change nothing. An admin reads no draft, transcript or audio, with or without
an open-records grant. All data is simulated."""

import json
import sqlite3
import unittest
import urllib.error
import urllib.request
from unittest import mock

from helpers import H
from test_accounts import AccountCase
from test_regenerate import LINES, plain
from test_server_robust import UploadMixin, fake_transcribe

from haven import accounts, audio, config, files, jobs, server, store  # noqa: E402

DRAFT, SIGNED = "C001-S61", "C001-S62"
UNKNOWN = {"sid": "C001-S98", "client": "C999", "job": "nojobnojobnojob", "export": "noexportid", "ticket": "A" * 22}
TABLES = ("results", "signed_notes", "addenda", "exports", "jobs", "audio_assets", "verdicts")


def raw(base, path, cookie=None, body=None, method=None):
    headers = dict(H)
    if cookie:
        headers["Cookie"] = f"{accounts.COOKIE}={cookie}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read(), e.headers


# (name, method, path template, body). {sid} {client} {job} {export} {ticket} are filled in.
# Every operation of spec 13.2 is a row; the test checks that each row ran.
BASE = {"base_revision": 0}
ROUTES = [
    ("read session", "GET", "/api/sessions/{sid}", None),
    ("open session", "POST", "/api/sessions/{sid}/open", {}),
    ("read brief", "GET", "/api/clients/{client}/brief", None),
    ("edit transcript", "POST", "/api/sessions/{sid}/transcript", dict(BASE, segments=[{"id": 1, "text": "改寫"}])),
    ("edit speakers", "POST", "/api/sessions/{sid}/speakers", dict(BASE, speakers={"1": "client"})),
    ("save draft", "POST", "/api/sessions/{sid}/verify", dict(BASE, template="SOAP", sections={"S": [{"text": "x", "evidence": [1], "source": "ai"}]})),
    ("restore draft", "POST", "/api/sessions/{sid}/restore", dict(BASE)),
    ("regenerate", "POST", "/api/sessions/{sid}/process", dict(BASE, discard_edits=True)),
    ("mark reviewed", "POST", "/api/sessions/{sid}/reviewed", dict(BASE, reviewed=True)),
    ("confirm (sign)", "POST", "/api/sessions/{sid}/sign", dict(BASE, signed_by="林心理師", note={"template": "SOAP", "sections": {}})),
    ("addendum", "POST", "/api/sessions/{sid}/addendum", {"text": "補充", "author": "林"}),
    ("audio ticket", "POST", "/api/sessions/{sid}/audio/ticket", {}),
    ("play with a ticket", "GET", "/media/audio/{ticket}", None),
    ("get job", "GET", "/api/jobs/{job}", None),
    ("retry job", "POST", "/api/jobs/{job}/retry", {}),
    ("export", "GET", "/api/sessions/{sid}/export?format=md", None),
    ("archive export", "POST", "/api/sessions/{sid}/exports/{export}/archived", {"archive_ref": "x"}),
    ("delete audio", "POST", "/api/sessions/{sid}/audio/delete", {}),
    ("upload into the client", "POST", "/api/upload?client={client}&session_id={client}-S77&consent=1&simulated=1", {}),
]


class SecurityAcceptanceTest(UploadMixin, AccountCase, unittest.TestCase):
    """B = wang owns C001; A = lin owns C002; boss is an admin."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.patch = mock.patch.object(server, "transcribe_audio", fake_transcribe())
        cls.patch.start()
        me = cls("test_the_fixture_is_real")  # the helpers are instance methods
        cls.b, cls.a, cls.boss = me.login("wang"), me.login("lin"), me.login("boss")
        # Session 1: an uploaded recording gives a transcript, a draft, an audio asset and a finished job.
        status, up = me.upload(DRAFT, "最近睡不好，每天大概只睡四個小時。\n睡不好的時候你都在想什麼？",
                                headers={"Cookie": f"{accounts.COOKIE}={cls.b}"}, wait=False)
        assert status == 202, up
        assert jobs.wait(up["job_id"])["status"] == "succeeded"
        cls.job_id = up["job_id"]
        # Session 2: a confirmed note with an addendum and an export.
        cls.data.add_session(SIGNED, LINES, session_no=62)
        _, view, _ = me.req(f"/api/sessions/{SIGNED}/open", {}, cookie=cls.b)
        s, sig, _ = me.req(f"/api/sessions/{SIGNED}/sign", {"note": plain(view), "base_revision": view["revision"], "signed_by": "王心理師"}, cookie=cls.b)
        assert s == 200, sig
        s, sig, _ = me.req(f"/api/sessions/{SIGNED}/addendum", {"text": "之後補充", "author": "王"}, cookie=cls.b)
        assert s == 200, sig
        _, _, headers = raw(cls.base, f"/api/sessions/{SIGNED}/export?format=md", cls.b)
        cls.export_id = headers["X-Export-Id"]
        s, t, _ = raw(cls.base, f"/api/sessions/{DRAFT}/audio/ticket", cls.b, {})
        assert s == 200, t
        cls.ticket = json.loads(t)["url"].rsplit("/", 1)[1]

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        super().tearDownClass()

    # ---- helpers ----

    def ids(self, sid, unknown=False):
        if unknown:
            return dict(UNKNOWN, sid=UNKNOWN["sid"])
        return {"sid": sid, "client": "C001", "job": self.job_id, "export": self.export_id, "ticket": self.ticket}

    def snapshot(self):
        with store.connect() as c:
            c.row_factory = sqlite3.Row
            tables = {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY 1, 2")] for t in TABLES}
            tables["audit_n"] = c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        return {"tables": tables,
                "sessions": {p.name: p.read_bytes() for p in config.SESSIONS_DIR.glob("*.json")},
                "audio": {p.name: p.read_bytes() for p in files.audio_dir().iterdir()},
                "listing": sorted(str(p.relative_to(config.DATA_DIR)) for p in config.DATA_DIR.rglob("*")
                                  if not p.name.endswith(("-wal", "-shm", "-journal"))),
                "clients": config.CLIENTS_FILE.read_bytes()}

    def assert_no_side_effect(self, before, after, label):
        self.assertEqual(before["listing"], after["listing"], f"{label}: files in data/ changed")
        for key in ("sessions", "audio", "clients"):
            self.assertEqual(before[key], after[key], f"{label}: {key} changed")
        b, a = dict(before["tables"]), dict(after["tables"])
        self.assertGreaterEqual(a.pop("audit_n"), b.pop("audit_n"))  # only audit rows may be added
        self.assertEqual(b, a, f"{label}: a table changed")

    def attempt(self, cookie, route, sid, unknown=False):
        _, method, path, body = route
        return raw(self.base, path.format(**self.ids(sid, unknown)), cookie, body, method)

    # ---- the acceptance tests ----

    def test_the_fixture_is_real(self):
        """B can do what A cannot, so a 404 for A is not a broken fixture."""
        for path in (f"/api/sessions/{DRAFT}", f"/api/sessions/{SIGNED}", "/api/clients/C001/brief", f"/api/jobs/{self.job_id}",
                     f"/media/audio/{self.ticket}", f"/api/sessions/{SIGNED}/export?format=md"):
            self.assertEqual(raw(self.base, path, self.b)[0], 200, path)
        self.assertEqual(audio.get(DRAFT)["status"], "available")
        self.assertTrue(store.get_result(DRAFT))

    def test_every_operation_on_another_therapists_session_is_404_with_no_side_effect(self):
        covered = set()
        for route in ROUTES:
            for sid in (DRAFT, SIGNED):
                before = self.snapshot()
                status, body, _ = self.attempt(self.a, route, sid)
                label = f"{route[0]} on {sid}"
                self.assertEqual(status, 404, f"{label}: {body[:200]!r}")
                self.assert_no_side_effect(before, self.snapshot(), label)
                covered.add(route[0])
        self.assertEqual(covered, {r[0] for r in ROUTES})

    def test_an_unknown_id_answers_exactly_like_another_therapists_id(self):
        for route in ROUTES:
            real = self.attempt(self.a, route, DRAFT)
            fake = self.attempt(self.a, route, DRAFT, unknown=True)
            self.assertEqual(real[0], fake[0], route[0])
            self.assertEqual(real[1], fake[1], f"{route[0]}: existence is visible in the body")
            self.assertEqual(real[2]["Content-Type"], fake[2]["Content-Type"], route[0])

    def test_b_still_has_everything_afterwards(self):
        for route in ROUTES:
            self.attempt(self.a, route, DRAFT)
        self.assertEqual(raw(self.base, f"/api/sessions/{DRAFT}", self.b)[0], 200)
        self.assertEqual(raw(self.base, f"/media/audio/{self.ticket}", self.b)[0], 200)
        self.assertEqual(raw(self.base, f"/api/sessions/{SIGNED}/export?format=md", self.b)[0], 200)

    def test_b_cannot_read_a_clients_brief_either(self):
        self.assertEqual(raw(self.base, "/api/clients/C002/brief", self.b)[0], 404)

    def test_admin_without_a_grant_reads_no_draft_transcript_or_audio(self):
        self.assert_admin_blocked("no grant")
        self.assertEqual(raw(self.base, "/api/admin/clients/C001/records", self.boss)[0], 403)

    def test_admin_with_a_time_limited_grant_still_gets_no_audio_and_no_draft(self):
        status, body, _ = raw(self.base, "/api/admin/clients/C001/access", self.boss, {"reason": "legal", "note": "地院函 123 號"})
        self.assertEqual(status, 200, body)
        self.addCleanup(raw, self.base, "/api/admin/clients/C001/access/end", self.boss, {})
        status, records, _ = raw(self.base, "/api/admin/clients/C001/records", self.boss)
        self.assertEqual(status, 200)
        self.assertNotIn("/media/audio", records.decode())
        self.assertNotIn(self.ticket, records.decode())
        self.assert_admin_blocked("with grant")

    def assert_admin_blocked(self, label):
        for route in ROUTES:
            if route[0] == "upload into the client":
                continue
            for sid in (DRAFT, SIGNED):
                before = self.snapshot()
                status, body, _ = self.attempt(self.boss, route, sid)
                self.assertIn(status, (403, 404), f"{label}: {route[0]} on {sid}: {body[:200]!r}")
                self.assertNotIn("segments", body.decode(), f"{label}: {route[0]}")
                self.assert_no_side_effect(before, self.snapshot(), f"{label}: {route[0]} on {sid}")
        # The grant never covers the audio.
        self.assertEqual(raw(self.base, f"/api/sessions/{DRAFT}/audio/ticket", self.boss, {})[0], 404)
        self.assertEqual(raw(self.base, f"/media/audio/{self.ticket}", self.boss)[0], 404)


if __name__ == "__main__":
    unittest.main()
