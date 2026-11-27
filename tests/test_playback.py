"""Authorized segment playback (ticket 09, AC-11, spec 10 P1/P2): a short-lived ticket,
single byte ranges, expiry, deleted audio, and other therapists' tickets."""

import json
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from unittest import mock

from helpers import H, ServerCase
from test_accounts import AccountCase

from haven import accounts, audio, playback, store, transcript  # noqa: E402

DATA = bytes(range(256)) * 12  # 3072 bytes


def put_audio(sid, suffix=".wav", data=DATA):
    src = Path(tempfile.mkdtemp()) / f"x{suffix}"
    src.write_bytes(data)
    audio.store_file(sid, src)
    audio.mark_available(sid)


def fetch(base, url, rng=None, cookie=None, headers=None):
    h = dict(headers or {})
    if rng:
        h["Range"] = rng
    if cookie:
        h["Cookie"] = f"{accounts.COOKIE}={cookie}"
    req = urllib.request.Request(base + url, headers=h)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        with e:
            return e.code, e.read(), e.headers


class PlaybackTest(ServerCase, unittest.TestCase):

    def setUp(self):
        playback.reset()
        put_audio("C001-S04")
        self.addCleanup(audio.delete_for_session, "C001-S04", "manual")

    def ticket(self):
        status, r = self.call("/api/sessions/C001-S04/audio/ticket", {})
        self.assertEqual(status, 200, r)
        return r

    def test_ticket_response_and_audit(self):
        r = self.ticket()
        self.assertEqual(r["expires_in"], 600)
        self.assertRegex(r["url"], r"^/media/audio/[A-Za-z0-9_-]{22,}$")
        self.assertFalse(r["timestamps_estimated"])
        row = next(a for a in store.recent_audit(20) if a["action"] == "audio_play_ticket")
        self.assertNotIn(r["url"].rsplit("/", 1)[1], row["detail"])
        self.assertEqual(len([a for a in store.recent_audit(80) if a["action"] == "audio_play_ticket"]) >= 1, True)

    def test_ticket_needs_the_csrf_header_but_media_does_not(self):
        status, _ = self.call("/api/sessions/C001-S04/audio/ticket", {}, headers={"Content-Type": "application/json"})
        self.assertEqual(status, 403)
        url = self.ticket()["url"]
        status, body, _ = fetch(self.base, url)  # no X-Haven
        self.assertEqual((status, body), (200, DATA))

    def test_range_0_1023_is_206_with_content_range(self):
        url = self.ticket()["url"]
        status, body, h = fetch(self.base, url, "bytes=0-1023")
        self.assertEqual(status, 206)
        self.assertEqual(h["Content-Range"], f"bytes 0-1023/{len(DATA)}")
        self.assertEqual(h["Content-Length"], "1024")
        self.assertEqual(body, DATA[:1024])
        self.assertEqual((h["Accept-Ranges"], h["Cache-Control"], h["Content-Type"]), ("bytes", "no-store", "audio/wav"))

    def test_open_ended_suffix_and_clamped_ranges(self):
        url = self.ticket()["url"]
        n = len(DATA)
        status, body, h = fetch(self.base, url, "bytes=3000-")
        self.assertEqual((status, h["Content-Range"], body), (206, f"bytes 3000-{n - 1}/{n}", DATA[3000:]))
        status, body, h = fetch(self.base, url, "bytes=-100")
        self.assertEqual((status, h["Content-Range"], body), (206, f"bytes {n - 100}-{n - 1}/{n}", DATA[-100:]))
        status, body, h = fetch(self.base, url, "bytes=10-99999")
        self.assertEqual((status, h["Content-Range"], body), (206, f"bytes 10-{n - 1}/{n}", DATA[10:]))

    def test_unsatisfiable_range_is_416_and_junk_is_a_full_200(self):
        url = self.ticket()["url"]
        status, _, h = fetch(self.base, url, "bytes=5000-6000")
        self.assertEqual((status, h["Content-Range"]), (416, f"bytes */{len(DATA)}"))
        for junk in ("bytes=0-1,5-9", "items=0-1", "bytes=9-3"):
            status, body, h = fetch(self.base, url, junk)
            self.assertEqual((status, body, h.get("Content-Range")), (200, DATA, None), junk)

    def test_unknown_ticket_and_path_tricks_are_404(self):
        for url in ("/media/audio/nope", "/media/audio/" + "A" * 22, "/media/audio/../app.js"):
            self.assertEqual(fetch(self.base, url)[0], 404, url)

    def test_expired_ticket_is_404(self):
        url = self.ticket()["url"]
        self.assertEqual(fetch(self.base, url)[0], 200)
        later = audio.now() + timedelta(seconds=601)
        with mock.patch.object(playback, "now", lambda: later):
            self.assertEqual(fetch(self.base, url)[0], 404)
        self.assertEqual(fetch(self.base, url)[0], 404)  # and it stays gone

    def test_ticket_works_until_expiry_by_clock(self):
        url = self.ticket()["url"]
        soon = audio.now() + timedelta(seconds=590)
        with mock.patch.object(playback, "now", lambda: soon):
            self.assertEqual(fetch(self.base, url, "bytes=0-1")[0], 206)

    def test_deleted_audio_gives_409_and_old_tickets_404(self):
        url = self.ticket()["url"]
        audio.delete_for_session("C001-S04", "manual")
        status, r = self.call("/api/sessions/C001-S04/audio/ticket", {})
        self.assertEqual(status, 409)
        self.assertEqual(r["audio_status"], "deleted")
        self.assertIn("原始錄音已清除", r["message_zh"])
        self.assertEqual(fetch(self.base, url)[0], 404)

    def test_expired_audio_is_noticed_by_a_ticket_request(self):
        later = audio.now() + timedelta(hours=100)
        with mock.patch.object(playback, "now", lambda: later):
            status, r = self.call("/api/sessions/C001-S04/audio/ticket", {})
        self.assertEqual((status, r["audio_status"]), (409, "deleted"))

    def test_session_without_audio_is_409_none(self):
        status, r = self.call("/api/sessions/C002-S05/audio/ticket", {})
        self.assertEqual((status, r["audio_status"]), (409, "none"))

    def test_unknown_session_is_404(self):
        self.assertEqual(self.call("/api/sessions/C001-S99/audio/ticket", {})[0], 404)

    def test_webm_is_not_offered_for_playback(self):
        put_audio("C001-S03", ".webm")
        self.addCleanup(audio.delete_for_session, "C001-S03", "manual")
        self.call("/api/sessions/C001-S03/open", {})
        self.assertIs(self.call("/api/sessions/C001-S03")[1]["playback_supported"], False)
        self.call("/api/sessions/C001-S04/open", {})
        self.assertIs(self.call("/api/sessions/C001-S04")[1]["playback_supported"], True)
        status, r = self.call("/api/sessions/C001-S03/audio/ticket", {})
        self.assertEqual((status, r["audio_status"]), (409, "unsupported"))

    def test_estimated_timestamps_are_flagged(self):
        t = transcript.load_transcript("C001-S04")
        t.timestamps_estimated = True
        transcript.save_transcript(t)
        self.addCleanup(lambda: (setattr(t, "timestamps_estimated", False), transcript.save_transcript(t)))
        self.assertTrue(self.ticket()["timestamps_estimated"])

    def test_asr_without_timestamps_sets_the_flag(self):
        reply = json.dumps({"text": "今天還好。睡不著。", "duration": 0}).encode()

        class R:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s, *a): return reply

        from haven import llm
        src = Path(tempfile.mkdtemp()) / "a.wav"
        src.write_bytes(b"x")
        with mock.patch.object(llm._OPENER, "open", lambda *a, **k: R()), mock.patch.object(llm, "assert_local"):
            raw = transcript._openai_server(src)
        self.assertTrue(raw.estimated)
        self.assertEqual(len(raw), 2)


