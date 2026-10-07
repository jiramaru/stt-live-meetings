// Pure transcript logic, shared by the content script and the background
// worker, and tested with `node --test` (no browser needed).

// Meet rewrites a caption block as it listens: words get appended, the tail
// gets corrected, and once a block is long its beginning scrolls away.
// Combine the text we already have with the block's current text.
export function mergeText(previous, current) {
  if (!previous) return current;
  if (!current || previous.endsWith(current)) return previous;
  if (current.startsWith(previous)) return current; // words appended

  // Same beginning, different end: the recognizer revised its last words.
  let common = 0;
  while (common < previous.length && previous[common] === current[common]) common++;
  if (common >= Math.min(15, previous.length)) return current;

  // The beginning scrolled out of the block: find where the current text
  // starts inside what we already have, and continue from there.
  const anchor = current.slice(0, 20);
  const at = previous.lastIndexOf(anchor);
  if (anchor.length >= 8 && at >= 0) return previous.slice(0, at) + current;

  return `${previous} ${current}`;
}

// Accumulates caption blocks into timed entries. `update` receives the blocks
// currently on screen, each with a stable key (one per DOM node).
export class CaptionLog {
  constructor(now = () => Date.now()) {
    this.now = now;
    this.entries = []; // { speaker, text, start, end }
    this.open = new Map(); // key -> entry still on screen
  }

  update(blocks) {
    const t = this.now();
    const seen = new Set();
    for (const { key, speaker, text } of blocks) {
      if (!text) continue;
      seen.add(key);
      const entry = this.open.get(key);
      if (entry) {
        entry.text = mergeText(entry.text, text);
        entry.end = t;
      } else {
        const created = { speaker: speaker || "Participant", text, start: t, end: t };
        this.entries.push(created);
        this.open.set(key, created);
      }
    }
    for (const key of [...this.open.keys()]) {
      if (!seen.has(key)) this.open.delete(key); // block left the screen: final
    }
  }

  // Restore entries saved earlier (page reloaded during the same meeting).
  restore(entries) {
    this.entries = entries.map((e) => ({ ...e }));
    this.open.clear();
  }
}

// Consecutive entries from the same person become one turn.
export function toTurns(entries) {
  const turns = [];
  for (const e of [...entries].sort((a, b) => a.start - b.start)) {
    const text = e.text.replace(/\s+/g, " ").trim();
    if (!text) continue;
    const last = turns.at(-1);
    if (last && last.speaker === e.speaker) {
      last.text = `${last.text} ${text}`;
      last.end = Math.max(last.end, e.end);
    } else {
      turns.push({ speaker: e.speaker, text, start: e.start, end: e.end });
    }
  }
  return turns;
}

export function participantsOf(turns) {
  return [...new Set(turns.map((t) => t.speaker))];
}

const pad = (n) => String(n).padStart(2, "0");

function clock(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`;
}

export function formatDate(ms) {
  const d = new Date(ms);
  return `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

// meeting: { title, code, startedAt, endedAt }
export function formatText(meeting, turns) {
  const lines = [
    meeting.title,
    `Date : ${formatDate(meeting.startedAt)}`,
    `Participants : ${participantsOf(turns).join(", ")}`,
    "",
  ];
  for (const t of turns) {
    lines.push(`[${clock(t.start - meeting.startedAt)}] ${t.speaker} : ${t.text}`);
  }
  return lines.join("\n") + "\n";
}

// HTML for the Google Doc (Drive converts it on upload).
export function formatDocHtml(meeting, turns) {
  const rows = turns.map((t) => `
    <p style="margin:12pt 0 0 0"><b>${escapeHtml(t.speaker)}</b>
      <span style="color:#666666;font-size:9pt">&nbsp;${clock(t.start - meeting.startedAt)}</span></p>
    <p style="margin:2pt 0 0 0">${escapeHtml(t.text)}</p>`).join("");
  return `<!doctype html><html><head><meta charset="utf-8"></head><body>
    <h1>${escapeHtml(meeting.title)}</h1>
    <p>Date : ${escapeHtml(formatDate(meeting.startedAt))}<br>
       Participants : ${escapeHtml(participantsOf(turns).join(", "))}<br>
       Code de la réunion : ${escapeHtml(meeting.code)}</p>
    <h2>Transcription</h2>${rows}
  </body></html>`;
}

export function formatEmailHtml(meeting, turns, docUrl) {
  const rows = turns.map((t) => `<p style="margin:10px 0 0"><b>${escapeHtml(t.speaker)}</b>
    <span style="color:#666">${clock(t.start - meeting.startedAt)}</span><br>${escapeHtml(t.text)}</p>`).join("");
  const link = docUrl
    ? `<p><a href="${escapeHtml(docUrl)}">Ouvrir la transcription dans Google Docs</a></p>`
    : "";
  return `<div style="font-family:Arial,sans-serif;font-size:14px;color:#17202c">
    <p>Bonjour,</p>
    <p>Voici la transcription de la réunion <b>${escapeHtml(meeting.title)}</b>
       du ${escapeHtml(formatDate(meeting.startedAt))}.</p>${link}
    <hr style="border:0;border-top:1px solid #dde2e9">${rows}
    <p style="color:#666;font-size:12px;margin-top:24px">Transcription automatique générée à partir
       des sous-titres de Google Meet : elle peut contenir des erreurs.</p></div>`;
}

// ---------------------------------------------------------------- e-mail (Gmail API)

function utf8Base64(str) {
  const bytes = new TextEncoder().encode(str);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

const wrap76 = (s) => s.replace(/.{1,76}/g, "$&\r\n");

// RFC 2822 message, base64url-encoded as the Gmail API `raw` field expects.
export function buildRawEmail({ to, subject, text, html }) {
  const boundary = `b${Math.random().toString(36).slice(2)}`;
  const message = [
    `To: ${to.join(", ")}`,
    `Subject: =?UTF-8?B?${utf8Base64(subject)}?=`,
    "MIME-Version: 1.0",
    `Content-Type: multipart/alternative; boundary="${boundary}"`,
    "",
    `--${boundary}`,
    "Content-Type: text/plain; charset=UTF-8",
    "Content-Transfer-Encoding: base64",
    "",
    wrap76(utf8Base64(text)),
    `--${boundary}`,
    "Content-Type: text/html; charset=UTF-8",
    "Content-Transfer-Encoding: base64",
    "",
    wrap76(utf8Base64(html)),
    `--${boundary}--`,
    "",
  ].join("\r\n");
  return utf8Base64(message).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// Who gets the e-mail. mode: "organizer" (only the organizer's extension
// sends, so a meeting is not mailed once per attendee), "always", "never".
export function recipients(event, mode, selfEmail) {
  if (mode === "never") return [];
  if (!event) return selfEmail ? [selfEmail] : []; // ad-hoc call: a copy for me
  const isOrganizer = Boolean(event.organizer?.self || event.creator?.self);
  if (mode === "organizer" && !isOrganizer) return [];
  const emails = (event.attendees || [])
    .filter((a) => a.email && !a.resource && a.responseStatus !== "declined")
    .map((a) => a.email.toLowerCase());
  if (selfEmail) emails.push(selfEmail.toLowerCase());
  return [...new Set(emails)];
}

// The calendar event whose Meet link is this call.
export function matchEvent(events, code) {
  return events.find((e) =>
    e.conferenceData?.conferenceId === code || (e.hangoutLink || "").endsWith(`/${code}`),
  ) || null;
}
