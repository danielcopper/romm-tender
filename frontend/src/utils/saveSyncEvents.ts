/** Tell every surface showing this ROM that its save-sync facts changed, so each re-reads them — and, where the caller
 *  knows it, whether the ROM has a conflict: the Play button changes its state only on an announcement that says. */
export function announceSaveSync(romId: number, hasConflict?: boolean): void {
  const conflict = hasConflict === undefined ? {} : { has_conflict: hasConflict };
  globalThis.dispatchEvent(
    new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: romId, ...conflict } }),
  );
}
