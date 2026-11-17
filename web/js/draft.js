// The Workbench draft and its backup (review P1, rounds 2-3). Pure, for node --test.
// A draft is the user's unless an opened run wrote it (`fromRun`). Opening a run keeps the user's draft aside;
// a draft an opened run wrote is never kept aside, so a second Open does not replace the user's backup.

/** The backup to store when a run is opened: the current draft if it is the user's, else the existing backup. */
export function backupOnOpen(currentJson, existingBackupJson) {
  if (!currentJson) return existingBackupJson ?? null;
  let current;
  try { current = JSON.parse(currentJson); } catch { return existingBackupJson ?? null; }
  return current && !current.fromRun ? currentJson : (existingBackupJson ?? null);
}

/** The state a request sends: with media attached, an empty or blank text is no state at all (`null`): a recording
 * alone is the whole state (SPIKE §2.5), and d1-Omni must not get a stray text beside it (review P6). */
export function bodyState(state, mediaCount) {
  return mediaCount > 0 && typeof state === 'string' && state.trim() === '' ? null : state;
}

/** The state field after the Text / JSON switch. Text → JSON keeps a text that already is a JSON object, array or
 * string and writes any other text as a JSON string, so a plain call text is sent unchanged. JSON → Text unwraps a JSON
 * string and keeps any other text as it is. A blank field is left as it is. */
export function switchStateText(text, mode) {
  if (text.trim() === '') return text;
  let value;
  try { value = JSON.parse(text); } catch { return mode === 'json' ? JSON.stringify(text) : text; }
  if (mode === 'json') return typeof value === 'string' || (value !== null && typeof value === 'object') ? text : JSON.stringify(text);
  return typeof value === 'string' ? value : text;
}
