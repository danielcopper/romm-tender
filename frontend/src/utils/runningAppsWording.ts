/**
 * What the panel says about apps Steam lists whose display status could not be
 * read: `docs/architecture/save-file-sync-architecture.md`, "Is the game
 * running".
 */

import { joined } from "./formatters";

/** The sentence for *names*, every one an app Steam lists whose status could not be read. */
export function statusUnreadSentence(names: readonly string[]): string {
  const listed = `Steam lists ${joined(names)} as running, and Tender can't tell whether`;
  return names.length > 1
    ? `${listed} they still are. Quit any that are open; if they have already closed, restart Steam.`
    : `${listed} it still is. Quit it if it's open; if it has already closed, restart Steam.`;
}
