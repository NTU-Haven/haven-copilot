"""One main PC, several therapists (2026-11-18): accounts, who may open which
client, recording in the browser, two screens on one draft, and the clinic
network checks."""

import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from helpers import H, IsolatedData, ServerCase, free_port

from haven import accounts, config, server, store  # noqa: E402
from haven.orchestrator import Orchestrator  # noqa: E402
from test_server_robust import fake_transcribe  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PASSWORD = "correct horse battery"


def assign(client_id: str, username: str) -> None:
    clients = json.loads(config.CLIENTS_FILE.read_text(encoding="utf-8"))
    for c in clients:
        if c["id"] == client_id:
            c["therapist_user"] = username
    config.CLIENTS_FILE.write_text(json.dumps(clients, ensure_ascii=False), encoding="utf-8")


class AccountCase(ServerCase):
    """Two therapists and an admin. C001 is wang's, C002 is lin's."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        accounts.reset()
        accounts.set_user("wang", "王心理師", PASSWORD)
        accounts.set_user("lin", "林心理師", PASSWORD)
        accounts.set_user("boss", "所長", PASSWORD, role="admin")
        assign("C001", "wang")
        assign("C002", "lin")

    @classmethod
    def tearDownClass(cls):
        accounts.reset()
        super().tearDownClass()

    def req(self, path, body=None, cookie=None, raw=None, method=None, headers=None):
        hdrs = dict(H, **(headers or {}))
        if cookie:
            hdrs["Cookie"] = f"{accounts.COOKIE}={cookie}"
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        r = urllib.request.Request(self.base + path, data=data, headers=hdrs, method=method or ("POST" if data is not None else "GET"))
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.load(resp), resp.headers
        except urllib.error.HTTPError as e:
            with e:
                return e.code, json.load(e), e.headers

    def login(self, username="wang", password=PASSWORD):
        status, body, headers = self.req("/api/login", {"username": username, "password": password})
        assert status == 200, body
        cookie = headers["Set-Cookie"]
        return cookie.split(";")[0].split("=", 1)[1]


class SignInTest(AccountCase, unittest.TestCase):
    def test_nothing_without_signing_in(self):
        for path in ("/api/clients", "/api/status", "/api/clients/C001/brief", "/api/sessions/C001-S04", "/api/audit"):
            status, body, _ = self.req(path)
            self.assertEqual(status, 401, path)
            self.assertTrue(body["login"])
        self.assertEqual(self.req("/api/sessions/C001-S04/open", {})[0], 401)
        status, me, _ = self.req("/api/me")
        self.assertEqual(status, 200)
        self.assertTrue(me["login_required"])
        self.assertIsNone(me["user"])

    def test_wrong_password_does_not_say_which_part_was_wrong(self):
        a = self.req("/api/login", {"username": "wang", "password": "wrong password!"})
        b = self.req("/api/login", {"username": "nobody", "password": "wrong password!"})
        self.assertEqual((a[0], b[0]), (401, 401))
        self.assertEqual(a[1]["message_zh"], b[1]["message_zh"])
        self.assertNotIn("Set-Cookie", a[2])

    def test_cookie_is_http_only_and_same_site(self):
        _, _, headers = self.req("/api/login", {"username": "wang", "password": PASSWORD})
        cookie = headers["Set-Cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertNotIn("Secure", cookie)  # plain http on loopback; https adds it (TlsTest)

    def test_sign_out_ends_the_session(self):
        token = self.login()
        self.assertEqual(self.req("/api/clients", cookie=token)[0], 200)
        _, _, headers = self.req("/api/logout", {}, cookie=token)
        self.assertIn("Max-Age=0", headers["Set-Cookie"])
        self.assertEqual(self.req("/api/clients", cookie=token)[0], 401)

    def test_idle_session_is_signed_out(self):
        token = self.login()
        with mock.patch.object(accounts, "IDLE_SECONDS", 0):
            time.sleep(0.01)
            self.assertEqual(self.req("/api/clients", cookie=token)[0], 401)
        self.assertEqual(self.req("/api/clients", cookie=token)[0], 401)  # gone, not just paused

    def test_too_many_failures_lock_the_account_for_a_while(self):
        accounts.set_user("temp", "暫時", PASSWORD)
        for _ in range(accounts.MAX_FAILURES):
            self.req("/api/login", {"username": "temp", "password": "not the password"})
        status, body, _ = self.req("/api/login", {"username": "temp", "password": PASSWORD})
        self.assertEqual(status, 401)
        self.assertIn("分鐘後再試", body["message_zh"])
        accounts.remove_user("temp")
        accounts.reset()

    def test_new_password_signs_that_account_out(self):
        accounts.set_user("temp2", "暫時", PASSWORD)
        token = self.login("temp2")
        accounts.set_user("temp2", "暫時", PASSWORD + "!")
        self.assertEqual(self.req("/api/me", cookie=token)[1]["user"], None)
        accounts.remove_user("temp2")

    def test_password_is_stored_as_a_hash(self):
        raw = (config.DATA_DIR / "users.json").read_text(encoding="utf-8")
        self.assertNotIn(PASSWORD, raw)
        self.assertEqual(os.stat(config.DATA_DIR / "users.json").st_mode & 0o777, 0o600)


class WhoMayOpenWhatTest(AccountCase, unittest.TestCase):
    def test_a_therapist_sees_only_their_own_clients(self):
        wang, lin = self.login("wang"), self.login("lin")
        self.assertEqual([c["id"] for c in self.req("/api/clients", cookie=wang)[1]], ["C001"])
        self.assertEqual([c["id"] for c in self.req("/api/clients", cookie=lin)[1]], ["C002"])

    def test_someone_elses_client_looks_like_no_client(self):
        lin = self.login("lin")
        for path in ("/api/clients/C001/brief", "/api/sessions/C001-S04"):
            self.assertEqual(self.req(path, cookie=lin)[0], 404, path)
        for path in ("/api/sessions/C001-S04/open", "/api/sessions/C001-S04/verify", "/api/sessions/C001-S04/sign",
                     "/api/clients/C001/ask"):
            self.assertEqual(self.req(path, {"q": "父親"}, cookie=lin)[0], 404, path)
        status, body, _ = self.req("/api/recordings", {"client": "C001", "session_id": "C001-S70", "consent": True}, cookie=lin)
        self.assertEqual(status, 404)

    def test_an_admin_reads_the_audit_log_but_not_records(self):
        wang, boss = self.login("wang"), self.login("boss")
        self.assertEqual(self.req("/api/audit", cookie=wang)[0], 403)
        self.assertEqual(self.req("/api/audit", cookie=boss)[0], 200)
        self.assertEqual(self.req("/api/clients", cookie=boss)[1], [])
        self.assertEqual(self.req("/api/sessions/C001-S04", cookie=boss)[0], 404)

    def test_the_signer_is_the_account_and_the_audit_log_names_it(self):
        wang = self.login("wang")
        _, r, _ = self.req("/api/sessions/C001-S03/open", {}, cookie=wang)
        note = {"template": r["note"]["template"], "sections": {
            k: [{"text": s["text"], "evidence": s["evidence"], "source": s["source"],
                 "confirmed_by_therapist": True, "keep_reason": "測試"} for s in v] for k, v in r["note"]["sections"].items()}}
        dismissals = [{"segment_id": u["segment_id"], "reason": "測試"} for u in r.get("risk_uncovered", [])]
        status, sig, _ = self.req("/api/sessions/C001-S03/sign", {"signed_by": "隨便打的名字", "note": note, "risk_dismissals": dismissals},
                                  cookie=wang)
        self.assertEqual(status, 200, sig)
        self.assertEqual(sig["signed_by"], "王心理師")
        sign_rows = [a for a in store.recent_audit(50) if a["action"] == "sign_note"]
        self.assertTrue(sign_rows[0]["detail"].startswith("[wang] "))
        self.assertTrue(any(a["action"] == "sign_in" and a["detail"].startswith("[wang]") for a in store.recent_audit(50)))


class TwoScreensTest(AccountCase, unittest.TestCase):
    def test_a_save_from_a_stale_page_is_refused(self):
        wang = self.login("wang")
        _, r, _ = self.req("/api/sessions/C001-S04/open", {}, cookie=wang)
        base = r.get("revision", 0)
        note = {"template": r["note"]["template"], "sections": {
            k: [{"text": s["text"], "evidence": s["evidence"], "source": s["source"]} for s in v] for k, v in r["note"]["sections"].items()}}
        status, first, _ = self.req("/api/sessions/C001-S04/verify", dict(note, base_revision=base), cookie=wang)
        self.assertEqual(status, 200)
        self.assertEqual(first["revision"], base + 1)
        # The other screen still has the old revision.
        status, body, _ = self.req("/api/sessions/C001-S04/verify", dict(note, base_revision=base), cookie=wang)
        self.assertEqual(status, 409)
        self.assertTrue(body["conflict"])
        self.assertIn("王心理師", body["message_zh"])
        self.assertEqual(self.req("/api/sessions/C001-S04/restore", {"base_revision": base}, cookie=wang)[0], 409)
        self.assertEqual(self.req("/api/sessions/C001-S04/verify", dict(note, base_revision=first["revision"]), cookie=wang)[0], 200)

    def test_page_shows_the_revision(self):
        wang = self.login("wang")
        _, r, _ = self.req("/api/sessions/C001-S04", cookie=wang)
        self.assertIn("revision", r)


class RecordingTest(AccountCase, unittest.TestCase):
    def start(self, token, sid, **extra):
        return self.req("/api/recordings", {"client": "C001", "session_id": sid, "consent": True, "simulated": True,
                                            "mime": "audio/webm", **extra}, cookie=token)

    def chunk(self, token, rid, seq, text):
        return self.req(f"/api/recordings/{rid}/chunk?seq={seq}", raw=text.encode(), cookie=token,
                        headers={"Content-Type": "application/octet-stream"})

    def test_pieces_become_one_session_and_the_audio_is_removed(self):
        wang = self.login("wang")
        status, rec, _ = self.start(wang, "C001-S60")
        self.assertEqual(status, 200)
        rid = rec["recording_id"]
        self.assertEqual(self.chunk(wang, rid, 0, "這週過得怎麼樣？\n")[0], 200)
        self.assertEqual(self.chunk(wang, rid, 0, "這週過得怎麼樣？\n")[1]["received"], 1)  # a repeat is ignored
        status, body, _ = self.chunk(wang, rid, 2, "跳過了一段\n")
        self.assertEqual((status, body["expected"]), (409, 1))
        self.assertEqual(self.chunk(wang, rid, 1, "好多了，比較睡得著。\n")[0], 200)
        with mock.patch.object(server, "transcribe_audio", fake_transcribe()):
            status, r, _ = self.req(f"/api/recordings/{rid}/finish", {}, cookie=wang)
        self.assertEqual(status, 200, r)
        self.assertEqual([s["text"] for s in r["transcript"]], ["這週過得怎麼樣？", "好多了，比較睡得著。"])
        self.assertEqual(list((config.DATA_DIR / "tmp").iterdir()), [])
        actions = [a["action"] for a in store.recent_audit(30)]
        self.assertIn("recording_started", actions)
        self.assertIn("transcribe_recording", actions)
        self.assertEqual(self.chunk(wang, rid, 2, "太晚了\n")[0], 404)

    def test_needs_consent_and_a_free_id(self):
        wang = self.login("wang")
        self.assertEqual(self.start(wang, "C001-S61", consent=False)[0], 400)
        self.assertEqual(self.start(wang, "C001-S04")[0], 409)  # exists
        status, rec, _ = self.start(wang, "C001-S62")
        self.assertEqual(self.start(wang, "C001-S62")[0], 409)  # being recorded
        self.req(f"/api/recordings/{rec['recording_id']}/cancel", {}, cookie=wang)

    def test_cancel_removes_the_audio_and_frees_the_id(self):
        wang = self.login("wang")
        _, rec, _ = self.start(wang, "C001-S63")
        self.chunk(wang, rec["recording_id"], 0, "一段話\n")
        self.assertEqual(self.req(f"/api/recordings/{rec['recording_id']}/cancel", {}, cookie=wang)[0], 200)
        self.assertEqual(list((config.DATA_DIR / "tmp").iterdir()), [])
        _, again, _ = self.start(wang, "C001-S63")
        self.assertEqual(self.req(f"/api/recordings/{again['recording_id']}/cancel", {}, cookie=wang)[0], 200)

    def test_another_account_cannot_send_to_or_finish_my_recording(self):
        wang, lin = self.login("wang"), self.login("lin")
        _, rec, _ = self.start(wang, "C001-S64")
        self.assertEqual(self.chunk(lin, rec["recording_id"], 0, "x\n")[0], 404)
        self.assertEqual(self.req(f"/api/recordings/{rec['recording_id']}/finish", {}, cookie=lin)[0], 404)
        self.req(f"/api/recordings/{rec['recording_id']}/cancel", {}, cookie=wang)

    def test_a_recording_that_stops_sending_is_removed(self):
        wang = self.login("wang")
        _, rec, _ = self.start(wang, "C001-S65")
        self.chunk(wang, rec["recording_id"], 0, "一段話\n")
        with mock.patch.object(server, "RECORDING_IDLE", -1):
            server._sweep_recordings()
        self.assertEqual(list((config.DATA_DIR / "tmp").iterdir()), [])
        self.assertIn("recording_abandoned", [a["action"] for a in store.recent_audit(10)])
        status, again, _ = self.start(wang, "C001-S65")
        self.assertEqual(status, 200)  # the id is free again
        self.req(f"/api/recordings/{again['recording_id']}/cancel", {}, cookie=wang)


class NoAccountsTest(ServerCase, unittest.TestCase):
    """A laptop with no accounts works as before, for the one person at it."""

    def test_open_without_signing_in(self):
        status, me = self.call("/api/me")
        self.assertEqual((status, me["login_required"], me["user"]), (200, False, None))
        self.assertEqual(self.call("/api/clients")[0], 200)
        status, body = self.call("/api/login", {"username": "x", "password": "y"})
        self.assertEqual(status, 409)


class NetworkTest(unittest.TestCase):
    def handler(self, address):
        h = server.Handler.__new__(server.Handler)
        h.client_address = (address, 50000)
        return h

    def test_only_this_machine_and_the_private_network(self):
        for ok in ("127.0.0.1", "192.168.1.30", "10.0.0.8", "172.16.4.2", "::1", "::ffff:192.168.1.30", "fe80::1%en0"):
            self.assertTrue(self.handler(ok)._address_ok(), ok)
        for bad in ("8.8.8.8", "140.112.1.1", "2001:4860:4860::8888", "::ffff:8.8.8.8", "not an address"):
            self.assertFalse(self.handler(bad)._address_ok(), bad)

    def test_ready_when_accounts_certificate_and_names_are_set(self):
        with IsolatedData():
            accounts.set_user("wang", "王心理師", PASSWORD)
            with mock.patch.object(config, "TLS_CERT", "c.pem"), mock.patch.object(config, "TLS_KEY", "k.pem"), \
                    mock.patch.object(config, "HOSTNAMES", ["192.168.1.20"]):
                self.assertEqual(server.lan_problems("0.0.0.0"), [])
                self.assertIn("192.168.1.20", server._allowed_hosts())


@unittest.skipUnless(shutil.which("openssl"), "needs the openssl command")
class TlsTest(unittest.TestCase):
    def test_https_with_the_made_certificate(self):
        with IsolatedData() as data:
            out = subprocess.run([sys.executable, str(REPO / "scripts" / "make_cert.py"), "127.0.0.1", "localhost"],
                                 capture_output=True, text=True, env=dict(os.environ, HAVEN_DATA_DIR=str(data.dir)))
            self.assertEqual(out.returncode, 0, out.stderr)
            cert, key = data.dir / "tls" / "cert.pem", data.dir / "tls" / "key.pem"
            self.assertEqual(os.stat(key).st_mode & 0o777, 0o600)
            accounts.reset()
            accounts.set_user("wang", "王心理師", PASSWORD)
            saved = (config.PORT, server.ORCH, config.TLS_CERT, config.TLS_KEY)
            config.PORT, config.TLS_CERT, config.TLS_KEY = free_port(), str(cert), str(key)
            server.ORCH = Orchestrator(use_llm=False)
            httpd = ThreadingHTTPServer(("127.0.0.1", config.PORT), server.Handler)
            httpd.socket = server.tls_context().wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
            threading.Thread(target=httpd.serve_forever, daemon=True).start()
            try:
                ctx = ssl.create_default_context(cafile=str(cert))  # trusted the way a clinic laptop would trust it
                req = urllib.request.Request(f"https://localhost:{config.PORT}/api/login", method="POST", headers=H,
                                             data=json.dumps({"username": "wang", "password": PASSWORD}).encode())
                with urllib.request.urlopen(req, context=ctx) as r:
                    self.assertIn("Secure", r.headers["Set-Cookie"])
                    self.assertTrue(json.load(r)["user"])
                with urllib.request.urlopen(urllib.request.Request(f"https://localhost:{config.PORT}/api/me", headers=H), context=ctx) as r:
                    self.assertTrue(json.load(r)["tls"])
            finally:
                httpd.shutdown()
                httpd.server_close()
                config.PORT, server.ORCH, config.TLS_CERT, config.TLS_KEY = saved
                accounts.reset()


class ScriptTest(unittest.TestCase):
    def test_add_user_assigns_clients(self):
        with IsolatedData() as data:
            env = dict(os.environ, HAVEN_DATA_DIR=str(data.dir), HAVEN_NEW_PASSWORD=PASSWORD)
            out = subprocess.run([sys.executable, str(REPO / "scripts" / "add_user.py"), "add", "wang", "--name", "王心理師", "--clients", "C001"],
                                 capture_output=True, text=True, env=env)
            self.assertEqual(out.returncode, 0, out.stderr)
            clients = {c["id"]: c for c in json.loads((data.dir / "clients.json").read_text(encoding="utf-8"))}
            self.assertEqual(clients["C001"].get("therapist_user"), "wang")
            self.assertNotIn("therapist_user", clients["C002"])
            listing = subprocess.run([sys.executable, str(REPO / "scripts" / "add_user.py"), "list"], capture_output=True, text=True, env=env)
            self.assertIn("C002", listing.stdout)  # listed as unassigned
            short = subprocess.run([sys.executable, str(REPO / "scripts" / "add_user.py"), "add", "lin", "--name", "林"],
                                   capture_output=True, text=True, env=dict(env, HAVEN_NEW_PASSWORD="short"))
            self.assertNotEqual(short.returncode, 0)


if __name__ == "__main__":
    unittest.main()
