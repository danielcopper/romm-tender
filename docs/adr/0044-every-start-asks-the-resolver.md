---
status: accepted
decided: 2026-11-03
updated: 2026-11-04
supersedes: [0009]
amends: [0012, 0014, 0015, 0019, 0020, 0025]
---

# Every start asks the resolver for its command, and a shortcut carries only its appId

Recorded for [#2221](https://github.com/danielcopper/romm-tender/issues/2221), whose `## Decisions` D2, D3, D7, D10, D11
and D15–D21 this ADR carries, and for its first cut, [#2708](https://github.com/danielcopper/romm-tender/issues/2708).
It goes on from [ADR-0042](0042-tender-takes-emulator-answers-starts-emulators-and-keeps-its-own-store.md), under which
Tender starts emulators itself.

## Context

[ADR-0009](0009-launcher-pure-exec-wrapper-baked-launch-options.md) wrote each game's start command into its Steam
shortcut's launch options, and the launcher ran whatever it found there. The command went through RetroDECK's
`run_game.sh` with `-e`, and Tender's own rule — the bake classifier — decided which ES-DE commands that form could
carry. Every change that bore on a start had to be written into Steam: the sync, a download, an adoption, an uninstall,
the RetroDECK home migration, a version switch, a core pick and a disc pick each rewrote the shortcut, and
`roms.applied_launch_options` recorded what was written so the sync could skip an unchanged one. A change Tender did not
see — a RetroDECK update shipping another command — left every shortcut stale, and a shortcut started while Tender was
not running ran a command Tender no longer stood behind. The vendored resolver now states each entry's complete launch
command for a game's own path: the program, its arguments, the environment it needs from its distribution's start and
the folder it starts in.

## Decision

**A start asks; a shortcut carries nothing about what starts its game.**

- Every bound shortcut's launch options are its appId, and its program is `tender-rom-launcher`
  (`tender-rom-launcher <appId>`). Nothing that bears on a start is written into Steam: a core or disc pick, an install,
  an uninstall, a version switch and the RetroDECK home migration change nothing in a shortcut, and the next start asks
  with what is in effect then (D7, D10).
- At every start — Tender's Play and Steam's own Play, which the start check intercepts — the check asks the resolver
  for the command of the entry in effect, with the path the emulator gets (the game's file, the picked disc, the folder
  of a game kept as one, D3), and hands it to the launcher for that start only: a file of its own,
  `<runtime dir>/start/<appId>`, written whole and readable by the user only. The launcher claims the file, removes it
  and runs the command in its folder; with no file, or one older than 30 seconds, it starts nothing (D11, D15). The
  check sets the shortcut's launch options back to its appId before each start, and the launcher ignores every word
  after it (D16).
- An entry the resolver gives no complete command for is not startable through Tender yet, with the resolver's reason,
  on the game's page, in its emulator menu and at a start; nothing is guessed, and no rule of Tender's own decides it
  (D2, D20). A game no emulator is found for is not started (D20), and neither is one whose answering source is one
  Tender starts no game through yet, which the start says in that source's sentence (D21).
- A start whose hand-over does not come about is refused with a sentence saying why (D19).
- `roms.applied_launch_options` gives way to `roms.shortcut_applied`, which only the sync's commit sets and Force Full
  Sync clears (D17).

A launch file kept per game and rewritten whenever something changes was not chosen: a change Tender does not see would
leave it stale, and a start that bypasses Tender would also bypass the save sync around it. The launcher asking the
resolver itself was not chosen: it would cost every start a Python start-up and tie the launcher to the resolver.
Writing the command into Steam's launch options before each start was not chosen: it would stay in Steam, and a start
without Tender would run a stale command.

## Consequences

- With Tender's backend stopped, Steam's Play on a RomM shortcut starts nothing; a start outside Steam (ES-DE,
  RetroDECK) is not Tender's and is left alone.
- A RetroDECK update that changes a command reaches the next start without a sync.
- The plain RetroDECK launch, `run_game.sh` and the bake classifier are gone; the platform page says nothing of a
  command, since without a game there is nothing to ask with (D20).
- The table of a PS3 game's boot folder (`FOLDER_BOOT_MARKERS`) still decides the folder a start is asked with, until a
  later cut of #2221 reads the resolver's.
