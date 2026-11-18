"""Manages therapist accounts on the main PC (data/users.json).

    python3 scripts/add_user.py add wang --name 王心理師 --clients C001,C002
    python3 scripts/add_user.py add admin --name 所長 --role admin
    python3 scripts/add_user.py assign wang C003        # give an existing client to an account
    python3 scripts/add_user.py list
    python3 scripts/add_user.py remove wang

``add`` asks for the password twice (or reads HAVEN_NEW_PASSWORD, for scripts);
running it again for the same account changes the password and signs that
account out everywhere. A therapist sees only the clients assigned to them
(``therapist_user`` in clients.json). With HAVEN_ENCRYPT=1 the account file is
encrypted like the rest, so the passphrase is asked first.

Once any account exists, Haven asks everyone to sign in, on this machine too.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from haven import accounts, config, crypto, files  # noqa: E402
from haven.orchestrator import load_clients  # noqa: E402


def assign(username: str, client_ids: list[str]) -> list[str]:
    clients = load_clients()
    known = {c["id"] for c in clients}
    missing = [c for c in client_ids if c not in known]
    if missing:
        raise SystemExit(f"沒有這些個案：{', '.join(missing)}")
    for c in clients:
        if c["id"] in client_ids:
            c["therapist_user"] = username
    files.write_private(config.CLIENTS_FILE, json.dumps(clients, ensure_ascii=False, indent=2) + "\n")
    return client_ids


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("username")
    a.add_argument("--name", required=True, help="顯示名稱，簽核時用，例如 王心理師")
    a.add_argument("--role", default="therapist", choices=accounts.ROLES)
    a.add_argument("--clients", default="", help="指派給這個帳號的個案編號，逗號分隔")
    s = sub.add_parser("assign")
    s.add_argument("username")
    s.add_argument("clients", help="個案編號，逗號分隔")
    sub.add_parser("list")
    r = sub.add_parser("remove")
    r.add_argument("username")
    args = ap.parse_args(argv)

    if crypto.enabled():
        print(crypto.start())
    files.secure_data_dir()
    if args.cmd == "add":
        password = os.environ.get("HAVEN_NEW_PASSWORD")
        if password is None:
            password = getpass.getpass("密碼（至少 10 個字）：")
            if getpass.getpass("再輸入一次：") != password:
                print("兩次輸入的密碼不一樣。", file=sys.stderr)
                return 1
        try:
            user = accounts.set_user(args.username, args.name, password, args.role)
        except accounts.AccountError as e:
            print(e, file=sys.stderr)
            return 1
        ids = [c.strip() for c in args.clients.split(",") if c.strip()]
        if ids:
            assign(user["username"], ids)
        print(f"已設定帳號 {user['username']}（{user['display_name']}，{user['role']}）" + (f"，個案：{', '.join(ids)}" if ids else ""))
    elif args.cmd == "assign":
        if not accounts.get_user(args.username):
            print(f"沒有帳號 {args.username}", file=sys.stderr)
            return 1
        ids = assign(args.username, [c.strip() for c in args.clients.split(",") if c.strip()])
        print(f"已把 {', '.join(ids)} 指派給 {args.username}")
    elif args.cmd == "list":
        clients = load_clients()
        for u in accounts.load_users():
            mine = [c["id"] for c in clients if c.get("therapist_user") == u["username"]]
            print(f"{u['username']:<16} {u['display_name']:<10} {u['role']:<10} 個案：{', '.join(mine) or '（無）'}")
        unassigned = [c["id"] for c in clients if not c.get("therapist_user")]
        if unassigned:
            print(f"尚未指派的個案（登入後沒有人看得到）：{', '.join(unassigned)}")
    elif args.cmd == "remove":
        print("已刪除" if accounts.remove_user(args.username) else "沒有這個帳號")
    return 0


if __name__ == "__main__":
    sys.exit(main())
