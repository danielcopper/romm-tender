import { describe, it, expect, vi, beforeEach } from "vitest";
import { render } from "@testing-library/react";
import { createElement, type ComponentProps } from "react";
import { buildPanelTabs } from "./panelTabContent";
import type { PanelState } from "./panelState";
import type { SaveSyncAvailability } from "../types";
import type { SavesTab } from "./SavesTab";
import type { AchievementsLoad } from "../utils/achievements";
import type { BiosTabState } from "./BiosTab";

const capturedSavesTab: ComponentProps<typeof SavesTab>[] = [];

vi.mock("./SlotSetupWizard", () => ({
  SlotSetupWizard: () => createElement("div", { "data-testid": "slot-setup-wizard" }),
}));

vi.mock("./SavesTab", () => ({
  SavesTab: (props: ComponentProps<typeof SavesTab>) => {
    capturedSavesTab.push(props);
    return createElement("div", { "data-testid": "saves-tab" });
  },
}));

// The sentence a refusal carries is the backend's; these tests only pass it through.
const REFUSAL = "a refusal's sentence, as the backend words it";

function state(overrides: Partial<PanelState> = {}): PanelState {
  return {
    loading: false,
    romId: 7,
    romName: "Game",
    platformName: "SNES",
    installed: true,
    installedRom: null,
    metadata: null,
    coverBase64: null,
    biosStatus: null,
    biosLevel: null,
    biosReadFailed: false,
    coreInfo: null,
    saveSyncEnabled: true,
    saveSyncAnswer: null,
    saveStatus: null,
    conflicts: [],
    error: false,
    activeTab: "saves",
    raId: null,
    slotConfirmed: true,
    activeSlot: "default",
    activeSlotKnown: true,
    availableSlots: [],
    lastKnownSlots: null,
    slotsLoading: false,
    regions: [],
    languages: [],
    ...overrides,
  };
}

const syncs = (overrides: Partial<SaveSyncAvailability> = {}): SaveSyncAvailability => ({
  available: true,
  installed: true,
  established: true,
  emulator_label: "mGBA",
  ...overrides,
});

const refuses = (
  reason: NonNullable<SaveSyncAvailability["reason"]>,
  overrides: Partial<SaveSyncAvailability> = {},
): SaveSyncAvailability => ({
  available: false,
  installed: true,
  established: true,
  emulator_label: "mGBA",
  reason,
  message: REFUSAL,
  ...overrides,
});

function savesTabOf(panel: PanelState) {
  const tabs = buildPanelTabs({
    appId: 1,
    binding: { romId: 7, write: vi.fn() },
    state: panel,
    achievements: {} as AchievementsLoad,
    bios: {} as BiosTabState,
  });
  const saves = tabs.find((tab) => tab.id === "saves");
  if (saves === undefined) throw new Error("no SAVES tab");
  return saves;
}

/** The mark beside the SAVES tab's title, rendered as Steam renders a tab's addon. */
function markOf(panel: PanelState): Element | null {
  const addon = savesTabOf(panel).renderTabAddon?.();
  if (addon === undefined) return null;
  return render(createElement("div", null, addon)).container.querySelector('[data-testid="saves-tab-warning"]');
}

function paneOf(panel: PanelState) {
  return render(createElement("div", null, savesTabOf(panel).content));
}

describe("the SAVES tab's mark (#2149 D1)", () => {
  it.each(["save_shape_unsupported", "savefiles_in_content_dir", "saves_root_missing"] as const)(
    "marks a game whose saves cannot sync, refused with %s",
    (reason) => {
      expect(markOf(state({ saveSyncAnswer: refuses(reason) }))).not.toBeNull();
    },
  );

  it("marks nothing while the answer is loading", () => {
    expect(savesTabOf(state({ saveSyncAnswer: null })).renderTabAddon).toBeUndefined();
  });

  it("marks nothing for a game whose saves sync", () => {
    expect(savesTabOf(state({ saveSyncAnswer: syncs() })).renderTabAddon).toBeUndefined();
  });

  it("marks nothing for a game that is not downloaded, even where its saves would not sync", () => {
    const answer = refuses("save_shape_unsupported", { installed: false });
    expect(savesTabOf(state({ saveSyncAnswer: answer })).renderTabAddon).toBeUndefined();
  });

  it("hides the mark from a screen reader, which reads the reason on the tab", () => {
    expect(markOf(state({ saveSyncAnswer: refuses("save_shape_unsupported") }))?.getAttribute("aria-hidden")).toBe(
      "true",
    );
  });
});

