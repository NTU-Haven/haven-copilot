"""Step 0 of the Qwen3-ASR evaluation (docs/specs/asr-qwen3-evaluation.md §5): can a
local Qwen3-ASR server be used by Haven at all?

    python3 scripts/qwen3_asr_smoke.py ~/L50-tts.ogg=C002-L50:1-339 ~/clip23s.ogg=C001-S04:10-12 \
        --url http://127.0.0.1:8002 --model qwen3-asr \
        --compare-url http://127.0.0.1:8001 --compare-model whisper --label b70-qwen3asr-smoke

For each recording it sends the same request Haven sends (OpenAI transcription API,
language zh) once per response format (verbose_json, json, text) and records: whether
the server accepts the format, whether segments with timestamps come back, how long it
took, and how much text came back. With a reference (``file=SESSION:FIRST-LAST``, the
demo script the recording reads) it also reports the character error rate after
Simplified -> Traditional conversion, how much of the script was covered, and whether
the last lines of the script appear (truncation). ``--compare-*`` runs the same
requests against a second server (Whisper) so both are scored the same way.

Standard library plus Haven itself. Only local addresses are accepted (Haven's own
endpoint check). Results go to docs/asr-eval/<label>.json and .md; the recordings and
the full transcripts stay out of the Markdown file.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from asr_eval import cer, plain  # noqa: E402
from haven.llm import _OPENER, assert_local  # noqa: E402
from haven.transcript import _clean, converts_to_traditional, load_transcript  # noqa: E402

FORMATS = ("verbose_json", "json", "text")
TAIL_LINES = 5


def models(url: str) -> list[str]:
    assert_local(url)
    try:
        with _OPENER.open(f"{url.rstrip('/')}/v1/models", timeout=10) as r:
            return [m.get("id", "") for m in json.load(r).get("data", [])]
    except (OSError, ValueError) as e:
        raise SystemExit(f"no model server at {url}: {type(e).__name__}: {e}") from None


def transcribe(url: str, model: str, audio: Path, fmt: str, timeout: float) -> dict:
    """One transcription request, built like haven.transcript._openai_server."""
    assert_local(url)
    boundary = uuid.uuid4().hex
    fields = {"model": model, "language": "zh", "response_format": fmt}
    body = b"".join(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode() for k, v in fields.items())
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio{audio.suffix}\"\r\n"
             "Content-Type: application/octet-stream\r\n\r\n").encode() + audio.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{url.rstrip('/')}/v1/audio/transcriptions", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    out = {"format": fmt, "status": None, "seconds": None, "error": "", "keys": [], "segments": 0,
           "first_segments": [], "duration": None, "text": ""}
    t0 = time.perf_counter()
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            raw = r.read()
            out["status"] = r.status
    except urllib.error.HTTPError as e:
        out["status"], out["error"] = e.code, e.read(600).decode("utf-8", "replace")
        out["seconds"] = round(time.perf_counter() - t0, 2)
        return out
    except OSError as e:
        out["error"] = f"{type(e).__name__}: {e}"
        out["seconds"] = round(time.perf_counter() - t0, 2)
        return out
    out["seconds"] = round(time.perf_counter() - t0, 2)
    text = raw.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        out["keys"] = sorted(data)
        segs = data.get("segments") if isinstance(data.get("segments"), list) else []
        out["segments"] = len(segs)
        out["first_segments"] = [{"start": s.get("start"), "end": s.get("end"), "text": str(s.get("text", ""))[:40]}
                                 for s in segs[:3] if isinstance(s, dict)]
        out["duration"] = data.get("duration")
        out["text"] = str(data.get("text") or "".join(str(s.get("text", "")) for s in segs if isinstance(s, dict)))
    else:
        out["text"] = text  # response_format=text
    return out


def score(text: str, reference) -> dict:
    if not reference:
        return {}
    hyp = plain(_clean(text))
    ref_text = "".join(s.text for s in reference)
    tail = reference[-TAIL_LINES:]
    grams = lambda s: {s[i:i + 2] for i in range(len(s) - 1)}  # noqa: E731
    hyp_grams = grams(hyp)
    tail_found = sum(1 for s in tail if grams(plain(s.text)) and len(grams(plain(s.text)) & hyp_grams) / len(grams(plain(s.text))) >= 0.5)
    return {"cer": round(cer(ref_text, hyp), 4), "coverage": round(len(hyp) / max(1, len(plain(ref_text))), 3),
            "tail_lines_found": f"{tail_found}/{len(tail)}"}


def parse_item(item: str):
    path, _, ref = item.partition("=")
    reference = None
    if ref:
        session, _, span = ref.partition(":")
        first, last = (int(x) for x in span.split("-"))
        reference = [s for s in load_transcript(session).segments if first <= s.id <= last]
        if not reference:
            raise SystemExit(f"{ref}: no such segments")
    audio = Path(path).expanduser()
    if not audio.is_file():
        raise SystemExit(f"{audio}: no such file")
    return audio, ref, reference


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("items", nargs="+", help="recording, optionally =SESSION:FIRST-LAST for the script it reads")
    ap.add_argument("--url", default="http://127.0.0.1:8002")
    ap.add_argument("--model", default="qwen3-asr")
    ap.add_argument("--compare-url")
    ap.add_argument("--compare-model", default="whisper")
    ap.add_argument("--formats", default=",".join(FORMATS))
    ap.add_argument("--timeout", type=float, default=1800)
    ap.add_argument("--label", required=True)
    args = ap.parse_args()

    servers = [("candidate", args.url, args.model)]
    if args.compare_url:
        servers.append(("baseline", args.compare_url, args.compare_model))
    served = {name: models(url) for name, url, _ in servers}
    for name, url, model in servers:
        if model not in served[name]:
            raise SystemExit(f"{url} serves {served[name]}, not {model!r}")

    formats = [f for f in args.formats.split(",") if f]
    runs = []
    for item in args.items:
        audio, ref, reference = parse_item(item)
        for name, url, model in servers:
            for fmt in formats:
                print(f"{audio.name} | {name} {model} | {fmt} ...", flush=True)
                r = transcribe(url, model, audio, fmt, args.timeout)
                r.update(score(r["text"], reference) if r["text"] else {})
                r.update({"audio": audio.name, "audio_bytes": audio.stat().st_size, "reference": ref, "server": name, "model": model})
                print(f"  HTTP {r['status']} in {r['seconds']} s, segments {r['segments']}, {len(plain(r['text']))} chars"
                      + (f", CER {r['cer']:.1%}, coverage {r['coverage']}, tail {r['tail_lines_found']}" if "cer" in r else "")
                      + (f" - {r['error'][:200]}" if r["error"] else ""), flush=True)
                runs.append(r)

    report = {"label": args.label, "date": datetime.now().astimezone().isoformat(timespec="seconds"),
              "to_traditional": converts_to_traditional(), "servers": [{"role": n, "url": u, "model": m, "served": served[n]} for n, u, m in servers],
              "runs": runs}
    out = ROOT / "docs" / "asr-eval"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.label}.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Qwen3-ASR smoke test: {args.label}", "",
             f"- Date: {report['date']}",
             f"- Servers: " + "; ".join(f"{s['role']} `{s['model']}` at {s['url']}" for s in report["servers"]),
             f"- Simplified → Traditional before scoring: {'on (opencc)' if report['to_traditional'] else 'OFF: install requirements-asr.txt, Simplified output counts as errors'}",
             "- Recordings are not in the repository; full transcripts are in the .json file.", "",
             "| Recording | Server | Format | HTTP | Seconds | Segments (timestamps) | Chars | CER | Coverage | Last lines found | Error |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in runs:
        cer_cell = f"{r['cer']:.1%}" if "cer" in r else "–"
        error_cell = " ".join(r["error"].split())[:80].replace("|", "/")
        lines.append(f"| {r['audio']} | {r['server']} `{r['model']}` | {r['format']} | {r['status']} | {r['seconds']} | {r['segments']} | "
                     f"{len(plain(r['text']))} | {cer_cell} | {r.get('coverage', '–')} | {r.get('tail_lines_found', '–')} | {error_cell} |")
    lines += ["", "Step 0 passes when the candidate answers at least one format for every recording, the long recording's "
              "coverage is close to the baseline's and its last lines are found, and no request fails. "
              "Segments = 0 means no timestamps (expected for Qwen3-ASR): Haven would need the hybrid design."]
    (out / f"{args.label}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote docs/asr-eval/{args.label}.md and .json")
    if not any(r["status"] == 200 for r in runs if r["server"] == "candidate"):
        sys.exit(1)


if __name__ == "__main__":
    main()
