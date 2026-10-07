// Calls to Google Calendar, Drive and Gmail with the user's own account
// (chrome.identity OAuth token). Scopes are declared in manifest.json.

export async function getToken(interactive = false) {
  const { token } = await chrome.identity.getAuthToken({ interactive });
  return token;
}

async function call(url, options = {}, retried = false) {
  const token = await getToken(false);
  const res = await fetch(url, {
    ...options,
    headers: { Authorization: `Bearer ${token}`, ...(options.headers || {}) },
  });
  if (res.status === 401 && !retried) {
    // Expired or revoked token: drop it from Chrome's cache and try once more.
    await chrome.identity.removeCachedAuthToken({ token });
    return call(url, options, true);
  }
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(`${res.status} ${url.split("?")[0]}: ${detail.slice(0, 300)}`);
  }
  return res.status === 204 ? null : res.json();
}

// Events of the primary calendar around a given time (the call's start).
export async function eventsAround(time) {
  const params = new URLSearchParams({
    timeMin: new Date(time - 12 * 3600e3).toISOString(),
    timeMax: new Date(time + 12 * 3600e3).toISOString(),
    singleEvents: "true",
    maxResults: "250",
  });
  const data = await call(`https://www.googleapis.com/calendar/v3/calendars/primary/events?${params}`);
  return data.items || [];
}

// The extension's Drive folder: created once, then reused. With the
// drive.file scope the extension only sees files and folders it created.
export async function ensureFolder(name) {
  const { folderId } = await chrome.storage.local.get("folderId");
  if (folderId) {
    try {
      const folder = await call(`https://www.googleapis.com/drive/v3/files/${folderId}?fields=id,trashed`);
      if (!folder.trashed) return folderId;
    } catch {
      // deleted or inaccessible: create a new one below
    }
  }
  const folder = await call("https://www.googleapis.com/drive/v3/files?fields=id", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, mimeType: "application/vnd.google-apps.folder" }),
  });
  await chrome.storage.local.set({ folderId: folder.id });
  return folder.id;
}

// Upload HTML that Drive converts into a Google Doc.
export async function createDoc({ name, html, folderId }) {
  const boundary = `b${Math.random().toString(36).slice(2)}`;
  const metadata = {
    name,
    mimeType: "application/vnd.google-apps.document",
    parents: folderId ? [folderId] : undefined,
  };
  const body = [
    `--${boundary}`,
    "Content-Type: application/json; charset=UTF-8",
    "",
    JSON.stringify(metadata),
    `--${boundary}`,
    "Content-Type: text/html; charset=UTF-8",
    "",
    html,
    `--${boundary}--`,
  ].join("\r\n");
  return call(
    "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,webViewLink",
    { method: "POST", headers: { "Content-Type": `multipart/related; boundary=${boundary}` }, body },
  );
}

// Give each recipient read access, without Drive's own notification e-mail
// (ours already contains the link).
export async function shareDoc(fileId, emails, role = "reader") {
  const failed = [];
  for (const emailAddress of emails) {
    try {
      await call(
        `https://www.googleapis.com/drive/v3/files/${fileId}/permissions?sendNotificationEmail=false`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ role, type: "user", emailAddress }),
        },
      );
    } catch {
      failed.push(emailAddress);
    }
  }
  return failed;
}

export async function sendEmail(raw) {
  return call("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ raw }),
  });
}
