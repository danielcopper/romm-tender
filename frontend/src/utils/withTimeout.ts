/**
 * The rejection {@link withTimeout} raises when its deadline wins the race —
 * distinguishable from the raced promise's own rejection without matching on
 * message text, for callers that report the two differently.
 */
export class TimeoutError extends Error {
  constructor(ms: number) {
    super(`timed out after ${ms}ms`);
    this.name = "TimeoutError";
  }
}

/**
 * Race a promise against a deadline. Resolves/rejects with `promise` when it
 * settles first; rejects with a {@link TimeoutError} once `ms` elapses. The
 * deadline timer is cleared as soon as the race settles, so a settled call never
 * leaves a pending timer behind.
 *
 * `endpoint()` has no timeout of its own (`api/hostSocket.ts`): a call issued
 * while the backend is down waits for it to come back — every endpoint call
 * that must stay responsive races through this.
 *
 * Losing the race abandons `promise`, it does not cancel it: a backend call that
 * answers late still runs to completion and still commits whatever it was going
 * to commit. Only race a call whose late completion is harmless or self-evident
 * on the next read of the state it wrote.
 */
export function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  let timer!: ReturnType<typeof setTimeout>;
  const timeout = new Promise<never>((_, reject) => {
    timer = setTimeout(() => reject(new TimeoutError(ms)), ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

/**
 * Rethrow `e` when it is an expired {@link withTimeout} deadline. For a catch
 * whose fallback answers a call that FAILED: a call that got no answer at all
 * is a different verdict, and the fallback must not answer it.
 */
export function rethrowTimeout(e: unknown): void {
  if (e instanceof TimeoutError) throw e;
}

/**
 * Race `call` against `ms` ({@link withTimeout}) and answer a call that FAILED
 * with `onFailure`. An expired deadline is rethrown rather than answered
 * ({@link rethrowTimeout}), so no fallback can turn "no answer" into a verdict.
 */
export function boundedOr<T, F>(call: Promise<T>, ms: number, onFailure: (e: unknown) => F): Promise<T | F> {
  return withTimeout(call, ms).catch((e: unknown) => {
    rethrowTimeout(e);
    return onFailure(e);
  });
}
