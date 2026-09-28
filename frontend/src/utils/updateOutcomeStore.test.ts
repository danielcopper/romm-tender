import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { toaster } from "../api/host";
import {
  acknowledgeUpdateAnnouncement,
  dismissUpdateFailure,
  getUpdateOutcome,
  logWarn,
  type UpdateOutcome,
} from "../api/backend";
import { TOAST_READINESS_DEADLINE_MS, TOAST_READINESS_POLL_MS } from "./steamReadyForToasts";
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

const NOTHING: UpdateOutcome = {
  announce_version: null,
  announce_direction: null,
  failure: null,
  failure_dismissed: false,
};
const UPDATED: UpdateOutcome = { ...NOTHING, announce_version: "1.3.0", announce_direction: "updated" };
const BACK: UpdateOutcome = { ...NOTHING, announce_version: "1.2.3", announce_direction: "back" };

const ROLLED_BACK_WIRE: UpdateOutcome = {
  announce_version: null,
  announce_direction: null,
  failure: { attempted_version: "1.3.0", restored_version: "1.2.3", rolled_back_at: "2026-09-25T10:15:00Z" },
  failure_dismissed: false,
};

const ROLLED_BACK: UpdateOutcomeState = {
  failure: { attemptedVersion: "1.3.0", restoredVersion: "1.2.3", rolledBackAt: "2026-09-25T10:15:00Z" },
  failureDismissed: false,
};

vi.mock("../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/backend")>()),
  logWarn: vi.fn(),
}));

/** Steam as the toast's readiness wait reads it; a test changes a field to move it along. */
interface SteamState {
  services: boolean;
  locked: boolean;
  bigPicture: "none" | "hidden" | "visible";
}

function stubSteam(state: SteamState): void {
  vi.stubGlobal("App", { GetServicesInitialized: () => state.services });
  vi.stubGlobal("securitystore", { IsLockScreenActive: () => state.locked });
  vi.stubGlobal("SteamUIStore", {
    WindowStore: {
      get GamepadUIMainWindowInstance() {
        if (state.bigPicture === "none") return null;
        return { BrowserWindow: { document: { visibilityState: state.bigPicture } } };
      },
    },
  });
}

/** A promise the test resolves by hand, to hold a call in flight. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

describe("updateOutcomeStore", () => {
  let steam: SteamState;

  beforeEach(() => {
    steam = { services: true, locked: false, bigPicture: "visible" };
    stubSteam(steam);
    vi.mocked(logWarn).mockClear();
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

  describe("a version that moved", () => {
    it("to a later release is announced as an update in one toast, then acknowledged", async () => {
      vi.mocked(getUpdateOutcome).mockResolvedValue(UPDATED);

      await fetchUpdateOutcome();

      expect(toaster.toast).toHaveBeenCalledTimes(1);
      expect(toaster.toast).toHaveBeenCalledWith({ title: "Tender", body: "Tender updated to 1.3.0" });
      expect(acknowledgeUpdateAnnouncement).toHaveBeenCalledTimes(1);
      expect(vi.mocked(toaster.toast).mock.invocationCallOrder[0]).toBeLessThan(
        vi.mocked(acknowledgeUpdateAnnouncement).mock.invocationCallOrder[0]!,
      );
    });

    it("back to an earlier release is announced as being back on it", async () => {
      vi.mocked(getUpdateOutcome).mockResolvedValue(BACK);

      await fetchUpdateOutcome();

      expect(toaster.toast).toHaveBeenCalledTimes(1);
      expect(toaster.toast).toHaveBeenCalledWith({ title: "Tender", body: "Tender is back on 1.2.3" });
      expect(acknowledgeUpdateAnnouncement).toHaveBeenCalledTimes(1);
    });

    it("nothing owed raises no toast and acknowledges nothing", async () => {
      vi.mocked(getUpdateOutcome).mockResolvedValue(NOTHING);

      await fetchUpdateOutcome();

      expect(toaster.toast).not.toHaveBeenCalled();
      expect(acknowledgeUpdateAnnouncement).not.toHaveBeenCalled();
    });

    it("an acknowledgement that failed rejects, so the caller's log names it", async () => {
      vi.mocked(getUpdateOutcome).mockResolvedValue(UPDATED);
      vi.mocked(acknowledgeUpdateAnnouncement).mockRejectedValue(new Error("socket closed"));

      await expect(fetchUpdateOutcome()).rejects.toThrow("socket closed");
      expect(toaster.toast).toHaveBeenCalledTimes(1);
    });
  });

  describe("the announcement waits until Steam can show it", () => {
    beforeEach(() => {
      vi.useFakeTimers();
      vi.mocked(getUpdateOutcome).mockResolvedValue(UPDATED);
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it.each<[string, Partial<SteamState>]>([
      ["Steam's services are not initialised", { services: false }],
      ["the lock screen is up", { locked: true }],
      ["Big Picture's window is hidden", { bigPicture: "hidden" }],
    ])("is not raised while %s, and is raised, then acknowledged, once it can be", async (_case, notYet) => {
      Object.assign(steam, notYet);
      const pending = fetchUpdateOutcome();
      await vi.advanceTimersByTimeAsync(TOAST_READINESS_POLL_MS * 8);

      expect(toaster.toast).not.toHaveBeenCalled();
      expect(acknowledgeUpdateAnnouncement).not.toHaveBeenCalled();

      Object.assign(steam, { services: true, locked: false, bigPicture: "visible" });
      await vi.advanceTimersByTimeAsync(TOAST_READINESS_POLL_MS);
      await pending;

      expect(toaster.toast).toHaveBeenCalledTimes(1);
      expect(toaster.toast).toHaveBeenCalledWith({ title: "Tender", body: "Tender updated to 1.3.0" });
      expect(vi.mocked(toaster.toast).mock.invocationCallOrder[0]).toBeLessThan(
        vi.mocked(acknowledgeUpdateAnnouncement).mock.invocationCallOrder[0]!,
      );
      expect(logWarn).not.toHaveBeenCalled();
    });

    it("is raised at the deadline if Steam never gets there, and the log says what it was still waiting for", async () => {
      steam.services = false;
      const pending = fetchUpdateOutcome();
      await vi.advanceTimersByTimeAsync(TOAST_READINESS_DEADLINE_MS - 1);
      expect(toaster.toast).not.toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(TOAST_READINESS_POLL_MS);
      await pending;

      expect(toaster.toast).toHaveBeenCalledTimes(1);
      expect(acknowledgeUpdateAnnouncement).toHaveBeenCalledTimes(1);
      expect(logWarn).toHaveBeenCalledWith(
        "Steam was not ready for a toast after 30 s (still waiting for services_initialized); raising the update announcement anyway",
      );
    });

    it("fills the store before it waits, so the rolled-back card is not held back by a toast", async () => {
      steam.services = false;
      vi.mocked(getUpdateOutcome).mockResolvedValue({ ...UPDATED, failure: ROLLED_BACK_WIRE.failure });
      const pending = fetchUpdateOutcome();
      await vi.advanceTimersByTimeAsync(0);

      expect(getUpdateOutcomeState()).toEqual(ROLLED_BACK);
      expect(toaster.toast).not.toHaveBeenCalled();

      await vi.advanceTimersByTimeAsync(TOAST_READINESS_DEADLINE_MS + TOAST_READINESS_POLL_MS);
      await pending;
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
