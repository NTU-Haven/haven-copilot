"""Puts the demo back to a clean, unsigned state before a presentation.

    python3 scripts/reset_demo.py                      # start clean with the current engine
    python3 scripts/reset_demo.py --from data/haven.demo.db   # start from drafts prepared earlier with a model
    python3 scripts/reset_demo.py --no-example         # do not plant the example error

What it does:
1. Moves the current database aside (data/haven.db-backup-<time with microseconds>); nothing is deleted.
2. Starts an empty database, or a copy of --from with its signatures and addenda removed.
3. Drafts a note for every session that has none, with the engine set by HAVEN_LLM.
4. When C001-S04 was written by the rule engine, adds one wrong sentence to its
   plan: 「安排下週五提前會談。」 citing line #19, where the therapist says 這週五.
   The rule engine only quotes the transcript, so without this there is nothing red
   to show. The session page says in a banner that the sentence was planted, and the
   audit log records it. It is the error a 7B model made in the first demo.

It refuses to run when the data directory holds a session that is not marked
simulated: this is a demo tool and must never move a clinic's records aside.
"""

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from haven import config, store  # noqa: E402
from haven.agents.writer import FALLBACK_ENGINE  # noqa: E402
from haven.orchestrator import Orchestrator, load_clients  # noqa: E402

EXAMPLE_SESSION = "C001-S04"
EXAMPLE = {"text": "安排下週五提前會談。", "evidence": [19]}
EXAMPLE_NOTICE = ("示範用：「安排下週五提前會談。」是 scripts/reset_demo.py 放進去的錯誤句（逐字稿 #19 說的是這週五），"
                  "用來示範查核與修改；它不是這次的引擎寫的。")


def backup_name(db: Path) -> Path:
    """A backup name no earlier backup has: microseconds, then a counter, so two resets
    in the same second never collide (Windows refuses the rename, POSIX would overwrite)."""
    stem = f"{db.name}-backup-{datetime.now():%Y%m%dT%H%M%S.%f}"
    candidate, n = db.with_name(stem), 1
    while candidate.exists() or candidate.with_name(candidate.name + "-wal").exists():
        candidate, n = db.with_name(f"{stem}-{n}"), n + 1
    return candidate


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--from", dest="source", type=Path, help="a database prepared earlier (for example data/haven.demo.db)")
    parser.add_argument("--no-example", action="store_true", help="do not plant the example error in C001-S04")
    args = parser.parse_args()

    real = [p.name for p in sorted(config.SESSIONS_DIR.glob("*.json"))
            if json.loads(p.read_text(encoding="utf-8")).get("simulated") is not True]
    if real:
        print(f"refusing: {config.SESSIONS_DIR} holds sessions not marked simulated ({', '.join(real)}). "
              "This script is for demo data only.", file=sys.stderr)
        return 1
    if args.source and not args.source.is_file():
        print(f"no such database: {args.source}", file=sys.stderr)
        return 1

    if config.DB_FILE.exists():
        backup = backup_name(config.DB_FILE)
        config.DB_FILE.rename(backup)
        for suffix in ("-wal", "-shm"):  # a WAL database keeps its last writes beside it
            side = config.DB_FILE.with_name(config.DB_FILE.name + suffix)
            if side.exists():
                side.rename(backup.with_name(backup.name + suffix))
        print(f"moved the old database to {backup}")
    for suffix in ("-journal", "-wal", "-shm"):  # sidecars of the old database must not attach to the new one
        for leftover in config.DATA_DIR.glob(f"{config.DB_FILE.name}{suffix}"):
            leftover.unlink()
    if args.source:
        shutil.copy(args.source, config.DB_FILE)
        conn = sqlite3.connect(config.DB_FILE)
        for table in ("signed_notes", "addenda"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                pass  # an older database without that table
        conn.commit()
        conn.close()
        print(f"copied {args.source} (signatures and addenda removed)")

    orch = Orchestrator()
    print(f"engine: {orch.engine}")
    store.audit("reset_demo", f"source={args.source.name if args.source else 'empty'} engine={orch.engine}")
    for client in load_clients():
        for session_id in client.get("sessions", []):
            result = store.get_result(session_id)
            if result is None:
                result = orch.process_session(session_id)
                made = "drafted"
            else:
                result = orch.refresh(result)  # drafts from an older database are checked under today's rules
                made = "kept, re-verified"
            if session_id == EXAMPLE_SESSION and not args.no_example and result["engine"] in ("rule-based demo", FALLBACK_ENGINE):
                # SOAP/DAP: the plan section; QUAL (the default): the interaction field, since its plan is human_only.
                sections = result["note"]["sections"]
                plan = sections.setdefault("P" if "P" in sections else "INTERACTION", [])
                if not any(s["text"] == EXAMPLE["text"] for s in plan):
                    plan.append(dict(EXAMPLE))
                    result["note"].setdefault("warnings", []).append(EXAMPLE_NOTICE)
                    result = orch.refresh(result)
                    store.audit("demo_example_planted", f"{session_id} P: one wrong sentence citing #19, for demonstration")
                    made += ", example error planted"
            v = result["verification"]
            print(f"{session_id}: {made}; {v['supported']}/{v['total']} supported, {v['unsupported']} unsupported, risk={(result['risk'] or {}).get('level', 'off')}")
    print("ready: no session is signed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
