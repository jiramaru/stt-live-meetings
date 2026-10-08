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
  meter: $("meter"),
  meterFill: $("meterFill"),
  timer: $("timer"),
  recordBtn: $("recordBtn"),
  recordIcon: $("recordIcon"),
  recordLabel: $("recordLabel"),
  stopBtn: $("stopBtn"),
  exportGroup: $("exportGroup"),
  toast: $("toast"),
  participantsBtn: $("participantsBtn"),
  participantsLabel: $("participantsLabel"),
  tabMeetings: $("tabMeetings"),
  tabProfiles: $("tabProfiles"),
  meetingsPane: $("meetingsPane"),
  meetingView: $("meetingView"),
  profilesView: $("profilesView"),
  menuBtn2: $("menuBtn2"),
  profileList: $("profileList"),
  enrolName: $("enrolName"),
  enrolConsent: $("enrolConsent"),
  readText: $("readText"),
  enrolProgress: $("enrolProgress"),
  enrolBar: $("enrolBar"),
  enrolBtn: $("enrolBtn"),
  enrolBtnLabel: $("enrolBtnLabel"),
  enrolStatus: $("enrolStatus"),
};

const state = {
  engineReady: false,
  recording: false,
  sessionId: null, // session shown in the main pane
  title: "", // title of that session (or of the next recording)
  hasAudio: false, // the session's audio was saved on the server
  segmentCount: 0,
  segmentList: [], // segments shown, in order
  speakers: {}, // speaker id -> custom name
  participants: [], // voice profile ids of the people present ([] = everyone)
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
// { heading } adds a section title, { note } a muted explanation, and
// { label, checked, onToggle } a checkbox that leaves the menu open.
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
    if (item.heading || item.note) {
      const text = document.createElement("div");
      text.className = item.heading ? "menu-heading" : "menu-note";
      text.textContent = item.heading || item.note;
      menu.append(text);
      continue;
    }
    if (item.onToggle) {
      const box = document.createElement("button");
      box.className = "menu-item";
      box.setAttribute("role", "menuitemcheckbox");
      const render = (checked) => {
        box.setAttribute("aria-checked", String(checked));
        const label = document.createElement("span");
        label.textContent = item.label;
        box.replaceChildren(icon(checked ? "check_box" : "check_box_outline_blank"), label);
      };
      render(item.checked);
      box.addEventListener("click", async () => {
        const checked = box.getAttribute("aria-checked") !== "true";
        render(checked);
        if ((await item.onToggle(checked)) === false) render(!checked); // refused: undo
      });
      menu.append(box);
      continue;
    }
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
      e.stopPropagation(); // close only the menu, not a dialog behind it
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
  state.segmentList = [];
  state.lastSpeaker = undefined;
  updateEmptyState();
}

function renderSession(session) {
  setParticipants(session.participants || []);
  const scroll = els.transcript.scrollTop;
  const partial = els.partial.textContent; // a live preview survives the redraw
  state.speakers = session.speakers || {};
  state.hasAudio = Boolean(session.audio);
  clearTranscript();
  session.segments.forEach(addSegment);
  els.partial.textContent = partial;
  els.transcript.scrollTop = scroll;
}

// ---------------------------------------------------------------- speakers

const SPEAKER_COLORS = 8;

function speakerName(id) {
  return state.speakers[id] || `Intervenant ${id.slice(1)}`;
}

// Unknown voices "S3" take color 3; voice profiles "P3fa9c2d1" a color
// derived from their id, so a person keeps the same color in every meeting.
function speakerColor(id) {
  let n = parseInt(id.slice(1), 10) - 1;
  if (id.startsWith("P")) n = [...id].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7);
  return `var(--spk-${(n % SPEAKER_COLORS) + 1})`;
}

// A speaker heading: colored name label plus a "more_vert" action menu.
function speakerTag(id) {
  const tag = document.createElement("div");
  tag.className = "speaker-tag";
  tag.dataset.speaker = id;
  tag.style.setProperty("--spk", speakerColor(id));

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
  state.segmentList.push(segment);
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
  text.className = "seg-text";
  text.textContent = segment.text;
  if (segment.edited) markEdited(li, segment);

  const more = document.createElement("button");
  more.className = "icon-btn more-btn seg-more";
  more.title = "Actions";
  more.setAttribute("aria-label", "Actions sur ce passage");
  more.setAttribute("aria-haspopup", "menu");
  more.append(icon("more_vert"));
  more.addEventListener("click", () => openMenu(more, [
    { icon: "edit", label: "Modifier le texte", action: () => editSegment(li, segment) },
    {
      icon: "switch_account",
      label: "Attribuer à un autre intervenant",
      action: () => chooseSpeaker(more, segment),
    },
  ]));

  li.dataset.id = segment.id;
  li.append(time, text, more);
  els.segments.append(li);
  state.segmentCount++;
  updateEmptyState();
  if (follow) scrollToBottom();
}

