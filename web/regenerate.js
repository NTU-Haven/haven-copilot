"use strict";
// Regenerate confirmation (ticket 07). Resolves true only when the therapist ticked
// the required box and pressed the button; the old draft is kept under "先前版本".
function confirmRegenerate() {
  const dlg = document.getElementById("regenerate-dialog");
  const form = document.getElementById("regenerate-form");
  form.understood.checked = false;
  return new Promise((resolve) => {
    let answer = false;
    form.onsubmit = (e) => { e.preventDefault(); answer = form.understood.checked; dlg.close(); };
    document.getElementById("regenerate-cancel").onclick = () => dlg.close();
    dlg.addEventListener("close", () => resolve(answer), { once: true });
    dlg.showModal();
  });
}
