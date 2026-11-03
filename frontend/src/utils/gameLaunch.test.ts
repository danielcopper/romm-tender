import { describe, expect, it } from "vitest";
import type { AnsweringSource, GameLaunch } from "../types";
import { HostTransportError } from "../api/host";
import {
  entryLaunchLine,
  launchNote,
  launchNotes,
  launchRefusal,
  launchRefused,
  startCheckFailureSentence,
} from "./gameLaunch";
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

describe("what a disc file opens, as the resolver read it (#2698 D5, D7)", () => {
  const disc = (overrides: Partial<GameLaunch>) =>
    launch({
      entry: "Beetle PSX",
      system: "psx",
      platform_name: "PlayStation",
      file: "Game.cue",
      extension: ".cue",
      reason_file: "Game (Track 2).bin",
      ...overrides,
    });

  it.each([
    ["opened_file_missing", "Game (Track 2).bin is missing"],
    ["track_refused", "Game (Track 2).bin is a track of a type it does not take"],
    ["sheet_refused", "it refuses the cue sheet or playlist Game (Track 2).bin"],
    ["format_refused", "Game (Track 2).bin is not a disc image it opens"],
    ["path_too_long", "the path of Game (Track 2).bin is too long for it to open"],
    ["playlist_refused", "it starts no game from a playlist"],
  ] as const)("a stop on %s turns Play off and names the file it stops on", (reason, why) => {
    const answer = disc({ state: "not_this_emulator", reason });
    expect(launchRefused(answer)).toBe(true);
    expect(launchRefusal(answer, true)).toBe(`Beetle PSX cannot start Game.cue: ${why}.`);
    expect(entryLaunchLine(answer, RETRODECK)).toBe(why);
    expect(launchNote(answer, RETRODECK)).toBeNull();
  });

  it("names the entries that do start it after the reason", () => {
    expect(
      launchRefusal(
        disc({ state: "not_this_emulator", reason: "playlist_refused", other_entries: ["SwanStation"] }),
        true,
      ),
    ).toBe(
      "Beetle PSX cannot start Game.cue: it starts no game from a playlist. SwanStation can — choose it in the emulator menu.",
    );
  });

  it.each([
    ["goes_on_file_missing", "it is missing"],
    ["goes_on_track_refused", "it is a track of a type it does not take"],
    ["goes_on_sheet_refused", "it refuses this cue sheet or playlist"],
    ["goes_on_format_refused", "it is not a disc image it opens"],
    ["goes_on_path_too_long", "its path is too long for it to use in full"],
  ] as const)(
    "a file the start goes on without (%s) keeps Play on, startable, never not established",
    (reason, why) => {
      const answer = disc({ state: "starts", reason });
      expect(launchRefused(answer)).toBe(false);
      expect(launchRefusal(answer, true)).toBeNull();
      expect(launchNote(answer, RETRODECK)).toBe(
        `Beetle PSX can start this game, but goes on without Game (Track 2).bin: ${why}.`,
      );
      expect(entryLaunchLine(answer, RETRODECK)).toBe(`can start it, without Game (Track 2).bin: ${why}`);
    },
  );

  it('an entry Tender cannot start yet keeps what its start goes on without, but not its "can start it"', () => {
    expect(entryLaunchLine(disc({ state: "starts", reason: "goes_on_file_missing" }), RETRODECK, false)).toBe(
      "without Game (Track 2).bin: it is missing",
    );
  });

  it.each([
    ["opened_format_unread", "how it opens Game (Track 2).bin was not checked"],
    ["opened_content_unread", "what it makes of Game (Track 2).bin is not established"],
    ["opened_file_unread", "Game (Track 2).bin could not be checked"],
    ["patch_setting_unread", "whether it loads a patch for the disc image could not be read"],
    [
      "opens_past_bound",
      "the check stopped at one of its limits at Game (Track 2).bin, so what it opens past that was not checked",
    ],
  ] as const)("what the reading could not establish (%s) keeps Play on and says so", (reason, why) => {
    const answer = disc({ state: "starts", reason });
    expect(launchRefused(answer)).toBe(false);
    expect(launchNote(answer, RETRODECK)).toBe(`Whether Beetle PSX starts this game cannot be established: ${why}.`);
    expect(entryLaunchLine(answer, RETRODECK)).toBe(`whether it starts the game is not established: ${why}`);
  });

  it("a playlist past the discs the emulator reads keeps Play on and says it does not read every disc", () => {
    const answer = disc({
      entry: "Genesis Plus GX",
      state: "starts",
      reason: "playlist_discs_past_limit",
      file: "Game.m3u",
      reason_file: "Game.m3u",
    });
    expect(launchRefused(answer)).toBe(false);
    expect(launchNote(answer, RETRODECK)).toBe(
      "Genesis Plus GX can start this game, but does not read every disc Game.m3u lists.",
    );
    expect(entryLaunchLine(answer, RETRODECK)).toBe("can start it, but does not read every disc Game.m3u lists");
    expect(entryLaunchLine(answer, RETRODECK, false)).toBe("does not read every disc Game.m3u lists");
  });

  it("a sheet path too long for the emulator to read in full keeps Play on and says what was not checked", () => {
    const answer = disc({
      entry: "Genesis Plus GX",
      state: "starts",
      reason: "path_too_long_unchecked",
      reason_file: "Game.cue",
    });
    expect(launchRefused(answer)).toBe(false);
    expect(launchNote(answer, RETRODECK)).toBe(
      "Whether Genesis Plus GX starts this game cannot be established: the path of Game.cue is too long for it to read in full, and what it does then was not checked.",
    );
  });

  it("a stop is no note on a file that starts, and a file the start goes on without is no refusal", () => {
    expect(launchNote(disc({ state: "starts", reason: "playlist_refused" }), RETRODECK)).toBeNull();
    expect(launchRefusal(disc({ state: "not_this_emulator", reason: "goes_on_file_missing" }), true)).toBe(
      "Beetle PSX does not start .cue.",
    );
  });
});

