---
status: accepted
decided: 2026-10-05
updated: 2026-10-05
amends: [0011, 0028, 0030, 0041]
---

# Tender takes emulator answers from emu-atlas, starts emulators itself, and keeps games and BIOS in its own store

Recorded for [#1735](https://github.com/danielcopper/romm-tender/issues/1735) and its four epics:
[#2217](https://github.com/danielcopper/romm-tender/issues/2217) (emulator sources and direct launch),
[#2218](https://github.com/danielcopper/romm-tender/issues/2218) (Tender's library and frontend links),
[#2219](https://github.com/danielcopper/romm-tender/issues/2219) (BIOS) and
[#2220](https://github.com/danielcopper/romm-tender/issues/2220) (saves). It is the ADR
[#2188](https://github.com/danielcopper/romm-tender/issues/2188) D10 names, written under
[#2231](https://github.com/danielcopper/romm-tender/issues/2231).

## Context

Tender knew one place emulators come from, RetroDECK, and depended on it three ways: it launched every game through
RetroDECK's start script, downloaded every game and BIOS file into RetroDECK's folders, and answered several emulator
questions with readers and tables of its own beside the vendored resolver. An audit on 2026-10-02 found Tender
re-deriving answers the resolver already gives, and getting some of them wrong. The goal is a choice among every
emulator found on the machine — from RetroDECK, EmuDeck, a RetroArch without a frontend or a standalone emulator — and
that does not fit any of the three dependencies.

Measured before deciding (2026-10-04):

- The resolver takes a content path as it is given; it resolves no link. Its save answer for RetroDECK's RetroArch
  depends on the name of the folder the game lies in (`sort_savefiles_by_content_enable`); with the same folder name
  above the file, a library path and a link path got the same answer in 888 of 888 questions.
- Saves, BIOS places, settings, hotkeys and achievements follow the emulator binary and its sandbox, not the start path.
  Started directly in RetroDECK's sandbox, an emulator reads the same configuration; RetroDECK's start script adds the
  placeholders and one library path (`rd_shared_libs`), nothing else that matters for a game.
- ES-DE follows links in its ROM folders, and RetroDECK's own documentation recommends linking games in. Every emulator
  examined resolves the relative references of a multi-file game (`.cue`, `.gdi`, `.m3u`) against the path it is given,
  so a multi-file game is linked as a whole folder.
- An emulator writes its save where its own configuration says. Making it write anywhere else needs a change to that
  configuration or a link in its save folder; RetroDECK and EmuDeck replace such links on their own updates.
- No other RomM client links games; all of them place real files and adopt files the user placed where they lie.

## Decision

1. **emu-atlas answers, Tender words it.** Whatever the resolver answers, Tender takes and only words; what it leaves to
   the caller (a game's region, a game's identity), Tender supplies from RomM; where it has no answer, Tender says the
   thing is not supported or not established. Tender has no reader of frontend files and no table of its own for
   anything the resolver answers. Every Tender issue names the resolver answers it builds on, and one that is missing is
   asked for upstream and blocks the issue that needs it.
2. **Emulator sources.** RetroDECK, EmuDeck, a RetroArch without a frontend and standalone emulators are emulator
   sources (the resolver calls them installations). Each is asked through the resolver's common interface, with no
   source-specific path; each has a switch in the settings; the default emulator follows a source order the user can
   change (RetroDECK → EmuDeck → RetroArch → standalone), and the user chooses per platform and per game. A health
   problem is shown per source, worded per finding code.
3. **Direct launch.** Tender starts every emulator itself, with the command the resolver gives for it — program and
   variant, arguments, environment, working folder — and recognises and stops it by the process the resolver names. An
   emulator without a complete command is listed as not startable through Tender yet. Tender asks the resolver with
   exactly the path it starts the emulator with.
4. **Tender's own store.** Downloaded games live in Tender's library, at a place the user chooses; BIOS files live once
   in Tender's firmware store. Both reach an emulator source through absolute symlinks Tender places and recognises as
   its own: a game in the source's ROM folder for its system (a multi-file game as its whole folder), a BIOS file at the
   place the resolver names. Tender moves its own files only on the user's request and creates no folder in a source.
5. **Files the user placed.** A game file the user placed in a source is recognised and started where it lies; Tender
   never deletes, moves or renames it, nor its saves, and records its origin.
6. **Saves stay with the emulator.** A save lives where the emulator writes it for the path Tender starts the game with.
   Tender keeps no save folder and no copy of its own; it syncs that place with RomM. Its backups before an overwrite
   live in Tender's own data folder.
7. **RetroDECK's own move is RetroDECK's.** Tender follows no RetroDECK move with file moves of its own; it asks the
   resolver again.
8. **One configuration write remains.** Tender writes no emulator's configuration, with one exception: the RetroArch
   `input_driver` fix, run only from its button and after confirmation.

## Considered options

- **Keep launching through RetroDECK.** Rejected: RetroDECK's start script resolves links and recognises a system only
  from a `roms/<system>` path, so a game in Tender's library would open a system dialog unless every start named the
  system; and no other source would ever be startable the same way.
- **Real files in the emulator source's folder, moved on a switch of source** (what every other RomM client does).
  Rejected: every switch of source is a move of game files, across drives a copy, and the library disappears with the
  source.
- **Links into every source that can start the system.** Rejected: games appear in every frontend whether wanted or not,
  and the number of links to keep right grows with every source.
- **A library with no links at all, started only through Tender.** Rejected: games could no longer be played from a
  frontend, which the user wants to keep possible.
- **Saves in Tender's store, linked into the emulators' save folders.** Rejected: it moves the user's existing saves,
  RetroDECK and EmuDeck replace such links on their own updates, a missing store makes the emulator write elsewhere or
  fail, and shared memory cards would make Tender own other games' saves.
- **BIOS copies or hard links per source.** Rejected for one way of placing games and BIOS alike; hard links do not
  cross drives.

## Consequences

- Tender's RetroDECK path reader, its health judge, its `es_find_rules.xml` reader and its migration that moved files
  after a RetroDECK move go.
- The delete and adoption code, built to refuse every symlink, gains one exception: a link of Tender's own, recognised
  by pointing at a file Tender records, is removed without following it; every other link stays untouched.
- The database records per game where its file lies, its origin and its chosen emulator, and per BIOS file its
  placements.
- Starting a game from a frontend through a link and starting it through Tender can write the save to different places
  where the emulator's settings make that so (saves kept beside the game, another emulator chosen in ES-DE). Tender says
  so on the game page; it does not change the emulator's settings.
- The work waits on resolver answers that do not exist yet (the launch command and its parts, links in the firmware
  root), tracked as emu-atlas issues the Tender issues are blocked by. Until they land, what is missing is shown as
  missing.
