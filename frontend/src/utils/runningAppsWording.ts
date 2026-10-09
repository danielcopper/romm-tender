/**
 * What the panel says about apps Steam lists as running whose display status
 * could not be read. Such an app counts as running wherever Tender asks, so it
 * holds whatever waits for no game to run — the stranded panel's reload and an
 * update's install — and only the reader can tell whether it has closed.
 */

import { joined } from "./biosGroup";

/** The sentence for *names*, every one an app Steam lists whose status could not be read. */
export function statusUnreadSentence(names: readonly string[]): string {
  const several = names.length > 1;
  return (
    `Steam lists ${joined(names)} as running, and Tender can't tell whether ${several ? "they are" : "it is"}. ` +
    `If ${several ? "they have" : "it has"} closed, restart Steam.`
  );
}
