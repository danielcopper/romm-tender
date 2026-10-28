import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EmulatorOption, EmulatorStart } from "../types";

vi.mock("../api/backend", () => ({
  getPlatformCoreInfo: vi.fn(),
  logError: vi.fn(),
}));

import { getPlatformCoreInfo } from "../api/backend";
import {
  emulatorRefusalForRom,
  emulatorStartRefusal,
  frontendChoiceSentence,
  sourceDefaultSentence,
  startRefused,
  vanishedPickSentence,
} from "./emulatorStart";

function start(overrides: Partial<EmulatorStart>): EmulatorStart {
  return { state: "starts", reason: null, entry: "mGBA", folder_entry: null, ...overrides };
}

function row(label: string, overrides: Partial<EmulatorOption> = {}): EmulatorOption {
  return {
    label,
    kind: "core",
    core_so: null,
    emulator: null,
    declared_index: 0,
    is_default: false,
    chosen_in_frontend: false,
    bakeable: true,
    reason: null,
    ...overrides,
  };
}

describe("why a start is refused", () => {
  it("says nothing where the emulator starts the game", () => {
    expect(emulatorStartRefusal(start({}), "this game")).toBeNull();
    expect(startRefused(start({}))).toBe(false);
  });

  it("says nothing where there is no entry at all, which other lines say", () => {
    expect(emulatorStartRefusal(start({ state: "no_entry", entry: null }), "this game")).toBeNull();
  });

  it("names an emulator that is not installed and points at the menu", () => {
    const words = emulatorStartRefusal(
      start({ state: "cannot_start", reason: "not_installed", entry: "Ryubing (Standalone)" }),
      "this game",
    );
    expect(words).toBe(
      "Ryubing (Standalone) is not installed, so this game cannot start. Choose another emulator in the emulator menu.",
    );
  });

  it("names an emulator RetroDECK's start script runs nothing for", () => {
    const words = emulatorStartRefusal(
      start({ state: "cannot_start", reason: "start_script_runs_nothing", entry: "PICO-8 (Standalone)" }),
      "these games",
    );
    expect(words).toBe(
      "RetroDECK's start script finds no emulator for PICO-8 (Standalone), so these games cannot start. Choose another emulator in the emulator menu.",
    );
  });

  it("names the entry that starts a folder where the chosen one cannot", () => {
    const words = emulatorStartRefusal(
      start({
        state: "cannot_start",
        reason: "folder_install",
        entry: "RPCS3 Shortcut (Standalone)",
        folder_entry: "RPCS3 Directory (Standalone)",
      }),
      "this game",
    );
    expect(words).toBe(
      "RPCS3 Shortcut (Standalone) cannot start a game installed as a folder. RPCS3 Directory (Standalone) can — choose it in the emulator menu.",
    );
  });

  it("asks for a choice where the default's installation could not be established, with its cause", () => {
    const answer = start({ state: "choose", reason: "no_find_rule", entry: "BlastEm" });
    expect(emulatorStartRefusal(answer, "this game")).toBe(
      "No emulator is the default, because whether BlastEm is installed could not be established (ES-DE has no rule for finding it). Choose an emulator in the emulator menu.",
    );
    expect(startRefused(answer)).toBe(true);
  });
});

describe("a pick whose name is gone", () => {
  it("names the pick, its source and the default used instead", () => {
    expect(vanishedPickSentence({ label: "Gone", source: "retrodeck", layer: "game", used: "mGBA" })).toBe(
      "Your choice 'Gone' no longer exists in RetroDECK; the default 'mGBA' is used.",
    );
  });

  it("says so where there is no default to use", () => {
    expect(vanishedPickSentence({ label: "Gone", source: "retrodeck", layer: "platform", used: null })).toBe(
      "Your choice 'Gone' no longer exists in RetroDECK; there is no default to use instead.",
    );
  });
});

describe("the choice made in ES-DE", () => {
  it("is said where it is not the emulator Tender starts the game with", () => {
    const emulators = [row("Gearsystem", { chosen_in_frontend: true }), row("Genesis Plus GX", { is_default: true })];
    expect(frontendChoiceSentence(emulators, "Genesis Plus GX")).toBe(
      "Gearsystem is chosen for this game in ES-DE; Tender starts it with its own choice, set in the emulator menu.",
    );
  });

  it("is not said where Tender starts the game with it, or where there is none", () => {
    expect(frontendChoiceSentence([row("Gearsystem", { chosen_in_frontend: true })], "Gearsystem")).toBeNull();
    expect(frontendChoiceSentence([row("Genesis Plus GX")], "Genesis Plus GX")).toBeNull();
  });
});

describe("each source's own default", () => {
  it("names the default and the ES-DE choice beside it", () => {
    expect(
      sourceDefaultSentence({
        kind: "retrodeck",
        default: "Genesis Plus GX",
        chosen_in_frontend: "Gearsystem",
        reason: null,
      }),
    ).toBe("RetroDECK's default: Genesis Plus GX (Gearsystem chosen in ES-DE).");
  });

  it("says a source lists no emulator, or that its default could not be established", () => {
    expect(
      sourceDefaultSentence({ kind: "emudeck", default: null, chosen_in_frontend: null, reason: "no_entry" }),
    ).toBe("EmuDeck lists no emulator for this platform.");
    expect(
      sourceDefaultSentence({ kind: "emudeck", default: null, chosen_in_frontend: null, reason: "path_unseen" }),
    ).toBe("EmuDeck's default could not be established: ES-DE looks for it where Tender cannot see.");
  });

  it("says why a source's list could not be read", () => {
    const words = sourceDefaultSentence({ kind: "emudeck", default: null, chosen_in_frontend: null, reason: "sealed" });
    expect(words.startsWith("EmuDeck: ")).toBe(true);
  });
});

describe("a start's own read of the emulator", () => {
  beforeEach(() => {
    vi.mocked(getPlatformCoreInfo).mockReset();
  });

  it("answers the page's sentence where the game cannot start", async () => {
    vi.mocked(getPlatformCoreInfo).mockResolvedValue({
      start: start({ state: "cannot_start", reason: "not_installed", entry: "Ryubing (Standalone)" }),
    } as Awaited<ReturnType<typeof getPlatformCoreInfo>>);
    expect(await emulatorRefusalForRom(7, "test")).toContain("Ryubing (Standalone) is not installed");
  });

  it("refuses nothing where the read fails", async () => {
    vi.mocked(getPlatformCoreInfo).mockRejectedValue(new Error("down"));
    expect(await emulatorRefusalForRom(7, "test")).toBeNull();
  });
});
