import assert from "node:assert/strict";
import { test } from "node:test";

import {
  CaptionLog,
  buildRawEmail,
  formatDocHtml,
  formatText,
  matchEvent,
  mergeText,
  recipients,
  toTurns,
} from "../src/transcript.js";

test("mergeText: words appended", () => {
  assert.equal(mergeText("Bonjour à", "Bonjour à tous"), "Bonjour à tous");
});

test("mergeText: last words revised", () => {
  assert.equal(
    mergeText("Nous allons commencer la réunion du comité", "Nous allons commencer la réunion du Cotech"),
    "Nous allons commencer la réunion du Cotech",
  );
});

test("mergeText: beginning scrolled out of the block", () => {
  const before = "Le premier point concerne la digitalisation des services publics";
  const after = "la digitalisation des services publics et le calendrier";
  assert.equal(
    mergeText(before, after),
    "Le premier point concerne la digitalisation des services publics et le calendrier",
  );
});

test("mergeText: unrelated text is appended, repeated tail ignored", () => {
  assert.equal(mergeText("Oui.", "On continue."), "Oui. On continue.");
  assert.equal(mergeText("Merci à tous", "à tous"), "Merci à tous");
});

test("CaptionLog follows blocks and closes them when they disappear", () => {
  let t = 0;
  const log = new CaptionLog(() => t);
  log.update([{ key: 1, speaker: "Awa", text: "Bonjour" }]);
  t = 1000;
  log.update([{ key: 1, speaker: "Awa", text: "Bonjour à tous" }]);
  t = 2000;
  log.update([
    { key: 1, speaker: "Awa", text: "Bonjour à tous" },
    { key: 2, speaker: "Paul", text: "Merci" },
  ]);
  t = 3000;
  log.update([{ key: 2, speaker: "Paul", text: "Merci Awa" }]);

  assert.deepEqual(
    log.entries.map(({ speaker, text, start, end }) => ({ speaker, text, start, end })),
    [
      { speaker: "Awa", text: "Bonjour à tous", start: 0, end: 2000 },
      { speaker: "Paul", text: "Merci Awa", start: 2000, end: 3000 },
    ],
  );
  assert.deepEqual([...log.open.keys()], [2]);
});

test("toTurns merges consecutive entries of the same speaker", () => {
  const turns = toTurns([
    { speaker: "Awa", text: "Bonjour.", start: 0, end: 1 },
    { speaker: "Awa", text: " On commence ?", start: 2, end: 3 },
    { speaker: "Paul", text: "Oui.", start: 4, end: 5 },
  ]);
  assert.deepEqual(turns, [
    { speaker: "Awa", text: "Bonjour. On commence ?", start: 0, end: 3 },
    { speaker: "Paul", text: "Oui.", start: 4, end: 5 },
  ]);
});

test("formats: text and doc HTML (escaped)", () => {
  const meeting = { title: "Cotech <PATN>", code: "abc-defg-hij", startedAt: 0 };
  const turns = [{ speaker: "Awa", text: "5 < 6 & ok", start: 65000, end: 70000 }];
  assert.match(formatText(meeting, turns), /\[00:01:05\] Awa : 5 < 6 & ok/);
  const html = formatDocHtml(meeting, turns);
  assert.match(html, /Cotech &lt;PATN&gt;/);
  assert.match(html, /5 &lt; 6 &amp; ok/);
});

test("buildRawEmail produces a decodable UTF-8 message", () => {
  const raw = buildRawEmail({
    to: ["a@x.cg", "b@x.cg"],
    subject: "Transcription : Comité",
    text: "Réunion terminée",
    html: "<p>Réunion terminée</p>",
  });
  assert.doesNotMatch(raw, /[+/=]/); // base64url
  const message = Buffer.from(raw, "base64url").toString("utf8");
  assert.match(message, /^To: a@x\.cg, b@x\.cg\r\n/);
  const subject = message.match(/Subject: =\?UTF-8\?B\?(.+)\?=/)[1];
  assert.equal(Buffer.from(subject, "base64").toString("utf8"), "Transcription : Comité");
  const parts = [...message.matchAll(/base64\r\n\r\n([\s\S]+?)\r\n--/g)].map((m) =>
    Buffer.from(m[1].replace(/\r\n/g, ""), "base64").toString("utf8"));
  assert.deepEqual(parts, ["Réunion terminée", "<p>Réunion terminée</p>"]);
});

test("recipients: only the organizer sends by default", () => {
  const event = {
    organizer: { email: "chef@x.cg" },
    attendees: [
      { email: "Chef@x.cg", organizer: true },
      { email: "awa@x.cg" },
      { email: "salle@resource.calendar.google.com", resource: true },
      { email: "absent@x.cg", responseStatus: "declined" },
    ],
  };
  assert.deepEqual(recipients(event, "organizer", "awa@x.cg"), []);
  assert.deepEqual(
    recipients({ ...event, organizer: { self: true } }, "organizer", "chef@x.cg"),
    ["chef@x.cg", "awa@x.cg"],
  );
  assert.deepEqual(recipients(event, "always", "awa@x.cg"), ["chef@x.cg", "awa@x.cg"]);
  assert.deepEqual(recipients(event, "never", "awa@x.cg"), []);
  assert.deepEqual(recipients(null, "organizer", "awa@x.cg"), ["awa@x.cg"]);
});

test("matchEvent finds the calendar event of the call", () => {
  const events = [
    { id: 1, hangoutLink: "https://meet.google.com/zzz-zzzz-zzz" },
    { id: 2, conferenceData: { conferenceId: "abc-defg-hij" } },
  ];
  assert.equal(matchEvent(events, "abc-defg-hij").id, 2);
  assert.equal(matchEvent(events, "zzz-zzzz-zzz").id, 1);
  assert.equal(matchEvent(events, "nop-nopq-nop"), null);
});
