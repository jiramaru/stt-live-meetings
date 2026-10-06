const $ = (id) => document.getElementById(id);

const els = {
  sidebar: $("sidebar"),
  menuBtn: $("menuBtn"),
  newBtn: $("newBtn"),
  sessionList: $("sessionList"),
  titleText: $("titleText"),
  titleInput: $("titleInput"),
  titleMenuBtn: $("titleMenuBtn"),
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
  stopBtn: $("stopBtn"),
  exportGroup: $("exportGroup"),
  toast: $("toast"),
};

const state = {
  engineReady: false,
  recording: false,
  sessionId: null, // session shown in the main pane
  title: "", // title of that session (or of the next recording)
  hasAudio: false, // the session's audio was saved on the server
  segmentCount: 0,
  speakers: {}, // speaker id -> custom name
  lastSpeaker: undefined, // speaker of the last rendered segment
  ws: null,
  audio: null, // { ctx, stream, node, source }
  paused: false, // meeting open, microphone off
  elapsed: 0, // seconds recorded before the current run (pauses excluded)
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

// ---------------------------------------------------------------- action menu

// Small dropdown opened from a "more_vert" button. Items:
// { icon, label, action, danger?, confirm? } where `confirm` is the label
// shown after a first click; the action only runs on the second click.
let openedMenu = null;

function closeMenu() {
  if (!openedMenu) return;
  openedMenu.anchor.setAttribute("aria-expanded", "false");
  openedMenu.el.remove();
  document.removeEventListener("pointerdown", openedMenu.onOutside, true);
  document.removeEventListener("keydown", openedMenu.onKey, true);
  window.removeEventListener("resize", closeMenu);
  els.transcript.removeEventListener("scroll", closeMenu);
  openedMenu = null;
}

function openMenu(anchor, items) {
  const reopen = openedMenu?.anchor === anchor;
  closeMenu();
  if (reopen) return; // second click on the same button closes it

  const menu = document.createElement("div");
  menu.className = "menu";
  menu.setAttribute("role", "menu");
  for (const item of items) {
    const btn = document.createElement("button");
    btn.className = "menu-item" + (item.danger ? " danger" : "");
    btn.setAttribute("role", "menuitem");
    const label = document.createElement("span");
    label.textContent = item.label;
    btn.append(icon(item.icon), label);
    btn.addEventListener("click", () => {
      if (item.confirm && !btn.dataset.armed) {
        btn.dataset.armed = "1";
        label.textContent = item.confirm;
        return;
      }
      closeMenu();
      item.action();
    });
    menu.append(btn);
  }
  document.body.append(menu);

  // Below the button, right-aligned with it; above it if there is no room.
  const r = anchor.getBoundingClientRect();
  const m = menu.getBoundingClientRect();
  const top = r.bottom + 4 + m.height > window.innerHeight ? r.top - 4 - m.height : r.bottom + 4;
  menu.style.top = `${Math.max(8, top)}px`;
  menu.style.left = `${Math.max(8, Math.min(r.right - m.width, window.innerWidth - m.width - 8))}px`;

  const buttons = [...menu.querySelectorAll(".menu-item")];
  const onOutside = (e) => {
    if (!menu.contains(e.target) && !anchor.contains(e.target)) closeMenu();
  };
  const onKey = (e) => {
    const i = buttons.indexOf(document.activeElement);
    if (e.key === "Escape") {
      closeMenu();
      anchor.focus();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      buttons[(i + step + buttons.length) % buttons.length].focus();
    }
  };
  document.addEventListener("pointerdown", onOutside, true);
  document.addEventListener("keydown", onKey, true);
  window.addEventListener("resize", closeMenu);
  els.transcript.addEventListener("scroll", closeMenu);
  anchor.setAttribute("aria-expanded", "true");
  openedMenu = { el: menu, anchor, onOutside, onKey };
  buttons[0]?.focus();
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
  state.hasAudio = Boolean(session.audio);
  clearTranscript();
  session.segments.forEach(addSegment);
  els.transcript.scrollTop = scroll;
}

// ---------------------------------------------------------------- speakers

const SPEAKER_COLORS = 8;

function speakerName(id) {
  return state.speakers[id] || `Intervenant ${id.slice(1)}`;
}

// A speaker heading: colored name label plus a "more_vert" action menu.
function speakerTag(id) {
  const tag = document.createElement("div");
  tag.className = "speaker-tag";
  tag.dataset.speaker = id;
  tag.style.setProperty("--spk", `var(--spk-${(parseInt(id.slice(1), 10) - 1) % SPEAKER_COLORS + 1})`);

  const chip = document.createElement("span");
  chip.className = "speaker-chip";
  chip.append(icon("person"), document.createTextNode(speakerName(id)));

  const more = document.createElement("button");
  more.className = "icon-btn more-btn";
  more.title = "Actions";
  more.setAttribute("aria-label", `Actions sur ${speakerName(id)}`);
  more.setAttribute("aria-haspopup", "menu");
  more.append(icon("more_vert"));
  more.addEventListener("click", () => {
    const items = [{ icon: "edit", label: "Renommer l'intervenant", action: () => startRename(tag) }];
    if (state.speakers[id]) {
      items.push({
        icon: "undo",
        label: "Rétablir le nom par défaut",
        action: () => saveSpeakerName(id, ""),
      });
    }
    openMenu(more, items);
  });

  tag.append(chip, more);
  return tag;
}

function refreshSpeakerTags() {
  for (const tag of els.segments.querySelectorAll(".speaker-tag")) {
    tag.replaceWith(speakerTag(tag.dataset.speaker));
  }
}

async function saveSpeakerName(id, name) {
  const res = await fetch(`/api/sessions/${state.sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ speakers: { [id]: name } }),
  });
  if (res.ok) state.speakers = (await res.json()).speakers;
  else toast("Impossible de renommer l'intervenant.");
  refreshSpeakerTags();
}

// Inline rename: the label becomes a text field; Enter or leaving it saves,
// Escape cancels. The new name applies to every turn of that speaker.
function startRename(tag) {
  const id = tag.dataset.speaker;
  const input = document.createElement("input");
  input.className = "speaker-input";
  input.value = state.speakers[id] || "";
  input.placeholder = speakerName(id);
  input.maxLength = 80;
  input.setAttribute("aria-label", "Nom de l'intervenant");
  input.style.setProperty("--spk", tag.style.getPropertyValue("--spk"));
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    if (save && input.value.trim() !== (state.speakers[id] || "")) {
      saveSpeakerName(id, input.value);
    } else {
      refreshSpeakerTags();
    }
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
  tag.replaceWith(input);
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
    turn.append(speakerTag(segment.speaker));
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
  setTitle(session.title);
  els.timer.textContent = clock(session.segments.at(-1)?.end ?? 0);
  renderSession(session);
  els.transcript.scrollTop = 0;
  els.sidebar.classList.remove("open");
  loadSessions();
}

async function removeSession(id) {
  await fetch(`/api/sessions/${id}`, { method: "DELETE" });
  if (state.sessionId === id) newMeeting();
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
  removeSession(id);
}

function newMeeting() {
  if (state.recording) return;
  state.sessionId = null;
  state.speakers = {};
  setTitle("");
  els.timer.textContent = clock(0);
  clearTranscript();
  els.sidebar.classList.remove("open");
  loadSessions();
}

// ---------------------------------------------------------------- recording

// Main button: Démarrer -> Pause <-> Reprendre. "Terminer" closes the meeting.
function updateRecordButton() {
  const live = state.recording && !state.paused;
  els.recordBtn.classList.toggle("recording", live);
  els.recordIcon.textContent = live ? "pause" : "mic";
  els.recordLabel.textContent = live ? "Pause" : state.paused ? "Reprendre" : "Démarrer";
  els.recordBtn.disabled = !state.engineReady && !state.recording;
  els.stopBtn.hidden = !state.recording;
  els.languageSelect.disabled = state.recording;
  els.newBtn.disabled = state.recording;
}

async function startAudio() {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: false, // room audio, nothing is played back
      // Noise suppression is tuned for calls: it reshapes the voice spectrum,
      // which hurts recognition and blurs the differences between voices.
      noiseSuppression: false,
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
        title: state.title,
        language: els.languageSelect.value,
      }));
    };
    ws.onmessage = ({ data }) => {
      const msg = JSON.parse(data);
      switch (msg.type) {
        case "started":
          state.sessionId = msg.session.id;
          state.speakers = msg.session.speakers;
          setTitle(msg.session.title);
          resolve(ws);
          break;
        case "partial":
          setPartial(msg.text);
          break;
        case "final":
          addSegment(msg.segment);
          break;
        case "paused":
          // Speaker labels were refined with hindsight: redraw with them.
          renderSession(msg.session);
          setPartial("En pause");
          break;
        case "resumed":
          setPartial("");
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
        state.paused = false;
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
  state.elapsed = 0;
  startTimer();
  loadSessions();
}

function startTimer() {
  state.startedAt = performance.now();
  state.timerHandle = setInterval(() => {
    els.timer.textContent = clock(state.elapsed + (performance.now() - state.startedAt) / 1000);
  }, 500);
}

function stopTimer() {
  if (!state.timerHandle) return;
  clearInterval(state.timerHandle);
  state.timerHandle = null;
  state.elapsed += (performance.now() - state.startedAt) / 1000;
}

function pauseRecording() {
  state.paused = true;
  stopAudio(); // releases the microphone while paused
  stopTimer();
  if (state.ws?.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify({ type: "pause" }));
  setPartial("Mise en pause…");
  updateRecordButton();
}

async function resumeRecording() {
  try {
    await startAudio();
  } catch (err) {
    return toast(err.name === "NotAllowedError" ? "Accès au micro refusé." : err.message);
  }
  state.paused = false;
  if (state.ws?.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify({ type: "resume" }));
  startTimer();
  updateRecordButton();
}

function finishRecording() {
  state.recording = false;
  state.paused = false;
  stopTimer();
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

function setTitle(title) {
  state.title = title;
  els.titleText.textContent = title || "Nouvelle réunion";
  els.titleText.classList.toggle("placeholder", !title);
}

// The title can be renamed at any time, recording or not. Before the first
// recording it is kept locally and sent with the "start" message.
async function saveTitle(title) {
  title = title.trim();
  if (!title || title === state.title) return;
  if (!state.sessionId) return setTitle(title);
  const res = await fetch(`/api/sessions/${state.sessionId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title }),
  });
  if (!res.ok) return toast("Impossible d'enregistrer le titre.");
  setTitle((await res.json()).title);
  loadSessions();
}

