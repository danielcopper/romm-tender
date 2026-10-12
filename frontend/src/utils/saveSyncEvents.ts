/** Tell every surface showing this ROM that its save-sync facts changed, so each re-reads them. */
export function announceSaveSync(romId: number): void {
  globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync", rom_id: romId } }));
}
