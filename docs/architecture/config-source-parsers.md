# Config Source Parsers

## Overview

The plugin reads configuration and metadata from multiple local files — ES-DE XML, RetroArch `.info` files,
`retrodeck.json`, `retroarch.cfg`, and more. Each source has its own format, its own update cycle, and its own
authoritative domain. None of them is redundant with another, even when they describe overlapping concepts like "core
name" or "supported extensions". (Two sources were dropped along the way: ES-DE's `gamelist.xml` until #947 — the plugin
no longer reads or writes it — and the bundled `core_defaults.json` snapshot until #1210, which made the live
`es_systems.xml` the sole core/emulator source; see the [dropped-source rows](#current-parsers) below.)

This page catalogs the sources, the rules for parsing them, and the mapping of questions to sources. It is the reference
that new parsers should follow and the justification for why existing parsers are shaped the way they are.

**Not covered on this page:** HTTP API clients (RomM, SteamGridDB, RetroAchievements). Those are handled as
network-facing adapter clients, not as config-source parsers. The same layering principles apply but the mechanics
(auth, retries, rate limiting) are different enough that they belong in their own documents.

## Why multiple sources?

RetroDECK is a stack of independent components — ES-DE as the frontend, RetroArch as the runtime, a glue config layer on
top, plus bundled tooling. Each component owns its own metadata in its own format:

- **ES-DE** keeps its system definitions in `es_systems.xml` and display-oriented labels it chose to include — the
  plugin reads a system's **default emulator** (the first safely-bakeable `<command>`) and the full classified command
  list the picker offers from here, and this is the **sole** source for that since #1210 (no bundled snapshot). (ES-DE
  also keeps per-game and per-system core choices in `gamelist.xml`, but the plugin no longer reads or writes that file
  — core deviations are the plugin's own state; see [Core and Emulator Selection](core-emulator-selection.md).)
- **RetroArch** keeps per-core metadata in `.info` files shipped alongside each `.so` — `corename`, `display_name`,
  `supported_extensions`, `firmware_*`, `database`, and more. It decides how saves are organized on disk, which firmware
  to require, and what extensions each core accepts.
- **RetroDECK glue** keeps user-facing configuration in `retrodeck.json` (paths for ROMs, saves, BIOS, state) and
  forwards RetroArch's own `retroarch.cfg` (sort settings, core directories, and everything else RetroArch reads at
  startup) — which the vendored emu-atlas resolver reads, not the plugin.

There is no unified source. The upstream components are developed independently, updated on independent schedules, and
have no mechanism to consolidate their metadata. The plugin must read each source individually.

Attempting to "normalize" these into a single internal model would fight against upstream — every time ES-DE renames a
label or RetroArch ships a new core, the normalization table would drift. The sustainable approach is the opposite: keep
each source as its own parser, keep the parsers ignorant of each other, and let services choose the right parser per
flow.

## Principle: one parser per source, no cross-contamination

Each external source gets exactly one parser. Parsers do not fall back to each other. Services are responsible for
picking the right parser for each business flow.

The principle has three parts:

1. **One parser per source.** Adding a second reader for the same file creates drift — sooner or later the two readers
   disagree and the bug lives in the newer one. `retrodeck.json` has one parser. `es_systems.xml` has one parser. Every
   new source follows suit.

