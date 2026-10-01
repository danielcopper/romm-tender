import { describe, it, expect } from "vitest";
import { availableCardVersion } from "./updateAvailableView";
import type { UpdateNoticeState } from "./updateNoticeStore";
import type { UpdateOutcomeState } from "./updateOutcomeStore";

const AVAILABLE: UpdateNoticeState = {
  available: true,
  newer: true,
  latestVersion: "1.1.0",
  currentVersion: "1.0.0",
  enabled: true,
  installedProgram: true,
  toastOwed: true,
};

const NO_OUTCOME: UpdateOutcomeState = { announcement: null, failure: null, failureDismissed: false };

const failedTo = (attemptedVersion: string, failureDismissed = false): UpdateOutcomeState => ({
  announcement: null,
  failure: { attemptedVersion, restoredVersion: "1.0.0", rolledBackAt: "2026-09-29T10:00:00Z", kind: "rollback" },
  failureDismissed,
});

describe("availableCardVersion", () => {
  it("names the newer release that was not dismissed", () => {
    expect(availableCardVersion(AVAILABLE, NO_OUTCOME, null)).toBe("1.1.0");
  });

  it("shows nothing for a dismissed release, which the backend reports as not available", () => {
    expect(availableCardVersion({ ...AVAILABLE, available: false }, NO_OUTCOME, null)).toBeNull();
  });

  it("shows nothing where no newer release is out", () => {
    expect(
      availableCardVersion({ ...AVAILABLE, available: false, newer: false, latestVersion: "1.0.0" }, NO_OUTCOME, null),
    ).toBeNull();
  });

  it("shows nothing before the backend answered", () => {
    expect(availableCardVersion({ ...AVAILABLE, available: false, latestVersion: null }, NO_OUTCOME, null)).toBeNull();
  });

  it.each([false, true])(
    "gives way to the installer's record of a failed update to the same release (record dismissed: %s)",
    (dismissed) => {
      expect(availableCardVersion(AVAILABLE, failedTo("1.1.0", dismissed), null)).toBeNull();
    },
  );

  it("stands beside a record of a failed update to an earlier release", () => {
    expect(availableCardVersion(AVAILABLE, failedTo("1.0.5"), null)).toBe("1.1.0");
  });

  it("gives way to a stopped attempt at the same release while it stands", () => {
    expect(availableCardVersion(AVAILABLE, NO_OUTCOME, { attemptedVersion: "1.1.0", fromVersion: "1.0.0" })).toBeNull();
  });

  it("comes back once the stopped attempt is dismissed, and stands beside one at another release", () => {
    expect(availableCardVersion(AVAILABLE, NO_OUTCOME, null)).toBe("1.1.0");
    expect(availableCardVersion(AVAILABLE, NO_OUTCOME, { attemptedVersion: "1.0.5", fromVersion: "1.0.0" })).toBe(
      "1.1.0",
    );
  });
});
