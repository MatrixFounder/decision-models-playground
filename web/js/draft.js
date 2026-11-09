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