// Second menu: who said this passage. The meeting's other speakers first,
// then enrolled voices not heard yet, then a brand new speaker.
async function chooseSpeaker(anchor, segment) {
  let profiles = [];
  try {
    profiles = await (await fetch("/api/profiles")).json();
  } catch {
    // profiles are optional here
  }
  const inMeeting = [...new Set(state.segmentList.map((s) => s.speaker).filter(Boolean))];
  const items = [{ heading: "Attribuer à" }];
  for (const id of inMeeting) {
    if (id === segment.speaker) continue;
    items.push({ icon: "person", label: speakerName(id), action: () => setSpeaker(segment, id) });
  }
  // People declared present but not heard yet come before the other voices.
  const ordered = [
    ...profiles.filter((p) => state.participants.includes(p.id)),
    ...profiles.filter((p) => !state.participants.includes(p.id)),
  ];
  for (const p of ordered) {
    if (inMeeting.includes(p.id)) continue;
    items.push({ icon: "account_circle", label: p.name, action: () => setSpeaker(segment, p.id) });
  }
  items.push({ icon: "person_add", label: "Nouvel intervenant", action: () => setSpeaker(segment, "new") });
  openMenu(anchor, items);
}

async function setSpeaker(segment, speaker) {
  const res = await fetch(`/api/sessions/${state.sessionId}/segments/${segment.id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ speaker }),
  });
  if (!res.ok) return toast("Impossible de changer l'intervenant.");
  // Turns regroup around the change: redraw from the saved meeting.
  const session = await (await fetch(`/api/sessions/${state.sessionId}`)).json();
  renderSession(session);
}

// The text was corrected by hand: say so, and keep what was recognized
// available on hover.
function markEdited(li, segment) {
  li.classList.add("edited");
  li.title = segment.original ? `Texte reconnu à l'origine : ${segment.original}` : "";
}

// Inline correction of a passage: Enter or leaving the field saves,
// Escape cancels.
function editSegment(li, segment) {
  const textEl = li.querySelector(".seg-text");
  const area = document.createElement("textarea");
  area.className = "seg-edit";
  area.value = segment.text;
  area.setAttribute("aria-label", "Texte du passage");
  const fit = () => {
    area.style.height = "auto";
    area.style.height = `${area.scrollHeight}px`;
  };
  area.addEventListener("input", fit);
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const value = area.value.trim();
    if (save && value && value !== segment.text) {
      const res = await fetch(`/api/sessions/${state.sessionId}/segments/${segment.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: value }),
      });
      if (res.ok) {
        Object.assign(segment, await res.json());
        textEl.textContent = segment.text;
        markEdited(li, segment);
      } else {
        toast("Impossible d'enregistrer la correction.");
      }
    }
    area.replaceWith(textEl);
  };
  area.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      finish(true);
    }
    if (e.key === "Escape") finish(false);
  });
  area.addEventListener("blur", () => finish(true));
  textEl.replaceWith(area);
  fit();
  area.focus();
  area.setSelectionRange(area.value.length, area.value.length);
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
  updateRecordButton();
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
  setParticipants([]);
  setTitle("");
  els.timer.textContent = clock(0);
  clearTranscript();
  updateRecordButton();
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
  // A finished meeting cannot be restarted: "Nouvelle réunion" starts another.
  const finished = Boolean(state.sessionId) && !state.recording;
  els.recordBtn.hidden = finished;
  els.participantsBtn.hidden = finished; // chosen before or during a meeting
  els.meter.hidden = finished;
  els.stopBtn.hidden = !state.recording;
  els.languageSelect.disabled = state.recording;
  els.newBtn.disabled = state.recording;
}

// Opens the microphone and calls onChunk({ pcm, level }) about every 100 ms
// with 16 kHz 16-bit PCM. Returns a function that releases the microphone.
async function openMic(onChunk) {
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
  node.port.onmessage = ({ data }) => onChunk(data);
  source.connect(node);
  return () => {
    source.disconnect();
    node.port.onmessage = null;
    stream.getTracks().forEach((t) => t.stop());
    ctx.close();
  };
}

async function startAudio() {
  const release = await openMic(({ pcm, level }) => {
    els.meterFill.style.width = `${Math.min(100, level * 400)}%`;
    if (state.ws?.readyState === WebSocket.OPEN) state.ws.send(pcm);
  });
  state.audio = { release };
}

function stopAudio() {
  if (!state.audio) return;
  state.audio.release();
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
        participants: state.participants,
      }));
    };
    ws.onmessage = ({ data }) => {
      const msg = JSON.parse(data);
      switch (msg.type) {
        case "started":
          state.sessionId = msg.session.id;
          state.speakers = msg.session.speakers;
          setParticipants(msg.session.participants);
          setTitle(msg.session.title);
          resolve(ws);
          break;
        case "partial":
          setPartial(msg.text);
          break;
        case "final":
          if (msg.speakers) state.speakers = msg.speakers;
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

// ---------------------------------------------------------------- participants

function setParticipants(ids) {
  state.participants = ids;
  els.participantsLabel.textContent = ids.length ? `Participants (${ids.length})` : "Participants";
}

// Who is in the meeting, among the enrolled voices. Before the start it is
// sent with "start"; during the meeting the server applies it right away.
async function openParticipants() {
  const profiles = await (await fetch("/api/profiles")).json();
  const items = [{ heading: "Personnes présentes" }];
  if (profiles.length === 0) {
    items.push({ note: "Aucune voix inscrite. Ajoutez-en depuis l'onglet Profils." });
  }
  for (const p of profiles) {
    items.push({
      label: p.name,
      checked: state.participants.includes(p.id),
      onToggle: (checked) => toggleParticipant(p.id, checked),
    });
  }
  if (profiles.length) {
    items.push({ note: "Si personne n'est coché, toutes les voix inscrites sont utilisées." });
  }
  openMenu(els.participantsBtn, items);
}

async function toggleParticipant(id, checked) {
  const ids = checked
    ? [...state.participants, id]
    : state.participants.filter((p) => p !== id);
  if (state.recording && state.sessionId) {
    const res = await fetch(`/api/sessions/${state.sessionId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ participants: ids }),
    });
    if (!res.ok) {
      toast("Impossible de mettre à jour les participants.");
      return false;
    }
    const session = await res.json();
    state.speakers = session.speakers;
    setParticipants(session.participants);
    refreshSpeakerTags();
    return true;
  }
  setParticipants(ids);
  return true;
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

