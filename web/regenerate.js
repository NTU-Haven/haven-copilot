"use strict";
// Regenerate confirmation (ticket 07). Resolves true only when the therapist ticked
// the required box and pressed the button; the old draft is kept under "先前版本".
function confirmRegenerate() {
  const dlg = document.getElementById("regenerate-dialog");
  const form = document.getElementById("regenerate-form");
  form.understood.checked = false;
  return new Promise((resolve) => {
    // Resolve from the buttons themselves; the dialog's "close" event is only a
    // fallback (Esc), since some embedded browsers do not fire it.
    let done = false;
    const finish = (answer) => { if (done) return; done = true; if (dlg.open) dlg.close(); resolve(answer); };
    form.onsubmit = (e) => { e.preventDefault(); finish(form.understood.checked); };
    document.getElementById("regenerate-cancel").onclick = () => finish(false);
    dlg.addEventListener("close", () => finish(false), { once: true });
    dlg.showModal();
  });
}
