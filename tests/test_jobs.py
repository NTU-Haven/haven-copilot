"""Background processing (ticket 05, AC-04): an upload is answered at once with a
job, the job runs queued -> running -> succeeded, a failed or interrupted job can
be retried, and the rest of Haven answers while a job runs."""

import json
import sqlite3
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

from helpers import IsolatedData, ServerCase, free_port
from test_asr import FakeWhisper
from test_server_robust import UploadMixin, fake_transcribe, settle_job

from haven import config, jobs, server, store  # noqa: E402
from haven.orchestrator import get_client  # noqa: E402

TEXT = "這週過得怎麼樣？\n不太好，睡不著，白天也提不起勁。"


class Gate:
    """A speech-to-text stand-in that holds the job until the test lets it go."""

    def __init__(self, fail=False):
        self.entered, self.release, self.fail = threading.Event(), threading.Event(), fail

    def __call__(self, path, *a, **k):
        self.entered.set()
        assert self.release.wait(10), "the test never released the job"
        if self.fail:
            raise RuntimeError("the process went away")
        return fake_transcribe()(path, *a, **k)


class JobsTest(UploadMixin, ServerCase, unittest.TestCase):

    def job(self, job_id):
        status, body = self.call(f"/api/jobs/{job_id}")
        self.assertEqual(status, 200, body)
        return body

    def until(self, job_id, status, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            body = self.job(job_id)
            if body["status"] == status:
                return body
            time.sleep(0.02)
        self.fail(f"job never reached {status}: {body}")

    def retry(self, job_id):
        return self.call(f"/api/jobs/{job_id}/retry", {})

    def test_upload_is_answered_at_once_and_the_job_runs_to_a_draft(self):
        gate = Gate()
        with mock.patch.object(server, "transcribe_audio", gate):
            t0 = time.monotonic()
            status, job = self.upload("C001-S40", TEXT, wait=False)
            self.assertEqual(status, 202, job)
            self.assertLess(time.monotonic() - t0, 1)
            self.assertEqual((job["session_id"], job["kind"], job["status"], job["attempt"]), ("C001-S40", "transcribe", "queued", 1))
            self.assertTrue(gate.entered.wait(5))
            running = self.until(job["job_id"], "running")
            self.assertEqual(running["stage"], "transcribe")
            # Other requests are answered while the job waits on speech-to-text.
            for path in ("/api/status", "/api/clients", "/api/sessions/C001-S04"):
                t0 = time.monotonic()
                self.call(path)
                self.assertLess(time.monotonic() - t0, 1, path)
            # The session does not exist yet, so its id cannot be taken by a second upload.
            self.assertEqual(self.upload("C001-S40", TEXT, wait=False)[0], 409)
            gate.release.set()
            done = jobs.wait(job["job_id"])
        self.assertEqual(done["status"], "succeeded")
        self.assertEqual(self.job(job["job_id"])["status"], "succeeded")
        status, page = self.call("/api/sessions/C001-S40/open", {})
        self.assertEqual(status, 200)
        self.assertEqual(page["transcript"][0]["text"], "這週過得怎麼樣？")
        self.assertIsNotNone(store.get_result("C001-S40"))  # drafted by the job, not by the open
        self.assertEqual(list((config.DATA_DIR / "audio").glob(f"job-{job['job_id']}*")), [])  # recording removed

    def test_asr_server_down_fails_with_a_message_and_a_retry_succeeds_once(self):
        port = free_port()
        saved = (config.ASR_BACKEND, config.ASR_URL)
        config.ASR_BACKEND, config.ASR_URL = "openai", f"http://127.0.0.1:{port}"
        self.addCleanup(lambda: setattr(config, "ASR_BACKEND", saved[0]) or setattr(config, "ASR_URL", saved[1]))
        status, job = self.upload("C001-S41", "RIFF-fake-audio", wait=False)
        self.assertEqual(status, 202)
        failed = jobs.wait(job["job_id"])
        self.assertEqual((failed["status"], failed["error_code"]), ("failed", "asr_unreachable"))
        self.assertIn("語音轉寫服務", failed["message_zh"])
        self.assertIsNotNone(jobs.audio_of(job["job_id"]))  # kept for the retry
        self.assertFalse((config.SESSIONS_DIR / "C001-S41.json").exists())
        _, brief = self.call("/api/clients/C001/brief")  # the client page can offer the retry
        self.assertIn(("C001-S41", "failed"), [(j["session_id"], j["status"]) for j in brief["jobs"]])

        whisper = ThreadingHTTPServer(("127.0.0.1", port), FakeWhisper)  # the speech-to-text server is back
        threading.Thread(target=whisper.serve_forever, daemon=True).start()
        self.addCleanup(whisper.server_close)
        self.addCleanup(whisper.shutdown)
        status, again = self.retry(job["job_id"])
        self.assertEqual((status, again["status"], again["attempt"]), (202, "queued", 2))
        self.assertEqual(jobs.wait(job["job_id"])["status"], "succeeded")
        self.assertEqual(get_client("C001")["sessions"].count("C001-S41"), 1)
        status, page = self.call("/api/sessions/C001-S41/open", {})
        self.assertEqual(status, 200)
        self.assertIn("活著好累", page["transcript"][0]["text"])
        self.assertIsNone(jobs.audio_of(job["job_id"]))
        self.assertEqual(self.retry(job["job_id"])[0], 409)  # nothing left to retry

    def test_a_job_cut_off_by_a_restart_is_interrupted_and_can_be_retried(self):
        gate = Gate(fail=True)
        with mock.patch.object(server, "transcribe_audio", gate):
            _, job = self.upload("C001-S42", TEXT, wait=False)
            self.assertTrue(gate.entered.wait(5))
            self.until(job["job_id"], "running")
            self.assertEqual(jobs.startup(), 1)  # what serve() does when Haven starts again
            body = self.job(job["job_id"])
            self.assertEqual((body["status"], body["error_code"]), ("interrupted", "interrupted"))
            self.assertIn("重試", body["message_zh"])
            gate.release.set()  # the old worker ends; it must not change the job any more
            jobs.wait(job["job_id"])
        self.assertEqual(self.job(job["job_id"])["status"], "interrupted")
        self.assertIsNotNone(jobs.audio_of(job["job_id"]))  # outside data/tmp, so start-up kept it
        with mock.patch.object(server, "transcribe_audio", fake_transcribe()):
            self.assertEqual(self.retry(job["job_id"])[0], 202)
            status, page = settle_job(self.base, job["job_id"])
        self.assertEqual(status, 200, page)
        self.assertEqual(get_client("C001")["sessions"].count("C001-S42"), 1)
        actions = [a["action"] for a in store.recent_audit(40)]
        self.assertIn("job_interrupted", actions)
        self.assertIn("job_retry", actions)

    def test_retry_after_the_transcript_was_stored_only_drafts(self):
        asr_calls = []

        def counted(*a, **k):
            asr_calls.append(1)
            return fake_transcribe()(*a, **k)

        with mock.patch.object(server, "transcribe_audio", counted):
            with mock.patch.object(server.ORCH, "process_session", side_effect=RuntimeError("model down")), mock.patch("sys.stderr"):
                status, failed = self.upload("C001-S43", TEXT)
            self.assertEqual((status, failed["error_code"]), ("failed", "internal"))
            self.assertTrue((config.SESSIONS_DIR / "C001-S43.json").exists())
            self.assertIsNone(jobs.audio_of(failed["job_id"]))  # transcribed: the recording is gone
            self.assertEqual(self.retry(failed["job_id"])[0], 202)
            status, page = settle_job(self.base, failed["job_id"])
        self.assertEqual(status, 200)
        self.assertEqual(len(asr_calls), 1)  # speech-to-text ran once
        self.assertEqual(get_client("C001")["sessions"].count("C001-S43"), 1)

    def test_two_retries_at_once_one_is_accepted(self):
        with mock.patch.object(server, "transcribe_audio", side_effect=OSError("down")):
            _, failed = self.upload("C001-S44", TEXT)
        self.assertEqual(failed["status"], "failed")
        gate = Gate()
        barrier, out = threading.Barrier(2), []

        def go():
            barrier.wait()
            out.append(self.retry(failed["job_id"])[0])

        with mock.patch.object(server, "transcribe_audio", gate):
            threads = [threading.Thread(target=go) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            gate.release.set()
            self.assertEqual(jobs.wait(failed["job_id"])["status"], "succeeded")
        self.assertEqual(sorted(out), [202, 409])
        self.assertEqual(self.job(failed["job_id"])["attempt"], 2)
        self.assertEqual(get_client("C001")["sessions"].count("C001-S44"), 1)

    def test_retry_without_the_recording_is_audio_gone(self):
        with mock.patch.object(server, "transcribe_audio", side_effect=OSError("down")):
            _, failed = self.upload("C001-S45", TEXT)
        jobs.drop_audio(failed["job_id"])
        status, body = self.retry(failed["job_id"])
        self.assertEqual((status, body["error_code"]), (409, "audio_gone"))
        self.assertIn("message_zh", body)
        self.assertEqual(self.job(failed["job_id"])["status"], "failed")

    def test_unknown_or_malformed_job_is_404_and_the_header_is_required(self):
        self.assertEqual(self.call("/api/jobs/no-such-job-id")[0], 404)
        self.assertEqual(self.call("/api/jobs/..%2F..%2Fx")[0], 404)
        self.assertEqual(self.retry("no-such-job-id")[0], 404)
        self.assertEqual(self.call("/api/jobs/no-such-job-id", headers={})[0], 403)


class SchemaTest(unittest.TestCase):
    def test_database_is_in_wal_mode_and_an_old_one_gets_the_jobs_table(self):
        with IsolatedData():
            conn = sqlite3.connect(config.DB_FILE)
            conn.execute("CREATE TABLE results (session_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL)")
            conn.commit()
            conn.close()
            store.audit("test", "after upgrade")
            with store.connect() as c:
                self.assertEqual(c.execute("PRAGMA journal_mode").fetchone()[0], "wal")
                c.execute("INSERT INTO jobs (job_id, session_id, kind, status, created_at, updated_at) VALUES ('a', 'S', 'transcribe', 'running', '', '')")
                with self.assertRaises(sqlite3.IntegrityError):  # one active job per session and kind
                    c.execute("INSERT INTO jobs (job_id, session_id, kind, status, created_at, updated_at) VALUES ('b', 'S', 'transcribe', 'queued', '', '')")
                c.execute("INSERT INTO jobs (job_id, session_id, kind, status, created_at, updated_at) VALUES ('c', 'S', 'transcribe', 'failed', '', '')")
                self.assertEqual(json.loads(json.dumps(c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])), 2)


if __name__ == "__main__":
    unittest.main()