2. **No cross-contamination.** When a parser cannot answer a question — the file is missing, a field is absent, the
   value is malformed — the parser returns `None` (or raises), it does **not** defer to another parser. Cross-parser
   fallbacks are how ES-DE's display label ended up being used as a RetroArch save directory name (the underlying bug of
   [#208](https://github.com/danielcopper/romm-tender/issues/208)). Each source owns its own answer or admits it has
   none.

3. **Services choose the parser per flow.** "Which core is active for this ROM?" is an ES-DE question. "What does
   RetroArch call that core in its own subsystem?" is a RetroArch question. Both questions may appear inside the same
   service method, and the service is responsible for asking each one at the right parser. Do not invent a
   parser-of-parsers to hide the choice.

### Worked example: ES-DE label vs RetroArch corename

The concrete case that motivated this principle, and the bug in
[#208](https://github.com/danielcopper/romm-tender/issues/208):

- `AtlasCatalogueAdapter` (asks the resolver, which reads ES-DE) returns a tuple `(core_so, label)` for "which core is
  active?". `label` is ES-DE's **display string** — e.g. `"Snes9x - Current"`. It is a UI-level name, chosen by the
  ES-DE/RetroDECK team to disambiguate in the core picker UI.
- RetroArch, when `sort_savefiles_enable = true`, writes saves into subdirectories named by the **`corename`** field of
  the core's `.info` file — e.g. `"Snes9x"`. It is RetroArch's canonical internal name, set by the core's maintainer,
  baked into RetroArch's runtime path logic.

These two values **are not redundant representations of the same thing**. They answer different questions at different
layers:

| Core            | ES-DE label        | RetroArch `corename` |
| --------------- | ------------------ | -------------------- |
| Snes9x          | `Snes9x - Current` | `Snes9x`             |
| mGBA            | `mGBA`             | `mGBA`               |
| Beetle PSX HW   | `Beetle PSX HW`    | `Beetle PSX HW`      |
| SwanStation     | `SwanStation`      | `SwanStation`        |
| Genesis Plus GX | `Genesis Plus GX`  | `Genesis Plus GX`    |

Four out of five happen to match textually. Snes9x does not — ES-DE added `" - Current"` to disambiguate from the older
`Snes9x 2010` variant. The match is **incidental**, not structural. Future cores, future ES-DE redesigns, and future
RetroDECK re-labelings will introduce new mismatches.

Reconciling by whitelist — a table of "ES-DE label → RetroArch corename" mappings — would be a perpetual maintenance
burden. Every new core, every label change, every RetroDECK release shifts the table. The correct answer is to not
reconcile at all: when you need the save directory name, ask RetroArch; when you need the UI label, ask ES-DE. The
lookup is O(1) per source, caching is local, and drift is impossible because neither parser pretends to speak for the
other.

The plugin no longer puts the save-directory question itself: where a game's save sits, a sort-by-core subfolder
included, is the resolver's answer, which reads RetroArch's own configuration and the core's `corename` the way
RetroArch does ([ADR-0040](../adr/0040-the-save-directory-is-the-resolvers-answer.md)). The example stays because it is
the clearest case of the rule.

## Question-to-source mapping

| Question                                                                       | Authoritative source                                                                                    | Why                                                                                                                                                                                  |
| ------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| What's the **default** emulator for **system X**?                              | ES-DE `es_systems.xml` (live, sole source)                                                              | The default is the first safely-bakeable `<command>` in ES-DE's document order (libretro or standalone). The plugin reads the live file only — never a snapshot, never the gamelist. |
| Which core has the user **pinned for a platform** (per-platform deviation)?    | `settings.json` `platform_cores` map                                                                    | A per-platform core is the plugin's own user-set intent (ADR-0003 bucket 1), not ES-DE's. See [Core and Emulator Selection](core-emulator-selection.md).                             |
| Which core is active for **ROM Y** (per-game)?                                 | Plugin DB (`roms.emulator_override`), layered on the platform and system layers                         | The per-game override is the plugin's own state, not ES-DE's. See [Core and Emulator Selection](core-emulator-selection.md).                                                         |
| Which emulator will **ROM Y actually launch with** (active core)?              | `ActiveCoreResolver`: per-game DB → per-platform `settings.json` → live es_systems.xml default → `None` | One resolver folds the plugin's two deviations over the live ES-DE default; the launched emulator is baked from the same answer (libretro or standalone).                            |
| What's the ES-DE display label for a core?                                     | ES-DE                                                                                                   | Label is an ES-DE/RetroDECK UI concern, chosen at the ES-DE config level.                                                                                                            |
| Where does an emulator keep one game's save (sort-by-core subfolder included)? | The save answer — the vendored resolver                                                                 | The resolver reads `retroarch.cfg` and the core's `.info` `corename` the way RetroArch does, so the plugin reads neither for a save path (ADR-0040).                                 |
| What ROM extensions does a core support?                                       | RetroArch `.info` `supported_extensions` field                                                          | libretro-maintainer-authoritative, updated with every core release.                                                                                                                  |
| What firmware files does a core need?                                          | RetroArch `.info` `firmware_count` + `firmwareN_*` fields                                               | libretro-maintainer-authoritative; optional flags included.                                                                                                                          |
| What datfile database matches a core's ROMs?                                   | RetroArch `.info` `database` field                                                                      | libretro-maintainer-authoritative.                                                                                                                                                   |
| Where does RetroDECK put ROMs, saves, BIOS, states, and its home directory?    | `retrodeck.json`                                                                                        | RetroDECK owns path configuration; it's the file users edit via the RetroDECK configurator.                                                                                          |

If a new question appears, the first step is to figure out which source authoritatively owns it. The mapping above grows
as new questions are added — treat this table as part of the contract, not a passive catalog.

## Parser layout template

New parsers follow a strict domain-plus-adapter split, matching the broader service/adapter architecture documented in
[Backend Architecture](backend-architecture.md): a pure parse function in `domain/`, all I/O in an `adapters/` class,
and a callback Protocol that services depend on. The examples below are written for a RetroArch `.info` parser; the
plugin has none of its own — the vendored resolver reads `.info` files — so they illustrate the shape rather than name
code in the tree.

```text
┌──────────────────────────────────────┐
│ services/                            │
│   <SomeService> depends on callback  │
└────────────┬─────────────────────────┘
             │ uses
┌────────────▼─────────────────────────┐
│ services/protocols/ (paths.py)       │
│   <Capability>ProviderFn (Protocol)  │
└────────────▲─────────────────────────┘
             │ implemented by method of
┌────────────┴─────────────────────────┐
│ adapters/<source>.py                 │
│   <Source>Adapter (I/O + caching)    │
└────────────┬─────────────────────────┘
             │ delegates parsing to
┌────────────▼─────────────────────────┐
│ domain/<source>.py                   │
│   pure parse function(s)             │
└──────────────────────────────────────┘
```

### 1. Pure parser in `backend/domain/<source>.py`

A module of pure functions. No I/O, no logging, no filesystem access. Takes text (or already-loaded data) as input and
returns a structured representation.

```python
# domain/<source>.py — illustration: a RetroArch .info parser

def parse_core_info(text: str) -> dict[str, str]:
    """Parse a RetroArch .info file's content into a key-value dict.

    Format: INI-like. Lines of the form `key = "value"`, `#` comments,
    blank lines. All values returned as strings — the caller decides
    how to interpret each field.
    """
    ...
```

Why in domain: parsing is pure logic. It's exhaustively testable with inline text fixtures — no tmp_path, no mocks, no
subprocess. This is the cleanest possible unit under test, and it's where format-level edge cases belong (comments,
blank lines, unquoted values, escaped characters, line-continuation quirks, etc.).

### 2. I/O-owning adapter in `backend/adapters/<source>.py`

A class with a single responsibility: resolve the right file path(s), read bytes, delegate parsing to the domain module,
cache the result, handle I/O errors.

```python
# adapters/<source>.py — illustration: a RetroArch .info adapter

class RetroArchCoreInfoAdapter:
    _SYSTEM_CORES_DIR = "/var/lib/flatpak/app/net.retrodeck.retrodeck/current/active/files/..."
    _USER_CORES_SUFFIX = os.path.join(".local", "share", "flatpak", ...)

    def __init__(self, *, user_home: str, logger: logging.Logger) -> None: ...

    def get_core_info(self, core_so: str) -> dict[str, str] | None:
        """Resolve path, read file, parse. None on any failure."""

    def get_corename(self, core_so: str) -> str | None:
        """Convenience accessor for the corename field."""
```

Why in adapter: anything that touches the filesystem (open, read, stat, glob) is I/O and belongs in the adapter layer
per the plugin's layering rules. Adapters are allowed to import domain modules — the reverse is not allowed (enforced by
import-linter).

### 3. Protocol(s) in `backend/services/protocols/`

Services never import concrete adapters. They depend on callable protocols defined in the `services/protocols/` package
(config-source parsers live in `paths.py`), and the concrete adapter method is wired up by `bootstrap/`.

```python
class SupportedExtensionsProviderFn(Protocol):
    def __call__(self, core_so: str) -> frozenset[str] | None: ...
```

One protocol per capability, not one protocol per adapter. This matches the existing pattern of individual callback
protocols (`SavesPathProvider`, `RomsPathProvider`, `BiosPathProvider`, etc.) introduced by the #196 refactor. When the
same adapter exposes multiple capabilities (`get_corename`, `get_supported_extensions`, `get_firmware_requirements`, …),
each gets its own protocol so services can depend on only what they actually use — and a test double only needs to stub
the callables a given test exercises.

### 4. Wiring in `backend/bootstrap/`

Adapter instance is created in `bootstrap/adapters.py`, and `bootstrap/services.py` threads its method into services
that need it. Illustration — `RetroArchCoreInfoAdapter` is the hypothetical adapter from step 2, and `SomeService` is a
placeholder:

```python
retroarch_core_info = RetroArchCoreInfoAdapter(user_home=user_home, logger=logger)
emulator_catalogue = AtlasCatalogueAdapter(choose_installation=..., ...)

some_service = SomeService(
    config=SomeServiceConfig(
        ...,
        get_active_core=emulator_catalogue.get_active_core,                        # ES-DE question
        get_supported_extensions=retroarch_core_info.get_supported_extensions,     # RetroArch question
    ),
)
```

The service receives callbacks, not an adapter. It has no knowledge of which file or format they come from — which keeps
services independent of any single parser and makes them straightforward to test with fake callables.

### 5. Tests at each layer

- **`tests/domain/test_<source>.py`** — pure-parse tests with inline string fixtures. Edge cases, malformed input,
  Unicode, whitespace variations, missing fields. No tmp_path, no mocks.
- **`tests/adapters/test_<source>.py`** — adapter tests with `tmp_path`. Happy path, file missing, `OSError`, cache
  hits, candidate-path fallback. This is the only layer that touches real files.
- **`tests/services/test_<service>.py`** — service tests with the callback mocked (`MagicMock`). Flow tests: what
  happens on `None`, what happens on success, what happens when the callback is not injected at all.

## Current parsers

| Source                         | Format                  | Parser location                                         | Layer status                                 | What it answers                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| ------------------------------ | ----------------------- | ------------------------------------------------------- | -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `retrodeck.json`               | JSON                    | `adapters/retrodeck_paths.py` — `RetroDeckPathsAdapter` | ✅ adapter (correct layering)                | Where RetroDECK puts saves, ROMs, BIOS, and its home directory.                                                                                                                                                                                                                                                                                                                                                                                                |
| `retroarch.cfg`                | INI-ish `key = "value"` | _none — read by the vendored resolver_                  | ⛔ no longer read by the plugin              | **Removed.** The plugin used to read the save-sorting flags from it to build save paths; the save answer now carries the directory those flags produce ([ADR-0040](../adr/0040-the-save-directory-is-the-resolvers-answer.md)).                                                                                                                                                                                                                                |
| `es_systems.xml`               | XML                     | `_vendor/atlas`, through `adapters/atlas_catalogue.py`  | ✅ adapter (correct layering)                | The **sole** core/emulator source (#1210): every `<command>` per system, the default emulator, the libretro active core for the BIOS filter, and the `<extension>` accept-list. The resolver applies ES-DE's `custom_systems` overlay and states its entries in EFFECTIVE order; the adapter re-sorts on the shipped `declared_index` so document order decides the default (ADR-0030). The pure classifier `domain/emulator_commands.py` decides bake-safety. |
| `es_find_rules.xml`            | XML                     | `adapters/es_find_rules.py` — `EsFindRulesAdapter`      | ✅ adapter (correct layering)                | Where an emulator's **binary** is, which the catalogue never states. Two questions: whether a standalone emulator is installed in RetroDECK (a bakeable one that is absent is downgraded to `needs_setup`/`not_installed`, ADR-0020 §2 — absence-only, so `systempath`-only / unreadable cases assume installed), and the sandbox component launcher the folder-boot bake execs (ADR-0019). Mtime-cached parse; the on-disk probes run per call.               |
| `gamelist.xml`                 | XML                     | _none — dropped in #947_                                | ⛔ no longer read or written                 | **Removed.** The plugin used to read the system-level `<alternativeEmulator>` and write it from the retired System page; it now neither reads nor writes the gamelist. Core deviations are the plugin's own state — per-platform in `settings.json` `platform_cores`, per-game in the DB (see [Core and Emulator Selection](core-emulator-selection.md)).                                                                                                      |
| `core_defaults.json` (bundled) | JSON                    | _none — dropped in #1210_                               | ⛔ no longer read (file + generator deleted) | **Removed.** Was a bundled snapshot generated _from_ `es_systems.xml` (via `scripts/generate_core_defaults.py`) as an offline default/standalone-curation fallback. Deleted because the live file is a strict superset and RetroDECK is a hard prerequisite, so the fallback could never help a launch (see [ADR-0020](../adr/0020-live-es-systems-emulator-resolution.md)).                                                                                   |
| RetroArch `.info`              | INI-ish `key = "value"` | _none — read by the vendored resolver_                  | ⛔ no longer read by the plugin              | **Removed.** The plugin's own parser answered only a core's `corename`, for per-core save subfolders; the save answer now carries that directory, so the parser and its adapter were deleted with nothing reading them.                                                                                                                                                                                                                                        |

`es_systems.xml` and RetroArch's `.info` files have no parser of the plugin's own: the vendored emu-atlas resolver reads
them, and `adapters/atlas_catalogue.py` is the seam that turns its answers into plugin vocabulary — no atlas type
reaches a service. `adapters/es_find_rules.py` keeps its XML parsing inline rather than in a separate pure-domain
module; that is acceptable because the adapter owns its I/O. A new source that warrants a pure-parse split should follow
the [parser layout template](#parser-layout-template) above (pure parse in `domain/`, I/O in `adapters/`).

### Best-effort fallback and config health (`retrodeck.json`)

`RetroDeckPathsAdapter` resolves all RetroDECK roots (ROMs, saves, BIOS, home) from the `paths` block of
`retrodeck.json`. The path getters (`roms_path`, `saves_path`, `bios_path`, `retrodeck_home`) are **best-effort and
never raise**: when the file is missing, unreadable, or malformed, each getter falls back to
`<user_home>/retrodeck/<subdir>`. That fallback is RetroDECK's own default root, so it is correct for a default install
but **wrong** for an SD-card install where the user pointed RetroDECK at external storage.

Every root is returned **symlink-resolved**, whichever of the two sources answered. The content roots (`roms_path`,
`saves_path`, `bios_path`, `states_path`) are handed to the path guards as safe roots, and the ROM paths those guards
are asked about are recorded resolved wherever `lib/path_safety.safe_join` built them — so a root left as
`retrodeck.json` spells it makes one directory look like two on any system where `/home` is a link to `/var/home`
(Bazzite, Silverblue, and the other image-based distributions), and uninstalling a downloaded ROM fails with
`Path is outside its safe root` ([#1838](https://github.com/danielcopper/romm-tender/issues/1838)). `realpath` on a path
that is not on disk resolves as far as it can instead of raising, so the getters stay best-effort.

`retrodeck_home()` is not a safe root, and it is resolved for a different reason: `MigrationService` stores it and diffs
the stored value against the live one on every startup to decide whether RetroDECK moved. Resolving one side is not
enough there, because a marker written before this change still carries the other spelling — so the service resolves
what it read from `kv_config` before comparing, through a `realpath` seam on `MigrationFileStore`. Two spellings of one
directory read as "unchanged"; a move away from a home since deleted still reads as a move, because `realpath` follows
the links in it that still exist and leaves the missing tail as spelled. A marker that survived from before the change
and turns out to name the live home is dropped on that same pass, because otherwise it would stand until the user
migrates or dismisses.

The install prune (`StartupHealingService`, via the `ResolvedPathFn` seam) resolves **both** sides before its prefix
match: the pending-home markers, and each install's own recorded paths. Neither side is reliably one spelling — a
download is recorded through `safe_join` and so resolved, while a row an older migration relocated carries whatever
spelling the home had when it ran — and a match that misses prunes a record whose files are still on disk. Resolving the
recorded path is safe there in a way it is not in the deletion guards: the prune decides what to keep and authorizes
nothing.

Three user-visible spellings change with this: the `root_missing` banner's "Expected at:" line reports `resolved_home`,
and the migration-blocked page renders `old_path` and `new_path`, both of which are now the resolved markers.

Silently operating on the wrong root is the failure mode [#948](https://github.com/danielcopper/romm-tender/issues/948)
addresses. The fix keeps the getters silent-and-best-effort but pairs them with a loud health signal that the frontend
surfaces as a QAM banner. `RetroDeckPathsAdapter.config_health()` returns a `RetroDeckConfigHealth` enum
(`backend/lib/retrodeck_health.py` — placed in `lib/` because the adapter, the `RetroDeckPaths` Protocol, and `main.py`
all import it, and import-linter forbids the adapter↔service directions). The four states:

| State          | When                                                                                         | Loud? | Rationale                                                                                                                                                                     |
| -------------- | -------------------------------------------------------------------------------------------- | ----- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ok`           | `retrodeck.json` read successfully **and** the resolved home exists on disk                  | no    | Healthy — roots are trustworthy.                                                                                                                                              |
| `absent`       | `retrodeck.json` not found (`FileNotFoundError`)                                             | no    | The legitimate fresh-install case — `~/retrodeck` is RetroDECK's own default root. `absent` wins over `root_missing` even when the `~/retrodeck` fallback does not exist yet. |
| `unreadable`   | file exists but cannot be read or parsed (`OSError` / `PermissionError` / `JSONDecodeError`) | yes   | We know RetroDECK is configured but cannot read where its roots point — derived paths are likely wrong.                                                                       |
| `root_missing` | `retrodeck.json` read OK, but the resolved home directory does not exist on disk             | yes   | The library volume is gone (e.g. SD card ejected) — syncs and downloads would target a missing/wrong location.                                                                |

`config_health()` reuses the same 30-second TTL cache as the path getters (`_load_config()`) — no second independent
file read — and tracks the last load outcome so it can distinguish `absent` from `unreadable` (a bare `None` would
conflate them). The `root_missing` disk probe (`os.path.isdir`) only runs when the config read OK; it never runs for
`absent`, so a fresh install stays quiet.

`main.py` exposes this via the `get_retrodeck_status()` callable, a discriminated-status union
(`{status, config_path, resolved_home}`) — the [Callable response shapes](backend-architecture.md) carve-out for >2
outcomes. The backend returns only the discriminant plus the probed paths; the frontend owns the human-readable copy
(`frontend/src/utils/retrodeckHealth.ts`) and renders the shared `WarningCard` in the QAM Status panel for the two loud
states. `ok` and `absent` render no banner.

## Known consumer gaps

One-parser-per-source compliance has two sides, and this page historically only covered one of them:

1. **Parser inventory (covered above).** Does each source have exactly one parser? Does that parser live in the right
   layer? Does it refuse to cross-contaminate?
2. **Consumer compliance (this section).** Does every service method that asks a question route it through the right
   parser? It is possible — and has happened in practice — for every parser to be individually correct while a consumer
   still builds the wrong answer by calling the wrong parser or by forgetting to call a parser at all.

The gap below is the consumer-side cautionary tale that motivated adding this section.

### Case study: SaveService missed the RetroArch corename after #208

The code this describes is gone — the save directory is the resolver's answer since
[ADR-0040](../adr/0040-the-save-directory-is-the-resolvers-answer.md) — and the lesson is kept because it is about
consumers, not about that code.

[#208](https://github.com/danielcopper/romm-tender/issues/208) introduced the `RetroArchCoreInfoAdapter` precisely to
fix the "ES-DE label leaks into RetroArch save path" bug described in the
[Worked example](#worked-example-es-de-label-vs-retroarch-corename) above. That PR correctly updated `MigrationService`
to ask ES-DE which core is active and then ask the RetroArch `.info` parser for the canonical `corename`. The parser was
written, the protocol was defined, and the migration flow was fixed.

It was not until PR #227 had already merged, during live testing on hardware, that the second shoe dropped:
`SaveService._get_rom_save_info` — which every save-sync flow on every game launch routes through — was still calling
`domain.save_path.resolve_save_dir(..., sort_by_core=True)` **without** the resolved `core_name` argument. The domain
function's guard `if sort_by_core and core_name:` silently skipped the core subdir branch, so every save path fell back
to `{saves_base}/{system}/` instead of `{saves_base}/{system}/{corename}/`.

The live reproduction: a user restored a GBA save from the game detail page. RetroArch had
`sort_savefiles_enable = "true"`, so the migrated save lived at
`saves/gba/mGBA/Example Quest - Second Journey (USA).srm`. The restore wrote to
`saves/gba/Example Quest - Second Journey (USA).srm` — the parent directory. RetroArch at launch read from the core
subdir, so the restored save was invisible to the game.

The parsers were fine. The migration consumer was fine. The save-sync consumer was not, because during the #208 fix the
author only updated the consumer that the issue explicitly named and did not audit other call sites for the same
mistake. That omission is exactly what the "one parser per source" principle is supposed to prevent — but the principle
had been documented only as parser-side guidance and never applied as an audit criterion against the existing codebase.

[#232](https://github.com/danielcopper/romm-tender/issues/232) closed the SaveService gap and added this section to make
the consumer-compliance dimension explicit.

### Consumer checklist for reviewers

When reviewing a PR that touches save-path resolution, core resolution, firmware requirements, or any other capability
in the question-to-source mapping table, walk each call site with these questions:

1. **Is the right parser being called at all?** The answer to "where does this game's save sit?" is always the save
   answer. The answer to "which core runs this ROM?" is always `ActiveCoreResolver` over ES-DE. A consumer that builds a
   save path of its own, or asks RetroArch which core is active, has skipped a source boundary.
2. **Are all inputs to the parser resolved from their own authoritative parsers?** A function that takes a value another
   parser answers — a core, a system — expects the caller to have asked that parser first. A call that passes `None`
   where the value is required is a silent bug wherever the function degrades quietly instead of refusing.
3. **Does the `None` case fail loudly?** When a required parser returns `None`, the consumer must either (a) skip the
   operation with a warning, or (b) log a warning and fall back to a documented behavior. **Silent fallback with no
   diagnostic is never acceptable** — that is what produced #232.
4. **Does the same call pattern appear elsewhere?** If you are fixing one consumer, grep the whole codebase for the same
   function name and audit every call site.
5. **Is the new consumer covered by regression tests?** Every consumer should have unit tests for: the happy path, the
   `None` path (parser unresolvable), and the "callback not injected at all" path. The `TestGetRomSaveInfo` class in
   `tests/services/saves/test_rom_info.py` is the reference shape.

### Historical examples

| Issue                                                          | Parser state                                                            | Consumer state                                                                                                                                   | Resolution                                                                                                                                            |
| -------------------------------------------------------------- | ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| [#208](https://github.com/danielcopper/romm-tender/issues/208) | `.info` parser did not exist; ES-DE label was the only core name source | `MigrationService` used the ES-DE label as the RetroArch save subdir name                                                                        | Add `RetroArchCoreInfoAdapter`, resolve corename from `.info`, wire into migration                                                                    |
| [#232](https://github.com/danielcopper/romm-tender/issues/232) | `.info` parser correct (from #208)                                      | `SaveService._get_rom_save_info` still called `resolve_save_dir` with `core_name=None`, so `sort_by_core` was a silent no-op for every save flow | Thread `get_core_name` into `SaveService`, extract `_resolve_retroarch_corename` helper mirroring `MigrationService`, warn+fallback when unresolvable |

Both issues are parser-side fine in the sense that the parser itself returned the right answer for the question asked.
They are consumer-side bugs: the consumer either asked the wrong parser or forgot to ask the right one. The checklist
above exists to catch the second shoe before it drops in production.

## Planned / future unlocks

The plugin has no `.info` parser of its own: the vendored resolver reads `.info` files for every answer the plugin puts
to it. The files carry more than the plugin asks about today, and the capabilities below would each read one of those
fields. Each should land in its own issue and its own PR, paced against real need rather than built ahead of it.

| Capability                    | `.info` field(s)       | Replaces today's                                                        | Value                                                                                                     |
| ----------------------------- | ---------------------- | ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| Per-core supported extensions | `supported_extensions` | ES-DE's per-system `<extension>` list, read off the installed catalogue | Narrows the accept-list to the core that launches rather than the list ES-DE writes for the whole system. |
| Core switching validation     | `supported_extensions` | (no check today)                                                        | Prevent assigning a core to a system whose ROM extensions it can't load.                                  |
| DAT/database identification   | `database`             | (no use today)                                                          | Match ROM headers against the right datfile for integrity checks.                                         |
| Core display names            | `display_name`         | ES-DE label only                                                        | Secondary source when an ES-DE label is unavailable (rare); never used to override ES-DE.                 |

Per-core **firmware requirements** used to head that list — the same `.info` fields (`firmware_count`,
`firmware<N>_desc`, `firmware<N>_path`, `firmware<N>_opt`) against the manual BIOS registry in `FirmwareService`. It is
done, and it did not land as a parser of the plugin's own: reading those fields is only half the job, and the other half
(resolving each declared path against the live firmware root, following symlinks, keeping "this core declares nothing"
apart from "this core's declaration could not be read") is what the vendored resolver already does. It reaches
`FirmwareService` through the `FirmwareResolver` seam — see
[Backend Architecture](backend-architecture.md#firmwareservice-notes-the-live-firmware-resolver). A capability above is
asked of the resolver the same way first; a parser of the plugin's own, built by the
[parser layout template](#parser-layout-template), is the fallback for a field the resolver does not answer.

## How to add a new source

When the plugin needs to read a new external config/metadata source, the checklist is:

1. **Identify the source and its authoritative domain.** Add a row to the **Question-to-source mapping** table above. If
   the new source overlaps with an existing one, explicitly decide which parser owns which question — do not merge them.
2. **Write the pure parser** in `backend/domain/<source>.py`. Start with the smallest function that answers the
   immediate need; grow the API later. Tests first.
3. **Write the adapter** in `backend/adapters/<source>.py`. Path resolution, file read, parsing delegation, caching.
   Tests with `tmp_path`.
4. **Add callback protocol(s)** in `backend/services/protocols/`. One protocol per capability, in the existing `*Fn`
   Call-protocol style.
5. **Wire in `bootstrap/`.** Instantiate the adapter in `adapters.py`; thread its method(s) from `services.py` into the
   services that need them.
6. **Consume in services.** Services depend on the protocol, not on the adapter. Handle the `None` / failure case at the
   service layer — no cross-parser fallbacks.
7. **Add a row to Current parsers** above and update the decisions log below if there's a non-obvious design choice
   worth recording.
8. **Update `.importlinter`** if the new adapter needs a contract line to stay independent of services or sibling
   adapters.

## Decisions log

Non-obvious design choices worth preserving:

- **Sibling adapters for RetroDECK/RetroArch-side config, not one bundle.** `RetroDeckPathsAdapter` reads
  `retrodeck.json` and nothing else; `retroarch.cfg` and the `.info` files are read by the vendored resolver and by no
  adapter of the plugin's. A single combined "RetroDECK/RetroArch config" adapter would conflate different owners
  (RetroDECK team vs libretro core maintainers), different change triggers (user configurator edits vs Flatpak core
  releases), and different file layouts (user home for `retrodeck.json`, Flatpak install tree for `.info`). This is the
  applied form of the "one parser per source" principle for the RetroDECK/RetroArch-side of the codebase.

- **`core_so` is the full `.so` basename including `_libretro`, and without the `.so`.**
  `AtlasCatalogueAdapter.get_active_core` returns `(core_so, label)` where `core_so` is e.g. `"snes9x_libretro"`, not
  `"snes9x"`. The resolver states it _with_ the extension (`snes9x_libretro.so`, extracted from the `<command>`); the
  adapter strips that, because every core identifier the plugin holds — the resolved active core, the `platform_cores`
  override, a classified option's `core_so` — is spelled without it, and comparing the two spellings would silently
  match nothing. The `.info` filename is therefore `{core_so}.info` (e.g. `snes9x_libretro.info`), not
  `{core_so}_libretro.info`. This is a subtle naming quirk documented here so future parser users don't double the
  suffix.

- **RetroDECK-only path resolution, standalone RetroArch out of scope.** The `.info` adapter only looks under
  `net.retrodeck.retrodeck` Flatpak paths — the per-user `~/.local/share/flatpak/...` before the system-wide
  `/var/lib/flatpak/...`, which is flatpak's own order for an app and therefore the deploy a launch would start
  (`adapters/flatpak_install.py`; the emulator catalogue and the find rules resolve through the same order, so all three
  describe one RetroDECK). Support for `org.libretro.RetroArch` Flatpak, native RetroArch installs, and other launchers
  is deferred — a separate long-term issue tracks broadening the plugin's launcher support beyond RetroDECK, and this
  parser will be extended alongside that work.

- **Candidate paths use the `current/active` Flatpak symlinks.** Both candidate paths for `.info` files (per-user and
  system) route through `current/active`, which is Flatpak's stable symlink to the installed commit. This means the
  adapter does not need to know the specific Flatpak commit hash, and Flatpak updates do not break path resolution.

---

**Related pages:**

- [Backend Architecture](backend-architecture.md) — service/adapter architecture, dependency diagram, boundary
  enforcement
- [Save File Sync Architecture](save-file-sync-architecture.md) — save sync details, conflict detection, following a
  moved save directory
- [RetroDECK Path Migration](../user-guide/retrodeck-path-migration.md) — user-facing guide for moving a RetroDECK
  install between storage locations
