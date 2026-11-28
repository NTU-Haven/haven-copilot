"""Original audio lifecycle (ticket 08, AC-10, spec 11.2): kept after transcription in
data/audio, removed on sign-off, on expiry (sweep with an injected clock) or by hand;
a delete that fails is retried; a failed transcription keeps the file for a retry."""

import os
import stat
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from helpers import ServerCase
from test_accounts import AccountCase
from test_server_robust import UploadMixin, fake_transcribe

from haven import audio, config, files, jobs, server, store  # noqa: E402

TEXT = "這週過得怎麼樣？\n不太好，睡不著，白天也提不起勁。"


def actions():
    return [a["action"] for a in store.recent_audit(80)]


class AudioLifecycleTest(UploadMixin, ServerCase, unittest.TestCase):

    def setUp(self):
        patcher = mock.patch.object(server, "transcribe_audio", fake_transcribe())
        patcher.start()
        self.addCleanup(patcher.stop)

    def view(self, sid):
        status, r = self.call(f"/api/sessions/{sid}")
        self.assertEqual(status, 200, r)
        return r

    def test_upload_keeps_the_audio_available_until_created_plus_ttl(self):
        status, page = self.upload("C001-S70", TEXT)
        self.assertEqual(status, 200)
        a = audio.get("C001-S70")
        self.assertEqual(a["status"], "available")
        self.assertEqual(audio.MIME[Path(a["storage_ref"]).suffix], a["mime"])
        self.assertEqual(a["bytes"], len(TEXT.encode()))
        created, expires = audio._parse(a["created_at"]), audio._parse(a["expires_at"])
        self.assertEqual(expires - created, timedelta(hours=config.AUDIO_TTL_HOURS))
        self.assertEqual(config.AUDIO_TTL_HOURS, 72)
        path = files.audio_dir() / a["storage_ref"]
        self.assertEqual(path.read_bytes(), TEXT.encode())  # the file the server made, not a client path
        self.assertNotIn("a.wav", a["storage_ref"])
        self.assertGreaterEqual(len(a["audio_id"]), 22)  # 128 random bits, url-safe
        self.assertEqual(list((config.DATA_DIR / "tmp").iterdir()), [])
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        view = self.view("C001-S70")
        self.assertEqual((view["audio_status"], view["audio_expires_at"]), ("available", a["expires_at"]))
        self.assertIn(view["audio_hours_left"], (71, 72))
        self.assertEqual(self.call("/api/clients/C001/brief")[1]["audio"]["C001-S70"], "available")
        entry = next(a for a in store.recent_audit(80) if a["action"] == "audio_stored")
        self.assertIn("C001-S70", entry["detail"])
        self.assertNotIn("睡不著", entry["detail"])

    def signable(self, sid):
        """A recording kept for demo session ``sid`` (C001-S04), and its draft with the planted red sentence fixed."""
        src = Path(tempfile.mkdtemp()) / "demo.m4a"
        src.write_bytes(b"audio")
        audio.store_file(sid, src)
        audio.mark_available(sid)
        _, note = self.draft(sid)
        for s in note["sections"]["P"]:
            if s["text"] == "安排下週五提前會談。":
                s["text"] = "下次會談提前到這週五。"
        return note

    def test_signing_deletes_the_audio_and_keeps_transcript_and_draft(self):
        note = self.signable("C001-S04")
        path = audio.for_session("C001-S04")["path"]
        self.assertTrue(path.is_file())
        status, body = self.call("/api/sessions/C001-S04/sign", {"signed_by": "王心理師", "note": note})
        self.assertEqual(status, 200, body)
        self.assertFalse(path.exists())
        sid = "C001-S04"
        a = audio.get(sid)
        self.assertEqual((a["status"], a["delete_reason"], a["cleanup_status"]), ("deleted", "confirm", "done"))
        view = self.view(sid)
        self.assertEqual((view["audio_status"], view["audio_delete_reason"]), ("deleted", "confirm"))
        self.assertTrue(view["signed"])
        self.assertTrue(view["transcript"])
        self.assertIn("audio_deleted", actions())

    def test_signing_keeps_the_audio_when_the_policy_is_off(self):
        note = self.signable("C001-S03")
        with mock.patch.object(config, "AUDIO_DELETE_ON_CONFIRM", False):
            status, body = self.call("/api/sessions/C001-S03/sign", {"signed_by": "王心理師", "note": note})
        self.assertEqual(status, 200, body)
        self.assertEqual(audio.get("C001-S03")["status"], "available")

    def test_sweep_past_the_ttl_deletes_the_audio_and_the_page_says_so(self):
        self.upload("C001-S73", TEXT)
        a = audio.get("C001-S73")
        path = files.audio_dir() / a["storage_ref"]
        expires = audio._parse(a["expires_at"])
        audio.sweep(expires - timedelta(minutes=1))
        self.assertTrue(path.exists())
        self.assertGreaterEqual(audio.sweep(expires + timedelta(minutes=1))["expired"], 1)  # and other tests' recordings
        self.assertFalse(path.exists())
        view = self.view("C001-S73")
        self.assertEqual((view["audio_status"], view["audio_delete_reason"]), ("deleted", "expiry"))
        self.assertTrue(view["audio_deleted_at"])
        self.assertIsNone(view["audio_hours_left"])
        self.assertTrue(view["transcript"])  # transcript and draft remain
        self.assertTrue(view["note"]["sections"])
        entries = actions()
        self.assertIn("audio_expired", entries)
        self.assertIn("audio_deleted", entries)
        self.assertIn("reason=expiry", next(a for a in store.recent_audit(80) if a["action"] == "audio_deleted")["detail"])
        self.assertEqual(audio.sweep(expires + timedelta(days=1)), {"expired": 0, "retried": 0, "failed": 0})  # nothing left to do

    def test_a_failed_delete_is_cleanup_failed_and_retried_with_backoff(self):
        self.upload("C001-S74", TEXT)
        a = audio.get("C001-S74")
        path = files.audio_dir() / a["storage_ref"]
        t0 = audio._parse(a["expires_at"]) + timedelta(minutes=1)
        with mock.patch.object(audio, "_unlink", side_effect=PermissionError("locked")):
            self.assertEqual(audio.sweep(t0)["failed"], 1)
            row = audio.get("C001-S74")
            self.assertEqual((row["status"], row["cleanup_status"], row["cleanup_attempts"], row["last_error"]),
                             ("cleanup_failed", "failed", 1, "PermissionError"))
            self.assertEqual(self.view("C001-S74")["audio_status"], "cleanup_failed")
            self.assertEqual(audio.sweep(t0 + timedelta(seconds=30)), {"expired": 0, "retried": 0, "failed": 0})  # backoff: 1 min
            self.assertEqual(audio.sweep(t0 + timedelta(minutes=2))["failed"], 1)
            self.assertEqual(audio.get("C001-S74")["cleanup_attempts"], 2)
            self.assertEqual(audio.sweep(t0 + timedelta(minutes=2, seconds=30))["failed"], 0)  # next wait is 5 min
            self.assertEqual(audio.sweep(t0 + timedelta(minutes=8))["failed"], 1)
            self.assertEqual(audio.get("C001-S74")["cleanup_attempts"], 3)
        self.assertTrue(path.exists())
        failed = [x for x in store.recent_audit(80) if x["action"] == "audio_cleanup_failed"]
        self.assertEqual(len(failed), 3)
        self.assertIn("error=PermissionError", failed[0]["detail"])
        self.assertNotIn("locked", failed[0]["detail"])  # the class of the error only
        self.assertEqual(audio.sweep(t0 + timedelta(minutes=20))["retried"], 0)  # 3rd failure waits 30 min
        self.assertEqual(audio.sweep(t0 + timedelta(minutes=40))["retried"], 1)  # the disk works again
        self.assertFalse(path.exists())
        row = audio.get("C001-S74")
        self.assertEqual((row["status"], row["delete_reason"], row["cleanup_attempts"]), ("deleted", "expiry", 3))

    def test_asr_failure_keeps_the_audio_for_a_retry_until_it_expires(self):
        with mock.patch.object(server, "transcribe_audio", side_effect=OSError("down")):
            status, failed = self.upload("C001-S75", TEXT)
        self.assertEqual(status, "failed")
        a = audio.get("C001-S75")
        self.assertEqual(a["status"], "failed")
        path = files.audio_dir() / a["storage_ref"]
        self.assertTrue(path.is_file())
        self.assertFalse(audio.for_session("C001-S75")["playable"])  # nothing to listen to yet
        status, again = self.call(f"/api/jobs/{failed['job_id']}/retry", {})
        self.assertEqual(status, 202)
        self.assertEqual(jobs.wait(failed["job_id"])["status"], "succeeded")
        self.assertEqual(audio.get("C001-S75")["status"], "available")
        self.assertEqual(audio.get("C001-S75")["audio_id"], a["audio_id"])  # the same recording
        # A failed one that is never retried is removed at expiry.
        with mock.patch.object(server, "transcribe_audio", side_effect=OSError("down")):
            _, failed2 = self.upload("C001-S76", TEXT)
        path2 = audio.asr_path("C001-S76")
        self.assertIsNotNone(path2)
        audio.sweep(audio.now() + timedelta(hours=73))
        self.assertFalse(path2.exists())
        status, body = self.call(f"/api/jobs/{failed2['job_id']}/retry", {})
        self.assertEqual((status, body["error_code"]), (409, "audio_gone"))

    def test_manual_delete(self):
        self.upload("C001-S77", TEXT)
        path = audio.for_session("C001-S77")["path"]
        status, body = self.call("/api/sessions/C001-S77/audio/delete", {})
        self.assertEqual(status, 200, body)
        self.assertEqual((body["audio_status"], body["audio_delete_reason"]), ("deleted", "manual"))
        self.assertFalse(path.exists())
        self.assertEqual(self.call("/api/sessions/C001-S77/audio/delete", {})[1]["audio_status"], "deleted")  # again: no change
        self.assertEqual(sum(1 for a in store.recent_audit(80) if a["action"] == "audio_deleted" and "C001-S77" in a["detail"]), 1)
        self.assertEqual(self.call("/api/sessions/C001-S04/audio/delete", {})[0], 404)  # never had audio
        self.assertEqual(self.call("/api/sessions/ZZZ-NOPE/audio/delete", {})[0], 404)
        self.assertEqual(self.call("/api/sessions/C001-S77/audio/delete", {}, headers={})[0], 403)  # needs X-Haven

    def test_a_second_recording_of_a_session_replaces_the_first(self):
        first = Path(tempfile.mkdtemp()) / "x.wav"
        first.write_bytes(b"one")
        second = Path(tempfile.mkdtemp()) / "y.m4a"
        second.write_bytes(b"two")
        a = audio.store_file("C001-S78", first)
        b = audio.store_file("C001-S78", second)
        self.assertFalse((files.audio_dir() / a["storage_ref"]).exists())
        self.assertEqual(audio.get("C001-S78")["audio_id"], b["audio_id"])
        self.assertEqual(b["mime"], "audio/mp4")

    def test_start_up_clears_tmp_but_keeps_available_audio(self):
        self.upload("C001-S79", TEXT)
        a = audio.get("C001-S79")
        left = files.temp_dir() / "upload-crashed.m4a"
        left.write_bytes(b"audio")
        stray = files.audio_dir() / "job-stray.wav"  # a file no row names
        stray.write_bytes(b"x")
        keep = files.audio_dir() / ".gitkeep"  # tracked by git; start-up must leave it (seen on the B70)
        keep.write_bytes(b"")
        self.assertEqual(files.clear_temp(), 1)
        self.assertEqual(jobs.startup(), 0)
        self.assertEqual(audio.clean_orphans(), 1)
        self.assertFalse(left.exists() or stray.exists())
        self.assertTrue(keep.exists())
        self.assertTrue((files.audio_dir() / a["storage_ref"]).is_file())
        self.assertEqual(audio.for_session("C001-S79")["status"], "available")

    def test_the_sweeper_does_not_run_unless_started(self):
        self.assertIsNone(audio._TIMER)

    def test_start_sweeper_sweeps_now_and_schedules_the_next_run(self):
        with mock.patch.object(audio, "sweep") as sweep:
            audio.start_sweeper(interval=3600)
            try:
                sweep.assert_called_once()
                self.assertTrue(audio._TIMER.daemon)
            finally:
                audio.stop_sweeper()
        self.assertIsNone(audio._TIMER)

    def test_for_session_is_what_playback_needs(self):
        self.assertIsNone(audio.for_session("C001-S03"))
        self.upload("C001-S80", TEXT)
        a = audio.for_session("C001-S80")
        self.assertTrue(a["playable"])
        self.assertEqual((a["status"], a["mime"], a["path"].parent), ("available", "audio/wav", files.audio_dir()))
        late = audio._parse(a["expires_at"]) + timedelta(minutes=1)
        self.assertFalse(audio.for_session("C001-S80", clock=late)["playable"])  # expired, not yet swept
        self.assertIsNone(audio.for_session("C001-S80", clock=late)["path"])

    def test_the_audio_log_holds_ids_and_reasons_only(self):
        self.upload("C001-S81", TEXT)
        audio.delete_for_session("C001-S81", "manual")
        text = " ".join(a["detail"] for a in store.recent_audit(80) if a["action"].startswith("audio_"))
        self.assertNotIn("睡不著", text)
        self.assertNotIn(".wav", text)


class AudioAuthorizationTest(AccountCase, unittest.TestCase):

    def test_another_therapist_cannot_delete_my_audio(self):
        src = Path(tempfile.mkdtemp()) / "x.wav"
        src.write_bytes(b"audio")
        a = audio.store_file("C001-S04", src)
        audio.mark_available("C001-S04")
        lin, wang = self.login("lin"), self.login("wang")
        status, _, _ = self.req("/api/sessions/C001-S04/audio/delete", {}, cookie=lin)
        self.assertEqual(status, 404)
        self.assertEqual(audio.get("C001-S04")["status"], "available")  # no side effect
        self.assertTrue((files.audio_dir() / a["storage_ref"]).exists())
        self.assertEqual(self.req("/api/sessions/C001-S04/audio/delete", {})[0], 401)
        status, body, _ = self.req("/api/sessions/C001-S04/audio/delete", {}, cookie=wang)
        self.assertEqual((status, body["audio_status"]), (200, "deleted"))
        self.assertIn("[wang]", next(x for x in store.recent_audit(20) if x["action"] == "audio_deleted")["detail"])


if __name__ == "__main__":
    unittest.main()
