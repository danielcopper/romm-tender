import { describe, it, expect, vi, afterEach } from "vitest";
import { withTimeout, TimeoutError, rethrowTimeout, boundedOr } from "./withTimeout";

describe("withTimeout", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("resolves with the promise's value when it settles before the deadline", async () => {
    await expect(withTimeout(Promise.resolve("ok"), 1000)).resolves.toBe("ok");
  });

  it("propagates the promise's rejection when it rejects before the deadline", async () => {
    await expect(withTimeout(Promise.reject(new Error("boom")), 1000)).rejects.toThrow("boom");
  });

  it("rejects with a timeout error once the deadline elapses first", async () => {
    vi.useFakeTimers();
    const pending = new Promise<string>(() => {
      /* never settles */
    });
    const assertion = expect(withTimeout(pending, 5000)).rejects.toThrow(/^timed out after 5000ms$/);
    await vi.advanceTimersByTimeAsync(5000);
    await assertion;
  });

  it("rejects with a TimeoutError on the deadline, and passes a raced rejection through untouched", async () => {
    vi.useFakeTimers();
    const pending = new Promise<string>(() => {
      /* never settles */
    });
    const deadline = expect(withTimeout(pending, 5000)).rejects.toBeInstanceOf(TimeoutError);
    await vi.advanceTimersByTimeAsync(5000);
    await deadline;
    // The promise's own failure must stay distinguishable from the deadline's.
    await expect(withTimeout(Promise.reject(new Error("boom")), 5000)).rejects.not.toBeInstanceOf(TimeoutError);
  });

  it("clears the deadline timer once the promise settles — no leaked timer", async () => {
    vi.useFakeTimers();
    const clearSpy = vi.spyOn(globalThis, "clearTimeout");
    await withTimeout(Promise.resolve("done"), 5000);
    // The finally-cleanup cleared the deadline, leaving nothing pending.
    expect(clearSpy).toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
    clearSpy.mockRestore();
  });
});

describe("rethrowTimeout", () => {
  it("rethrows an expired deadline", () => {
    const expired = new TimeoutError(5000);
    expect(() => rethrowTimeout(expired)).toThrow(expired);
  });

  it("lets any other failure through to the caller's fallback", () => {
    expect(() => rethrowTimeout(new Error("connection_lost"))).not.toThrow();
  });
});

describe("boundedOr", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("resolves with the call's answer when it answers in time", async () => {
    const onFailure = vi.fn(() => "fallback");
    await expect(boundedOr(Promise.resolve("answer"), 1000, onFailure)).resolves.toBe("answer");
    expect(onFailure).not.toHaveBeenCalled();
  });

  it("answers a call that failed with the fallback", async () => {
    const failure = new Error("connection_lost");
    const onFailure = vi.fn(() => "fallback");
    await expect(boundedOr(Promise.reject(failure), 1000, onFailure)).resolves.toBe("fallback");
    expect(onFailure).toHaveBeenCalledWith(failure);
  });

  it("rejects with the expired deadline, and never asks the fallback, when the call does not answer", async () => {
    vi.useFakeTimers();
    const onFailure = vi.fn(() => "fallback");
    const bounded = boundedOr(new Promise<string>(() => {}), 5000, onFailure);
    const settled = expect(bounded).rejects.toBeInstanceOf(TimeoutError);

    await vi.advanceTimersByTimeAsync(5000);

    await settled;
    expect(onFailure).not.toHaveBeenCalled();
  });
});
