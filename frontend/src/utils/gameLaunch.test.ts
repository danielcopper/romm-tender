import { describe, expect, it } from "vitest";
import type { AnsweringSource, GameLaunch } from "../types";
import { HostTransportError } from "../api/host";
import { entryLaunchLine, launchNote, launchRefusal, launchRefused, startCheckFailureSentence } from "./gameLaunch";
import { TimeoutError } from "./withTimeout";

function launch(overrides: Partial<GameLaunch> = {}): GameLaunch {
  return {
    state: "starts",
    entry: "RPCS3 Directory (Standalone)",
    system: "ps3",
    platform_name: "PlayStation 3",
    file: "Game.ps3",
    extension: ".ps3",
    starts_from: [],
    other_entries: [],
    install_in: null,
    reason: null,
    contents_unread: false,
    format_unestablished: false,
    ...overrides,
  };
}

const RETRODECK: AnsweringSource = { kind: "retrodeck", starts_games: true };
const EMUDECK: AnsweringSource = { kind: "emudeck", starts_games: false };

const PS3_PKG = launch({
  state: "needs_installing",
  file: "Puppeteer.pkg",
  extension: ".pkg",
  install_in: ["RPCS3 Game Serial (Standalone)"],
});
const DREAMCAST_TRACK = launch({
  state: "not_a_launch_file",
  entry: "Flycast",
  system: "dreamcast",
  platform_name: "Sega Dreamcast",
  file: "track03.bin",
  extension: ".bin",
  starts_from: [".chd", ".cue", ".gdi"],
});
const NOT_THIS_EMULATOR = launch({
  state: "not_this_emulator",
  entry: "Mupen64Plus-Next",
  system: "n64",
  file: "Game.n64",
  extension: ".n64",
  other_entries: ["ParaLLEl N64"],
});
const NOT_ESTABLISHED = launch({
  state: "not_established",
  file: "Game.sfc",
  reason: "system_unknown",
  system: "dc",
  platform_name: "Sega Dreamcast",
});

