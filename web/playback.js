"use strict";
// Segment playback (ticket 09): a mini player above the transcript and a play button on
// each line. The button asks for a short-lived ticket (POST .../audio/ticket, which sends
// X-Haven), points an <audio> element at the ticket URL, jumps to the line's start and
// pauses at its end. Loaded after app.js and uses its helpers (el, post, toast, fmtTime...).
// All text goes in as text nodes.

const PLAY_TICKET_MARGIN_MS = 30 * 1000; // ask again a little before a ticket runs out
let playState = null; // { sid, audio, ticket, fetchedAt, stopAt }

function stopPlayback() {
  if (playState && playState.audio) { try { playState.audio.pause(); } catch (e) { /* gone */ } }
  playState = null;
}

// Called by renderSession after the page is drawn.
function decoratePlayback(r) {
  stopPlayback();
  const old = document.getElementById("player-bar");
  if (old) old.remove();
  const lines = document.querySelector("section.transcript .transcript-lines");
  if (!lines || !r || !r.audio_status || r.audio_status === "none") return;
  let reason = null;
  if (r.audio_status === "deleted") reason = "原始錄音已清除，無法回放。";
  else if (r.audio_status === "cleanup_failed") reason = "原始錄音正在清除，無法回放。";
  else if (r.playback_supported === false) reason = "瀏覽器錄製的錄音不支援回放（無法拖動到指定時間）；請改用上傳的錄音檔，或以其他方式核對。";
  else if (r.audio_status !== "available") reason = "錄音還沒準備好，暫時無法回放。";
  const bar = el("div", { class: "player-bar", id: "player-bar" });
  if (reason) {
    bar.append(el("span", { class: "muted", role: "status" }, reason));
    lines.before(bar);
    return;
  }
  const audioEl = el("audio", { controls: "", preload: "none", "aria-label": "原始錄音" });
  const note = el("span", { class: "muted", id: "player-note", role: "status" }, "點段落前的 ▶ 從該段開始播放。");
  bar.append(audioEl, note);
  lines.before(bar);
  const state = { sid: r.session_id, audio: audioEl, ticket: null, fetchedAt: 0, stopAt: null, note, estimated: false };
  playState = state;
  audioEl.addEventListener("timeupdate", () => {
    if (state.stopAt != null && audioEl.currentTime >= state.stopAt) { state.stopAt = null; audioEl.pause(); }
  });
  audioEl.addEventListener("error", () => { state.ticket = null; }); // an expired ticket: ask again next time
  for (const s of r.transcript) {
    const row = document.getElementById(`seg-${s.id}`);
    const t = row && row.querySelector(".t");
    if (!t) continue;
    t.prepend(el("button", { type: "button", class: "seg-play", "aria-label": `播放 #${s.id}`, title: "從這段開始播放",
      onclick: () => playSegment(state, s) }), " ");
  }
}

async function playTicket(state) {
  const fresh = state.ticket && Date.now() - state.fetchedAt < (state.ticket.expires_in * 1000 - PLAY_TICKET_MARGIN_MS);
  if (fresh) return state.ticket;
  const t = await post(`/api/sessions/${encodeURIComponent(state.sid)}/audio/ticket`);
  state.ticket = t;
  state.fetchedAt = Date.now();
  state.audio.src = appUrl(t.url); // relative to the page (works under a path prefix)
  state.estimated = !!t.timestamps_estimated;
  state.note.textContent = state.estimated ? "時間位置為估計值，播放位置可能與逐字稿有幾秒落差。" : "點段落前的 ▶ 從該段開始播放。";
  return t;
}

async function playSegment(state, seg) {
  if (playState !== state) return;
  try {
    await playTicket(state);
    const a = state.audio;
    if (a.readyState < 1) { // metadata first: currentTime cannot be set before it
      await new Promise((resolve, reject) => {
        a.addEventListener("loadedmetadata", resolve, { once: true });
        a.addEventListener("error", () => reject(new Error("錄音載入失敗")), { once: true });
        a.load();
      });
    }
    a.currentTime = seg.start;
    state.stopAt = seg.end > seg.start ? seg.end : null;
    await a.play();
  } catch (err) {
    state.ticket = null;
    if (err.status === 409 && err.data && err.data.audio_status) {
      state.note.textContent = err.message;
      if (typeof session !== "undefined" && session) { session.audio_status = err.data.audio_status; decoratePlayback(session); }
    } else if (err.name !== "AbortError") {
      toast(`無法播放這段錄音：${err.message}`);
    }
  }
}
