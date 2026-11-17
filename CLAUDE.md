# Tender — RomM library into Steam

## What This Is

Syncs a self-hosted RomM library into Steam as Non-Steam shortcuts; games launch via RetroDECK. The backend runs as its
own process (no longer a Decky plugin), serves the panel over a loopback port and loads it into Steam over the CEF
debugger — so **dev tasks restart Steam** (see Development). `install.sh` installs it as a systemd user unit; every
shortcut starts through `~/.local/bin/tender-rom-launcher`.

This file holds only traps, cross-cutting invariants and how we work; everything else lives in `docs/` and
`.claude/rules/`.

## Where the details live

Read an area's page in `docs/architecture/` before working there; it owns the area's current truth (reach ADRs through
it, don't cite them from here). Vocabulary: `GLOSSARY.md`. Pages:
[steam-non-steam-shortcuts](docs/architecture/steam-non-steam-shortcuts.md),
[qam-panel](docs/architecture/qam-panel.md), [frontend-bundles](docs/architecture/frontend-bundles.md) (how the panel is
built), [loading-the-panel](docs/architecture/loading-the-panel.md) (how it gets into Steam),
[save-file-sync-architecture](docs/architecture/save-file-sync-architecture.md),
[save-sync-coverage](docs/architecture/save-sync-coverage.md),
[removed-game-cleanup](docs/architecture/removed-game-cleanup.md),
[backend-architecture](docs/architecture/backend-architecture.md),
[database-design](docs/architecture/database-design.md),
[core-emulator-selection](docs/architecture/core-emulator-selection.md),
[config-source-parsers](docs/architecture/config-source-parsers.md),
[steam-remote-play](docs/architecture/steam-remote-play.md), [controller-focus](docs/architecture/controller-focus.md),
[desktop-dom-architecture](docs/architecture/desktop-dom-architecture.md). User-facing behaviour: `docs/user-guide/`;
dev setup: `docs/contributing/`.

## Path-scoped rules

Coding conventions live in `.claude/rules/*.md`, each with a `paths:` glob. Read every rule file whose `paths:` cover an
area before writing code there — especially before creating a file, since automatic loading arrives only once a matching
file is read.

## Documentation

A change to architecture, data flow, behaviour or UI updates its page under `docs/` in the same PR — never as a
follow-up issue; when unsure, it does. CI checks it; a PR with nothing to document opts out with the `no-docs-change`
label. Preview with `mise run docs`.

## Where decisions live

Work starts from an issue: open questions under `## To decide` (one checkbox each, checked off with a pointer like
`→ D1` once answered), decisions under `## Decisions` (appended; a changed one is struck through and re-added with its
date). An epic holds its own; a sub-issue says "See epic #N". A hard-to-reverse, surprising trade-off also gets an ADR
in `docs/adr/`, linked from the issue. Every `## Done when` item becomes a test seen failing first, or is marked
"(device)". The PR body repeats the decisions and becomes the squash commit. The `decisions` CI check enforces the issue
sections and the ADR format ([rules](https://github.com/danielcopper/.github#the-decisions-check)); the rest is for
review.

In an issue someone else opened, their text stays as they wrote it; the decisions go into a section appended below it,
opened by `> [!NOTE]` / `> Added by the maintainer.`

## Traps — non-obvious rules that bite silently

- **The build output is `<repo>/dist/`, not under `frontend/`** — the seam the backend serves; don't move it into the
  frontend package. `frontend/.size-limit.json` points up there too, and the `build` script empties it.
- **The build makes three files: `globals.js` and two copies of the panel** — `index.js` bundles `@decky/ui`,
  `index-coexistence.js` takes it from a running Decky. The injector (`backend/host/inject/bundles.py`) picks one by the
  machine. Importing `@decky/ui` a second time beside a Decky that is already rendering crashes Steam's UI, and an `if`
  at runtime can't prevent it (the import runs first) — so never load `index.js` or `globals.js` beside Decky.
  `pnpm -C frontend check:bundle`, not `pnpm build`, checks both bundles. Detail:
  [frontend-bundles.md](docs/architecture/frontend-bundles.md).
- **Our React globals (`SP_REACT`, `SP_REACTDOM`, `SP_JSX` in `frontend/src/boot/steamGlobals.ts`) must match Decky's
  exactly** — when ours run first, Decky's whole UI renders through them. `decky-globals-block.txt` pins Decky's
  version; if `steamGlobals.test.ts` fails, find out which side moved instead of fixing the test.
- **Shortcuts go through `SteamClient.Apps.AddShortcut()`, never VDF writes** (those need a Steam restart). It ignores
  `startDir` and `launchOptions`: set them with the `Set*` calls once `waitForAppOverview` sees the new app (no fixed
  delay), and pass the exe unquoted.
- **A shortcut's appId is assigned by Steam, never derived** (the `CRC32(exe + name)` formula is wrong) — Tender stores
  it in `roms.shortcut_app_id` and recognises its shortcuts by exe path. Changing `launchOptions`, `startDir` or `exe`
  keeps the appId (each measured); whether renaming (`SetShortcutName`) does is **unmeasured** — don't assume it.
- **Frontend API:** Steam's components come from `@decky/ui`; everything from the host comes from
  `frontend/src/api/host.ts`, which replaced `@decky/api` — never import `@decky/api` or Decky's loader API, even when
  Decky runs. There is no `routerHook`: the game-page section uses `installGamePagePatch.ts`. Detail:
  [frontend-bundles.md](docs/architecture/frontend-bundles.md), "Talking to the backend".
- **An endpoint is a public method on `Endpoints` with `@route` (topmost decorator), `def` or `async def` alike** —
  every public method there needs it. The frontend's `endpoint("name")` declarations decide what should be reachable;
  the gate checks both sides.
- **RomM API:** the filter is `platform_ids` (plural); cover URLs need URL-encoding; list calls take their page size
  from `lib/romm_paging.py`, and every `offset` walk over `/api/roms` sends `order_by=id` (otherwise unstable under a
  running scan). RomM below 5.4.1 is refused (`MIN_ROMM_VERSION`).
- **Every adapter talking to a remote server takes a `user_agent`** (`<package>/<version>` from `domain/identity.py`,
  wired in bootstrap) — SteamGridDB and RomM behind Cloudflare reject Python's default with 403, GitHub refuses none.
  Never hardcode it. `VERSION` is stamped by release-please, never edited by hand.
- **Large payloads are chunked, never sent whole:** an answer over ~12 MiB fails that call (`host/dispatch.py`), a frame
  over 16 MiB closes the socket and every call on it (`host/connection.py`). Use per-item endpoints or paged lists.
- **No `BIsModOrShortcut` bypass** — removed on purpose; we own the game page. Don't bring it back.
- **`instanceof` against a DOM global is always false in QAM and desktop-surface code** — the code runs in one window,
  the nodes belong to another, so such a guard silently disables the feature. Take constructors from the node
  (`el.ownerDocument.defaultView`, as `WidePage` does). The tests can't catch this: happy-dom answers `instanceof` the
  same in both windows.
- **CI's Python is not the device's** — before changing anything under `_vendor/`, read
  `.claude/rules/vendored-assets.md`.
- **In an emu-atlas answer, an empty list means "nothing found", not "nothing needed".** The verdict is
  `requirements_met` (`True` / `False` / `None` = unknown, never `True` by default); what an empty `requirements` means
  depends only on `declaration` (`read` + empty is the only "needs no firmware"). Answer entry questions from the
  entries, never from the flattened `answer.requirements`. Detail:
  [resolver.md](docs/architecture/invariants/resolver.md).

## Development

- **Setup** `mise run setup` · **Build** `pnpm -C frontend build` · **Tests** `mise run test` (backend),
  `mise run test:frontend` · **Coverage** `mise run test:frontend:coverage`, backend via
  `pytest --cov=backend --cov-branch` · **Lint** `mise run lint` · **Gate** `mise run gate` — the CI battery, slow, run
  once before pushing.
- **Package** `mise run package` → `build/romm-tender-<V>.tar.gz`; `install.sh --from <tarball>` installs or updates
  (rolls back by itself on failure), `--rollback` / `--uninstall` by hand. Tests never run these against the real
  machine — always under a `tmp_path` HOME.
- **Release** is release-please: `version.txt` and `VERSION` in `domain/identity.py` are its output, never edited by
  hand. `release-as: "1.0.0"` pins every release PR to 1.0.0 — **remove it once 1.0.0 has shipped**. Fixes go forward on
  `main`. The one exception — a published release broken for its users in a way they cannot wait for `main` to fix —
  ships from a branch `release/X.Y` off its tag, as `docs/contributing/development.md`, "Releasing", says.
- **Run it** `mise run dev` builds the panel, **restarts Steam** and runs the backend (needs
  `~/.steam/steam/.cef-enable-remote-debugging`); no hot reload. `dev:bpm` / `dev:desktop` pick the window,
  `dev:*-reset` only restart Steam (the way out when a task refuses), `dev:backend` / `dev:frontend` split the two
  halves. Never start these on the owner's session without asking — they restart his Steam. Guide:
  `docs/contributing/frontend-dev-loop.md`.
- **Dependencies** are pinned in lock files; after changing one, `mise run lock-update`.
- **The pre-commit hook** formats and stops a commit Ruff can't fix (keep it under ~2 s; no heavy checks there); a file
  staged in part with `git add -p` is left unformatted and named.

## Pipeline

- **Gate**: `mise run gate`. **Review checks**: `ruff check <files>`, `basedpyright <files>`,
  `pnpm -C frontend exec eslint <files>` (paths relative to `frontend/`). **Worktree task**: `mise run worktree-new`.
- **Agent rules**:
  - Scratch files never go in the worktree — `lint:md` and `tsc` pick up any stray file and break the gate.
  - The gate takes 8–15 minutes; never start a second run while one is going.
  - A new backend test shares no file, port or global state with another (pytest-xdist).
  - Never raise a bundle size budget to make the gate pass; report the overrun.

## Invariant register

Rules that span files live in `docs/architecture/invariants/`, one file per area
([index](docs/architecture/invariants/index.md) maps areas to paths). **Before implementing or reviewing in an area,
read its file.** A rule is never weakened to stay green. Two rules stand here because a breach loses user data for good:

- **Every destructive operation has backup-or-confirm; never delete data that exists nowhere else.** — what holds it:
  [cross-cutting.md, "Backup or confirm"](docs/architecture/invariants/cross-cutting.md#backup-or-confirm)
- **Server-supplied path components pass `safe_join` (`lib/path_safety.py`).** — what holds it:
  [cross-cutting.md, "Server-supplied paths"](docs/architecture/invariants/cross-cutting.md#server-supplied-paths)

## Security

The RomM credentials in `~/.config/romm-tender/` are read or handed to an agent only with my OK, and never appear in a
log, report or public text.

## Working style

Research an unknown before building on it, and discuss architecture before code — explain a problem and offer options
rather than fixing it outright. Epic bodies carry no sub-issue lists; link sub-issues natively.
