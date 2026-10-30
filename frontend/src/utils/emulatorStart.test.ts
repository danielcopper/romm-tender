import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EmulatorOption, EmulatorStart } from "../types";

vi.mock("../api/backend", () => ({
  getPlatformCoreInfo: vi.fn(),
  logError: vi.fn(),
}));

import { getPlatformCoreInfo } from "../api/backend";
import {
  canPickAnother,
  emulatorRefusalForRom,
  emulatorStartRefusal,
  frontendChoiceLines,
  frontendChoiceSentence,
  gameCanPickAnother,
  startRefused,
  vanishedPickSentence,
} from "./emulatorStart";
import { coreInfo } from "../test-utils/coreFixtures";

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
    expect(emulatorStartRefusal(start({}), "this game", true)).toBeNull();
    expect(startRefused(start({}))).toBe(false);
  });

  it("says nothing where there is no entry at all, which other lines say", () => {
    expect(emulatorStartRefusal(start({ state: "no_entry", entry: null }), "this game", true)).toBeNull();
  });

  it("names an emulator that is not installed and points at the menu", () => {
    const words = emulatorStartRefusal(
      start({ state: "cannot_start", reason: "not_installed", entry: "Ryubing (Standalone)" }),
      "this game",
      true,
    );
    expect(words).toBe(
      "Ryubing (Standalone) is not installed, so this game cannot start. Choose another emulator in the emulator menu.",
    );
  });

  it("names an emulator RetroDECK's start script cannot start", () => {
    const words = emulatorStartRefusal(
      start({ state: "cannot_start", reason: "start_script_runs_nothing", entry: "PICO-8 (Standalone)" }),
      "these games",
      true,
    );
    expect(words).toBe(
      "RetroDECK's start script cannot start PICO-8 (Standalone), so these games cannot start. Choose another emulator in the emulator menu.",
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
      true,
    );
    expect(words).toBe(
      "RPCS3 Shortcut (Standalone) cannot start a game installed as a folder. RPCS3 Directory (Standalone) can — choose it in the emulator menu.",
    );
  });

  it("asks for a choice where the default's installation could not be established, with its cause", () => {
    const answer = start({ state: "choose", reason: "no_find_rule", entry: "BlastEm" });
    expect(emulatorStartRefusal(answer, "this game", true)).toBe(
      "No emulator is the default, because whether BlastEm is installed could not be established (ES-DE has no rule for finding it). Choose an emulator in the emulator menu.",
    );
    expect(startRefused(answer)).toBe(true);
  });

  it.each([
    [
      "not installed",
      start({ state: "cannot_start", reason: "not_installed", entry: "LRPS2" }),
      "LRPS2 is not installed, so this game cannot start.",
    ],
    [
      "run by nothing",
      start({ state: "cannot_start", reason: "start_script_runs_nothing", entry: "PICO-8 (Standalone)" }),
      "RetroDECK's start script cannot start PICO-8 (Standalone), so this game cannot start.",
    ],
    [
      "unable to start a folder",
      start({ state: "cannot_start", reason: "folder_install", entry: "RPCS3 Shortcut (Standalone)" }),
      "RPCS3 Shortcut (Standalone) cannot start a game installed as a folder.",
    ],
    [
      "not established",
      start({ state: "choose", reason: "no_find_rule", entry: "BlastEm" }),
      "No emulator is the default, because whether BlastEm is installed could not be established (ES-DE has no rule for finding it).",
    ],
  ])("points at no menu where no entry can be pinned, for an emulator %s", (_shape, answer, sentence) => {
    expect(emulatorStartRefusal(answer, "this game", false)).toBe(sentence);
  });
});

describe("whether another entry can be pinned", () => {
  it("is so only for a bakeable entry other than the one in effect", () => {
    expect(canPickAnother([row("A"), row("B")], "A")).toBe(true);
    expect(canPickAnother([row("A"), row("B", { bakeable: false })], "A")).toBe(false);
    expect(canPickAnother([row("A")], "A")).toBe(false);
    expect(canPickAnother([row("A")], null)).toBe(true);
  });

  it("is never so on a game's page with one emulator, where the page offers no menu", () => {
    expect(gameCanPickAnother([row("A")], null)).toBe(false);
    expect(gameCanPickAnother([row("A"), row("B")], "A")).toBe(true);
  });
});

