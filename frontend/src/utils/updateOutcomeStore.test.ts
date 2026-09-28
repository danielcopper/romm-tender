import { describe, it, expect, beforeEach, vi } from "vitest";
import { toaster } from "../api/host";
import {
  acknowledgeUpdateAnnouncement,
  dismissUpdateFailure,
  getUpdateOutcome,
  type UpdateOutcome,
} from "../api/backend";
import {
  dismissUpdateFailureRecord,
  failureCardShows,
  failureTakesThePlaceOf,
  fetchUpdateOutcome,
  getUpdateOutcomeState,
  onUpdateOutcomeChange,
  resetUpdateOutcomeStoreForTests,
  updateFailureSentence,
  type UpdateOutcomeState,
} from "./updateOutcomeStore";

const NOTHING: UpdateOutcome = { announce_version: null, failure: null, failure_dismissed: false };

const ROLLED_BACK_WIRE: UpdateOutcome = {
  announce_version: null,
  failure: { attempted_version: "1.3.0", restored_version: "1.2.3", rolled_back_at: "2026-09-25T10:15:00Z" },
  failure_dismissed: false,
};

const ROLLED_BACK: UpdateOutcomeState = {
  failure: { attemptedVersion: "1.3.0", restoredVersion: "1.2.3", rolledBackAt: "2026-09-25T10:15:00Z" },
  failureDismissed: false,
};

/** A promise the test resolves by hand, to hold a call in flight. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("updateOutcomeStore", () => {
  beforeEach(() => {
    resetUpdateOutcomeStoreForTests();
    vi.mocked(getUpdateOutcome).mockReset();
    vi.mocked(acknowledgeUpdateAnnouncement).mockReset().mockResolvedValue({ success: true });
    vi.mocked(dismissUpdateFailure).mockReset().mockResolvedValue({ success: true });
    vi.mocked(toaster.toast).mockClear();
  });

  it("starts with no record", () => {
    expect(getUpdateOutcomeState()).toEqual({ failure: null, failureDismissed: false });
  });

  it("maps the backend's record onto the store and notifies", async () => {
    vi.mocked(getUpdateOutcome).mockResolvedValue(ROLLED_BACK_WIRE);
    const listener = vi.fn();
    onUpdateOutcomeChange(listener);

    await fetchUpdateOutcome();

    expect(getUpdateOutcomeState()).toEqual(ROLLED_BACK);
    expect(listener).toHaveBeenCalledTimes(1);
  });

  describe("an update that went through", () => {
    it("is announced in one toast, then acknowledged", async () => {
      vi.mocked(getUpdateOutcome).mockResolvedValue({ ...NOTHING, announce_version: "1.3.0" });

      await fetchUpdateOutcome();

      expect(toaster.toast).toHaveBeenCalledTimes(1);
      expect(toaster.toast).toHaveBeenCalledWith({ title: "Tender", body: "Tender updated to 1.3.0" });
      expect(acknowledgeUpdateAnnouncement).toHaveBeenCalledTimes(1);
      expect(vi.mocked(toaster.toast).mock.invocationCallOrder[0]).toBeLessThan(
        vi.mocked(acknowledgeUpdateAnnouncement).mock.invocationCallOrder[0]!,
      );
    });

    it("nothing owed raises no toast and acknowledges nothing", async () => {
      vi.mocked(getUpdateOutcome).mockResolvedValue(NOTHING);

      await fetchUpdateOutcome();

      expect(toaster.toast).not.toHaveBeenCalled();
      expect(acknowledgeUpdateAnnouncement).not.toHaveBeenCalled();
    });

    it("an acknowledgement that failed rejects, so the caller's log names it", async () => {
      vi.mocked(getUpdateOutcome).mockResolvedValue({ ...NOTHING, announce_version: "1.3.0" });
      vi.mocked(acknowledgeUpdateAnnouncement).mockRejectedValue(new Error("socket closed"));

      await expect(fetchUpdateOutcome()).rejects.toThrow("socket closed");
      expect(toaster.toast).toHaveBeenCalledTimes(1);
    });
  });

  describe("Dismiss", () => {
    it("persists first, then takes the card down", async () => {
      vi.mocked(getUpdateOutcome).mockResolvedValue(ROLLED_BACK_WIRE);
      await fetchUpdateOutcome();

      await dismissUpdateFailureRecord("2026-09-25T10:15:00Z");

      expect(dismissUpdateFailure).toHaveBeenCalledWith("2026-09-25T10:15:00Z");
      expect(getUpdateOutcomeState()).toEqual({ ...ROLLED_BACK, failureDismissed: true });
    });

    it("a refused write rejects and leaves the card up", async () => {
      vi.mocked(getUpdateOutcome).mockResolvedValue(ROLLED_BACK_WIRE);
      await fetchUpdateOutcome();
      vi.mocked(dismissUpdateFailure).mockResolvedValue({
        success: false,
        reason: "invalid_value",
        message: "Invalid record",
      });

      await expect(dismissUpdateFailureRecord("")).rejects.toThrow("invalid_value: Invalid record");
      expect(getUpdateOutcomeState().failureDismissed).toBe(false);
    });

    it("a read that was in flight when Dismiss landed writes nothing over it", async () => {
      const read = deferred<UpdateOutcome>();
      vi.mocked(getUpdateOutcome).mockReturnValue(read.promise);
      const pending = fetchUpdateOutcome();

      await dismissUpdateFailureRecord("2026-09-25T10:15:00Z");
      read.resolve(ROLLED_BACK_WIRE);
      await pending;

      expect(getUpdateOutcomeState().failureDismissed).toBe(true);
    });
  });

  describe("the rules the two cards are drawn by", () => {
    it("words a rolled-back update in one sentence", () => {
      expect(updateFailureSentence(ROLLED_BACK.failure!)).toBe("Update to 1.3.0 failed — you are still on 1.2.3.");
    });

    it("the rolled-back card stands while a record does and was not dismissed", () => {
      expect(failureCardShows(ROLLED_BACK)).toBe(true);
      expect(failureCardShows({ ...ROLLED_BACK, failureDismissed: true })).toBe(false);
      expect(failureCardShows({ failure: null, failureDismissed: false })).toBe(false);
    });

    it("the record takes the place of the available card for the version it tried, dismissed or not", () => {
      expect(failureTakesThePlaceOf("1.3.0", ROLLED_BACK)).toBe(true);
      expect(failureTakesThePlaceOf("1.3.0", { ...ROLLED_BACK, failureDismissed: true })).toBe(true);
    });

    it("and of no other version's", () => {
      expect(failureTakesThePlaceOf("1.4.0", ROLLED_BACK)).toBe(false);
      expect(failureTakesThePlaceOf(null, ROLLED_BACK)).toBe(false);
      expect(failureTakesThePlaceOf("1.3.0", { failure: null, failureDismissed: false })).toBe(false);
    });
  });
});
