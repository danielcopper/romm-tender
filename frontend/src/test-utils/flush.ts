/**
 * Waiting out work a test cannot see the end of.
 *
 * An `await act(async () => {})` runs microtasks and no timer, so an answer
 * that lands one timer later passes a test that reads straight after it. A read
 * that asserts something did NOT happen waits for this first.
 */

import { act } from "@testing-library/react";

/** Run what is queued up to and including the next zero-delay timer, inside `act`. */
export function flushMacrotask(): Promise<void> {
  return act(() => new Promise<void>((resolve) => setTimeout(resolve, 0)));
}
