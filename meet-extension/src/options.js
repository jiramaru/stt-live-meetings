import { getToken } from "./google.js";
import { loadSettings, saveSettings } from "./settings.js";

const $ = (id) => document.getElementById(id);

async function showAccount() {
  const { email } = await chrome.identity.getProfileUserInfo({ accountStatus: "ANY" });
  let authorized = false;
  try {
    authorized = Boolean(await getToken(false));
  } catch {
    // not authorized yet
  }
  $("account").className = authorized ? "ok" : "muted";
  $("account").textContent = authorized
    ? `  Autorisé${email ? ` (${email})` : ""}`
    : "  Pas encore autorisé";
}

async function init() {
  const s = await loadSettings();
  document.querySelector(`input[name="emailMode"][value="${s.emailMode}"]`).checked = true;
  $("shareDoc").checked = s.shareDoc;
  $("folderName").value = s.folderName;
  showAccount();
}

$("connect").addEventListener("click", async () => {
  try {
    await getToken(true);
  } catch (err) {
    $("account").className = "error";
    $("account").textContent = `  ${err.message}`;
    return;
  }
  showAccount();
});

$("save").addEventListener("click", async () => {
  await saveSettings({
    emailMode: document.querySelector('input[name="emailMode"]:checked').value,
    shareDoc: $("shareDoc").checked,
    folderName: $("folderName").value.trim() || "Transcriptions Meet",
  });
  $("status").className = "ok";
  $("status").textContent = "Enregistré";
  setTimeout(() => { $("status").textContent = ""; }, 2000);
});

init();
