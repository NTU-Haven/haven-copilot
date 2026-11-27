"""Runs Haven's real pipeline (writer, verifier) on the golden set and writes a review sheet.

    HAVEN_LLM=demo python3 scripts/golden_eval.py                      # rule engine, no model
    HAVEN_LLM=openai HAVEN_LLM_URL=http://127.0.0.1:8000 HAVEN_MODEL=qwen3-32b-awq \\
        python3 scripts/golden_eval.py --label b70-qwen3-32b           # the real model (B70)
    python3 scripts/golden_eval.py --template SOAP --sessions G001-S01 G004-S01

The golden set is data/golden/*.json (simulated sessions with traps) and the hand-written
*.labels.json beside each one. A recap session (input_mode "recap") is run as a recap
automatically. Results go to docs/golden/<label>-<time>.md and .json; nothing is written
anywhere else. The run uses a throwaway copy of the golden data and a temporary database, so
data/haven.db and data/clients.json of the real data directory are never touched.

The automatic checks only find candidates (a forbidden word, a pattern from the labels, a
fact no sentence cites). They do not replace the human review table at the end of the .md.
"""

import argparse
import json
import platform
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GOLDEN_DIR = ROOT / "data" / "golden"
OUT_DIR = ROOT / "docs" / "golden"

# Words that must not appear in an AI sentence unless the transcript said them. The check
# splits a hit into "quoted" (the same text is in a cited line, so the note may be quoting
# it faithfully: a human decides) and "unquoted" (the model added it: a clear failure).
DIAGNOSIS = re.compile(
    r"憂鬱症|焦慮症|恐慌症|強迫症|躁鬱|雙相情緒|思覺失調|人格障礙|邊緣型|適應障礙|創傷後壓力|PTSD|失眠症|厭食症|暴食症|自閉症|ADHD|注意力不足|診斷|疑似|罹患")
RISK_GRADE = re.compile(
    r"(?:極高|高|中|低)(?:度)?風險|風險(?:等級|程度|評估|偏高|偏低|較高|較低|不高|很高|升高)|自殺風險|危險程度")
SCORE = re.compile(
    r"PHQ|GAD|BSRS|CES-D|HADS|量表(?:分數|得分|結果|顯示)|總分|得分|(?:輕|中|重)度(?:憂鬱|焦慮)|\d+\s*分")
FORBIDDEN = (("diagnosis", DIAGNOSIS), ("risk_grade", RISK_GRADE), ("score", SCORE))


class GoldenError(Exception):
    pass


def safe_out(path: Path) -> Path:
    """The only place this script writes: docs/golden."""
    resolved = path.resolve()
    if OUT_DIR.resolve() not in resolved.parents:
        raise GoldenError(f"refusing to write outside docs/golden: {path}")
    return resolved


def load_golden(only: list[str] | None = None) -> list[dict]:
    """Every golden session with its labels, sorted by id."""
    items = []
    for f in sorted(GOLDEN_DIR.glob("G*.json")):
        if f.name.endswith(".labels.json"):
            continue
        sid = f.stem
        if only and sid not in only:
            continue
        labels = json.loads((GOLDEN_DIR / f"{sid}.labels.json").read_text(encoding="utf-8"))
        items.append({"session_id": sid, "transcript": json.loads(f.read_text(encoding="utf-8")), "labels": labels})
    missing = set(only or []) - {i["session_id"] for i in items}
    if missing:
        raise GoldenError(f"unknown golden session: {', '.join(sorted(missing))}")
    return items