describe("a pick whose name is gone", () => {
  it("names the pick, its source and the default used instead", () => {
    expect(
      vanishedPickSentence({ label: "Gone", source: "retrodeck", layer: "game", used: "mGBA", used_layer: "default" }),
    ).toBe("Your choice 'Gone' no longer exists in RetroDECK; the default 'mGBA' is used.");
  });

  it("names the platform choice where that is what is used instead", () => {
    expect(
      vanishedPickSentence({ label: "Gone", source: "retrodeck", layer: "game", used: "mGBA", used_layer: "platform" }),
    ).toBe("Your choice 'Gone' no longer exists in RetroDECK; your platform choice 'mGBA' is used.");
  });

  it("says so where there is no default to use", () => {
    expect(
      vanishedPickSentence({ label: "Gone", source: "retrodeck", layer: "platform", used: null, used_layer: null }),
    ).toBe("Your choice 'Gone' no longer exists in RetroDECK; there is no default to use instead.");
  });
});

describe("the choice made in ES-DE", () => {
  it("is said where it is not the emulator Tender starts the game with", () => {
    const emulators = [row("Gearsystem", { chosen_in_frontend: true }), row("Genesis Plus GX", { is_default: true })];
    expect(frontendChoiceSentence(emulators, "Genesis Plus GX")).toBe(
      "Gearsystem is chosen in ES-DE; Tender starts this game with the emulator chosen in its emulator menu.",
    );
  });

  it("is not said where Tender starts the game with it, or where there is none", () => {
    expect(frontendChoiceSentence([row("Gearsystem", { chosen_in_frontend: true })], "Gearsystem")).toBeNull();
    expect(frontendChoiceSentence([row("Genesis Plus GX")], "Genesis Plus GX")).toBeNull();
  });
});

describe("the emulators chosen in ES-DE, on the platform page", () => {
  const source = (kind: string, chosen: string | null) => ({
    kind,
    default: null,
    chosen_in_frontend: chosen,
    reason: null,
  });

  it("says the choice alone where one source is listed", () => {
    expect(frontendChoiceLines([source("retrodeck", "Gearsystem")])).toEqual(["Gearsystem chosen in ES-DE"]);
  });

  it("names the source where more than one is listed, and leaves out a source with no choice", () => {
    expect(
      frontendChoiceLines([
        source("retrodeck", "Gearsystem"),
        source("emudeck", null),
        source("bare_retroarch_flatpak", "Genesis Plus GX"),
      ]),
    ).toEqual(["RetroDECK: Gearsystem chosen in ES-DE", "RetroArch (Flatpak): Genesis Plus GX chosen in ES-DE"]);
  });

  it("says nothing where no source has a choice", () => {
    expect(frontendChoiceLines([source("retrodeck", null)])).toEqual([]);
  });
});

describe("a start's own read of the emulator", () => {
  beforeEach(() => {
    vi.mocked(getPlatformCoreInfo).mockReset();
  });

  it("answers the page's sentence where the game cannot start", async () => {
    vi.mocked(getPlatformCoreInfo).mockResolvedValue({
      start: start({ state: "cannot_start", reason: "not_installed", entry: "Ryubing (Standalone)" }),
      emulators: [row("Ryubing (Standalone)", { bakeable: false }), row("Ryujinx (Standalone)")],
      active_core_label: "Ryubing (Standalone)",
    } as Awaited<ReturnType<typeof getPlatformCoreInfo>>);
    expect(await emulatorRefusalForRom(7, "test")).toBe(
      "Ryubing (Standalone) is not installed, so this game cannot start. Choose another emulator in the emulator menu.",
    );
  });

  it("points at no menu where the game's page offers none", async () => {
    vi.mocked(getPlatformCoreInfo).mockResolvedValue({
      start: start({ state: "cannot_start", reason: "not_installed", entry: "Ryubing (Standalone)" }),
      emulators: [row("Ryubing (Standalone)", { bakeable: false })],
      active_core_label: "Ryubing (Standalone)",
    } as Awaited<ReturnType<typeof getPlatformCoreInfo>>);
    expect(await emulatorRefusalForRom(7, "test")).toBe(
      "Ryubing (Standalone) is not installed, so this game cannot start.",
    );
  });

  it("refuses nothing where the read fails", async () => {
    vi.mocked(getPlatformCoreInfo).mockRejectedValue(new Error("down"));
    expect(await emulatorRefusalForRom(7, "test")).toBeNull();
  });

  it("refuses with the file's own sentence where the emulator starts but the file does not (#2195 D2)", async () => {
    vi.mocked(getPlatformCoreInfo).mockResolvedValue(
      coreInfo({
        start: start({ entry: "Flycast" }),
        emulators: [row("Flycast")],
        active_core_label: "Flycast",
        launch: {
          state: "not_a_launch_file",
          entry: "Flycast",
          system: "dreamcast",
          file: "track03.bin",
          extension: ".bin",
          starts_from: [".cue", ".gdi"],
          other_entries: [],
          install_in: null,
          reason: null,
          contents_unread: false,
          format_unestablished: false,
        },
      }),
    );
    expect(await emulatorRefusalForRom(7, "test")).toBe(
      "track03.bin is not the file dreamcast starts from; it starts from .cue, .gdi.",
    );
  });
});
