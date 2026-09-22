# Development

Guide for setting up a development environment and contributing to Tender.

## Prerequisites

- [mise](https://mise.jdx.dev/) — manages Node, pnpm, and Python versions
- Git
- A Steam Deck or Linux PC running Steam, with CEF remote debugging enabled — that is what the backend loads the panel
  through, and it creates the marker itself when one is missing
  ([How the panel gets into Steam](../architecture/loading-the-panel.md#steams-remote-debugging-marker)).
  [Decky Loader](https://decky.xyz/) is no longer needed, and the panel coexists with one that is installed.

> **On Windows, develop inside [WSL2](https://learn.microsoft.com/windows/wsl/install).** The plugin targets Linux —
> some adapters import Unix-only modules (e.g. `fcntl`), a few dev dependencies have no Windows wheel, and CI runs on
> Linux. Native Windows is not supported for running the test suite; in a WSL2 Linux distro the same `mise install` /
> `mise run setup` / `mise run test` work unchanged.

## Setup

```bash
git clone https://github.com/danielcopper/romm-tender.git
cd romm-tender
mise install          # installs Node LTS, pnpm, Python
mise run setup        # installs JS + Python dependencies
```

This creates a Python virtual environment (auto-activated by mise via `_.python.venv` in `mise.toml`) and installs all
npm packages.

`mise run setup` also points `core.hooksPath` at `.githooks/`, so the repo's pre-commit hook formats staged files on
every commit. Git allows only one hooks path, so that setting replaces a global one rather than adding to it: the hook
therefore runs whatever global `pre-commit` you have installed first and aborts the commit if it refuses, which keeps a
guard you rely on in your other repos working here too. If you have no global hook, nothing changes.

Python dependencies are installed from `requirements-dev.lock` — fully-pinned versions compiled from
`requirements-dev.txt` by uv. After changing a source (`requirements-dev.txt` / `docs/requirements.txt`) or bumping a
pin, run `mise run lock-update` to regenerate the locks.

### Automated dependency updates

Update PRs are managed by [Renovate](https://docs.renovatebot.com/) (`renovate.json`) across pip, npm, and GitHub
Actions, and in-range minor/patch updates auto-merge once CI is green. For the full picture — **where every version
lives, what's coupled, what auto-merges, and how to bump things by hand** — see
[Dependency management](dependency-management.md).

The toolchain versions — `node`, `pnpm`, `python`, `uv`, `deno` — are **excluded** from Renovate: they are pinned and
cross-file-coupled (each appears in `mise.toml` and in `frontend/package.json`'s `packageManager` and/or the workflow
`setup-*` version inputs, and all copies must match — `python` to Decky's embedded libpython3.11, `uv` for lock
reproducibility). Renovate is disabled for these by dependency name so a bot bump can't desync one copy; bump them by
hand, together. The `setup-*` action SHAs themselves stay auto-updated.

## Building

```bash
pnpm -C frontend build   # Rollup -> the repository's dist/, not the package's
```

Rollup produces **three** files there, plus `@decky/ui`'s licence text beside them: `dist/globals.js` installs Steam's
React, and `dist/index.js` and `dist/index-coexistence.js` are the same panel differing in whether `@decky/ui` is
bundled into it or taken from Decky Loader's own copy. The backend serves `dist/` and loads one of the two panels into
Steam itself — [How the panel gets into Steam](../architecture/loading-the-panel.md). What each file is and why there
are two panels: [How the panel is built and loaded](../architecture/frontend-bundles.md).

## Testing

```bash
python -m pytest tests/ -q     # run the backend test suite
mise run test                   # same thing via mise
```

To run with coverage:

```bash
python -m pytest tests/ -q --cov=backend --cov-report=term --cov-branch
```

Tests mirror the source layout (`tests/services/`, `tests/adapters/`, `tests/domain/`, `tests/models/`, `tests/lib/`),
with each test file mapping 1:1 to a source module. Shared mocks live in `tests/conftest.py`, which also provides a mock
`decky` module so tests run without Decky Loader.

Frontend component tests run with `mise run test:frontend` (`pnpm -C frontend test`); see
`.claude/rules/testing-frontend.md` for the backend-event harness, and for what that suite cannot see — `api/host` is
stubbed suite-wide, so no socket is ever opened in it.

### Property-based tests

The pure decision kernels carry an extra tier of [Hypothesis](https://hypothesis.readthedocs.io/) property tests
alongside the hand-enumerated cases. They state the safety invariants directly and exercise them across a generated
input space. The in-tree kernels (`domain/save_path.py`, `domain/iso_time.py`) have theirs in
`tests/domain/test_*_property.py`; the save-sync decision runs in the compiled gavel core, so its properties drive
`GavelNativeAdapter` from `tests/adapters/test_gavel_native_property.py`. Run them like any other test:

```bash
python -m pytest tests/adapters/test_gavel_native_property.py tests/domain/test_save_path_property.py -q
```

Hypothesis is a dev-only dependency (pinned in `requirements-dev.txt`, compiled into `requirements-dev.lock` via
`mise run lock-update` — it never ships in the plugin). A CI-safe profile in `tests/conftest.py` sets `deadline=None`
(no timing flakes on shared runners) and a fixed example count. The example database is written to `.hypothesis/`, which
is gitignored. See `.claude/rules/testing-backend.md` for the convention on pinning a property that encodes an open bug.

### Contract tests

`tests/contract/` is a tier that crosses the frontend↔backend wire. Where the unit tests check each side against its own
mocked idea of the other, the contract tier builds the **real** `Plugin` through the **real** `bootstrap()` +
`wire_services()` (real settings dict, real SQLite + migrations, real file-store adapters, all under `tmp_path`) and
drives the actual `main.py` callables **exactly as the frontend does** — positional, JSON-shaped arguments with the arg
types declared in `frontend/src/api/backend.ts` (literal `None` where the TS type says `null`). The assertions pin the
response _shape_ (canonical failure shape, discriminated-status unions, partial-success flags), not delegation. Only the
outermost edges are faked: the RomM + SteamGridDB network transports, the Clock/UuidGen/Sleeper seams, `emit`, and the
retry backoff. Run them like any other test:

```bash
python -m pytest tests/contract/ -q
```

A `backend.ts` manifest gate (Phase 2) that pins the frontend and backend to one parsed artifact is a forthcoming
separate change. See `.claude/rules/testing-backend.md` for the full contract-tier rules.

### Gavel conformance vectors

The save-sync decisions are also published as a standalone client contract,
[romm-gavel](https://github.com/danielcopper/romm-gavel) — and since both of them run in gavel's compiled core (vendored
as `backend/native/libgavel-x86_64-linux.so`), the two vector families are what keep the shipped binary and the
published spec from silently drifting apart:

- **ladder** (the 409 resolution ladder) — `tests/adapters/test_gavel_native.py`.
- **decision-table** (the full per-`(rom, filename, slot)` decision) —
  `tests/adapters/test_gavel_native_table_vectors.py`.

Both families read the core through `GavelNativeAdapter`, the seam production decides on. The vectors are vendored
verbatim under `tests/adapters/gavel_vectors/`, one subdirectory per family mirroring upstream `vectors/` (`ladder/` — a
curated named-case set plus the exhaustive equivalence classes; `decision-table/` — curated named cases) — there is no
submodule and no network in CI, so every contract change lands as a reviewable diff. Run them like any other test:

```bash
python -m pytest tests/adapters/test_gavel_native_table_vectors.py tests/adapters/test_gavel_native.py -q
```

Updating the vectors means deliberately re-copying the JSON from the matching upstream `vectors/<family>/` directory and
bumping the release tag in `tests/adapters/gavel_vectors/README.md` — in lockstep with the `.so`, which is pinned to the
same release; never edit a vector to match the core.

### emu-atlas conformance vectors

The config-aware emulator knowledge — where a RetroArch / RetroDECK install keeps its saves — is likewise published as a
standalone library, [emu-atlas](https://github.com/danielcopper/emu-atlas), extracted from this plugin. Its `machines`
vector family (16 fixture machines in, detected installations + save placements out) runs against the plugin's own
save-path kernel in `tests/test_atlas_machine_vectors.py`, so the two can't silently drift. Each vector materializes a
`{path: content}` file tree under a `tmp_path` fake home, then drives the real adapters (`RetroDeckPathsAdapter` +
`RetroArchConfigAdapter`) and the domain save-path functions (`resolve_save_dir` / `compute_local_save_target`).

The overlap is partial, so every vector carries an explicit check level (an `_CHECK_LEVELS` allowlist entry that also
records _why_):

- **`full`** — end-to-end placement. The plugin derives the saves root the same way atlas does (from `retrodeck.json`,
  or the `~/retrodeck` fallback), so the final directory + filename strings are compared. Covers the RetroDECK-flavor
  `InSaveDir` cases and the RetroDECK-first coexistence case.
- **`layout-only`** — only the `retroarch.cfg` interpretation overlaps. The plugin has no standalone-RetroArch
  saves-root concept (its saves base always comes from RetroDECK paths), so a vector whose placement hangs off a
  standalone `savefile_directory` is checked on the `SaveLayout` the plugin derives from the same cfg text — the sort
  flags for an `InSaveDir` placement, or the `ContentDir` (next-to-ROM) classification.
- **`n/a`** — no overlap (the plugin has no installation-enumeration surface, so atlas's "nothing detected" outcome has
  no plugin equivalent). The check only guards that the vector stays in its non-checkable shape.

No vector is silently skipped: a new upstream vector without an allowlist entry (or a stale entry for a removed one)
fails at collection. The vectors are vendored verbatim under `tests/atlas_vectors/machines/` at a pinned upstream
release tag — no submodule, no network in CI. Run it like any other test:

```bash
python -m pytest tests/test_atlas_machine_vectors.py -q
```

Updating means deliberately re-copying the JSON from upstream `vectors/machines/` and bumping the release tag in
`tests/atlas_vectors/README.md`; never edit a vector to match the kernel.

Every backend feature or callable where testing makes sense should have unit tests covering:

- **Happy path** — normal successful operation
- **Bad path** — invalid input, missing data, API errors, network failures
- **Edge cases** — empty strings, None values, boundary conditions

## Running it

```bash
mise run dev
```

Builds the panel, **restarts the running Steam**, and runs the backend in the foreground. The backend binds a loopback
port, serves `dist/` from it, and loads the panel into Steam's renderer over the CEF debugger — nothing is copied into a
plugin directory and no plugin loader is restarted. Ctrl-C stops it and lets it unload.

**The restart closes whatever is open in Steam**, and the task does it rather than leaving it to you: there is no hot
reload, so a rebuilt bundle reaches Steam only in a fresh JS context, and a new backend process strands the panel the
old one loaded. Steam comes back into the window and display a `dev:bpm*` / `dev:desktop*` task last chose — the desktop
client, placed nowhere, if none has. A Steam that is not running is simply started.

Steam's remote-debugging marker has to exist, and the backend creates it when it does not. Steam reads it at start-up,
so the task's own restart is what picks up a marker that has just been created
([the marker](../architecture/loading-the-panel.md#steams-remote-debugging-marker)).

The whole loop, the Big Picture window, and how to judge layout at the Deck's real metrics are in
[Frontend dev loop](frontend-dev-loop.md); what the injector does and how it protects the Steam UI from itself is in
[How the panel gets into Steam](../architecture/loading-the-panel.md).

Two switches exist, both read from the environment at start-up: `TENDER_INJECT=off` serves the panel and loads it
nowhere, and `TENDER_INJECT=force` loads it even where the crash watchdog has stopped.

### Running an installed one

`mise run dev` is the development loop. What a user gets instead is a systemd **user** unit,
`~/.config/systemd/user/romm-tender.service`, written by `install.sh`:

```bash
systemctl --user status romm-tender     # what it is doing
systemctl --user restart romm-tender    # after replacing the code by hand
systemctl --user cat romm-tender        # the roots this install resolved
journalctl --user -u romm-tender        # what it wrote to stderr
```

Its own log is `~/.local/state/romm-tender/backend.log`, and the journal carries the same lines plus the one start-up
address with the token UNREDACTED. The log file has that line too, with the token replaced — the redaction is the file
handler's own formatter, so the two differ deliberately rather than by accident.

**Tender runs as a service, and the Quick Access entry is something it PUTS there.** The entry is not a file Steam reads
at start-up — it is code this backend loads into Steam's renderer — so a unit that is not running when Steam starts
means a Steam with no Tender entry in it, and starting the unit puts one there without restarting Steam. A backend that
stops after it has loaded the panel leaves the entry where it is: the code is already in Steam, and what it loses is the
backend to talk to.

To install a build of your own rather than a release:

```bash
mise run package                              # builds the frontend, writes build/romm-tender-<V>.tar.gz
bash install.sh --from build/romm-tender-<V>.tar.gz
```

That is the same path a release takes, with the download skipped — `install.sh --uninstall` takes it back out, and
leaves the database, the settings and the launcher where they are.

Literally the same path: the release workflow's second job runs those same two steps on the tagged tree and attaches
what comes out, so there is one producer for a local build and a published one. What it attaches is held to
`scripts/check_release_tarball.py`, which opens the archive and asserts what the installer relies on — one top-level
`romm-tender/` directory, the files an install starts from together with the version file and the licence texts a
distributed copy carries, nothing the packager prunes, and a sidecar `sha256sum -c` accepts. That check is not first run
at the tag: CI's build job packs a tarball from every pull request's own build and runs it there too, for the reason
[ADR-0039](../adr/0039-the-release-ships-the-packagers-tarball.md) gives. What no run of it can say is whether the code
inside works — the script's own docstring states the blind spot.

## Linting

```bash
PYTHONPATH=backend lint-imports   # check service/adapter layer rules
mise run lint                     # same via mise
```

The `.importlinter` config enforces the layer boundary contracts:

- Services must not import concrete adapter implementations (Protocols are allowed)
- Adapters must not import services
- Utilities (`lib/`) must not import services, adapters, or domain
- Domain must not import services or adapters (`lib` is allowed)
- Models must not import services, adapters, domain, or lib
- Services must not import stdlib I/O / non-deterministic primitives (`time`, `uuid`, `random`, `subprocess`,
  `threading`, `requests`)
- Services must be independent of each other (no cross-service imports)

`mise run lint` also runs `scripts/check_cosmic_call_bans.sh`, which complements the import rules at the call site:
services may not call `datetime.now()` / `asyncio.sleep()` / `time.time()` / `time.monotonic()` / `uuid.uuid4()` /
`random.*` directly — they inject the `Clock` / `Sleeper` / `UuidGen` Protocol instead.

`mise run lint` (and CI) also runs `scripts/check_service_independence_contract.py`, which derives the expected service
list from `backend/services/` and fails if `.importlinter`'s `service-independence` contract drifts — omitting a service
or carrying a stale entry — keeping the hand-maintained `modules` list self-healing.

`mise run lint` (and CI) also runs `scripts/check_failure_shape.py --check`, which fails if any `success: False` return
in `services/` is missing the canonical `reason` + `message` keys or carries the forbidden `error` / `error_code` key —
collapsing the failure-shape dialects onto one vocabulary (the two documented carve-outs are pattern-exempt). Run it
without `--check` for a report-mode inventory.

`mise run lint` (and CI) also runs `scripts/check_callable_manifest.py`, which pins the frontend↔backend callable
surface to one source of truth: it derives the frontend names + arities from every `callable<[Args], Return>("name")` in
`frontend/src/**/*.ts` and the backend surface from the public `async def` methods on the `Plugin` class in `main.py`,
then fails if they diverge — a callable declared on only one side (either direction) or a matching name whose arity
(positional param count) differs. Arg types stay out of scope (Python signatures carry no hints), so arity is the only
mechanically checkable shape. The same parity assertion is surfaced inside the pytest run by
`tests/contract/test_callable_manifest.py`.

`mise run lint` (and CI) also runs `scripts/check_event_parity.py`, which fails if a backend `emit("name", ...)` event
has no matching frontend `addEventListener("name", ...)` (or vice versa). The event names are bare string literals, so
the gate matches the two surfaces by literal event name — the backend side parsed via AST (`emit` / `_emit` calls), the
frontend side via a text scan of bare `addEventListener` calls. Static sibling of the callable-manifest gate, for the
event channel. The same parity assertion is surfaced inside the pytest run by `tests/contract/test_event_parity.py`.

`mise run lint` (and CI) also runs `scripts/check_settings_owner.py`, which fails if the `settings.json` filename
literal appears anywhere except its owning adapter (`adapters/persistence.py`); confining the literal to one module
keeps all settings writes in the single crash-safe owner.

`mise run lint` (and CI) also runs `scripts/check_module_size.py`, the decomposition-threshold ratchet: no module in
`services/`, `bootstrap/`, `adapters/`, `domain/`, `lib/` or `models/` may cross the ~1000-LOC threshold, and the
modules that were already over it when the gate landed are pinned at their exact size, so they cannot grow. A pin moves
up in exactly one case: a change that adds no code — a rename, or a reformat whose only effect is that the formatter
re-wraps lines that were already there — may raise it to the newly measured size, with the reason recorded at the
`ALLOWLIST` entry and argued in the PR. Nothing else qualifies, and deleting lines elsewhere to pay for the ones you add
least of all — that is the growth the gate exists to stop. A raise taken silently has retired the gate, which costs more
than any module's size. The pin list lives in the script and entries only ever come out — a module that drops back under
the threshold has to leave it, and the gate fails until it does — while a module that banks 50+ lines of slack gets a
non-fatal note asking for its ceiling to be lowered. What the gate does not walk is listed at `SCOPE_DIRS` with the
reason for each: `main.py` grows with the callable surface by design, `_vendor/` holds checksum-pinned upstream copies,
a large file under `tests/` is the one-file-per-source-module rule working, `scripts/` never ships, and `frontend/src/`
needs a per-scope glob before it can be added. There is deliberately no `--update` flag — re-baselining should be a
reviewable diff, never a command someone runs to get back to green.

`mise run lint` (and CI) also runs `scripts/check_generated_installer_logo.py`, which holds `install.sh`'s greeter to
the mark. The installer draws the logo before it does anything, and it cannot render an image, so the art is text
embedded in the script between two markers — drawn and rasterised by `scripts/logo/terminal.py` (so it needs librsvg,
which CI installs for the check). There are two drawings: a half-block icon, where each cell's two colours ARE the
picture, for a terminal in a UTF-8 locale that takes colour, and a class-drawn ASCII one for a terminal that is not. A
run that may write no colour gets no icon at all, because half-blocks in one tone are a slab rather than a mark. A
drawing kept by hand is the one that drifts away from the mark it is a picture of, so the check regenerates both, and
the wordmark beside them, and fails on any difference; re-run `python3 scripts/logo/build.py --install --terminal` and
commit the result. What the two drawings are and why they differ is `scripts/logo/README.md`.

`mise run lint` (and CI) also runs `scripts/check_shell_answer_functions.py`, which holds the repository's shell to one
rule: **a function whose value is taken with `$(...)` never reaches `exit`.** `exit` inside a command substitution ends
that subshell and nothing else, so a function that answers with a value and aborts on a bad input does neither — it
prints its message, and the caller carries on with an empty answer, complaining a second time about the emptiness or
building a request out of it. Both end up non-zero, which is why the shape survives a test that only reads the status.
The answer is that such a function RETURNS non-zero and its caller aborts.

Which helper ends the run is derived rather than listed: a function "reaches exit" if it runs `exit` itself or calls one
of the same file's functions that does, so a second abort helper is covered the day it is written. What the gate scans
is `install.sh`, `scripts/package.sh`, `bin/tender-rom-launcher` — named because it carries no extension the glob would
find — and every `*.sh` under `scripts/` and `bin/`, through a hand-written lexer that knows quotes, comments, heredocs
and nesting — not a bash parser. Its blind spots are listed in the script's docstring, which is their one home; the two
worth knowing at this distance are that a function reached through a variable is invisible to it — `install.sh`'s own
`step` is the live example — and that `( f )` and `f | cmd` swallow an `exit` the same way without being checked.
Because the lexer is hand-written rather than bash, a construct it misreads drops real code in silence, so the shapes it
gets right are pinned one by one in its test file.

The frontend has no size gate — deliberately, because a threshold only works when something else forbids the cheap way
of getting under it, and `frontend/src/` has no equivalent of `service-independence`. What it has instead is direction
rules, in `frontend/eslint.config.js` via `eslint-plugin-import-x`: `frontend/src/utils/` and `frontend/src/api/` may
not import either surface (`frontend/src/bigpicture/` or `frontend/src/desktop/`), the two surfaces may not import each
other, and no module in `frontend/src/` may take part in an import cycle. The cycle rule is the one that matters most,
because a cycle is the signature of a split whose two halves still call each other — the wrong seam, detectable without
judgment. What none of them catch is a helper imported by exactly one parent that takes a dozen parameters and does
nothing on its own: it is neither a cycle nor a direction violation. These rules make the worst seam fail; they do not
certify that a seam is right.

Two settings in that config are load-bearing and neither is the plugin's default. `import-x/extensions` ships as
`['.js']`, so until it names `.ts`/`.tsx` the plugin resolves an import but never opens the target file to read _its_
imports — `no-cycle` then walks a graph one edge deep and reports nothing, on any codebase. `import-x/parsers` supplies
the parser it needs for that reading. Because the failure mode is silence rather than noise,
`frontend/src/eslintBoundaries.test.ts` lints known-bad fixtures through the real config and fails if any of the seven
rules stops reporting. A green `pnpm -C frontend lint` on its own does not distinguish a working rule from an inert one.

See [Backend Architecture](../architecture/backend-architecture.md) for details.

## Full CI gate

```bash
mise run gate         # run every PR check from .github/workflows/ci.yml, locally
```

`mise run gate` is the single local battery that mirrors CI. It runs the backend tests (`mise run test`) and the
architecture/lint gates (`mise run lint`), then adds the rest of what CI enforces: `ruff check` + `ruff format --check`,
`basedpyright`, the frontend `eslint` / `prettier --check` / build / `tsc` typecheck / bundle-size budget, the frontend
tests (`pnpm -C frontend test`), and `deno fmt --check` for Markdown. It is slow — a full pytest run plus a production
frontend build — so it is a pre-push check, not something to run on every save. The only CI jobs it can't reproduce are
the SonarCloud scan and its `sonar-gate` (they need `SONAR_TOKEN` and the CI coverage artifacts).

## Code Quality

- **SonarCloud** — CI-based analysis on every human PR and push to main. Quality Gate enforces 80% coverage on new code,
  0 bugs, 0 vulnerabilities. The scan is skipped on Dependabot PRs (no `SONAR_TOKEN` access in that restricted context);
  the required status check is the `sonar-gate` job, which passes when SonarCloud succeeded or was skipped and fails
  only when it failed — so dependency PRs aren't deadlocked on a check that can never run for them.
- **Ruff** — Python linting in CI. Expanded ruleset includes B (bugbear), SIM (simplify), UP (pyupgrade), RUF
  (ruff-specific), and ARG (unused arguments) in addition to the base E/F rules.
- **basedpyright** — Type checking in CI. Checks all source files including the test suite (tests/ is not excluded).
- **import-linter** — Layer boundary enforcement in CI (see Linting section above).
- **pytest-cov** — Branch coverage reported to SonarCloud.
- **pytest-timeout** — Bounds a single test at 120 s (`timeout` in `pytest.ini`), so a test that blocks fails by name
  instead of running the CI job out of its `timeout-minutes: 15` with nothing to say which test it was; on the main
  thread the default `signal` method raises inside the test, so the rest of the session still runs. Reading such a
  failure: the traceback is only as sharp as the block is synchronous — a test stuck on an `await` points into the event
  loop's own `select()` rather than at the awaiting line, so the test name is what identifies it. A test that
  legitimately waits longer raises its own with `@pytest.mark.timeout(<seconds>)`.

## Where the coding conventions live

Two files, split by how often they apply:

- **`CLAUDE.md`** (repo root) — the traps, the cross-cutting invariant register, and the workflow. Everything here
  applies no matter which file you touch, so it is read up front.
- **`.claude/rules/*.md`** — the per-area conventions (services, adapters/domain, Python naming and docstrings, callable
  shapes, bootstrap wiring, vendored assets, backend and frontend testing). Each file carries a `paths:` frontmatter
  glob and is loaded when a matching file is opened, which keeps the always-on set small.

Most of what lives in `.claude/rules/` has no mechanical check — Protocol suffixes, constructor shape, docstring intent,
verb-named mutations — so those rules hold only if they are carried while writing. `CLAUDE.md` indexes them with the
failure mode each one prevents.

Note that `.gitignore` ignores `.claude/*` (worktrees, local agents, `settings.local.json`) and re-includes
`.claude/rules/` explicitly. Adding a rule file works; adding anything else under `.claude/` will silently not be
tracked.

## Project Structure

```text
backend/
  main.py                            # Plugin entry — Decky lifecycle + callable surface
  bootstrap/                         # Composition root — re-exported through __init__.py
    adapters.py                      # bootstrap() builds every adapter and the typed bundles
    services.py                      # wire_services() builds every service from those bundles
  services/                          # Orchestration / business logic (Protocol-typed deps via *ServiceConfig)
    protocols/                       # Protocol interfaces, grouped: transport / determinism /
                                     #   persistence / paths / infra / files / cross_service
    library/                         # LibraryService façade — fetcher, sync_orchestrator, reporter, shared state box
    saves/                           # SaveService aggregate — state, sync_engine/, slots/, status/, versions
    downloads.py                     # DownloadService — ROM downloads, ZIP/M3U, fcntl queue
    firmware/                        # FirmwareService façade — listing, demand, status, downloads, deletion
    session_lifecycle.py             # SessionLifecycleService — post-exit orchestration
    migration/                       # MigrationService — RetroDECK home migration; SaveSortMigrator — save-sort migration
    steamgrid.py                     # SteamGridService — SteamGridDB artwork
    artwork.py                       # ArtworkService — cover art staging/cleanup
    game_detail.py / playtime.py / achievements.py / settings.py / cores.py
    metadata.py / rom_removal.py / shortcut_removal.py / launch_gate.py
    startup_healing.py / connection.py
  adapters/                          # I/O boundaries — implement Protocols
    romm/{http,romm_api}.py          # RomM HTTP transport + REST adapter
    steam_config.py / steamgriddb.py / sgdb_artwork_cache.py / cover_art_file_store.py
    persistence.py                   # settings.json read/write + one-time legacy save_sync_state fold
    repositories/                    # SqliteUnitOfWork (unit_of_work.py) + 9 repos (8 aggregate + kv_config)
                                     #   (rom, rom_install, rom_metadata, playtime, rom_save_sync_state,
                                     #    bios_file, firmware_cache, sync_run, kv_config)
    sqlite_migrations.py / machine_id.py  # schema migration runner (PRAGMA user_version) + machine-id reader
    download_file.py / firmware_file.py / migration_file.py / rom_files.py / save_file.py
    retrodeck_paths.py / retroarch_config.py / retroarch_core_info.py / es_find_rules.py
    atlas_catalogue.py / atlas_firmware.py / atlas_saves.py  # the adapters over the vendored emu-atlas resolver
    system_clock.py / system_uuid_gen.py / asyncio_sleeper.py / hostname.py / path_probe.py / debug_logger.py
  db/
    migrations/001_initial.sql       # SQLite schema DDL
  domain/                            # Pure compute — no I/O, no service/adapter imports
    _aggregate.py                    # the @cosmic_aggregate decorator
    rom.py / rom_install.py / rom_metadata.py / rom_metadata_mapping.py / playtime.py
    rom_save_sync_state.py / bios_file.py / firmware_cache.py / sync_run.py
    sync_action.py / sync_diff.py / preview_delta.py / work_unit.py
    save_path.py / save_status*.py / save_attribution.py / save_answer.py
    firmware_paths.py / bios.py / achievements.py / shortcut_data.py / steam_categories.py
    sgdb_artwork.py / installed_roms.py / rom_files.py / retroarch_core_info.py
    state_migrations.py / sync_state.py / emulator_tag.py / version.py
  models/                            # Data shapes (TypedDicts/dataclasses) — independent of other layers
  lib/                               # Cross-cutting utilities (errors, list_result, iso_time, path_safety, late_binding, ...)
  _vendor/                           # Vendored third-party deps — not our code, only imported by adapters
    README.md                        # Provenance per package: upstream URL, version/commit, local patches
    vdf/                             # Valve Data Format parser (Steam shortcuts.vdf)
      LICENSE                        # Upstream MIT license — preserved on redistribution
frontend/src/                        # Frontend TypeScript
  index.tsx                          # Plugin entry, event listeners, QAM router, the Quick Access entry's install
  qam/                               # Tender's own Quick Access entry: the patch, the tab glyph, the panel's boundary
  bigpicture/                        # The gamepad surface: React components (QAM pages, game detail UI)
    layout/                          # Wide-page frame primitives: WidePage, ScrollRegion, Columns, ListDetail, pane
    library/ settings/ sync/         # Component groups for the Library, Settings and Sync pages
    saves/                           # Slot and save-file components, shared across the game-detail panel's tabs
    patches/                         # The game-detail route patch
  desktop/                           # The desktop-client surface — peer of bigpicture/, see its README
  api/backend.ts                     # callable() wrappers (typed)
  types/                             # TypeScript interfaces and Steam API declarations
  utils/                             # Shortcut CRUD, sync, downloads, collections, session manager, store patches
bin/tender-rom-launcher              # Pure exec wrapper — installed to <bin root> at every start, and run from there
defaults/config.json                 # platform_map: 153 platform slug -> RetroDECK system mappings
tests/                               # Backend unit tests, mirroring backend/ layout
```

See [Backend Architecture](../architecture/backend-architecture.md) for the service/adapter design, dependency diagram,
and layer enforcement rules.
