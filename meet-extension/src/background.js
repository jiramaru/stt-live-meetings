// Service worker: stores the transcript while the call runs, then delivers it
// (Google Doc + e-mail) once the call has ended.

import { createDoc, ensureFolder, eventsAround, sendEmail, shareDoc } from "./google.js";
import { loadSettings } from "./settings.js";
import {
  buildRawEmail,
  formatDocHtml,
  formatEmailHtml,
  formatText,
  matchEvent,
  participantsOf,
  recipients,
  toTurns,
} from "./transcript.js";

// When the page goes away without the "left the call" signal (tab closed,
// browser crash), wait this long for the same meeting to reconnect (page
// reload) before treating the call as over.
const RECONNECT_GRACE_MINUTES = 2;

const key = (code) => `active:${code}`;

// ---------------------------------------------------------------- live session

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "meet") return;
  let code = null;
  // Messages from one tab are handled one at a time, in order: an "update"
  // still being saved must not land after the "ended" delivery.
  let queue = Promise.resolve();

  port.onMessage.addListener((msg) => {
    queue = queue.then(() => handle(msg)).catch((err) => console.error(err));
  });

  async function handle(msg) {
    if (msg.type === "hello") {
      code = msg.code;
      await chrome.alarms.clear(key(code)); // reconnected: not over after all
      const saved = (await chrome.storage.local.get(key(code)))[key(code)];
      port.postMessage({ type: "restore", entries: saved?.entries || [] });
      setBadge(port.sender?.tab?.id, true);
    } else if (msg.type === "update" && code) {
      await save(code, msg);
    } else if (msg.type === "ended") {
      const ended = code;
      code = null; // handled here, not by the disconnect below
      setBadge(port.sender?.tab?.id, false);
      await save(ended, msg);
      await finalize(ended);
    }
  }

  port.onDisconnect.addListener(() => {
    if (code) chrome.alarms.create(key(code), { delayInMinutes: RECONNECT_GRACE_MINUTES });
  });
});

async function save(code, { title, startedAt, entries }) {
  const saved = (await chrome.storage.local.get(key(code)))[key(code)];
  await chrome.storage.local.set({
    [key(code)]: {
      code,
      title,
      startedAt: saved?.startedAt || startedAt,
      entries,
      updatedAt: Date.now(),
    },
  });
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name.startsWith("active:")) finalize(alarm.name.slice("active:".length));
});

function setBadge(tabId, on) {
  if (tabId == null) return;
  chrome.action.setBadgeText({ tabId, text: on ? "REC" : "" });
  chrome.action.setBadgeBackgroundColor({ tabId, color: "#c0362c" });
}

// ---------------------------------------------------------------- delivery

const running = new Set(); // codes being finalized (ended + alarm can race)

async function finalize(code) {
  if (running.has(code)) return;
  running.add(code);
  try {
    const session = (await chrome.storage.local.get(key(code)))[key(code)];
    if (!session) return;
    const turns = toTurns(session.entries || []);
    if (turns.length === 0) {
      await chrome.storage.local.remove(key(code));
      return;
    }
    const record = await deliver(session, turns);
    await addToHistory(record);
    await chrome.storage.local.remove(key(code));
  } finally {
    running.delete(code);
  }
}

async function deliver(session, turns) {
  const s = await loadSettings();
  const record = {
    code: session.code,
    title: session.title || `Réunion Meet ${session.code}`,
    startedAt: session.startedAt,
    endedAt: session.updatedAt || Date.now(),
    participants: participantsOf(turns),
    turns: turns.length,
    docUrl: null,
    emailedTo: [],
    error: null,
    // Kept so a failed delivery can be retried from the popup.
    entries: session.entries,
  };
  try {
    let event = null;
    try {
      event = matchEvent(await eventsAround(session.startedAt), session.code);
    } catch (err) {
      console.warn("Calendar lookup failed", err);
    }
    if (event?.summary) record.title = event.summary;

    const meeting = { ...record };
    const { email: selfEmail } = await chrome.identity.getProfileUserInfo({ accountStatus: "ANY" });
    const to = recipients(event, s.emailMode, selfEmail);

    const folderId = await ensureFolder(s.folderName);
    const doc = await createDoc({
      name: `${record.title} - ${new Date(record.startedAt).toLocaleDateString("fr-FR")}`,
      html: formatDocHtml(meeting, turns),
      folderId,
    });
    record.docUrl = doc.webViewLink;

    if (s.shareDoc && to.length) {
      await shareDoc(doc.id, to.filter((e) => e !== selfEmail?.toLowerCase()));
    }
    if (to.length) {
      await sendEmail(buildRawEmail({
        to,
        subject: `Transcription : ${record.title}`,
        text: `Transcription : ${record.docUrl}\n\n${formatText(meeting, turns)}`,
        html: formatEmailHtml(meeting, turns, record.docUrl),
      }));
      record.emailedTo = to;
    }
    delete record.entries;
  } catch (err) {
    console.error("Delivery failed", err);
    record.error = String(err.message || err);
  }
  return record;
}

async function addToHistory(record) {
  const { history = [] } = await chrome.storage.local.get("history");
  history.unshift(record);
  await chrome.storage.local.set({ history: history.slice(0, 30) });
}

// Popup "Réessayer" button: deliver a failed transcript again.
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  if (msg.type !== "retry") return;
  (async () => {
    const { history = [] } = await chrome.storage.local.get("history");
    const item = history[msg.index];
    if (!item?.entries) return reply({ ok: false });
    const record = await deliver(
      { code: item.code, title: item.title, startedAt: item.startedAt, updatedAt: item.endedAt, entries: item.entries },
      toTurns(item.entries),
    );
    history[msg.index] = record;
    await chrome.storage.local.set({ history });
    reply({ ok: !record.error, error: record.error });
  })();
  return true; // async reply
});
