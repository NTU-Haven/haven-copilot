"""Makes the HTTPS certificate for Haven on the clinic network.

    python3 scripts/make_cert.py 192.168.1.20 haven.local

Writes data/tls/cert.pem and data/tls/key.pem (the key 0600) for the given
addresses and names, valid for two years, and prints the settings to start
Haven with. Needs the ``openssl`` command (present on macOS and Ubuntu).

The certificate is self-signed: browsers warn until it is trusted. Install
cert.pem (not the key) once on each laptop or tablet as a trusted certificate,
so that the browser shows the padlock and allows the microphone. Never copy
key.pem off this machine.
"""

from __future__ import annotations

import ipaddress
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from haven import config  # noqa: E402


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    alt = []
    for name in argv:
        try:
            ipaddress.ip_address(name)
            alt.append(f"IP:{name}")
        except ValueError:
            alt.append(f"DNS:{name}")
    out = config.DATA_DIR / "tls"
    out.mkdir(parents=True, exist_ok=True)
    os.chmod(out, 0o700)
    cert, key = out / "cert.pem", out / "key.pem"
    conf = ("[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n[dn]\nCN=Haven clinic server\nO=Haven\n"
            f"[v3]\nsubjectAltName={','.join(alt)}\nbasicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n")
    with tempfile.NamedTemporaryFile("w", suffix=".cnf", delete=False) as f:
        f.write(conf)
    old = os.umask(0o077)
    try:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256", "-days", "730",
                        "-keyout", str(key), "-out", str(cert), "-config", f.name], check=True, capture_output=True)
    except FileNotFoundError:
        print("找不到 openssl 指令。", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(e.stderr.decode(errors="replace"), file=sys.stderr)
        return 1
    finally:
        os.umask(old)
        os.unlink(f.name)
    os.chmod(key, 0o600)
    names = ",".join(argv)
    print(f"已產生 {cert} 與 {key}（給 {names}，兩年有效）。啟動方式：\n")
    print(f"  HAVEN_HOST=0.0.0.0 HAVEN_HOSTNAMES={names} HAVEN_TLS_CERT={cert} HAVEN_TLS_KEY={key} python3 -m haven\n")
    print(f"然後在每台筆電或平板上把 {cert.name}（不是 key.pem）安裝成信任的憑證，再開 https://{argv[0]}:{config.PORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
