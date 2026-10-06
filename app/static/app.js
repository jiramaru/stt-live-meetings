const $ = (id) => document.getElementById(id);

const els = {
  sidebar: $("sidebar"),
  menuBtn: $("menuBtn"),
  newBtn: $("newBtn"),
  sessionList: $("sessionList"),
  titleInput: $("titleInput"),
  languageSelect: $("languageSelect"),
  engineStatus: $("engineStatus"),
  transcript: $("transcript"),
  emptyState: $("emptyState"),
  segments: $("segments"),
  partial: $("partial"),
  meterFill: $("meterFill"),
  timer: $("timer"),
  recordBtn: $("recordBtn"),
  recordIcon: $("recordIcon"),
  recordLabel: $("recordLabel"),
  exportGroup: $("exportGroup"),
  toast: $("toast"),
};

const state = {
  engineReady: false,
  recording: false,
  sessionId: null, // session shown in the main pane
  segmentCount: 0,
  speakers: {}, // speaker id -> custom name
  lastSpeaker: undefined, // speaker of the last rendered segment
  ws: null,
  audio: null, // { ctx, stream, node, source }
  startedAt: 0,
  timerHandle: null,
};

// ---------------------------------------------------------------- helpers

function clock(seconds) {
  const s = Math.floor(seconds);
  const pad = (n) => String(n).padStart(2, "0");
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
}

let toastTimer;
function toast(message) {
  els.toast.textContent = message;
  els.toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => els.toast.classList.remove("show"), 4000);
}

function icon(name) {
  const span = document.createElement("span");
  span.className = "material-symbols-outlined";
  span.textContent = name;
  return span;
}

function isNearBottom() {
  const t = els.transcript;
  return t.scrollHeight - t.scrollTop - t.clientHeight < 80;
}

function scrollToBottom() {
  els.transcript.scrollTop = els.transcript.scrollHeight;
}

// ---------------------------------------------------------------- transcript view

function clearTranscript() {
  els.segments.replaceChildren();
  els.partial.textContent = "";
  state.segmentCount = 0;
  state.lastSpeaker = undefined;
  updateEmptyState();
}

function renderSession(session) {
  const scroll = els.transcript.scrollTop;
  state.speakers = session.speakers || {};
  clearTranscript();
  session.segments.forEach(addSegment);
  els.transcript.scrollTop = scroll;
}

// ---------------------------------------------------------------- speakers

const SPEAKER_COLORS = 8;

function speakerName(id) {
  return state.speakers[id] || `Intervenant ${id.slice(1)}`;
}

function speakerChip(id) {
  const chip = document.createElement("button");
  chip.className = "speaker-chip";
  chip.dataset.speaker = id;
  chip.style.setProperty("--spk", `var(--spk-${(parseInt(id.slice(1), 10) - 1) % SPEAKER_COLORS + 1})`);
  chip.title = "Cliquer pour renommer";
  chip.append(icon("person"), document.createTextNode(speakerName(id)));
  chip.addEventListener("click", () => startRename(chip));
  return chip;
}

function refreshSpeakerChips() {
  for (const chip of els.segments.querySelectorAll(".speaker-chip")) {
    chip.replaceWith(speakerChip(chip.dataset.speaker));
  }
}

