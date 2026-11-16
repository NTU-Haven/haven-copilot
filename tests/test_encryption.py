"""Encryption at rest (HAVEN_ENCRYPT=1): what reaches the disk is unreadable
without the passphrase, and the app works the same with it on."""

import importlib.util
import json
import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

from helpers import IsolatedData, ServerCase

from haven import config, crypto, signing, store
from haven.transcript import load_transcript

HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


def db_bytes() -> bytes:
    return config.DB_FILE.read_bytes() + b"".join(p.read_bytes() for p in config.DATA_DIR.glob("*.db-wal"))


def encrypt_existing():
    import encrypt_data
    with mock.patch("builtins.print"):
        assert encrypt_data.main() == 0


@unittest.skipUnless(HAS_CRYPTO, "cryptography not installed")
class CryptoUnitTest(unittest.TestCase):
    def setUp(self):
        self.data = IsolatedData().__enter__()
        self.env = mock.patch.dict(os.environ, {"HAVEN_ENCRYPT": "1"})
        self.env.start()
        crypto.lock()

    def tearDown(self):
        crypto.lock()
        self.env.stop()
        self.data.__exit__()

    def test_first_unlock_creates_salt_not_key(self):
        self.assertTrue(crypto.unlock("正確的密語"))
        saved = json.loads((config.DATA_DIR / "key.json").read_text())
        self.assertEqual(set(saved), {"kdf", "n", "r", "p", "salt", "check"})
        self.assertFalse(crypto.unlock("正確的密語"))  # same passphrase: existing key

    def test_wrong_passphrase_is_refused_and_leaves_it_locked(self):
        crypto.unlock("正確的密語")
        crypto.lock()
        with self.assertRaises(crypto.WrongPassphrase):
            crypto.unlock("錯的")
        self.assertFalse(crypto.unlocked())

    def test_sealed_text_round_trips_and_differs_each_time(self):
        crypto.unlock("pw")
        a, b = crypto.seal("個案表示想結束生命"), crypto.seal("個案表示想結束生命")
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("enc1:"))
        self.assertEqual(crypto.open_(a), "個案表示想結束生命")
        self.assertEqual(crypto.open_("plain text stays"), "plain text stays")

    def test_locked_read_of_sealed_data_fails_loudly(self):
        crypto.unlock("pw")
        sealed = crypto.seal("x")
        crypto.lock()
        with self.assertRaises(crypto.LockedError):
            crypto.open_(sealed)

    def test_tampered_value_is_rejected(self):
        crypto.unlock("pw")
        sealed = crypto.seal("個案沒有具體計畫")
        flipped = sealed[:-6] + ("A" if sealed[-6] != "A" else "B") + sealed[-5:]
        with self.assertRaises(Exception):
            crypto.open_(flipped)

    def test_transcript_file_is_sealed_and_loads(self):
        crypto.unlock("pw")
        before = load_transcript("C001-S04")
        sha_before = signing.transcript_sha256("C001-S04")
        encrypt_existing()
        raw = (config.SESSIONS_DIR / "C001-S04.json").read_text()
        self.assertTrue(raw.startswith("enc1:"))
        self.assertNotIn(before.segments[0].text, raw)
        self.assertEqual(load_transcript("C001-S04"), before)
        # Signatures taken before encryption still match the transcript.
        self.assertEqual(signing.transcript_sha256("C001-S04"), sha_before)
        self.assertTrue(config.CLIENTS_FILE.read_text().startswith("enc1:"))

    def test_running_the_script_twice_changes_nothing(self):
        crypto.unlock("pw")
        encrypt_existing()
        first = (config.SESSIONS_DIR / "C001-S04.json").read_text()
        encrypt_existing()
        self.assertEqual((config.SESSIONS_DIR / "C001-S04.json").read_text(), first)

    def test_verdict_cache_hides_the_sentence_and_still_matches(self):
        crypto.unlock("pw")
        sentence = "個案表示上週買了木炭。"
        store.save_verdicts("S1", "sha", "v1", {(sentence, "[3]"): {"status": "flagged"}})
        self.assertEqual(store.get_verdicts("S1", "sha", "v1"), {(sentence, "[3]"): {"status": "flagged"}})
        self.assertNotIn(sentence.encode(), db_bytes())
        with sqlite3.connect(config.DB_FILE) as c:
            self.assertTrue(c.execute("SELECT text FROM verdicts").fetchone()[0].startswith("h1:"))

    def test_plain_rows_from_before_still_read(self):
        with mock.patch.dict(os.environ, {"HAVEN_ENCRYPT": ""}):
            store.save_result("S2", {"a": "明文"})
            store.save_verdicts("S2", "sha", "v1", {("句子", "[1]"): {"status": "supported"}})
        crypto.unlock("pw")
        self.assertEqual(store.get_result("S2"), {"a": "明文"})
        self.assertEqual(store.get_verdicts("S2", "sha", "v1"), {("句子", "[1]"): {"status": "supported"}})
        encrypt_existing()
        self.assertNotIn("明文".encode(), db_bytes())
        self.assertEqual(store.get_result("S2"), {"a": "明文"})
        self.assertEqual(store.get_verdicts("S2", "sha", "v1"), {("句子", "[1]"): {"status": "supported"}})

    def test_start_refuses_an_encrypted_folder_without_the_flag(self):
        crypto.unlock("pw")
        crypto.lock()
        with mock.patch.dict(os.environ, {"HAVEN_ENCRYPT": ""}), self.assertRaises(SystemExit):
            crypto.start()

    def test_start_asks_twice_for_a_new_passphrase(self):
        answers = iter(["一", "二"])
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HAVEN_PASSPHRASE", None)
            with self.assertRaises(SystemExit):
                crypto.start(prompt=lambda _: next(answers))
        self.assertFalse((config.DATA_DIR / "key.json").exists())
        answers = iter(["同一句", "同一句"])
        os.environ.pop("HAVEN_PASSPHRASE", None)
        self.assertIn("new key", crypto.start(prompt=lambda _: next(answers)))
        crypto.lock()
        with self.assertRaises(SystemExit):
            crypto.start(prompt=lambda _: "不對")


