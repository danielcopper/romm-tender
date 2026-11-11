import { describe, it, expect, vi, beforeEach } from "vitest";
import { logFailure, logRefusal } from "./backendLog";
import * as backend from "../api/backend";

vi.mock("../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/backend")>()),
  logWarn: vi.fn(),
  logError: vi.fn(),
}));

describe("backendLog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("logs a refusal with its reason and message, or 'no reason' when none was given", () => {
    logRefusal("Tag", "remove", 42, { success: false, reason: "in_progress", message: "Busy" });
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "Tag: the backend refused to remove romId=42: in_progress: Busy",
    );

    vi.clearAllMocks();
    logRefusal("Tag", "remove", 42, { success: false, message: "Read-only" });
    expect(backend.logWarn).toHaveBeenCalledExactlyOnceWith(
      "Tag: the backend refused to remove romId=42: no reason: Read-only",
    );
  });

  it("logs a failure with what was asked, the ROM and the error", () => {
    logFailure("Tag", "remove", 42, new Error("socket closed"));
    expect(backend.logError).toHaveBeenCalledExactlyOnceWith("Tag: could not remove romId=42: Error: socket closed");
  });
});
