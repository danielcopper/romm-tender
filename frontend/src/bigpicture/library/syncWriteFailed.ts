/**
 * What a Library sync write that did not take says when there is no answer to
 * quote. A refusal carries a message (`.claude/rules/callables.md`) and that
 * message is what the reader gets; this stands in where one arrives empty. A
 * REJECTION has no answer at all — the call never returned one — and the revert
 * would otherwise happen in silence.
 */
export const SYNC_WRITE_FAILED = "Could not save that; the change was undone.";
