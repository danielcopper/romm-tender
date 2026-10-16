import { describe, it, expect, vi } from "vitest";
import { gamepadLaunchPrompts } from "./launchPrompts";
import { showCoreChangeModal } from "./CoreChangeModal";
import { showFallbackLaunchModal } from "./FallbackLaunchModal";
import { showOfflineDriftModal } from "./OfflineDriftModal";
import { handleConflicts } from "./SyncConflictModal";
import { showUncheckedSourceModal } from "./UncheckedSourceModal";
import type { SyncConflict } from "../types";

vi.mock("./CoreChangeModal", () => ({ showCoreChangeModal: vi.fn(() => Promise.resolve(true)) }));
vi.mock("./FallbackLaunchModal", () => ({ showFallbackLaunchModal: vi.fn(() => Promise.resolve(false)) }));
vi.mock("./OfflineDriftModal", () => ({ showOfflineDriftModal: vi.fn(() => Promise.resolve("retry")) }));
vi.mock("./SyncConflictModal", () => ({ handleConflicts: vi.fn(() => Promise.resolve("cancel")) }));
vi.mock("./UncheckedSourceModal", () => ({ showUncheckedSourceModal: vi.fn(() => Promise.resolve(true)) }));

describe("gamepadLaunchPrompts", () => {
  it("asks each question through its gamepad modal and answers with the modal's answer", async () => {
    const conflicts = [{ filename: "save.srm" } as SyncConflict];

    expect(await gamepadLaunchPrompts.confirmCoreChange("Old", "New")).toBe(true);
    expect(showCoreChangeModal).toHaveBeenCalledWith("Old", "New");

    expect(await gamepadLaunchPrompts.resolveConflicts(conflicts)).toBe("cancel");
    expect(handleConflicts).toHaveBeenCalledWith(conflicts);

    expect(await gamepadLaunchPrompts.askOfflineDrift()).toBe("retry");
    expect(showOfflineDriftModal).toHaveBeenCalledTimes(1);

    expect(await gamepadLaunchPrompts.confirmFallbackLaunch("Server said no")).toBe(false);
    expect(showFallbackLaunchModal).toHaveBeenCalledWith("Server said no");

    expect(await gamepadLaunchPrompts.confirmUncheckedSource()).toBe(true);
    expect(showUncheckedSourceModal).toHaveBeenCalledTimes(1);
  });
});
