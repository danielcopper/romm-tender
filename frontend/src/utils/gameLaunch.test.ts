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
  });

  it("a format the entry was not established to read is said under Play, and lets the game start (#2195 D41)", () => {
    const answer = launch({
      entry: "RPCS3 Shortcut (Standalone)",
      file: "Game.iso",
      extension: ".iso",
      format_unestablished: true,
    });
    expect(launchRefusal(answer, true)).toBeNull();
    expect(launchNote(answer, RETRODECK)).toBe(
      "Whether RPCS3 Shortcut (Standalone) reads .iso files is not established.",
    );
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

describe("the resolver's own reasons (#2195 D21, D40)", () => {
  const SHORTCUT = "RPCS3 Shortcut (Standalone)";
  const DIRECTORY = "RPCS3 Directory (Standalone)";
  const ISO = "RPCS3 ISO (Standalone)";
  const refused = (reason: GameLaunch["reason"], overrides: Partial<GameLaunch> = {}) =>
    launch({ state: "not_this_emulator", entry: SHORTCUT, reason, ...overrides });
  const stoppedShort = (reason: GameLaunch["reason"], overrides: Partial<GameLaunch> = {}) =>
    launch({ state: "starts", entry: DIRECTORY, reason, ...overrides });

  it.each([
    [
      "takes_no_folder",
      "RPCS3 Shortcut (Standalone) cannot start a game installed as a folder.",
      "cannot start a game installed as a folder",
    ],
    [
      "nothing_to_start",
      "RPCS3 Shortcut (Standalone) finds nothing to start in Game.ps3.",
      "finds nothing to start in this folder",
    ],
    [
      "not_a_game_file",
      "RPCS3 Shortcut (Standalone) cannot start Game.ps3: it is neither a disc image nor a game program.",
      "cannot start it: neither a disc image nor a game program",
    ],
    [
      "disc_not_found",
      "RPCS3 Shortcut (Standalone) cannot start this game: it is a disc game, and no PS3_GAME folder above it has a valid PS3_DISC.SFB beside it.",
      "the game's disc cannot be mounted",
    ],
    [
      "disc_key_missing",
      "RPCS3 Shortcut (Standalone) cannot start Game.ps3: the image is encrypted and no key file for it was found.",
      "the image is encrypted and has no key file",
    ],
  ] as const)("a refusal for %s refuses the start and names the reason", (reason, sentence, line) => {
    const answer = refused(reason);
    expect(launchRefused(answer)).toBe(true);
    expect(launchRefusal(answer, true)).toBe(sentence);
    expect(entryLaunchLine(answer, RETRODECK)).toBe(line);
    expect(launchNote(answer, RETRODECK)).toBeNull();
  });

  it("names the one entry the resolver says starts it, pointing at the emulator menu", () => {
    expect(launchRefusal(refused("takes_no_folder", { other_entries: [DIRECTORY] }), true)).toBe(
      "RPCS3 Shortcut (Standalone) cannot start a game installed as a folder. RPCS3 Directory (Standalone) can — choose it in the emulator menu.",
    );
  });

  it("names every entry the resolver says starts it", () => {
    expect(launchRefusal(refused("takes_no_folder", { other_entries: [DIRECTORY, ISO] }), true)).toBe(
      "RPCS3 Shortcut (Standalone) cannot start a game installed as a folder. RPCS3 Directory (Standalone) or RPCS3 ISO (Standalone) can — choose one in the emulator menu.",
    );
  });

  it("points at no entry where the emulator menu offers none", () => {
    expect(launchRefusal(refused("takes_no_folder", { other_entries: [DIRECTORY] }), false)).toBe(
      "RPCS3 Shortcut (Standalone) cannot start a game installed as a folder.",
    );
  });

  it("a refusal with no reason of the resolver's keeps its own sentence", () => {
    const answer = refused(null, { entry: "Dolphin (Standalone)", extension: ".zip" });
    expect(launchRefusal(answer, true)).toBe("Dolphin (Standalone) does not start .zip.");
    expect(entryLaunchLine(answer, RETRODECK)).toBe("cannot start .zip files");
  });

  it.each([
    ["content_missing", "Game.ps3 was not found"],
    ["content_unreadable", "what RPCS3 would look at could not be read"],
    ["game_list_unread", "the emulator's own list of installed games was not read"],
    ["game_kind_unfollowed", "its PARAM.SFO names a kind of game whose start was not followed"],
    ["inside_emulator_drive", "the game lies inside the emulator's own game folder, which starts games another way"],
    ["emulator_drive_unknown", "where the emulator's internal drive is could not be read"],
    ["savestate_unread", "RPCS3 opens Game.ps3 as a savestate, which was not followed"],
    [
      "image_layout_unread",
      "the disc image states a region count RPCS3 does not accept, and what its start does then was not followed",
    ],
    ["encryption_unread", "whether the disc image is encrypted could not be read"],
    ["key_untested", "whether its key file fits the disc image was not checked"],
    [
      "key_folder_unknown",
      "no key file is beside the disc image, and where the emulator keeps its other key files could not be read",
    ],
    [
      "disc_folder_set",
      "the emulator's own disc folder holds a disc it mounts instead, and which disc that boots was not followed",
    ],
    ["disc_folder_unknown", "where the emulator's own disc folder is could not be read"],
    ["content_unchecked", ".ps3 files are taken, but the content of Game.ps3 was not checked"],
    ["version_dependent", "whether it reads .ps3 depends on the emulator's version"],
  ] as const)("a file that starts says what its reading stopped short of: %s", (reason, why) => {
    const answer = stoppedShort(reason);
    expect(launchRefused(answer)).toBe(false);
    expect(launchRefusal(answer, true)).toBeNull();
    expect(launchNote(answer, RETRODECK)).toBe(
      `Whether RPCS3 Directory (Standalone) starts this game cannot be established: ${why}.`,
    );
    expect(entryLaunchLine(answer, RETRODECK)).toBe(`whether it starts the game is not established: ${why}`);
  });

  it("a refusal's reason on a file that starts is no note", () => {
    const answer = stoppedShort("takes_no_folder");
    expect(launchNote(answer, RETRODECK)).toBeNull();
    expect(entryLaunchLine(answer, RETRODECK)).toBe("can start it");
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