// Inline rename: the chip becomes a text field; Enter or leaving it saves,
// Escape cancels. The new name applies to every turn of that speaker.
function startRename(chip) {
  const id = chip.dataset.speaker;
  const input = document.createElement("input");
  input.className = "speaker-input";
  input.value = state.speakers[id] || "";
  input.placeholder = speakerName(id);
  input.maxLength = 80;
  input.style.setProperty("--spk", chip.style.getPropertyValue("--spk"));
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    if (save && input.value.trim() !== (state.speakers[id] || "")) {
      const res = await fetch(`/api/sessions/${state.sessionId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ speakers: { [id]: input.value } }),
      });
      if (res.ok) state.speakers = (await res.json()).speakers;
      else toast("Impossible de renommer l'intervenant.");
    }
    refreshSpeakerChips();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
  chip.replaceWith(input);
  input.focus();
}

function updateEmptyState() {
  els.emptyState.hidden = state.segmentCount > 0 || state.recording;
  const canExport = Boolean(state.sessionId) && state.segmentCount > 0;
  for (const btn of els.exportGroup.querySelectorAll("button")) btn.disabled = !canExport;
}

function addSegment(segment) {
  const follow = isNearBottom();
  if (segment.speaker && segment.speaker !== state.lastSpeaker) {
    const turn = document.createElement("li");
    turn.className = "turn";
    turn.append(speakerChip(segment.speaker));
    els.segments.append(turn);
  }
  state.lastSpeaker = segment.speaker;
  const li = document.createElement("li");
  li.className = "segment";
  const time = document.createElement("time");
  time.textContent = clock(segment.start);
  if (segment.language && els.languageSelect.value === "auto") {
    const lang = document.createElement("span");
    lang.className = "lang";
    lang.textContent = segment.language;
    time.append(lang);
  }
  const text = document.createElement("p");
  text.style.margin = "0";
  text.textContent = segment.text;
  li.append(time, text);
  els.segments.append(li);
  state.segmentCount++;
  updateEmptyState();
  if (follow) scrollToBottom();
}

function setPartial(text) {
  const follow = isNearBottom();
  els.partial.textContent = text;
  if (follow) scrollToBottom();
}

// ---------------------------------------------------------------- engine status

async function pollHealth() {
  try {
    const res = await fetch("/api/health");
    const health = await res.json();
    const label = els.engineStatus.querySelector(".engine-label");
    els.engineStatus.classList.remove("ready", "error");
    if (health.status === "ready") {
      state.engineReady = true;
      els.engineStatus.classList.add("ready");
      label.textContent = `Modèle ${health.model}`;
      els.engineStatus.title = `Moteur prêt (${health.model}, ${health.device})`;
    } else if (health.status === "error") {
      els.engineStatus.classList.add("error");
      label.textContent = "Erreur moteur";
      els.engineStatus.title = health.error || "";
    } else {
      label.textContent = "Chargement du modèle…";
      setTimeout(pollHealth, 2000);
    }
  } catch {
    setTimeout(pollHealth, 4000);
  }
  updateRecordButton();
}

// ---------------------------------------------------------------- sessions

async function loadSessions() {
  const res = await fetch("/api/sessions");
  const sessions = await res.json();
  els.sessionList.replaceChildren();
  if (sessions.length === 0) {
    const li = document.createElement("li");
    li.className = "session-empty";
    li.textContent = "Aucune réunion enregistrée.";
    els.sessionList.append(li);
    return;
  }
  for (const s of sessions) {
    const li = document.createElement("li");
    li.className = "session-item" + (s.id === state.sessionId ? " active" : "");
    li.tabIndex = 0;

    const meta = document.createElement("div");
    meta.className = "session-meta";
    const title = document.createElement("div");
    title.className = "session-title";
    title.textContent = s.title;
    const sub = document.createElement("div");
    sub.className = "session-sub";
    const date = new Date(s.started_at).toLocaleString("fr-FR", {
      day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    });
    sub.textContent = `${date} · ${clock(s.duration)}`;
    meta.append(title, sub);

    const del = document.createElement("button");
    del.className = "icon-btn";
    del.title = "Supprimer";
    del.setAttribute("aria-label", "Supprimer la réunion");
    del.append(icon("delete"));
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSession(s.id, del);
    });

    li.append(meta, del);
    li.addEventListener("click", () => openSession(s.id));
    li.addEventListener("keydown", (e) => { if (e.key === "Enter") openSession(s.id); });
    els.sessionList.append(li);
  }
}

async function openSession(id) {
  if (state.recording) {
    toast("Arrêtez l'enregistrement avant d'ouvrir une autre réunion.");
    return;
  }
  const res = await fetch(`/api/sessions/${id}`);
  if (!res.ok) return toast("Réunion introuvable.");
  const session = await res.json();
  state.sessionId = id;
  els.titleInput.value = session.title;
  els.timer.textContent = clock(session.segments.at(-1)?.end ?? 0);
  renderSession(session);
  els.transcript.scrollTop = 0;
  els.sidebar.classList.remove("open");
  loadSessions();
}

// Two-step delete: first click arms the button, second click confirms.
async function deleteSession(id, button) {
  if (!button.dataset.armed) {
    button.dataset.armed = "1";
    button.replaceChildren(icon("delete_forever"));
    button.style.color = "var(--danger)";
    button.title = "Cliquer à nouveau pour confirmer";
    setTimeout(() => {
      delete button.dataset.armed;
      button.replaceChildren(icon("delete"));
      button.style.color = "";
    }, 3000);
    return;
  }
  await fetch(`/api/sessions/${id}`, { method: "DELETE" });
  if (state.sessionId === id) newMeeting();
  loadSessions();
}

function newMeeting() {
  if (state.recording) return;
  state.sessionId = null;
  state.speakers = {};
  els.titleInput.value = "";
  els.timer.textContent = clock(0);
  clearTranscript();
  els.sidebar.classList.remove("open");
  loadSessions();
}

// ---------------------------------------------------------------- recording

function updateRecordButton() {
  els.recordBtn.classList.toggle("recording", state.recording);
  els.recordIcon.textContent = state.recording ? "stop" : "mic";
  els.recordLabel.textContent = state.recording ? "Arrêter" : "Démarrer";
  els.recordBtn.disabled = !state.engineReady && !state.recording;
  els.languageSelect.disabled = state.recording;
  els.newBtn.disabled = state.recording;
}

async function startAudio() {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: false, // room audio, nothing is played back
      noiseSuppression: true,
      autoGainControl: true,
    },
  });
  const ctx = new AudioContext();
  await ctx.audioWorklet.addModule("/static/pcm-worklet.js");
  const source = ctx.createMediaStreamSource(stream);
  const node = new AudioWorkletNode(ctx, "pcm-processor");
  node.port.onmessage = ({ data }) => {
    els.meterFill.style.width = `${Math.min(100, data.level * 400)}%`;
    if (state.ws?.readyState === WebSocket.OPEN) state.ws.send(data.pcm);
  };
  source.connect(node);
  state.audio = { ctx, stream, node, source };
}

function stopAudio() {
  if (!state.audio) return;
  const { ctx, stream, node, source } = state.audio;
  source.disconnect();
  node.port.onmessage = null;
  stream.getTracks().forEach((t) => t.stop());
  ctx.close();
  state.audio = null;
  els.meterFill.style.width = "0";
}

function openSocket() {
  return new Promise((resolve, reject) => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws/transcribe`);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      ws.send(JSON.stringify({
        type: "start",
        title: els.titleInput.value,
        language: els.languageSelect.value,
      }));
    };
    ws.onmessage = ({ data }) => {
      const msg = JSON.parse(data);
      switch (msg.type) {
        case "started":
          state.sessionId = msg.session.id;
          state.speakers = msg.session.speakers;
          els.titleInput.value = msg.session.title;
          resolve(ws);
          break;
        case "partial":
          setPartial(msg.text);
          break;
        case "final":
          addSegment(msg.segment);
          break;
        case "stopped":
          setPartial("");
          // Speaker labels were refined with hindsight: redraw with them.
          renderSession(msg.session);
          loadSessions();
          break;
        case "error":
          if (state.recording) toast(msg.message);
          reject(new Error(msg.message));
          break;
      }
    };
    ws.onerror = () => reject(new Error("Connexion au serveur impossible."));
    ws.onclose = () => {
      if (state.ws === ws && state.recording) {
        toast("Connexion perdue. La transcription jusqu'ici est enregistrée.");
        finishRecording();
      }
    };
  });
}

