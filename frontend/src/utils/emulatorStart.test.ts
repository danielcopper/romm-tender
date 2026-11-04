import { beforeEach, describe, expect, it, vi } from "vitest";
import type { EmulatorOption, EmulatorStart, GameLaunch } from "../types";

vi.mock("../api/backend", () => ({
  getPlatformCoreInfo: vi.fn(),
  logError: vi.fn(),
}));

import { getPlatformCoreInfo } from "../api/backend";
import {
  canPickAnother,
  commandRefusalLine,
  emulatorRefusalForRom,
  emulatorStartRefusal,
  frontendChoiceLines,
  frontendChoiceSentence,
  gameCanPickAnother,
  isChoosable,
  startRefusal,
  startRefused,
  startRemarkSentences,
  vanishedPickSentence,
} from "./emulatorStart";
import { coreInfo } from "../test-utils/coreFixtures";

function start(overrides: Partial<EmulatorStart>): EmulatorStart {
  return { state: "starts", reason: null, entry: "mGBA", ...overrides };
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
      "not established",
      start({ state: "choose", reason: "no_find_rule", entry: "BlastEm" }),
      "No emulator is the default, because whether BlastEm is installed could not be established (ES-DE has no rule for finding it).",
    ],
  ])("points at no menu where no entry can be pinned, for an emulator %s", (_shape, answer, sentence) => {
    expect(emulatorStartRefusal(answer, "this game", false)).toBe(sentence);
  });
});

describe("whether another entry can be pinned", () => {
  it("is so only for an entry not stated not installed, other than the one in effect", () => {
    expect(canPickAnother([row("A"), row("B")], "A")).toBe(true);
    expect(canPickAnother([row("A"), row("B", { reason: "not_installed" })], "A")).toBe(false);
    expect(canPickAnother([row("A")], "A")).toBe(false);
    expect(canPickAnother([row("A")], null)).toBe(true);
  });

  it("counts an entry whose installation could not be established, but never one the resolver states not installed (#2195 D9, D23)", () => {
    expect(isChoosable(row("B", { reason: "no_find_rule" }))).toBe(true);
    expect(isChoosable(row("B", { reason: "not_installed" }))).toBe(false);
    expect(canPickAnother([row("A"), row("B", { reason: "no_find_rule" })], "A")).toBe(true);
    expect(canPickAnother([row("A"), row("B", { reason: "not_installed" })], "A")).toBe(false);
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
      emulators: [row("Ryubing (Standalone)", {}), row("Ryujinx (Standalone)")],
      active_core_label: "Ryubing (Standalone)",
    } as Awaited<ReturnType<typeof getPlatformCoreInfo>>);
    expect(await emulatorRefusalForRom(7, "test")).toBe(
      "Ryubing (Standalone) is not installed, so this game cannot start. Choose another emulator in the emulator menu.",
    );
  });

  it("points at no menu where the game's page offers none", async () => {
    vi.mocked(getPlatformCoreInfo).mockResolvedValue({
      start: start({ state: "cannot_start", reason: "not_installed", entry: "Ryubing (Standalone)" }),
      emulators: [row("Ryubing (Standalone)", {})],
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
          platform_name: "Sega Dreamcast",
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
      "track03.bin is not the file Sega Dreamcast starts from; it starts from .cue, .gdi.",
    );
  });
});