// ---------------------------------------------------------------- voice profiles

const ENROL_MIN_SECONDS = 15; // enough speech for a reliable voice print
const ENROL_MAX_SECONDS = 45; // the text takes about 30 s to read
let enrolment = null; // { release, chunks, startedAt, timer } while recording

// Tabs: "Réunions" (transcripts) and "Profils" (voice profiles). A meeting
// keeps recording while the profiles tab is open.
function showTab(name) {
  if (enrolment) return; // finish the voice recording first
  const profiles = name === "profiles";
  closeMenu();
  els.tabMeetings.setAttribute("aria-selected", String(!profiles));
  els.tabProfiles.setAttribute("aria-selected", String(profiles));
  els.meetingView.hidden = profiles;
  els.meetingsPane.hidden = profiles;
  els.profilesView.hidden = !profiles;
  els.sidebar.classList.remove("open");
  if (profiles) loadProfiles();
}

async function loadProfiles() {
  const profiles = await (await fetch("/api/profiles")).json();
  if (profiles.length === 0) {
    const li = document.createElement("li");
    li.className = "empty-row";
    li.textContent = "Aucune voix inscrite pour l'instant.";
    els.profileList.replaceChildren(li);
    return;
  }
  els.profileList.replaceChildren(...profiles.map(profileRow));
}

function profileRow(p) {
  const li = document.createElement("li");
  li.style.setProperty("--spk", speakerColor(p.id));
  const person = icon("account_circle");
  person.classList.add("person");
  const meta = document.createElement("div");
  meta.className = "meta";
  const name = document.createElement("div");
  name.className = "name";
  name.textContent = p.name;
  const sub = document.createElement("div");
  sub.className = "sub";
  sub.textContent = `Inscrite le ${new Date(p.created_at).toLocaleDateString("fr-FR")} · ${Math.round(p.seconds)} s de parole`;
  meta.append(name, sub);

  const more = document.createElement("button");
  more.className = "icon-btn more-btn";
  more.title = "Actions";
  more.setAttribute("aria-label", `Actions sur ${p.name}`);
  more.setAttribute("aria-haspopup", "menu");
  more.append(icon("more_vert"));
  more.addEventListener("click", () => openMenu(more, [
    { icon: "edit", label: "Renommer", action: () => renameProfile(p, name) },
    {
      icon: "delete",
      label: "Supprimer la voix",
      confirm: "Confirmer la suppression",
      danger: true,
      action: async () => {
        await fetch(`/api/profiles/${p.id}`, { method: "DELETE" });
        loadProfiles();
      },
    },
  ]));
  li.append(person, meta, more);
  return li;
}