describe("the SAVES pane of a game whose saves cannot sync (#2149 D2, D5)", () => {
  beforeEach(() => {
    capturedSavesTab.length = 0;
  });

  it("shows the refusal's sentence and no slots for a game set up earlier", () => {
    const { container, queryByTestId } = paneOf(
      state({ slotConfirmed: true, saveSyncAnswer: refuses("save_shape_unsupported") }),
    );

    expect(container.querySelector(".romm-panel-section-title")?.textContent).toBe("Save Sync");
    expect(container.textContent).toContain(REFUSAL);
    expect(queryByTestId("saves-tab")).toBeNull();
    expect(queryByTestId("slot-setup-wizard")).toBeNull();
  });

  it("shows the refusal's sentence and no setup for a game never set up", () => {
    const { container, queryByTestId } = paneOf(
      state({ slotConfirmed: false, saveSyncAnswer: refuses("saves_root_missing") }),
    );

    expect(container.textContent).toContain(REFUSAL);
    expect(queryByTestId("slot-setup-wizard")).toBeNull();
  });

  it("stands RetroArch's setting under the sentence of a game whose saves are written beside the game file", () => {
    const { getByTestId } = paneOf(state({ saveSyncAnswer: refuses("savefiles_in_content_dir") }));

    expect(getByTestId("save-sync-refusal-hint").textContent).toContain("Write Saves to Content Directory");
    expect(getByTestId("save-sync-refusal-hint").previousElementSibling?.textContent).toBe(REFUSAL);
  });

  it("names RetroArch's setting for no other reason", () => {
    const { queryByTestId } = paneOf(state({ saveSyncAnswer: refuses("save_shape_unsupported") }));

    expect(queryByTestId("save-sync-refusal-hint")).toBeNull();
  });
});

describe("the SAVES pane of a game whose saves sync (#2149 D3)", () => {
  beforeEach(() => {
    capturedSavesTab.length = 0;
  });

  it("hands the slots the line naming the entry the game starts with", () => {
    paneOf(state({ saveSyncAnswer: syncs({ emulator_label: "mGBA" }) }));

    expect(capturedSavesTab[capturedSavesTab.length - 1]?.noSaveLine).toBe(
      "No save yet — mGBA's saves for this game sync with RomM once it writes one.",
    );
  });

  it("shows the slots, unmarked, while the answer is loading", () => {
    const { queryByTestId } = paneOf(state({ saveSyncAnswer: null }));

    expect(queryByTestId("saves-tab")).not.toBeNull();
    expect(capturedSavesTab[capturedSavesTab.length - 1]?.noSaveLine).toBe("No save yet.");
  });
});

describe("the SAVES pane of a game that is not downloaded (#2149 D4)", () => {
  it("says whose saves will sync once it is, above the slot setup as it stands", () => {
    const { getByTestId, queryByTestId } = paneOf(
      state({ slotConfirmed: false, saveSyncAnswer: syncs({ installed: false, emulator_label: "mGBA" }) }),
    );

    expect(getByTestId("saves-not-downloaded").textContent).toBe(
      "Not downloaded. Once it is, mGBA's saves for this game sync with RomM.",
    );
    expect(queryByTestId("slot-setup-wizard")).not.toBeNull();
  });

  it("says its saves will not sync, and why, above the slot setup as it stands", () => {
    const { getByTestId, queryByTestId } = paneOf(
      state({ slotConfirmed: false, saveSyncAnswer: refuses("save_shape_unsupported", { installed: false }) }),
    );

    expect(getByTestId("saves-not-downloaded").textContent).toBe(
      `Not downloaded. Once it is, its saves will not sync:${REFUSAL}`,
    );
    expect(queryByTestId("slot-setup-wizard")).not.toBeNull();
  });

  it("stands RetroArch's setting under the sentence where the saves would be written beside the game file", () => {
    const { getByTestId } = paneOf(
      state({ saveSyncAnswer: refuses("savefiles_in_content_dir", { installed: false }) }),
    );

    expect(getByTestId("saves-not-downloaded").textContent).toContain("Write Saves to Content Directory");
  });

  it("says it could not be established where the reading established nothing (#2149 D9)", () => {
    const { getByTestId, queryByTestId } = paneOf(
      state({
        slotConfirmed: false,
        saveSyncAnswer: refuses("save_shape_unsupported", { installed: false, established: false }),
      }),
    );

    expect(getByTestId("saves-not-downloaded").textContent).toBe(
      "Not downloaded. Whether its saves will sync could not be established.",
    );
    expect(queryByTestId("slot-setup-wizard")).not.toBeNull();
  });

  it("says nothing of it for a game that is downloaded", () => {
    const { queryByTestId } = paneOf(state({ saveSyncAnswer: syncs() }));

    expect(queryByTestId("saves-not-downloaded")).toBeNull();
  });
});
