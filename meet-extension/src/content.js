// Runs inside meet.google.com: turns captions on, reads them as they appear,
// and streams the transcript to the background worker.
//
// Meet's markup is not a public API and changes over time. Everything that
// depends on it is in MEET below, matched on accessibility labels (French and
// English) rather than on generated class names, which change on every release.

const MEET = {
  // "Quitter l'appel" / "Leave call": present only while in a call.
  leaveButton: 'button[aria-label*="Quitter l" i], button[aria-label*="Leave call" i]',
  // "Activer les sous-titres" / "Turn on captions"
  captionsButton: 'button[aria-label*="sous-titres" i], button[aria-label*="captions" i]',
  captionsRegion: '[role="region"][aria-label*="sous-titres" i], [role="region"][aria-label*="captions" i]',
};

// Meet is a single-page app: a call can start without a page load (joining
// from the Meet home page), so the meeting code is read when the call starts.
const meetingCode = () => location.pathname.match(/^\/([a-z]{3}-[a-z]{4}-[a-z]{3})\b/)?.[1];

(async () => {
  const { CaptionLog } = await import(chrome.runtime.getURL("src/transcript.js"));

  let code = null;
  let log = new CaptionLog();
  const keys = new WeakMap(); // caption block element -> stable key
  let nextKey = 1;
  let port = null;
  let startedAt = null;
  let inCall = false;
  let ended = false;
  let observer = null;
  let observedRegion = null;
  let dirty = false;

  // ------------------------------------------------------------ background link

  function connect() {
    port = chrome.runtime.connect({ name: "meet" });
    port.onMessage.addListener((msg) => {
      // Page reloaded during the call: continue the transcript saved so far.
      if (msg.type === "restore" && msg.entries.length && log.entries.length === 0) {
        log.restore(msg.entries);
        startedAt = Math.min(startedAt ?? Infinity, msg.entries[0].start);
      }
    });
    // The worker can be stopped by Chrome; reconnect while the call goes on.
    port.onDisconnect.addListener(() => {
      port = null;
      if (inCall && !ended) setTimeout(connect, 1000);
    });
    port.postMessage({ type: "hello", code });
  }

  function send(msg) {
    try {
      port?.postMessage(msg);
    } catch {
      port = null;
    }
  }

  function meetingTitle() {
    // Tab title is "Meet - <name>" for named meetings, "Meet - abc-defg-hij" otherwise.
    const name = document.title.replace(/^Meet\s*[-–]\s*/i, "").trim();
    return name && name !== code ? name : `Réunion Meet ${code}`;
  }

  function flush() {
    if (!dirty || !port) return;
    dirty = false;
    send({ type: "update", title: meetingTitle(), startedAt, entries: log.entries });
  }

  // ------------------------------------------------------------ captions

  function readBlocks(region) {
    // Each child of the region is one speaker's caption block; its visible
    // text is the speaker's name on the first line, then what they said.
    const blocks = [];
    for (const el of region.children) {
      const lines = el.innerText.split("\n").map((l) => l.trim()).filter(Boolean);
      if (lines.length < 2) continue;
      if (!keys.has(el)) keys.set(el, nextKey++);
      blocks.push({ key: keys.get(el), speaker: lines[0], text: lines.slice(1).join(" ") });
    }
    return blocks;
  }

  function onCaptionsChanged() {
    log.update(readBlocks(observedRegion));
    dirty = true;
  }

  function watchCaptions() {
    const region = document.querySelector(MEET.captionsRegion);
    if (region === observedRegion) return;
    observer?.disconnect();
    observedRegion = region;
    if (!region) return;
    observer = new MutationObserver(onCaptionsChanged);
    observer.observe(region, { childList: true, subtree: true, characterData: true });
    onCaptionsChanged();
  }

  // Captions are switched on once at the start of the call. If someone turns
  // them off afterwards, respect it: the indicator says the transcript paused.
  let captionsSeen = false;

  function ensureCaptionsOn() {
    if (document.querySelector(MEET.captionsRegion)) {
      captionsSeen = true;
      return;
    }
    if (captionsSeen) return;
    const button = [...document.querySelectorAll(MEET.captionsButton)]
      .find((b) => b.getAttribute("aria-pressed") === "false");
    button?.click();
  }

  // ------------------------------------------------------------ indicator

  let indicator = null;

  function showIndicator(on) {
    if (on && !indicator) {
      indicator = document.createElement("div");
      indicator.setAttribute("role", "status");
      Object.assign(indicator.style, {
        position: "fixed", top: "12px", left: "50%", transform: "translateX(-50%)",
        zIndex: 2147483647, padding: "4px 12px", borderRadius: "999px",
        background: "rgba(192, 54, 44, 0.92)", color: "#fff",
        font: "500 12px/1.6 Arial, sans-serif", pointerEvents: "none",
      });
      document.body.append(indicator);
    } else if (!on && indicator) {
      indicator.remove();
      indicator = null;
    }
    if (indicator) {
      indicator.textContent = observedRegion
        ? "Transcription en cours"
        : "Transcription en pause : sous-titres désactivés";
    }
  }

  // ------------------------------------------------------------ call lifecycle

  let missingSince = null;

  function startCall() {
    code = meetingCode();
    log = new CaptionLog();
    inCall = true;
    ended = false;
    captionsSeen = false;
    observedRegion = null;
    startedAt = Date.now();
    connect();
    showIndicator(true);
  }

  function tick() {
    const present = Boolean(document.querySelector(MEET.leaveButton));
    // A new call: the first one in this tab, another meeting joined later, or
    // the same meeting rejoined after leaving (a new transcript).
    if (present && !inCall && meetingCode()) startCall();
    if (!inCall) return;

    if (present) {
      missingSince = null;
      ensureCaptionsOn();
      watchCaptions();
      showIndicator(true);
      flush();
    } else {
      // The leave button also vanishes briefly when Meet re-renders its
      // toolbar: only conclude the call ended after a few seconds.
      missingSince ??= Date.now();
      if (Date.now() - missingSince > 4000) endCall();
    }
  }

  function endCall() {
    if (ended) return;
    ended = true;
    inCall = false;
    observer?.disconnect();
    // The final transcript travels with the "ended" message itself, so the
    // worker never delivers before the last update is stored.
    send({ type: "ended", code, title: meetingTitle(), startedAt, entries: log.entries });
    showIndicator(false);
  }

  setInterval(tick, 1000);
  // Also a heartbeat: regular messages keep the background worker alive.
  setInterval(() => {
    dirty = true;
    flush();
  }, 20000);
})();
