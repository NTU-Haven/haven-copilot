"use strict";
// Template picker and the placeholder for human_only fields. Loaded before app.js;
// uses el(), api() and regenerate() from it when called.

// Same text as haven/human_fields.py PLACEHOLDER. The server decides what blocks signing;
// the page only hides the "keep this sentence" button for it.
const PLACEHOLDER = "待心理師填寫";

function isPlaceholder(text) {
  return String(text).trim().replace(/[。.]$/, "").trim() === PLACEHOLDER;
}

// What an empty section says. A generated field with an "empty" label in its template
// (QUAL: 未提及) shows that; everything else keeps the usual line.
function emptyText(key, locked) {
  const f = templateField(key);
  if (f && f.ai_mode === "generate" && f.empty) return f.empty;
  return locked ? "（無）" : "（這一欄目前沒有句子）";
}

let templateList = null;

// A select built from GET /api/templates, and a button that drafts the note again
// with the chosen template (the existing draft is discarded after a confirmation).
function templatePicker(r) {
  const box = el("div", { class: "template-picker" }, el("span", {}, "紀錄模板："));
  const fill = (list) => {
    if (list.length < 2) return;
    const select = el("select", { id: "template-pick", "aria-label": "紀錄模板" },
      list.map((t) => el("option", { value: t.id, selected: t.id === r.note.template }, t.name)));
    const go = el("button", { type: "button", id: "template-go", onclick: (e) => {
      if (select.value === r.note.template) { showMessage("目前就是這個模板。", true); return; }
      regenerate(e.currentTarget, select.value);
    } }, "用此模板重新產生");
    box.append(select, go);
  };
  if (templateList) fill(templateList);
  else api("/api/templates").then((list) => { templateList = list; if (box.isConnected) fill(list); }).catch(() => { box.remove(); });
  return box;
}