class PlaybackAuthorizationTest(AccountCase, unittest.TestCase):

    def setUp(self):
        playback.reset()
        put_audio("C001-S04")
        self.addCleanup(audio.delete_for_session, "C001-S04", "manual")

    def get_ticket(self, cookie):
        status, r, _ = self.req("/api/sessions/C001-S04/audio/ticket", {}, cookie=cookie)
        return status, r

    def test_owner_plays_others_get_404(self):
        wang, lin = self.login("wang"), self.login("lin")
        status, r = self.get_ticket(wang)
        self.assertEqual(status, 200)
        url = r["url"]
        self.assertEqual(fetch(self.base, url, "bytes=0-1023", cookie=wang)[0], 206)
        self.assertEqual(fetch(self.base, url, "bytes=0-1023", cookie=lin)[0], 404)  # lin holding wang's ticket
        self.assertEqual(fetch(self.base, url, "bytes=0-1023")[0], 404)  # no cookie at all
        self.assertEqual(fetch(self.base, url, cookie=self.login("boss"))[0], 404)

    def test_another_therapist_cannot_get_a_ticket(self):
        before = len([a for a in store.recent_audit(200) if a["action"] == "audio_play_ticket"])
        status, r = self.get_ticket(self.login("lin"))
        self.assertEqual(status, 404)
        self.assertNotIn("audio_status", r)
        self.assertEqual(self.get_ticket(None)[0], 401)
        after = len([a for a in store.recent_audit(200) if a["action"] == "audio_play_ticket"])
        self.assertEqual(before, after)  # no side effect

    def test_access_taken_away_stops_the_stream(self):
        from test_accounts import assign
        wang = self.login("wang")
        url = self.get_ticket(wang)[1]["url"]
        assign("C001", "lin")
        self.addCleanup(assign, "C001", "wang")
        self.assertEqual(fetch(self.base, url, "bytes=0-1", cookie=wang)[0], 404)


class RangeParserTest(unittest.TestCase):
    def test_parse(self):
        p = playback.parse_range
        self.assertEqual(p("bytes=0-1023", 5000), (0, 1023))
        self.assertEqual(p("bytes=4000-", 5000), (4000, 4999))
        self.assertEqual(p("bytes=-500", 5000), (4500, 4999))
        self.assertEqual(p("bytes=-9999", 5000), (0, 4999))
        self.assertEqual(p("bytes=5000-", 5000), "unsatisfiable")
        self.assertIsNone(p(None, 10))


if __name__ == "__main__":
    unittest.main()
