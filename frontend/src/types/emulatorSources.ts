/**
 * Emulator sources as the backend answers them: what the resolver detected,
 * keyed by its installation kind, and what each one's health states.
 */

/** One health finding of a source, as the resolver states it: a stable `code`
 *  and the facts it established (`path`, `status`, `key`, `problem`, `hub`, …).
 *  The resolver's message text is never carried; the wording is Tender's. */
export interface SourceHealthFinding {
  code: string;
  data: Record<string, unknown>;
}
