import { describe, it, expect, beforeEach, vi } from "vitest";
import { dismissStoppedUpdateAttempt, getStoppedUpdateAttempt as readStopped } from "../api/backend";
import {
  dismissStoppedUpdateCard,
  endStoppedAttempt,
  fetchStoppedUpdateAttempt,
  getStoppedUpdateAttempt,
  resetStoppedUpdateStoreForTests,
  stoppedAttemptTakesThePlaceOf,
  takePushedStoppedAttempt,
} from "./stoppedUpdateStore";
import { onUpdateOutcomeChange } from "./updateOutcomeStore";

const WIRE = { attempted_version: "1.1.0", from_version: "1.0.0", started_at: "2026-09-29T10:00:00Z" };

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => (resolve = r));
  return { promise, resolve };
}

describe("stoppedUpdateStore", () => {
  beforeEach(() => {
    resetStoppedUpdateStoreForTests();
    vi.mocked(readStopped).mockReset().mockResolvedValue(null);
    vi.mocked(dismissStoppedUpdateAttempt).mockReset().mockResolvedValue({ success: true });
  });

  it("takes what the backend found at start", async () => {
    vi.mocked(readStopped).mockResolvedValue(WIRE);

    await fetchStoppedUpdateAttempt();

    expect(getStoppedUpdateAttempt()).toEqual({ attemptedVersion: "1.1.0", fromVersion: "1.0.0" });
  });

  it("holds nothing where the backend found nothing", async () => {
    await fetchStoppedUpdateAttempt();

    expect(getStoppedUpdateAttempt()).toBeNull();
  });

  it("takes the card down once the backend removed the record, and tells every subscriber", async () => {
    vi.mocked(readStopped).mockResolvedValue(WIRE);
    await fetchStoppedUpdateAttempt();
    const heard = vi.fn();
    const stop = onUpdateOutcomeChange(heard);

    await dismissStoppedUpdateCard();
    stop();

    expect(dismissStoppedUpdateAttempt).toHaveBeenCalledTimes(1);
    expect(getStoppedUpdateAttempt()).toBeNull();
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it("leaves the card up where the dismissal failed", async () => {
    vi.mocked(readStopped).mockResolvedValue(WIRE);
    await fetchStoppedUpdateAttempt();
    vi.mocked(dismissStoppedUpdateAttempt).mockRejectedValue(new Error("connection_lost"));

    await expect(dismissStoppedUpdateCard()).rejects.toThrow("connection_lost");

    expect(getStoppedUpdateAttempt()).not.toBeNull();
  });

  it("writes nothing from a read that was in flight when Dismiss was pressed", async () => {
    const slow = deferred<typeof WIRE | null>();
    vi.mocked(readStopped).mockReturnValueOnce(slow.promise);

    const reading = fetchStoppedUpdateAttempt();
    await dismissStoppedUpdateCard();
    slow.resolve(WIRE);
    await reading;

    expect(getStoppedUpdateAttempt()).toBeNull();
  });

  it("takes a stopped attempt the backend pushed after panel load, and tells every subscriber", () => {
    const heard = vi.fn();
    const stop = onUpdateOutcomeChange(heard);

    takePushedStoppedAttempt(WIRE);
    stop();

    expect(getStoppedUpdateAttempt()).toEqual({ attemptedVersion: "1.1.0", fromVersion: "1.0.0" });
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it("keeps what was pushed over a read that was in flight before it", async () => {
    const slow = deferred<typeof WIRE | null>();
    vi.mocked(readStopped).mockReturnValueOnce(slow.promise);

    const reading = fetchStoppedUpdateAttempt();
    takePushedStoppedAttempt(WIRE);
    slow.resolve(null);
    await reading;

    expect(getStoppedUpdateAttempt()).not.toBeNull();
  });

  it("takes the card down at an accepted press, and a read in flight before it does not put it back", async () => {
    vi.mocked(readStopped).mockResolvedValue(WIRE);
    await fetchStoppedUpdateAttempt();
    const slow = deferred<typeof WIRE | null>();
    vi.mocked(readStopped).mockReturnValueOnce(slow.promise);
    const reading = fetchStoppedUpdateAttempt();

    endStoppedAttempt();
    slow.resolve(WIRE);
    await reading;

    expect(getStoppedUpdateAttempt()).toBeNull();
  });

  it("takes the place of the available card for the version it tried, and no other", () => {
    const stopped = { attemptedVersion: "1.1.0", fromVersion: "1.0.0" };

    expect(stoppedAttemptTakesThePlaceOf("1.1.0", stopped)).toBe(true);
    expect(stoppedAttemptTakesThePlaceOf("1.2.0", stopped)).toBe(false);
    expect(stoppedAttemptTakesThePlaceOf("1.1.0", null)).toBe(false);
  });
});