@unittest.skipUnless(HAS_CRYPTO, "cryptography not installed")
class EncryptedServerTest(ServerCase, unittest.TestCase):
    """The whole flow — open, sign, addendum — over an encrypted data folder."""

    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, {"HAVEN_ENCRYPT": "1", "HAVEN_PASSPHRASE": "測試密語"})
        cls.env.start()
        super().setUpClass()
        crypto.lock()
        crypto.start()
        encrypt_existing()
        cls.r, cls.note = cls.draft("C001-S03")
        cls.status, cls.signature = cls.call("/api/sessions/C001-S03/sign", {"signed_by": "王心理師", "note": cls.note})
        cls.add_status, _ = cls.call("/api/sessions/C001-S03/addendum", {"text": "會談後個案來電，改約下週三。", "author": "王心理師"})

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        crypto.lock()
        cls.env.stop()

    def test_signing_and_addendum_work(self):
        self.assertEqual(self.status, 200, self.signature)
        self.assertEqual(self.add_status, 200)
        self.assertEqual(store.get_addenda("C001-S03")[0]["text"], "會談後個案來電，改約下週三。")
        sig = store.get_signature("C001-S03")
        self.assertEqual(sig["note_sha256"], signing.note_sha256(store.get_signed_note("C001-S03")))

    def test_no_note_or_transcript_text_in_the_database(self):
        raw = db_bytes()
        sentences = [s["text"] for v in self.note["sections"].values() for s in v]
        self.assertTrue(sentences)
        for text in sentences + ["改約下週三"]:
            self.assertNotIn(text.encode(), raw, text)
        for seg in load_transcript("C001-S03").segments[:5]:
            self.assertNotIn(seg.text.encode(), raw)

    def test_audit_chain_still_verifies(self):
        self.assertTrue(store.verify_audit()["ok"])


if __name__ == "__main__":
    unittest.main()