describe("every statement the resolver makes, each in its own words (#2698 D8)", () => {
  const playlist = (overrides: Partial<GameLaunch>) =>
    launch({
      entry: "SwanStation",
      system: "psx",
      platform_name: "PlayStation",
      file: "Game.m3u",
      extension: ".m3u",
      ...overrides,
    });
  const missingDisc2 = { reason: "goes_on_file_missing", file: "Disc 2.cue" } as const;
  const disc3Unread = { reason: "opened_format_unread", file: "Disc 3.chd" } as const;

  it("the page lists every statement in the resolver's order, none weighed against another", () => {
    const answer = playlist({
      state: "starts",
      reason: "goes_on_file_missing",
      statements: [missingDisc2, disc3Unread],
    });
    expect(launchNotes(answer, RETRODECK)).toEqual([
      "SwanStation can start this game, but goes on without Disc 2.cue: it is missing.",
      "Whether SwanStation starts this game cannot be established: how it opens Disc 3.chd was not checked.",
    ]);
    const swapped = playlist({
      state: "starts",
      reason: "opened_format_unread",
      statements: [disc3Unread, missingDisc2],
    });
    expect(launchNotes(swapped, RETRODECK)).toEqual([
      "Whether SwanStation starts this game cannot be established: how it opens Disc 3.chd was not checked.",
      "SwanStation can start this game, but goes on without Disc 2.cue: it is missing.",
    ]);
    expect(launchRefused(answer)).toBe(false);
  });

  it("the menu shows the first statement and how many more there are", () => {
    const answer = playlist({
      state: "starts",
      reason: "goes_on_file_missing",
      statements: [missingDisc2, disc3Unread],
    });
    expect(entryLaunchLine(answer, RETRODECK)).toBe("can start it, without Disc 2.cue: it is missing +1 more");
    expect(entryLaunchLine(answer, RETRODECK, false)).toBe("without Disc 2.cue: it is missing +1 more");
  });

  it("the format and archive notes are statements of their own, in their place", () => {
    const answer = playlist({
      state: "starts",
      reason: "patch_setting_unread",
      format_unestablished: true,
      statements: [
        { reason: "patch_setting_unread", file: null },
        { reason: "format_unestablished", file: null },
      ],
    });
    expect(launchNotes(answer, RETRODECK)).toEqual([
      "Whether SwanStation starts this game cannot be established: whether it loads a patch for the disc image could not be read.",
      "Whether SwanStation reads .m3u files is not established.",
    ]);
    expect(entryLaunchLine(answer, RETRODECK)).toBe(
      "whether it starts the game is not established: whether it loads a patch for the disc image could not be read +1 more",
    );
  });

  it("a refusal names the stop first, then each file the emulator would have gone on without", () => {
    const answer = playlist({
      entry: "Beetle PCE",
      state: "not_this_emulator",
      reason: "playlist_refused",
      reason_file: "Game.m3u",
      other_entries: ["Beetle PCE Fast"],
      statements: [missingDisc2],
    });
    expect(launchRefusal(answer, true)).toBe(
      "Beetle PCE cannot start Game.m3u: it starts no game from a playlist. It would go on without Disc 2.cue: it is missing. Beetle PCE Fast can — choose it in the emulator menu.",
    );
    expect(entryLaunchLine(answer, RETRODECK)).toBe("it starts no game from a playlist +1 more");
    expect(launchNotes(answer, RETRODECK)).toEqual([]);
  });

  it("a refusal then says each thing the resolver could not establish, after the files it would go on without", () => {
    const answer = playlist({
      entry: "Beetle PCE",
      state: "not_this_emulator",
      reason: "playlist_refused",
      reason_file: "Game.m3u",
      other_entries: ["Beetle PCE Fast"],
      statements: [{ reason: "opened_format_unread", file: "Game (Disc 2).chd" }],
    });
    expect(launchRefusal(answer, true)).toBe(
      "Beetle PCE cannot start Game.m3u: it starts no game from a playlist. How it opens Game (Disc 2).chd was not checked. Beetle PCE Fast can — choose it in the emulator menu.",
    );
    const both = playlist({
      ...answer,
      statements: [{ reason: "opened_format_unread", file: "Game (Disc 2).chd" }, missingDisc2],
    });
    expect(launchRefusal(both, false)).toBe(
      "Beetle PCE cannot start Game.m3u: it starts no game from a playlist. It would go on without Disc 2.cue: it is missing. How it opens Game (Disc 2).chd was not checked.",
    );
    expect(entryLaunchLine(both, RETRODECK)).toBe("it starts no game from a playlist +2 more");
  });

  it("an answer that carries no statements says its reason and its two notes", () => {
    const answer = playlist({
      state: "starts",
      reason: "goes_on_file_missing",
      reason_file: "Disc 2.cue",
      contents_unread: true,
    });
    expect(launchNotes(answer, RETRODECK)).toEqual([
      "SwanStation can start this game, but goes on without Disc 2.cue: it is missing.",
      "What is inside Game.m3u was not read, so whether SwanStation finds a game in it cannot be established.",
    ]);
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

describe("entryLaunchLine for an entry Tender cannot start (#2195 D42)", () => {
  it("says no plain 'can start it'", () => {
    expect(entryLaunchLine(launch(), RETRODECK, false)).toBeNull();
    expect(entryLaunchLine(launch(), RETRODECK)).toBe("can start it");
  });

  it("keeps every line of the resolver's", () => {
    expect(entryLaunchLine(launch({ format_unestablished: true }), RETRODECK, false)).toBe(
      "whether it reads .ps3 is not established",
    );
    expect(entryLaunchLine(launch({ state: "not_this_emulator", reason: "takes_no_folder" }), RETRODECK, false)).toBe(
      "cannot start a game installed as a folder",
    );
  });
});
