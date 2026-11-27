"use strict";
// Transcript corrections on the session page (ticket 06): edit a line's words,
// change a confirmed line's speaker, and the "needs review" banner. Loaded after
// app.js and uses its helpers (el, put, post, session, nav, renderSession, ...).
// All text goes in as text nodes, never as HTML.

// Called by renderSession after the page is drawn.
function decorateTranscript(r) {
  if (!r || r.signed) return;
  for (const s of r.transcript) {
    const row = document.getElementById(`seg-${s.id}`);
    if (!row) continue;
    const said = row.querySelector(".said");
    if (s.asr_text != null && s.asr_text !== s.text) {
      said.append(" ", el("span", { class: "seg-edited", title: `原始語音辨識：${s.asr_text}` }, "已修改"));
    }
    said.append(" ", el("button", { type: "button", class: "seg-edit", "aria-label": `修改 #${s.id} 的文字`, onclick: () => editLine(s, row) }, "編輯"));
    if (s.speaker_source === "confirmed") confirmedSpeakerPick(s, row);
  }
  reviewBanner(r);
}

// A confirmed line keeps a dropdown, so a wrong confirmation can be corrected (AC-06).
function confirmedSpeakerPick(s, row) {
  const who = row.querySelector(".who");
  if (!who) return;
  const pick = el("select", { class: "who-pick confirmed", "aria-label": `#${s.id} 的說話者（已確認）`,
    onchange: () => saveTranscript("speakers", { [s.id]: pick.value }, "說話者已更正，紀錄已重新查核。") },
  [["therapist", "心理師"], ["client", "個案"]].map(([v, label]) => el("option", { value: v, selected: s.speaker === v }, label)));
  who.replaceWith(pick);
}

function editLine(s, row) {
  const said = row.querySelector(".said");
  if (!said || said.querySelector(".seg-input")) return;
  const kept = [...said.childNodes];
  const box = el("span", {
    class: "seg-input", contenteditable: EDITABLE, role: "textbox", "aria-label": `#${s.id} 的文字，Enter 存檔，Esc 取消`,
    onpaste: pastePlain,
    onkeydown: (e) => {
      if (e.key === "Escape") { e.preventDefault(); put(said, kept); }
      else if (e.key === "Enter") {
        e.preventDefault();
        const text = box.textContent.trim();
        if (!text) { showMessage("逐字稿段落不能是空白。", true); return; }
        if (text.length > 2000) { showMessage("一段逐字稿最多 2000 字。", true); return; }
        if (text === s.text) { put(said, kept); return; }
        box.setAttribute("contenteditable", "false");
        saveTranscript("transcript", { [s.id]: text }, `#${s.id} 已修改；引用這段的句子請重新核對。`)
          .then((ok) => { if (!ok && box.isConnected) box.setAttribute("contenteditable", EDITABLE); });
      }
    },
  }, s.text);
  put(said, box, el("span", { class: "seg-hint" }, "Enter 存檔，Esc 取消"));
  box.focus();
}

// kind: "transcript" (words) or "speakers". Unsaved draft edits are saved first,
// because the server re-checks the whole draft after a transcript change.
async function saveTranscript(kind, changes, done) {
  if (!session || session.signed || session.conflict) return false;
  if (edited && !(await reverify())) return false;
  const my = nav, sid = session.session_id;
  const body = kind === "transcript" ? { segments: changes } : { speakers: changes };
  try {
    const r = await post(`/api/sessions/${encodeURIComponent(sid)}/${kind}`, { ...body, base_revision: session.revision || 0 });
    if (my !== nav) return false;
    renderSession(r);
    showMessage(done);
    return true;
  } catch (err) {
    if (my !== nav) return false;
    if (err.data && err.data.conflict) showConflict(err.message);
    else showMessage(`沒有存檔：${err.message}`, true);
    return false;
  }
}

function reviewBanner(r) {
  const box = document.getElementById("note-box");
  if (!box || r.review_status !== "needs_review") return;
  const n = new Set(r.transcript.filter((s) => s.edited_at).map((s) => s.id)).size;
  const btn = el("button", { type: "button", class: "confirm", onclick: () => markReviewed(btn) }, "我已依目前逐字稿核對");
  box.before(el("div", { class: "review-banner", role: "alert" },
    el("span", {}, `逐字稿在草稿產生或上次核對後修改過（目前版本 ${r.transcript_revision}${n ? `，${n} 段有修改` : ""}），需要重新審閱。標黃的句子引用了修改過的段落。`),
    btn));
}

async function markReviewed(btn) {
  if (edited && !(await reverify())) return;
  const my = nav, sid = session.session_id;
  await busy(btn, "存檔中", async () => {
    try {
      const r = await post(`/api/sessions/${encodeURIComponent(sid)}/reviewed`, { base_revision: session.revision || 0 });
      if (my !== nav) return;
      renderSession(r);
      showMessage("已記錄：草稿已依目前逐字稿核對。");
    } catch (err) {
      if (my !== nav) return;
      if (err.data && err.data.conflict) showConflict(err.message);
      else showMessage(`沒有存檔：${err.message}`, true);
    }
  });
}

// Under a draft sentence whose cited line changed after the last review (sentenceRow calls this).
function sourceChangedNote(s) {
  if (!s.source_changed || !session || session.signed) return null;
  return el("div", { class: "reason source-changed" }, "來源已修改，請重新核對");
}
