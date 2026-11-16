"""Encrypts what an existing data folder holds in plain text (HAVEN_ENCRYPT=1).

Run once after turning encryption on, with Haven stopped:

    HAVEN_ENCRYPT=1 python3 scripts/encrypt_data.py

Asks for the passphrase (or reads HAVEN_PASSPHRASE). Values already encrypted
are left alone, so running it twice is harmless. Signatures stay valid: a
signed note's transcript hash is taken over the text, not the file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from haven import config, crypto, files, store  # noqa: E402


def main() -> int:
    if not crypto.enabled():
        print("Set HAVEN_ENCRYPT=1 first.", file=sys.stderr)
        return 2
    print(crypto.start())
    counts = {"files": 0, "results": 0, "signed_notes": 0, "addenda": 0, "verdicts": 0}
    for path in [config.CLIENTS_FILE, *sorted(config.SESSIONS_DIR.glob("*.json"))]:
        if path.is_file() and not crypto.is_sealed(path.read_text(encoding="utf-8")):
            files.write_private(path, path.read_text(encoding="utf-8"))
            counts["files"] += 1
    with store.connect() as c:
        for table, column, key in (("results", "payload", "session_id"), ("signed_notes", "note", "session_id"), ("addenda", "text", "id")):
            for row_key, value in c.execute(f"SELECT {key}, {column} FROM {table}").fetchall():
                if not crypto.is_sealed(value):
                    c.execute(f"UPDATE {table} SET {column} = ? WHERE {key} = ?", (crypto.seal(value), row_key))
                    counts[table] += 1
        rows = c.execute("SELECT session_id, transcript_sha256, verifier_version, text, evidence, verdict FROM verdicts").fetchall()
        for sid, sha, version, text, evidence, verdict in rows:
            if crypto.is_sealed(verdict):
                continue
            c.execute("DELETE FROM verdicts WHERE session_id = ? AND transcript_sha256 = ? AND verifier_version = ? AND text = ? AND evidence = ?",
                      (sid, sha, version, text, evidence))
            c.execute("INSERT OR REPLACE INTO verdicts VALUES (?, ?, ?, ?, ?, ?)",
                      (sid, sha, version, *store._verdict_row(text, evidence, json.loads(verdict))))
            counts["verdicts"] += 1
    print("Encrypted: " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    print("Note: data/clients.json is tracked by Git; once encrypted it shows as changed. Do not commit it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
