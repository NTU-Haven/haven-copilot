"use strict";
// Original audio on the session page (ticket 08): a notice above the transcript saying
// whether the recording is still kept, how long it will be, and a button to delete it
// now. Loaded after app.js and uses its helpers (el, post, busy, toast, session, ...).
// All text goes in as text nodes. Playback is a separate ticket.

const AUDIO_REASON_ZH = { expiry: "到期", confirm: "已確認", manual: "手動", replaced: "被新的錄音取代" };

function fmtAudioTime(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? String(iso || "") : d.toLocaleString("zh-TW", { hour12: false });
}

// Called by renderSession after the page is drawn.
function decorateAudio(r) {
  const box = document.querySelector("section.transcript");
  if (!box || !r) return;
  const old = document.getElementById("audio-notice");
  if (old) old.remove();
  let notice = null;
  if (r.audio_status === "deleted") {
    notice = el("div", { class: "audio-notice deleted", id: "audio-notice", role: "status" },
      `原始錄音已於 ${fmtAudioTime(r.audio_deleted_at)} 清除（原因：${AUDIO_REASON_ZH[r.audio_delete_reason] || r.audio_delete_reason || "—"}）；逐字稿與草稿仍保留。`);
  } else if (r.audio_status === "cleanup_failed") {
    notice = el("div", { class: "audio-notice problem", id: "audio-notice", role: "status" },
      "原始錄音正在清除，但主機暫時刪不掉，Haven 會稍後自動重試。");
  } else if (r.audio_status === "available" || r.audio_status === "failed" || r.audio_status === "processing") {
    const left = r.audio_hours_left;
    const btn = el("button", { type: "button", class: "confirm", id: "audio-delete-btn", onclick: () => deleteAudio(btn) }, "刪除原始錄音");
    notice = el("div", { class: "audio-notice", id: "audio-notice", role: "status" },
      el("span", {}, `主機保留著原始錄音，供核對逐字稿；${r.signed ? "" : "簽核後會自動刪除；"}原音剩餘 ${left == null ? "—" : left} 小時（${fmtAudioTime(r.audio_expires_at)} 到期後刪除）。`),
      r.audio_status === "processing" ? null : btn);
  }
  if (notice) box.querySelector("h3").after(notice);
}

async function deleteAudio(btn) {
  if (!confirm("刪除原始錄音？刪除後無法復原，也無法再回聽；逐字稿與草稿仍會保留。")) return;
  const sid = session.session_id, my = nav;
  await busy(btn, "刪除中", async () => {
    try {
      const a = await post(`/api/sessions/${encodeURIComponent(sid)}/audio/delete`);
      if (my !== nav) return;
      Object.assign(session, a);
      decorateAudio(session);
      decoratePlayback(session);
      toast("原始錄音已刪除；逐字稿與草稿仍保留。");
    } catch (err) {
      toast(`無法刪除原始錄音：${err.message}`);
    }
  });
}
