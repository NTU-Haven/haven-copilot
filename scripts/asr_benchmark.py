"""Step 1 of the Qwen3-ASR evaluation (docs/specs/asr-qwen3-evaluation.md §5): Whisper vs
Qwen3-ASR on public Mandarin / Mandarin-English sets, scored the same way, plus a check of
the "alignment hybrid" (Whisper's timed segments, Qwen3-ASR's text cut to fit them).

    # 1. download a sample (public Hugging Face sets, no token; run on the B70 host)
    python3 scripts/asr_benchmark.py fetch ascend --n 60 --out ~/asr-bench
    python3 scripts/asr_benchmark.py fetch cszs --n 40 --out ~/asr-bench
    # optional: add the 46-minute synthetic session as one more line
    python3 scripts/asr_benchmark.py add ~/asr-bench/manifest.jsonl ~/L50-tts.ogg --script C002-L50:1-339 --category long-zh
    # 2. score both servers
    python3 scripts/asr_benchmark.py run ~/asr-bench/manifest.jsonl --label b70-asr-bench \
        --candidate-url http://127.0.0.1:8002 --candidate-model qwen3-asr \
        --baseline-url http://127.0.0.1:8001 --baseline-model whisper

Sets (both public, no token):
  ascend  CAiRE/ASCEND, test split: spontaneous conversation, 23 bilingual speakers in Hong
          Kong, each utterance tagged zh / en / mixed. CC BY-SA 4.0.
  cszs    ky552/cszs_zh_en, test split, the "correct" utterances: Mandarin-English
          code-switched sentences (ICASSP 2024 zero-resource code-switch benchmark). CC BY 4.0.
Neither is Taiwan Mandarin, counselling speech or Taiwanese; say so wherever the numbers go.

Scoring: both texts lower-cased, punctuation removed, converted to Traditional (opencc s2twp,
as Haven does) so a script difference is not an error. Tokens are single CJK characters and
English words. CER counts CJK characters only, WER English words only, MER both together
(the usual mixed error rate for code-switching). Only the candidate's json answer and the
baseline's verbose_json answer are used: verbose_json is what Haven sends to Whisper, and
Whisper's json answer was measured to drop text (smoke test 2026-11-27).

Downloading uses the public internet and plain urllib; scoring talks only to the local
model servers through Haven's endpoint check. Audio and the manifest stay outside the
repository; only the scores and a few examples go to docs/asr-eval/<label>.{md,json}.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from asr_eval import edit_distance  # noqa: E402
from qwen3_asr_smoke import models, transcribe  # noqa: E402

SETS = {
    "ascend": {"dataset": "CAiRE/ASCEND", "config": "main", "split": "test",
               "audio": "audio", "text": "transcription", "category": lambda row: f"ascend-{row.get('language', '?')}"},
    "cszs": {"dataset": "ky552/cszs_zh_en", "config": "default", "split": "test",
             "audio": "correct_audio", "text": "correct_transcription", "category": lambda row: "cszs-mixed"},
}
ROWS_API = "https://datasets-server.huggingface.co/rows"
TOKEN = re.compile(r"[㐀-鿿豈-﫿]|[a-z0-9']+")
CJK = re.compile(r"[㐀-鿿豈-﫿]")

_s2t = None


def to_traditional(text: str) -> str:
    global _s2t
    if _s2t is None:
        try:
            from opencc import OpenCC
            _s2t = OpenCC("s2twp").convert
        except ImportError:
            print("WARNING: opencc is not installed; Simplified references will not match Traditional output. "
                  "pip install -r requirements-asr.txt", file=sys.stderr)
            _s2t = False
    return _s2t(text) if _s2t else text


def tokens(text: str) -> list[str]:
    return TOKEN.findall(to_traditional(text).lower())


def rates(reference: str, hypothesis: str) -> dict:
    ref, hyp = tokens(reference), tokens(hypothesis)
    ref_zh, hyp_zh = [t for t in ref if CJK.fullmatch(t)], [t for t in hyp if CJK.fullmatch(t)]
    ref_en, hyp_en = [t for t in ref if not CJK.fullmatch(t)], [t for t in hyp if not CJK.fullmatch(t)]
    return {"ref_tokens": len(ref), "mer_err": edit_distance(ref, hyp),
            "ref_zh": len(ref_zh), "cer_err": edit_distance(ref_zh, hyp_zh),
            "ref_en": len(ref_en), "wer_err": edit_distance(ref_en, hyp_en)}


# ---------- fetch ----------

def _get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "haven-asr-benchmark"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def fetch(name: str, n: int, out: Path) -> None:
    spec = SETS[name]
    out.mkdir(parents=True, exist_ok=True)
    (out / name).mkdir(exist_ok=True)
    manifest = out / "manifest.jsonl"
    q = {"dataset": spec["dataset"], "config": spec["config"], "split": spec["split"]}
    total = _get_json(f"{ROWS_API}?{urllib.parse.urlencode({**q, 'offset': 0, 'length': 1})}").get("num_rows_total", n)
    stride = max(1, total // n)  # spread the sample over the whole split, deterministically
    written = 0
    with manifest.open("a", encoding="utf-8") as m:
        for k in range(n):
            offset = k * stride
            rows = _get_json(f"{ROWS_API}?{urllib.parse.urlencode({**q, 'offset': offset, 'length': 1})}").get("rows", [])
            if not rows:
                break
            row = rows[0]["row"]
            audio, text = row.get(spec["audio"]), row.get(spec["text"])
            if not (isinstance(audio, list) and audio and audio[0].get("src") and isinstance(text, str) and text.strip()):
                continue
            path = out / name / f"{name}-{offset:06d}.wav"
            if not path.exists():
                with urllib.request.urlopen(urllib.request.Request(audio[0]["src"], headers={"User-Agent": "haven-asr-benchmark"}), timeout=120) as r:
                    path.write_bytes(r.read())
            m.write(json.dumps({"id": f"{name}-{offset}", "audio": str(path), "reference": text,
                                "category": spec["category"](row), "source": spec["dataset"]}, ensure_ascii=False) + "\n")
            written += 1
    print(f"{name}: {written} utterances from {total} (every {stride}th) -> {manifest}")


def add(manifest: Path, audio: Path, script: str, category: str) -> None:
    from haven.transcript import load_transcript
    session, _, span = script.partition(":")
    first, last = (int(x) for x in span.split("-"))
    reference = "".join(s.text for s in load_transcript(session).segments if first <= s.id <= last)
    with manifest.open("a", encoding="utf-8") as m:
        m.write(json.dumps({"id": f"{session}:{span}", "audio": str(audio.expanduser()), "reference": reference,
                            "category": category, "source": f"Haven demo script {script}"}, ensure_ascii=False) + "\n")
    print(f"added {session}:{span} ({len(reference)} characters) -> {manifest}")


# ---------- alignment hybrid ----------

_PLAIN = re.compile(r"[㐀-鿿豈-﫿A-Za-z0-9]")


SNAP = 6  # how far (in characters) an unsure cut may move to reach the candidate's punctuation


def align(segments: list[dict], text: str, stats: dict | None = None) -> list[str]:
    """Cuts ``text`` (the candidate's full transcript) into pieces matching ``segments``
    (the baseline's timed segments), so each piece keeps that segment's id and times.
    Character-level alignment of the two transcripts with difflib; inside a replaced
    stretch the cut falls proportionally. A cut is sure when the characters on both
    sides of it are the same in both transcripts; any other cut moves to the nearest
    punctuation of the candidate within SNAP characters (Whisper's segments end at
    clause ends, and the candidate punctuates them), or stays put if there is none.
    ``stats`` gets the number of cuts that moved. Standard library only."""
    w_plain, bounds = "", []
    for s in segments:  # character by character, so conversion never changes a length
        w_plain += "".join(to_traditional(c).lower() for c in _PLAIN.findall(str(s.get("text", ""))))
        bounds.append(len(w_plain))
    q_chars = [(i, c) for i, c in enumerate(text) if _PLAIN.fullmatch(c)]
    q_plain = "".join(to_traditional(c).lower() for _, c in q_chars)
    ops = difflib.SequenceMatcher(None, w_plain, q_plain, autojunk=False).get_opcodes()
    # j is a punctuation gap when something other than spaces stands between plain characters j-1 and j
    gaps = [j for j in range(1, len(q_chars)) if text[q_chars[j - 1][0] + 1:q_chars[j][0]].strip()]

    def to_q(b: int) -> int:
        for tag, i1, i2, j1, j2 in ops:
            if i1 < b < i2 and tag == "equal":
                return j1 + b - i1  # sure
        for tag, i1, i2, j1, j2 in ops:
            if i1 <= b <= i2:
                j = j1 + (b - i1 if tag == "equal" else round((b - i1) * (j2 - j1) / max(1, i2 - i1)))
                near = min(gaps, key=lambda g: (abs(g - j), g), default=None)
                if near is not None and near != j and abs(near - j) <= SNAP:
                    if stats is not None:
                        stats["snapped"] = stats.get("snapped", 0) + 1
                    return near
                return j
        return len(q_plain)

    cuts = [0] + [q_chars[j][0] if j < len(q_chars) else len(text) for j in (to_q(b) for b in bounds[:-1])] + [len(text)]
    cuts = [max(cuts[i], cuts[i - 1]) if i else c for i, c in enumerate(cuts)]  # never backwards
    return [text[cuts[i]:cuts[i + 1]].strip() for i in range(len(segments))]


# ---------- run ----------

def run(args) -> None:
    items = [json.loads(line) for line in Path(args.manifest).expanduser().read_text(encoding="utf-8").splitlines() if line.strip()]
    for url, model in ((args.candidate_url, args.candidate_model), (args.baseline_url, args.baseline_model)):
        if model not in models(url):
            raise SystemExit(f"{url} does not serve {model!r}")
    results = []
    for n, item in enumerate(items, 1):
        audio = Path(item["audio"])
        cand = transcribe(args.candidate_url, args.candidate_model, audio, "json", args.timeout)
        base = transcribe(args.baseline_url, args.baseline_model, audio, "verbose_json", args.timeout, keep_segments=True)
        row = {"id": item["id"], "category": item["category"], "reference": item["reference"],
               "candidate": {"status": cand["status"], "seconds": cand["seconds"], "text": cand["text"], "error": cand["error"][:300]},
               "baseline": {"status": base["status"], "seconds": base["seconds"], "text": base["text"], "error": base["error"][:300]}}
        if cand["status"] == 200:
            row["candidate"].update(rates(item["reference"], cand["text"]))
        if base["status"] == 200:
            row["baseline"].update(rates(item["reference"], base["text"]))
        segs = base.get("all_segments", []) if item["category"].startswith("long") else []
        if segs and cand["status"] == 200:
            moved: dict = {}
            pieces = align(segs, cand["text"], moved)
            empty = sum(1 for p in pieces if not tokens(p))
            disagree = [edit_distance(tokens(p), tokens(s["text"])) / max(1, len(tokens(s["text"]))) for p, s in zip(pieces, segs)]
            row["hybrid"] = {"segments": len(segs), "snapped_cuts": moved.get("snapped", 0), "empty_pieces": empty, "median_piece_vs_whisper": round(statistics.median(disagree), 3),
                             "sample": [{"start": s["start"], "end": s["end"], "whisper": s["text"], "hybrid": p}
                                        for s, p in list(zip(segs, pieces))[::max(1, len(segs) // 20)][:20]]}
        results.append(row)
        print(f"[{n}/{len(items)}] {item['id']} {item['category']}: candidate {_fmt(row['candidate'])} | baseline {_fmt(row['baseline'])}", flush=True)
    write_report(args, results)


def _cell(text: str, limit: int = 80) -> str:
    return " ".join(str(text).split())[:limit].replace("|", "/")


def _fmt(r: dict) -> str:
    if r["status"] != 200:
        return f"HTTP {r['status']}"
    return f"MER {r['mer_err'] / max(1, r['ref_tokens']):.1%} ({r['seconds']} s)"


def _rate(rows: list[dict], who: str, err: str, ref: str) -> str:
    ok = [r[who] for r in rows if r[who]["status"] == 200]
    total = sum(r[ref] for r in ok)
    return f"{sum(r[err] for r in ok) / total:.1%}" if total else "–"


def write_report(args, results: list[dict]) -> None:
    by_cat = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r)
    lines = [f"# ASR benchmark: {args.label}", "",
             f"- Date: {datetime.now().astimezone().isoformat(timespec='seconds')}",
             f"- Candidate: `{args.candidate_model}` at {args.candidate_url} (response_format json; no timestamps)",
             f"- Baseline: `{args.baseline_model}` at {args.baseline_url} (response_format verbose_json, as Haven sends it)",
             f"- Simplified → Traditional before scoring: {'on (opencc s2twp)' if _s2t else 'OFF'}",
             "- Sets: CAiRE/ASCEND test (Hong Kong bilingual speakers, spontaneous; CC BY-SA 4.0) and ky552/cszs_zh_en "
             "test (code-switched sentences; CC BY 4.0). Not Taiwan Mandarin, not counselling speech, no Taiwanese.",
             "- Micro-averaged over each category. Lower is better.", "",
             "| Category | Utterances | MER Qwen3-ASR | MER Whisper | CER Qwen3-ASR | CER Whisper | WER Qwen3-ASR | WER Whisper | Failed (Q / W) | Seconds (Q / W) |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for cat in sorted(by_cat) + ["all"]:
        rows = results if cat == "all" else by_cat[cat]
        failed = (sum(r["candidate"]["status"] != 200 for r in rows), sum(r["baseline"]["status"] != 200 for r in rows))
        secs = (round(sum(r["candidate"]["seconds"] or 0 for r in rows), 1), round(sum(r["baseline"]["seconds"] or 0 for r in rows), 1))
        lines.append(f"| {cat} | {len(rows)} | {_rate(rows, 'candidate', 'mer_err', 'ref_tokens')} | {_rate(rows, 'baseline', 'mer_err', 'ref_tokens')} | "
                     f"{_rate(rows, 'candidate', 'cer_err', 'ref_zh')} | {_rate(rows, 'baseline', 'cer_err', 'ref_zh')} | "
                     f"{_rate(rows, 'candidate', 'wer_err', 'ref_en')} | {_rate(rows, 'baseline', 'wer_err', 'ref_en')} | "
                     f"{failed[0]} / {failed[1]} | {secs[0]} / {secs[1]} |")
    for r in results:
        h = r.get("hybrid")
        if h:
            lines += ["", f"## Alignment hybrid on {r['id']}", "",
                      f"{h['segments']} Whisper segments; {h.get('snapped_cuts', 0)} unsure cuts moved to Qwen3-ASR punctuation; {h['empty_pieces']} got no Qwen3-ASR text; median difference between a "
                      f"segment's Qwen3-ASR piece and Whisper's own text {h['median_piece_vs_whisper']:.1%} (this includes Whisper's own errors). "
                      "Read the sample: a boundary is right when both columns say the same thing.", "",
                      "| Time | Whisper | Hybrid (Qwen3-ASR text) |", "| --- | --- | --- |"]
            lines += [f"| {s['start']}–{s['end']} | {_cell(s['whisper'], 200)} | {_cell(s['hybrid'], 200)} |" for s in h["sample"]]
    worst = sorted((r for r in results if r["candidate"].get("ref_tokens") and r["baseline"].get("ref_tokens")),
                   key=lambda r: r["candidate"]["mer_err"] / r["candidate"]["ref_tokens"] - r["baseline"]["mer_err"] / r["baseline"]["ref_tokens"],
                   reverse=True)[:5]
    lines += ["", "## Where Qwen3-ASR did worst relative to Whisper", "", "| Id | Reference | Qwen3-ASR | Whisper |", "| --- | --- | --- | --- |"]
    lines += [f"| {r['id']} | {_cell(r['reference'])} | {_cell(r['candidate']['text'])} | {_cell(r['baseline']['text'])} |" for r in worst]
    out = ROOT / "docs" / "asr-eval"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{args.label}.json").write_text(json.dumps({"label": args.label, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / f"{args.label}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote docs/asr-eval/{args.label}.md and .json")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("set", choices=sorted(SETS))
    f.add_argument("--n", type=int, default=50)
    f.add_argument("--out", type=Path, default=Path("~/asr-bench").expanduser())
    a = sub.add_parser("add")
    a.add_argument("manifest", type=Path)
    a.add_argument("audio", type=Path)
    a.add_argument("--script", required=True, help="SESSION:FIRST-LAST of the demo script the recording reads")
    a.add_argument("--category", default="long-zh", help="a category starting with 'long' also gets the alignment hybrid")
    r = sub.add_parser("run")
    r.add_argument("manifest")
    r.add_argument("--label", required=True)
    r.add_argument("--candidate-url", default="http://127.0.0.1:8002")
    r.add_argument("--candidate-model", default="qwen3-asr")
    r.add_argument("--baseline-url", default="http://127.0.0.1:8001")
    r.add_argument("--baseline-model", default="whisper")
    r.add_argument("--timeout", type=float, default=1800)
    args = ap.parse_args()
    if args.cmd == "fetch":
        fetch(args.set, args.n, args.out.expanduser())
    elif args.cmd == "add":
        add(args.manifest.expanduser(), args.audio, args.script, args.category)
    else:
        run(args)


if __name__ == "__main__":
    main()
