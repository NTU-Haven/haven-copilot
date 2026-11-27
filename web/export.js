"use strict";
// Export of a confirmed note (ticket 11): download as Markdown or plain text, and mark
// an export as filed. Haven cannot see the other system, so "已下載" and "已歸檔" are
// separate states and only the therapist sets the second one.
// The file is fetched with the X-Haven header and saved from a Blob. A blob: link is a
// download, not a request or a script, so the strict CSP needs no change.
const EXPORT_STATUS = { not_exported: "尚未匯出", downloaded: "已下載（尚未標記歸檔）", archived: "已歸檔" };

async function downloadExport(sid, fmt) {
  let res;
  try {
    res = await fetch(`/api/sessions/${encodeURIComponent(sid)}/export?format=${fmt}`, { headers: { "X-Haven": "1" }, cache: "no-store" });
  } catch (err) {
    throw new Error("連不上本機的 Haven 伺服器，請確認程式還在執行。");
  }
  if (!res.ok) {
    let data = {};
    try { data = await res.json(); } catch (err) { /* not JSON */ }
    throw new Error(data.message_zh || `匯出沒有完成（代碼 ${res.status}）。`);
  }
  const m = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") || "");
  const url = URL.createObjectURL(await res.blob());
  const a = el("a", { href: url, download: m ? m[1] : `haven-note.${fmt}` });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
  return res.headers.get("X-Export-Id");
}

function exportBox(r) {
  const status = el("div", { class: "export-status", role: "status" });
  const ref = el("input", { type: "text", maxlength: "100", "aria-label": "歸檔編號", placeholder: "歸檔編號（選填，例如院內系統的紀錄號）" });
  const archive = el("button", { type: "button" }, "標記為已歸檔");
  let exports = r.exports || [];
  const draw = () => {
    const state = exports.some((e) => e.status === "archived") ? "archived" : exports.length ? "downloaded" : "not_exported";
    const done = exports.find((e) => e.status === "archived");
    put(status, `匯出狀態：${EXPORT_STATUS[state]}`,
      done ? `（${fmtDateTime(done.archived_at)}${done.archive_ref ? `・編號 ${done.archive_ref}` : ""}）` : "");
    ref.disabled = archive.disabled = state !== "downloaded";
  };
  const download = (fmt, label) => el("button", { type: "button", onclick: (e) => busy(e.currentTarget, "匯出中", async () => {
    try {
      const id = await downloadExport(r.session_id, fmt);
      const now = await api(`/api/sessions/${encodeURIComponent(r.session_id)}`);
      exports = now.exports || exports;
      draw();
      showMessage(`已下載匯出檔（${id}）。檔案不含逐字稿；存進您的紀錄系統後，請按「標記為已歸檔」。`);
    } catch (err) {
      showMessage(`無法匯出：${err.message}`, true);
    }
  }) }, label);
  archive.addEventListener("click", () => busy(archive, "標記中", async () => {
    const target = [...exports].reverse().find((e) => e.status !== "archived");
    if (!target) return;
    try {
      const out = await post(`/api/sessions/${encodeURIComponent(r.session_id)}/exports/${encodeURIComponent(target.export_id)}/archived`, { archive_ref: ref.value.trim() });
      exports = exports.map((e) => (e.export_id === out.export.export_id ? out.export : e));
      draw();
      showMessage("已標記為已歸檔。");
    } catch (err) {
      showMessage(`無法標記歸檔：${err.message}`, true);
    }
  }));
  draw();
  return el("div", { class: "export-box" },
    el("div", { class: "meta" }, "匯出（不含逐字稿，含確認者、時間、紀錄雜湊與「AI 輔助產生、心理師確認」聲明）"),
    el("div", { class: "export-actions" }, download("md", "匯出 Markdown"), download("txt", "匯出純文字"), ref, archive),
    status);
}
