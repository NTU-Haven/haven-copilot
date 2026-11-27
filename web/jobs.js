// Processing screen for background jobs (ticket 05). An upload or a finished
// recording is answered at once with a job; this screen polls GET /api/jobs/<id>
// every 2 seconds, opens the draft when the job succeeds, and offers 重試 when it
// failed or was cut off by a restart. Uses el(), api(), post(), busy(), nav,
// openSession() and openClient() from app.js. Text is inserted as text only.

const JOB_POLL_MS = 2000;
const JOB_STEPS = [["transcribe", "轉寫"], ["speakers", "說話者"], ["draft", "草稿"]];
const JOB_STAGE_TEXT = { transcribe: "轉寫中…", speakers: "推測說話者中…", draft: "產生草稿中…" };

function jobStatusText(job) {
  if (job.status === "queued") return "排隊等待處理…";
  if (job.status === "running") return JOB_STAGE_TEXT[job.stage] || "處理中…";
  if (job.status === "succeeded") return "完成，正在開啟紀錄草稿…";
  return job.message_zh || "處理沒有完成。";
}

function jobSteps(job) {
  const steps = job.kind === "regenerate" ? JOB_STEPS.slice(2) : JOB_STEPS; // a regenerate only drafts
  const at = steps.findIndex(([k]) => k === job.stage);
  return el("ol", { class: "job-steps" }, steps.map(([, label], i) => {
    const state = job.status === "succeeded" || (at >= 0 && i < at) ? "done"
      : i === at && job.status === "running" ? "current" : "pending";
    return el("li", { class: state }, label);
  }));
}

function showJob(job) {
  const my = ++nav;
  const main = document.getElementById("main");
  const clientId = job.session_id.split("-")[0];
  const steps = el("div", {});
  const status = el("p", { class: "job-status", role: "status", "aria-live": "polite" });
  const actions = el("div", { class: "job-actions" });
  main.replaceChildren(el("section", { class: "card job-card" },
    el("h2", {}, `處理會談 ${job.session_id}`),
    steps, status, actions,
    el("p", { class: "hint" }, "處理在主機上進行，可以先離開這個畫面；完成後可從個案頁開啟這次會談。"),
    el("button", { type: "button", class: "back", onclick: () => openClient(clientId) }, "← 回個案")));

  const render = (j) => {
    put(steps, jobSteps(j));
    status.className = j.status === "failed" || j.status === "interrupted" ? "job-status problem" : "job-status";
    status.textContent = jobStatusText(j) + (j.attempt > 1 && j.status !== "failed" ? `（第 ${j.attempt} 次嘗試）` : "");
    actions.replaceChildren();
    if (j.status === "failed" || j.status === "interrupted") {
      actions.append(el("button", { type: "button", onclick: (e) => retryJob(j, e.currentTarget, status) }, "重試"));
    }
  };

  const tick = async () => {
    if (my !== nav) return; // the therapist went elsewhere; stop polling
    let j;
    try {
      j = await api(`/api/jobs/${encodeURIComponent(job.job_id)}`);
    } catch (err) {
      if (my !== nav) return;
      status.textContent = `暫時無法取得處理狀態（${err.message}），稍後會再查詢。`;
      setTimeout(tick, JOB_POLL_MS);
      return;
    }
    if (my !== nav) return;
    render(j);
    if (j.status === "succeeded") {
      await loadClients();
      if (my !== nav) return;
      toast(`${j.session_id} 已轉寫並產生紀錄草稿。${KEEP_NO_AUDIO}`);
      openSession(j.session_id);
      return;
    }
    if (j.status === "queued" || j.status === "running") setTimeout(tick, JOB_POLL_MS);
  };

  render(job);
  if (job.status === "queued" || job.status === "running") setTimeout(tick, JOB_POLL_MS);
}

async function retryJob(job, btn, status) {
  await busy(btn, "重試中", async () => {
    try {
      showJob(await post(`/api/jobs/${encodeURIComponent(job.job_id)}/retry`));
    } catch (err) {
      status.className = "job-status problem";
      status.textContent = `無法重試：${err.message}`;
    }
  });
}

// For the client page: sessions still being processed, or whose processing failed.
function jobButtons(jobs) {
  return (jobs || []).filter((j) => j.status !== "succeeded").map((j) => {
    const state = j.status === "queued" || j.status === "running" ? "處理中" : "處理失敗，可重試";
    return el("button", { type: "button", class: `job-button ${j.status}`, title: j.session_id, onclick: () => showJob(j) },
      `${j.session_id}・${state}`);
  });
}
