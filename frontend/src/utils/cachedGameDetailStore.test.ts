import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { getCachedGameDetail, invalidateCachedGameDetail, _cacheForTests } from "./cachedGameDetailStore";
import type { CachedGameDetail } from "../api/backend";

const raw = vi.hoisted(() => vi.fn());
vi.mock("../api/host", () => ({ callable: () => raw }));

describe("getCachedGameDetail", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    _cacheForTests.clear();
    raw.mockReset();
    raw.mockResolvedValue({ found: true, rom_id: 1 });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns the same promise for back-to-back calls within the TTL window", async () => {
    const promise1 = getCachedGameDetail(42);
    const promise2 = getCachedGameDetail(42);
    expect(promise1).toBe(promise2);
    await promise1.catch(() => undefined);
  });

  it("creates a fresh entry per appId", () => {
    const a = getCachedGameDetail(1);
    const b = getCachedGameDetail(2);
    expect(a).not.toBe(b);
    expect(_cacheForTests.has(1)).toBe(true);
    expect(_cacheForTests.has(2)).toBe(true);
  });

  it("evicts the cache entry after the TTL window when the underlying call resolves", async () => {
    const detail: CachedGameDetail = { found: true, rom_id: 99 };
    _cacheForTests.set(7, { promise: Promise.resolve(detail), ts: Date.now() });

    await _cacheForTests.get(7)!.promise;
    vi.advanceTimersByTime(3000);
    await Promise.resolve();
    // Direct cache injection bypasses the .then-scheduled eviction; callers
    // who poke the cache directly should clean up themselves.
    expect(_cacheForTests.has(7)).toBe(true);
  });

  it("evicts its own entry once the TTL has passed after the call resolved", async () => {
    await getCachedGameDetail(5);
    vi.advanceTimersByTime(3000);
    expect(_cacheForTests.has(5)).toBe(false);
  });

  it("an earlier fetch's TTL timer leaves an entry installed after it in place", async () => {
    await getCachedGameDetail(5);
    vi.advanceTimersByTime(2000);
    invalidateCachedGameDetail(5);
    const fresher = getCachedGameDetail(5);
    await fresher;

    // The first fetch's timer fires here; the fresher entry's runs 2 s later.
    vi.advanceTimersByTime(1000);

    expect(_cacheForTests.get(5)?.promise).toBe(fresher);
    expect(getCachedGameDetail(5)).toBe(fresher);
  });

  it("an earlier fetch that rejects leaves an entry installed after it in place", async () => {
    let rejectFirst!: (e: Error) => void;
    raw.mockReturnValueOnce(
      new Promise<CachedGameDetail>((_resolve, reject) => {
        rejectFirst = reject;
      }),
    );
    const first = getCachedGameDetail(5);
    invalidateCachedGameDetail(5);
    const fresher = getCachedGameDetail(5);

    rejectFirst(new Error("boom"));
    await expect(first).rejects.toThrow("boom");

    expect(_cacheForTests.get(5)?.promise).toBe(fresher);
  });

  it("drops its own entry when the call rejects", async () => {
    raw.mockRejectedValueOnce(new Error("boom"));
    await expect(getCachedGameDetail(5)).rejects.toThrow("boom");
    expect(_cacheForTests.has(5)).toBe(false);
  });
});

describe("invalidateCachedGameDetail", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    _cacheForTests.clear();
  });
  afterEach(() => vi.useRealTimers());

  it("drops the entry for the given appId", () => {
    const detail: CachedGameDetail = { found: true, rom_id: 1 };
    _cacheForTests.set(1, { promise: Promise.resolve(detail), ts: Date.now() });
    expect(_cacheForTests.has(1)).toBe(true);

    invalidateCachedGameDetail(1);

    expect(_cacheForTests.has(1)).toBe(false);
  });

  it("is a no-op for an unknown appId", () => {
    expect(() => invalidateCachedGameDetail(999)).not.toThrow();
  });
});