function startTitleEdit() {
  const input = els.titleInput;
  input.value = state.title;
  els.titleText.hidden = true;
  els.titleMenuBtn.hidden = true;
  input.hidden = false;
  input.focus();
  input.select();
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    input.removeEventListener("keydown", onKey);
    input.removeEventListener("blur", onBlur);
    if (save) await saveTitle(input.value);
    input.hidden = true;
    els.titleText.hidden = false;
    els.titleMenuBtn.hidden = false;
  };
  const onKey = (e) => {
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  };
  const onBlur = () => finish(true);
  input.addEventListener("keydown", onKey);
  input.addEventListener("blur", onBlur);
}

function openTitleMenu() {
  const items = [{ icon: "edit", label: "Renommer la réunion", action: startTitleEdit }];
  if (state.sessionId && state.hasAudio && !state.recording) {
    const id = state.sessionId;
    items.push({
      icon: "download",
      label: "Télécharger l'audio",
      action: () => { location.href = `/api/sessions/${id}/audio`; },
    });
  }
  if (state.sessionId && !state.recording) {
    const id = state.sessionId;
    items.push({
      icon: "delete",
      label: "Supprimer la réunion",
      confirm: "Confirmer la suppression",
      danger: true,
      action: () => removeSession(id),
    });
  }
  openMenu(els.titleMenuBtn, items);
}

// ---------------------------------------------------------------- wiring

els.titleMenuBtn.addEventListener("click", openTitleMenu);
els.recordBtn.addEventListener("click", () => {
  if (!state.recording) startRecording();
  else if (state.paused) resumeRecording();
  else pauseRecording();
});
els.stopBtn.addEventListener("click", stopRecording);
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

setTitle("");
updateRecordButton();
updateEmptyState();
pollHealth();
loadSessions();
