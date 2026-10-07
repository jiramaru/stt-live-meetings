import { formatDate } from "./transcript.js";

const $ = (id) => document.getElementById(id);

function el(tag, attrs = {}, ...children) {
  const node = Object.assign(document.createElement(tag), attrs);
  node.append(...children);
  return node;
}

async function render() {
  const all = await chrome.storage.local.get(null);
  const active = Object.entries(all).filter(([k]) => k.startsWith("active:")).map(([, v]) => v);
  $("live").hidden = active.length === 0;
  $("live").replaceChildren(...active.map((a) =>
    el("div", {}, el("b", { textContent: "Transcription en cours" }),
      el("div", { className: "sub muted", textContent: `${a.title} · ${a.entries.length} interventions` }))));

  const history = all.history || [];
  if (history.length === 0) {
    $("history").replaceChildren(el("li", { className: "muted", textContent:
      "Aucune transcription pour l'instant. Rejoignez un appel Meet : elle démarre automatiquement." }));
    return;
  }
  $("history").replaceChildren(...history.map((h, index) => {
    const sub = el("div", { className: "sub muted",
      textContent: `${formatDate(h.startedAt)} · ${h.participants.length} participants` });
    const li = el("li", {}, el("div", { className: "title", textContent: h.title }), sub);
    if (h.docUrl) {
      li.append(el("div", { className: "sub" },
        el("a", { href: h.docUrl, target: "_blank", textContent: "Ouvrir le document" }),
        h.emailedTo.length ? ` · envoyé à ${h.emailedTo.length} personne(s)` : ""));
    }
    if (h.error) {
      const retry = el("button", { className: "btn", textContent: "Réessayer" });
      retry.addEventListener("click", async () => {
        retry.disabled = true;
        retry.textContent = "Envoi…";
        await chrome.runtime.sendMessage({ type: "retry", index });
        render();
      });
      li.append(el("div", { className: "sub error", textContent: `Échec de l'envoi : ${h.error}` }), retry);
    }
    return li;
  }));
}

$("options").addEventListener("click", () => chrome.runtime.openOptionsPage());
render();
