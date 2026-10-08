/**
 * In-memory stand-in for the socket's word on a stranded panel, for component tests.
 *
 * `api/host` is stubbed for the whole suite, so no socket ever learns it is
 * stranded there. The global mock factory in `frontend/src/test-setup.ts` wires
 * `strandedAnswer` and `onStrandedAnswerChange` to this module; a test makes the
 * panel stranded with {@link setStrandedAnswer}, inside `act(...)` when a
 * mounted component reads it. `recheckStranded` is a plain `vi.fn()` in that
 * factory, so a test arranges what a re-check finds by giving it an
 * implementation that calls {@link setStrandedAnswer}.
 *
 * Like the socket, a listener hears only a CHANGE of answer. The state is
 * cleared by the global `afterEach`.
 */

import type { StrandedAnswer } from "../utils/strandedPanelWording";

let answer: StrandedAnswer | null = null;
const listeners = new Set<(answer: StrandedAnswer) => void>();

/** Stand-in for `api/host`'s `strandedAnswer`. */
export function mockStrandedAnswer(): StrandedAnswer | null {
  return answer;
}

/** Stand-in for `api/host`'s `onStrandedAnswerChange`. */
export function mockOnStrandedAnswerChange(listener: (answer: StrandedAnswer) => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** Make the panel stranded with *next*, telling every listener — unless that is already the answer. */
export function setStrandedAnswer(next: StrandedAnswer): void {
  if (answer === next) return;
  answer = next;
  for (const listener of listeners) listener(next);
}

/** Back to a panel nobody refused, with nobody listening. Wired into the global `afterEach`. */
export function resetStrandedPanel(): void {
  answer = null;
  listeners.clear();
}