describe("an entry the resolver gives no complete command for (#2221 D2, D20)", () => {
  const answer = start({ state: "cannot_start", reason: "desktop_file", entry: "RPCS3 Shortcut (Standalone)" });

  it("refuses the start, saying Tender cannot start it yet and why", () => {
    expect(startRefused(answer)).toBe(true);
    expect(emulatorStartRefusal(answer, "this game", false)).toBe(
      "Tender cannot start RPCS3 Shortcut (Standalone) yet: it starts through a .desktop file.",
    );
  });

  it("points at the emulator menu where it offers another entry", () => {
    expect(emulatorStartRefusal(answer, "this game", true)).toBe(
      "Tender cannot start RPCS3 Shortcut (Standalone) yet: it starts through a .desktop file. Choose another emulator in the emulator menu.",
    );
  });

  it("refuses Steam's own Play with the same words", async () => {
    vi.mocked(getPlatformCoreInfo).mockResolvedValue({
      start: answer,
      emulators: [row("RPCS3 Shortcut (Standalone)")],
      active_core_label: "RPCS3 Shortcut (Standalone)",
    } as Awaited<ReturnType<typeof getPlatformCoreInfo>>);
    expect(await emulatorRefusalForRom(7, "test")).toBe(
      "Tender cannot start RPCS3 Shortcut (Standalone) yet: it starts through a .desktop file.",
    );
  });

  it.each([
    ["command_unread", "how ES-DE starts it could not be read"],
    ["desktop_file", "it starts through a .desktop file"],
    ["injected_file_unread", "a file its command reads in could not be read"],
    ["no_program", "its command names no program"],
    ["rom_root_unknown", "where its games are kept could not be established"],
    ["command_too_long", "its command is longer than can be read"],
    ["frontend_build_unread", "which ES-DE build the source ships could not be read"],
    ["environment_unread", "what it needs from RetroDECK's start could not be read"],
    ["environment_unknown", "what it needs from RetroDECK's start is not known yet"],
    ["folder_unplaced", "the folder it starts in could not be placed"],
  ])("words %s as the owner signed it", (reason, why) => {
    expect(emulatorStartRefusal(start({ state: "cannot_start", reason, entry: "Dolphin" }), "this game", false)).toBe(
      `Tender cannot start Dolphin yet: ${why}.`,
    );
    expect(commandRefusalLine(reason)).toBe(`Tender cannot start it yet: ${why}`);
  });

  it("says a label the resolver does not know is no longer there", () => {
    expect(
      emulatorStartRefusal(
        start({ state: "cannot_start", reason: "entry_unknown", entry: "Gone" }),
        "this game",
        false,
      ),
    ).toBe("Your choice 'Gone' no longer exists in its emulator source.");
    expect(commandRefusalLine("entry_unknown")).toBe("no longer exists in its emulator source");
  });
});

describe("a game no emulator starts (#2221 D20)", () => {
  it("is refused, saying so", () => {
    expect(startRefusal(start({ state: "no_entry", entry: null }), null, true)).toBe("No emulator starts this game.");
  });
});

describe("what is said beside a start that goes ahead (#2708 Q3)", () => {
  it("names a placeholder ES-DE leaves unfilled", () => {
    expect(startRemarkSentences([{ reason: "placeholder_unfilled", placeholder: "%RPCS3_GAMEID%" }])).toEqual([
      "ES-DE leaves %RPCS3_GAMEID% unfilled in this command.",
    ]);
  });

  it("says nothing where there is nothing to say", () => {
    expect(startRemarkSentences(undefined)).toEqual([]);
  });
});

describe("which refusal a game's page says (#2221 D20, #2698 D8)", () => {
  const unread = start({ state: "cannot_start", reason: "desktop_file", entry: "RPCS3 Shortcut (Standalone)" });
  const folder: GameLaunch = {
    state: "not_this_emulator",
    entry: "RPCS3 Shortcut (Standalone)",
    system: "ps3",
    platform_name: "PlayStation 3",
    file: "Game.ps3",
    extension: ".ps3",
    starts_from: [],
    other_entries: ["RPCS3 Directory (Standalone)"],
    install_in: null,
    reason: "takes_no_folder",
    contents_unread: false,
    format_unestablished: false,
  };

  it("says both where the resolver refuses the file and gives no complete command, the file's first", () => {
    expect(startRefusal(unread, folder, false)).toBe(
      "RPCS3 Shortcut (Standalone) cannot start a game installed as a folder. Tender cannot start RPCS3 Shortcut (Standalone) yet: it starts through a .desktop file.",
    );
  });

  it("says Tender cannot start the entry yet where the resolver refuses nothing", () => {
    expect(startRefusal(unread, { ...folder, state: "starts", reason: null }, false)).toBe(
      "Tender cannot start RPCS3 Shortcut (Standalone) yet: it starts through a .desktop file.",
    );
    expect(startRefusal(unread, null, false)).toBe(
      "Tender cannot start RPCS3 Shortcut (Standalone) yet: it starts through a .desktop file.",
    );
  });

  it("keeps an emulator that is not installed ahead of the file's refusal", () => {
    const missing = start({ state: "cannot_start", reason: "not_installed", entry: "RPCS3 Shortcut (Standalone)" });
    expect(startRefusal(missing, folder, false)).toBe(
      "RPCS3 Shortcut (Standalone) is not installed, so this game cannot start.",
    );
  });

  it("refuses Steam's own Play with both sentences too", async () => {
    vi.mocked(getPlatformCoreInfo).mockResolvedValue({
      start: unread,
      launch: folder,
      emulators: [row("RPCS3 Shortcut (Standalone)")],
      active_core_label: "RPCS3 Shortcut (Standalone)",
    } as Awaited<ReturnType<typeof getPlatformCoreInfo>>);
    expect(await emulatorRefusalForRom(7, "test")).toBe(
      "RPCS3 Shortcut (Standalone) cannot start a game installed as a folder. Tender cannot start RPCS3 Shortcut (Standalone) yet: it starts through a .desktop file.",
    );
  });
});