async function startRecording() {
  clearTranscript();
  state.sessionId = null;
  state.speakers = {};
  state.recording = true;
  updateRecordButton();
  updateEmptyState();
  try {
    await startAudio();
    state.ws = await openSocket();
  } catch (err) {
    toast(err.name === "NotAllowedError" ? "Accès au micro refusé." : err.message);
    stopAudio();
    state.ws?.close();
    state.ws = null;
    state.recording = false;
    updateRecordButton();
    updateEmptyState();
    return;
  }
  state.startedAt = performance.now();
  state.timerHandle = setInterval(() => {
    els.timer.textContent = clock((performance.now() - state.startedAt) / 1000);
  }, 500);
  loadSessions();
}

function finishRecording() {
  state.recording = false;
  clearInterval(state.timerHandle);
  stopAudio();
  updateRecordButton();
  updateEmptyState();
}

function stopRecording() {
  const ws = state.ws;
  finishRecording();
  if (ws?.readyState === WebSocket.OPEN) {
    setPartial("Finalisation…");
    ws.send(JSON.stringify({ type: "stop" }));
  }
}

// ---------------------------------------------------------------- title

// The title can be edited at any time, recording or not. Before the first
// recording it is simply sent with the "start" message.
async function saveTitle() {
  const title = els.titleInput.value.trim();
  if (!state.sessionId || !title) return;
  const res = await fetch(`/api/sessions/${state.sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) return toast("Impossible d'enregistrer le titre.");
  els.titleInput.value = (await res.json()).title;
  loadSessions();
}

// ---------------------------------------------------------------- wiring

els.titleInput.addEventListener("change", saveTitle);
els.titleInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") els.titleInput.blur();
});
els.recordBtn.addEventListener("click", () => {
  state.recording ? stopRecording() : startRecording();
});
els.newBtn.addEventListener("click", newMeeting);
els.menuBtn.addEventListener("click", () => els.sidebar.classList.toggle("open"));
els.exportGroup.addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-format]");
  if (!btn || !state.sessionId) return;
  location.href = `/api/sessions/${state.sessionId}/export?format=${btn.dataset.format}`;
});
window.addEventListener("beforeunload", (e) => {
  if (state.recording) e.preventDefault();
});

updateRecordButton();
updateEmptyState();
pollHealth();
loadSessions();