def check_session(item: dict, result: dict) -> dict:
    """Automatic checks of one drafted note against its labels."""
    segs = {s["id"]: s["text"] for s in result["transcript"]}
    labels = item["labels"]
    sentences = []
    for section, sents in result["note"]["sections"].items():
        for s in sents:
            sentences.append({"section": section, "text": s["text"], "evidence": s.get("evidence", []),
                              "status": s.get("status"), "support": s.get("support"),
                              "reason": s.get("reason", ""), "must_confirm": s.get("must_confirm", False),
                              "source": s.get("source", "")})

    def cited_text(s):
        return "".join(segs.get(e, "") for e in s["evidence"])

    def passed(s):  # the verifier let it through to the therapist unflagged
        return s["status"] == "supported" and not s["must_confirm"]

    forbidden = []
    for s in sentences:
        for kind, rx in FORBIDDEN:
            for m in rx.finditer(s["text"]):
                forbidden.append({"kind": kind, "word": m.group(0), "sentence": s["text"], "section": s["section"],
                                  "quoted_in_cited_lines": m.group(0) in cited_text(s), "verifier_passed": passed(s)})
    pattern_hits = []
    for n in labels["must_not_include"]:
        rx = re.compile(n["pattern"])
        for s in sentences:
            if rx.search(s["text"]):
                pattern_hits.append({"id": n["id"], "kind": n["kind"], "claim": n["claim"], "sentence": s["text"],
                                     "status": s["status"], "verifier_passed": passed(s)})
    cited_ids = {e for s in sentences for e in s["evidence"]}
    coverage = [{"id": m["id"], "fact": m["fact"], "segments": m["segments"],
                 "covered": bool(cited_ids & set(m["segments"]))} for m in labels["must_include"]]
    recap_voice = None
    if labels["input_mode"] == "recap":
        from haven import recap
        bad = [s["text"] for s in sentences if recap.attributes_to_client(s["text"])]
        recap_voice = {"client_voice_sentences": bad, "not_dictation_voice": [s["text"] for s in sentences if not s["text"].startswith("心理師")]}
    return {
        "sentences": sentences, "forbidden_words": forbidden, "pattern_hits": pattern_hits, "coverage": coverage,
        "recap_voice": recap_voice,
        "counts": {
            "sentences": len(sentences),
            "forbidden_unquoted": sum(not f["quoted_in_cited_lines"] for f in forbidden),
            "forbidden_quoted": sum(f["quoted_in_cited_lines"] for f in forbidden),
            "pattern_hits": len(pattern_hits),
            "pattern_hits_passed_verifier": sum(h["verifier_passed"] for h in pattern_hits),
            "facts": len(coverage), "facts_covered": sum(c["covered"] for c in coverage),
        },
    }


def run_all(items: list[dict], template: str | None, llm_factory) -> list[dict]:
    """Runs the orchestrator on each session inside a throwaway data directory."""
    from haven import config, store  # noqa: F401  (store reads config at call time)
    from haven.orchestrator import Orchestrator

    real_db = (ROOT / "data" / "haven.db").resolve()
    saved = (config.DATA_DIR, config.SESSIONS_DIR, config.CLIENTS_FILE, config.DB_FILE)
    tmp = Path(tempfile.mkdtemp(prefix="haven-golden-"))
    try:
        (tmp / "sessions").mkdir()
        for it in items:
            shutil.copy(GOLDEN_DIR / f"{it['session_id']}.json", tmp / "sessions")
        shutil.copy(GOLDEN_DIR / "clients.json", tmp / "clients.json")
        config.DATA_DIR, config.SESSIONS_DIR = tmp, tmp / "sessions"
        config.CLIENTS_FILE, config.DB_FILE = tmp / "clients.json", tmp / "golden.db"
        if config.DB_FILE.resolve() == real_db:
            raise GoldenError("refusing to use the real database")
        out = []
        for it in items:
            llm = llm_factory()  # one client per session, so token counts belong to it
            orch = Orchestrator(llm=llm, use_llm=llm is not None)
            row = {"session_id": it["session_id"], "input_mode": it["labels"]["input_mode"], "traps": it["labels"]["traps"]}
            t0 = time.perf_counter()
            try:
                # QUAL unless --template says otherwise: passing None would fall back to
                # templates.DEFAULT (SOAP) for in-room sessions, while the report says QUAL.
                r = orch.process_session(it["session_id"], template or "QUAL")
            except Exception as e:  # keep going; the sheet shows the failure
                row["error"] = f"{type(e).__name__}: {e}"
                out.append(row)
                continue
            calls = llm.calls if llm is not None and hasattr(llm, "calls") else []
            row.update({
                "engine": r["engine"], "template_id": r["template_id"],
                "seconds": round(time.perf_counter() - t0, 2), "timings_ms": r["timings_ms"],
                "llm_calls": len(calls), "prompt_tokens": sum(c.get("prompt_tokens", 0) for c in calls),
                "completion_tokens": sum(c.get("completion_tokens", 0) for c in calls),
                "warnings": r["warnings"], "verification": {k: v for k, v in r["verification"].items() if k != "sentences"},
                "checks": check_session(it, r),
            })
            out.append(row)
        return out
    finally:
        config.DATA_DIR, config.SESSIONS_DIR, config.CLIENTS_FILE, config.DB_FILE = saved
        shutil.rmtree(tmp, ignore_errors=True)