function renameProfile(p, nameEl) {
  const input = document.createElement("input");
  input.className = "text-input";
  input.value = p.name;
  input.maxLength = 80;
  let done = false;
  const finish = async (save) => {
    if (done) return;
    done = true;
    const name = input.value.trim();
    if (save && name && name !== p.name) {
      await fetch(`/api/profiles/${p.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
    }
    loadProfiles();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") finish(true);
    if (e.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
  nameEl.replaceWith(input);
  input.focus();
  input.select();
}

function setEnrolStatus(text, error = false) {
  els.enrolStatus.textContent = text;
  els.enrolStatus.classList.toggle("error", error);
}

async function toggleEnrolment() {
  if (enrolment) return finishEnrolment();
  if (state.recording) return setEnrolStatus("Terminez d'abord la réunion en cours.", true);
  if (!els.enrolName.value.trim()) return setEnrolStatus("Indiquez le nom de la personne.", true);
  if (!els.enrolConsent.checked) return setEnrolStatus("Le consentement de la personne est requis.", true);

  const chunks = [];
  let release;
  try {
    release = await openMic(({ pcm }) => chunks.push(new Int16Array(pcm)));
  } catch (err) {
    return setEnrolStatus(err.name === "NotAllowedError" ? "Accès au micro refusé." : err.message, true);
  }
  enrolment = { release, chunks, startedAt: performance.now() };
  els.enrolProgress.hidden = false;
  els.readText.classList.add("reading");
  els.enrolBtn.classList.add("recording");
  els.enrolBtn.querySelector(".material-symbols-outlined").textContent = "stop";
  els.enrolName.disabled = els.enrolConsent.disabled = true;
  setEnrolStatus("Lisez le texte à voix haute…");
  enrolment.timer = setInterval(() => {
    const elapsed = (performance.now() - enrolment.startedAt) / 1000;
    els.enrolBar.style.width = `${Math.min(100, (elapsed / ENROL_MAX_SECONDS) * 100)}%`;
    els.enrolBtn.disabled = elapsed < ENROL_MIN_SECONDS;
    els.enrolBtnLabel.textContent = elapsed < ENROL_MIN_SECONDS
      ? `Encore ${Math.ceil(ENROL_MIN_SECONDS - elapsed)} s`
      : "Terminer";
    if (elapsed >= ENROL_MAX_SECONDS) finishEnrolment();
  }, 200);
}

async function finishEnrolment() {
  const { release, chunks, timer } = enrolment;
  clearInterval(timer);
  release();
  enrolment = null;
  els.enrolBtn.disabled = true;
  els.enrolBtnLabel.textContent = "Analyse…";
  setEnrolStatus("Calcul de l'empreinte vocale…");

  const res = await fetch("/api/profiles", {
    method: "POST",
    headers: {
      "Content-Type": "application/octet-stream",
      "X-Profile-Name": encodeURIComponent(els.enrolName.value.trim()),
      "X-Consent": "yes",
    },
    body: new Blob(chunks),
  });
  resetEnrolForm();
  if (!res.ok) {
    const { detail } = await res.json().catch(() => ({}));
    setEnrolStatus(detail || "L'inscription a échoué.", true);
    return;
  }
  const profile = await res.json();
  els.enrolName.value = "";
  els.enrolConsent.checked = false;
  setEnrolStatus(`Voix de ${profile.name} enregistrée.`);
  loadProfiles();
}

function resetEnrolForm() {
  els.enrolProgress.hidden = true;
  els.readText.classList.remove("reading");
  els.enrolBar.style.width = "0";
  els.enrolBtn.disabled = false;
  els.enrolBtn.classList.remove("recording");
  els.enrolBtn.querySelector(".material-symbols-outlined").textContent = "mic";
  els.enrolBtnLabel.textContent = "Commencer la lecture";
  els.enrolName.disabled = els.enrolConsent.disabled = false;
}

// ---------------------------------------------------------------- wiring

els.participantsBtn.addEventListener("click", openParticipants);
els.tabMeetings.addEventListener("click", () => showTab("meetings"));
els.tabProfiles.addEventListener("click", () => showTab("profiles"));
els.menuBtn2.addEventListener("click", () => els.sidebar.classList.toggle("open"));
els.enrolBtn.addEventListener("click", toggleEnrolment);

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
