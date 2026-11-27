"use strict";
// Post-session recap mode (ticket 04): the upload and recording forms offer 完整晤談錄音 or
// 會後口述, and the consent text follows the choice. Loaded after app.js. Text only, never HTML.

const RECAP_CONSENT = "本段口述僅含心理師整理，不含個案錄音";

// "session" or "recap", from the form's radio buttons.
function inputMode(form) {
  const picked = form.querySelector("input[name=input_mode]:checked");
  return picked ? picked.value : "session";
}

// Swaps the consent wording when 會後口述 is chosen. The checkbox stays required.
function bindInputMode(form) {
  const text = form.querySelector(".consent-text");
  if (!text) return;
  const full = text.textContent;
  const apply = () => { text.textContent = inputMode(form) === "recap" ? RECAP_CONSENT : full; };
  form.querySelectorAll("input[name=input_mode]").forEach((r) => r.addEventListener("change", apply));
  apply();
}

// While recording the choice cannot change.
function lockInputMode(form, on) {
  form.querySelectorAll("input[name=input_mode]").forEach((r) => { r.disabled = on; });
}