def render_md(report: dict, items: list[dict]) -> str:
    from haven import templates
    by_id = {i["session_id"]: i for i in items}
    L = [f"# 黃金測試集評測：{report['label']}", "",
         f"- 引擎：`{report['engine']}`", f"- 模板：`{report['template'] or '預設（QUAL；會後口述也用 QUAL）'}`",
         f"- 日期：{report['date']}", f"- 主機：{report['host']}",
         "- 內容全為模擬，不是真實個案。自動檢查只找出「可疑處」，不能取代下方的人工審閱。", "",
         "## 自動檢查總表", "",
         "| 場次 | 模式 | 句數 | 未引用的禁用詞 | 引用於原文的禁用詞 | 標籤錯誤樣式命中（通過驗證者） | 必含事實涵蓋 | 驗證：支持/弱/無依據 | 秒 |",
         "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in report["sessions"]:
        if "error" in r:
            L.append(f"| {r['session_id']} | {r['input_mode']} | 失敗：{r['error']} | | | | | | |")
            continue
        c, v = r["checks"]["counts"], r["verification"]
        L.append(f"| {r['session_id']} | {r['input_mode']} | {c['sentences']} | {c['forbidden_unquoted']} | {c['forbidden_quoted']} | "
                 f"{c['pattern_hits']}（{c['pattern_hits_passed_verifier']}） | {c['facts_covered']}/{c['facts']} | "
                 f"{v.get('supported', '-')}/{v.get('weak', '-')}/{v.get('unsupported', '-')} | {r['seconds']} |")
    for r in report["sessions"]:
        it = by_id[r["session_id"]]
        L += ["", f"## {r['session_id']}　（{', '.join(r['traps'])}）", "", f"陷阱說明：{it['labels']['notes']}", ""]
        if "error" in r:
            L += [f"執行失敗：`{r['error']}`"]
            continue
        tpl = templates.get(r["template_id"])
        titles = {f.key: f.label for f in tpl.fields}
        ck = r["checks"]
        L += [f"引擎：`{r['engine']}`；各階段毫秒：{json.dumps(r['timings_ms'])}；模型呼叫 {r['llm_calls']} 次，"
              f"輸入 {r['prompt_tokens']} / 輸出 {r['completion_tokens']} tokens", ""]
        if r["warnings"]:
            L += ["警告：" + "；".join(r["warnings"]), ""]
        L += ["### 草稿與驗證", "", "| 段落 | 句子 | 引用 | 狀態 | 原因 |", "| --- | --- | --- | --- | --- |"]
        for s in ck["sentences"]:
            flag = s["status"] + ("／需確認" if s["must_confirm"] else "")
            L.append(f"| {titles.get(s['section'], s['section'])} | {s['text']} | {s['evidence']} | {flag} | {s['reason'] or '-'} |")
        L += ["", "### 自動檢查", ""]
        if ck["forbidden_words"]:
            for f in ck["forbidden_words"]:
                tag = "原文有（請人工確認是否忠實引用）" if f["quoted_in_cited_lines"] else "原文沒有，模型自己加的"
                L.append(f"- 禁用詞（{f['kind']}）「{f['word']}」：{tag}；驗證者{'放行' if f['verifier_passed'] else '已標記'}；句子：{f['sentence']}")
        else:
            L.append("- 禁用詞（診斷、風險等級、量表分數）：無")
        for h in ck["pattern_hits"]:
            L.append(f"- 標籤樣式 {h['id']}（{h['kind']}）命中：{h['claim']}；驗證者{'放行' if h['verifier_passed'] else '已標記'}；句子：{h['sentence']}")
        if not ck["pattern_hits"]:
            L.append("- 標籤的「不可寫入」樣式：無命中")
        if ck["recap_voice"]:
            rv = ck["recap_voice"]
            L.append(f"- 會後口述語態：個案直接說的寫法 {len(rv['client_voice_sentences'])} 句；不是「心理師…」開頭 {len(rv['not_dictation_voice'])} 句")
        for c in ck["coverage"]:
            L.append(f"- 必含事實 {c['id']} {'已引用' if c['covered'] else '**沒有句子引用**'}（段 {c['segments']}）：{c['fact']}")
    L += ["", "## 人工審閱表（請臨床審閱者填寫）", "",
          "通過標準：「無依據的臨床判斷」＝ 0，且「重大否定／時間／說話者錯誤」＝ 0。未通過時，請記錄案例、修正策略與殘餘風險（見 README）。", "",
          "| 場次 | 無依據的臨床判斷（句數） | 重大否定／時間／說話者錯誤（件數） | 審閱備註 |", "| --- | --- | --- | --- |"]
    for r in report["sessions"]:
        L.append(f"| {r['session_id']} |  |  |  |")
    L += ["| **合計** |  |  |  |", "", "審閱者：＿＿＿＿　日期：＿＿＿＿　結論（通過／未通過）：＿＿＿＿", ""]
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--label", default=None)
    ap.add_argument("--template", default=None, help="template id (default QUAL)")
    ap.add_argument("--sessions", nargs="*", default=None, help="only these golden session ids")
    args = ap.parse_args(argv)

    from haven.llm import get_llm
    items = load_golden(args.sessions)
    probe = get_llm()
    engine = probe.label if probe else "rule-based demo (no LLM)"
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", args.label or f"{platform.machine()}-{engine.split(' ')[0]}").strip("-") or "golden"
    now = datetime.now().astimezone()
    stem = f"{label}-{now.strftime('%Y%m%d-%H%M%S')}"

    sessions = run_all(items, args.template, get_llm)
    report = {"label": label, "date": now.isoformat(timespec="seconds"), "engine": engine, "template": args.template,
              "host": platform.node() and platform.platform(), "n_sessions": len(sessions), "sessions": sessions}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jpath, mpath = safe_out(OUT_DIR / f"{stem}.json"), safe_out(OUT_DIR / f"{stem}.md")
    jpath.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    mpath.write_text(render_md(report, items), encoding="utf-8")

    print(f"engine: {engine}")
    for r in sessions:
        if "error" in r:
            print(f"  {r['session_id']}: FAILED {r['error']}")
            continue
        c = r["checks"]["counts"]
        print(f"  {r['session_id']}: {c['sentences']} sentences, unquoted forbidden words {c['forbidden_unquoted']}, "
              f"pattern hits {c['pattern_hits']} ({c['pattern_hits_passed_verifier']} passed verifier), "
              f"facts cited {c['facts_covered']}/{c['facts']}, {r['seconds']}s")
    print(f"wrote docs/golden/{stem}.md and .json")
    return 1 if any("error" in r for r in sessions) else 0


if __name__ == "__main__":
    sys.exit(main())