describe("each verdict has its own sentence (#2195 D2)", () => {
  it("a PS3 .pkg has to be installed in the emulator the resolver names", () => {
    expect(launchRefusal(PS3_PKG, true)).toBe("This game has to be installed in RPCS3 Game Serial (Standalone) first.");
  });

  it("several emulators that install it are named as alternatives", () => {
    expect(
      launchRefusal(
        launch({ ...PS3_PKG, install_in: ["RPCS3 Game Serial (Standalone)", "RPCS3 Directory (Standalone)"] }),
        true,
      ),
    ).toBe("This game has to be installed in RPCS3 Game Serial (Standalone) or RPCS3 Directory (Standalone) first.");
  });

  it("with nothing known to install it, the sentence names no emulator", () => {
    expect(launchRefusal(launch({ ...PS3_PKG, install_in: [] }), true)).toBe("This game has to be installed first.");
  });

  it("a single Dreamcast track is not the file the platform starts from, named as the game page names it (D36)", () => {
    expect(launchRefusal(DREAMCAST_TRACK, true)).toBe(
      "track03.bin is not the file Sega Dreamcast starts from; it starts from .chd, .cue, .gdi.",
    );
  });

  it("lists an extension ES-DE spells in both cases once, as the resolver spells it first", () => {
    const dreamcast = { ...DREAMCAST_TRACK, starts_from: [".cdi", ".CDI", ".chd", ".CHD", ".Cue", ".cue", ".CUE"] };
    expect(launchRefusal(dreamcast, true)).toBe(
      "track03.bin is not the file Sega Dreamcast starts from; it starts from .cdi, .chd, .Cue.",
    );
  });

  it("a file one emulator does not take points at the emulator choice", () => {
    expect(launchRefusal(NOT_THIS_EMULATOR, true)).toBe(
      "Mupen64Plus-Next does not start .n64; other emulators of this system do. Choose another emulator in the emulator menu.",
    );
  });

  it("points at the emulator menu only where it offers another entry", () => {
    expect(launchRefusal(NOT_THIS_EMULATOR, false)).toBe(
      "Mupen64Plus-Next does not start .n64; other emulators of this system do.",
    );
  });

  it("says no other emulator does where the resolver names none", () => {
    expect(launchRefusal(launch({ ...NOT_THIS_EMULATOR, other_entries: [] }), true)).toBe(
      "Mupen64Plus-Next does not start .n64.",
    );
  });

  it("an unestablished file says why, and lets the game start", () => {
    expect(launchRefusal(NOT_ESTABLISHED, true)).toBeNull();
    expect(launchNote(NOT_ESTABLISHED, RETRODECK)).toBe(
      "Whether Game.sfc starts cannot be established: RetroDECK does not list the system Sega Dreamcast.",
    );
  });

  it.each([
    ["entry_unknown", "RetroDECK has no emulator named RPCS3 Directory (Standalone)"],
    ["catalogue_unread", "RetroDECK's list of emulators could not be read"],
    ["no_source", "no emulator source is installed and switched on"],
    [null, "RetroDECK gave no answer"],
  ] as const)("an unestablished verdict for %s names the source as the user knows it (D36)", (reason, words) => {
    expect(launchNote(launch({ state: "not_established", reason }), RETRODECK)).toBe(
      `Whether Game.ps3 starts cannot be established: ${words}.`,
    );
  });

  it("names the source that answered, whichever it is", () => {
    expect(launchNote(launch({ state: "not_established", reason: "catalogue_unread" }), EMUDECK)).toBe(
      "Whether Game.ps3 starts cannot be established: EmuDeck's list of emulators could not be read.",
    );
  });

  it("with no source named, says the emulator source", () => {
    expect(launchNote(NOT_ESTABLISHED, null)).toBe(
      "Whether Game.sfc starts cannot be established: the emulator source does not list the system Sega Dreamcast.",
    );
  });

  it("an archive whose contents were not read starts, and says so", () => {
    const archive = launch({ entry: "DeSmuME", file: "Game.zip", extension: ".zip", contents_unread: true });
    expect(launchRefusal(archive, true)).toBeNull();
    expect(launchNote(archive, RETRODECK)).toBe(
      "What is inside Game.zip was not read, so whether DeSmuME finds a game in it cannot be established.",
    );
  });

  it("a file that starts says nothing", () => {
    expect(launchRefusal(launch(), true)).toBeNull();
    expect(launchNote(launch(), RETRODECK)).toBeNull();
    expect(launchNote(launch({ format_unestablished: true }), RETRODECK)).toBeNull();
  });

  it("nothing asked says nothing", () => {
    expect(launchRefusal(null, true)).toBeNull();
    expect(launchNote(null, RETRODECK)).toBeNull();
  });
});

describe("launchRefused", () => {
  it("refuses exactly the three verdicts that refuse a start", () => {
    expect([PS3_PKG, DREAMCAST_TRACK, NOT_THIS_EMULATOR].map(launchRefused)).toEqual([true, true, true]);
    expect([launch(), NOT_ESTABLISHED, null].map(launchRefused)).toEqual([false, false, false]);
  });
});

describe("entryLaunchLine — the emulator menu's line per entry (#2195 D22, D29, D36)", () => {
  it.each([
    [launch(), "can start it"],
    [launch({ format_unestablished: true }), "whether it reads .ps3 is not established"],
    [launch({ file: "Game.zip", contents_unread: true }), "what is inside Game.zip was not read"],
    [PS3_PKG, "cannot start it: the game has to be installed first"],
    [DREAMCAST_TRACK, "Sega Dreamcast does not start from .bin"],
    [NOT_THIS_EMULATOR, "cannot start .n64 files"],
    [NOT_ESTABLISHED, "whether it starts cannot be established: RetroDECK does not list the system Sega Dreamcast"],
  ])("words %o as %s", (verdict, words) => {
    expect(entryLaunchLine(verdict, RETRODECK)).toBe(words);
  });
});

describe("startCheckFailureSentence — what went wrong with the page's check (#2195 D32, D36)", () => {
  it.each([
    [new TimeoutError(5000), "Tender's backend did not answer in time"],
    [new HostTransportError("connection_lost", "socket closed"), "the connection to Tender's backend was lost"],
    [new Error("boom"), "boom"],
    ["odd", "odd"],
  ])("words %o", (error, words) => {
    expect(startCheckFailureSentence(error)).toBe(`Tender could not check whether this game can start: ${words}.`);
  });
});
