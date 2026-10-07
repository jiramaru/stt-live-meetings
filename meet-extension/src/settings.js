// Settings shared by the background worker and the options page.

export const DEFAULT_SETTINGS = {
  emailMode: "organizer", // "organizer" | "always" | "never"
  shareDoc: true, // give e-mail recipients read access to the Google Doc
  folderName: "Transcriptions Meet",
};

export async function loadSettings() {
  const { settings } = await chrome.storage.sync.get("settings");
  return { ...DEFAULT_SETTINGS, ...settings };
}

export async function saveSettings(settings) {
  await chrome.storage.sync.set({ settings });
}
