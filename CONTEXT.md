# CONTEXT.md — Tender domain glossary

This file is a glossary. It defines the canonical meaning of project-specific terms so that conversations, issues, PRs,
and code stay aligned. It is _not_ a spec or design doc — implementation docs live in `docs/architecture/`, and
architectural decisions live in `docs/adr/`.

When a term resolves during a discussion, it gets added here. When a term's meaning changes, the entry gets rewritten —
not appended to.

## Terms

### Aggregate

A cluster of domain objects treated as a single unit for data consistency. Per Cosmic Python chapters 1–7:

- Has **one root** entity (a dataclass) that is the only external entry point.
- Enforces all of its own **invariants** — outside code cannot violate them.
- Is the **transaction boundary**: saved atomically as a unit.
- Has exactly one **identity**. Other aggregates reference it by ID only, never by holding a Python reference to its
  internals.
- Mutation is **only via methods on the root**, named after the domain event that occurred (`adopt_baseline(...)`,
  `confirm_slot(...)`, `mark_installed(...)`). Direct field assignment from services is forbidden.
- Has exactly **one Repository** Protocol. The Repository's job is "give me this aggregate by ID, save this aggregate" —
  it may touch multiple tables under the hood.

What an aggregate is _not_: a DTO sent to the frontend, a query projection, or "stuff that happens to live in the same
file." Aggregate boundaries are **invariant boundaries**, not storage boundaries.

Chapters 8+ of the CP book (domain events + message bus) are explicitly out of scope. Triggers for revisiting that scope
are documented in `CLAUDE.md`. The concrete aggregate set, its tables, and the enforcement layers live in
`docs/architecture/database-design.md` — this entry defines the term, not the inventory.

### Value Object

An immutable member of an aggregate, built whole and never mutated in place — a `@dataclass(frozen=True, slots=True)`,
not a `@cosmic_aggregate` root. It has no identity of its own and no mutation surface to police, so it carries neither
the decorator nor the verb-method discipline an aggregate root does. `FileSyncState` (inside `RomSaveSyncState`) is the
canonical example. The foil to **Aggregate**: a root has identity and methods; a value object has neither.

### Repository

The single persistence seam for one **Aggregate** — "give me this aggregate by id, save this aggregate." Exactly one
Repository per aggregate root (Protocol in `services/protocols/`, SQLite adapter behind it). It may touch several tables
to reconstruct or persist the aggregate — the `RomSaveSyncState` repository spans `rom_save_sync_states` +
`rom_save_files` — but callers see only `get(id)` / `save(id, aggregate)`. It _is_ the load/save layer; the service
layer never wraps it in a second one (no `StateService`-style holder between service and repository). Reached only
through the **Unit of Work** (`uow.rom_save_sync_states`, `uow.rom_installs`, …), never constructed directly.

### Unit of Work

The atomic transaction boundary one operation works inside, and the carrier of the **Repositories** for that
transaction. Owned by the service layer at the operation's entry (never by `main.py` callables); a `with uow:` block
opens one connection, exposes the repositories, and commits on clean exit / rolls back on exception (stdlib `sqlite3` +
`run_in_executor`, per [ADR-0004](docs/adr/0004-sync-sqlite-unit-of-work.md)). Kept **narrow** — it wraps only the
database reads/writes, never network/file I/O or a frontend round-trip; cross-operation consistency comes from the
operation's own serialization (the per-ROM save lock, the single library-sync task), not from holding a UoW open.

### What Tender is

A **Steam plugin** and a **RomM client**. It extends Steam's own interface — a Quick Access entry, a section on the game
page, the panel — and talks to a RomM server as a registered client. It is not a Decky Loader plugin: it hosts itself,
and it runs beside a Decky Loader when one is installed. "The plugin" means Tender in that first sense.

_Avoid_: **Decky plugin**, **plugin loader**, **plugin folder** — each implies a host that loads Tender, and there is
none.

### Display name (Tender) vs identifier (romm-tender)

The program has two names, and they are not interchangeable. The **display name** is `Tender`: the one a person reads —
a toast's sender, the label on the Client API Token in their RomM account, the client a registered device is listed
under, the headline of a README they open by hand. The **identifier** is `romm-tender`: the one a machine spends —
folder names, the outgoing `User-Agent`, the `localStorage` breadcrumb key. A new string picks by its reader, never by
which one looks better in place.

Both live in `backend/domain/identity.py`, beside `VERSION` — which is the release this is, not a name at all. Three
constants in one module is not a fold: they are three different values, so no edit to one reproduces another by
accident. The identically SPELT pair is `PACKAGE_NAME` against `APP_DIR_NAME`, and that pair is exactly the one still
standing in two modules.

The display name has **two** homes, and nothing checks that they agree: `DISPLAY_NAME` in `backend/domain/identity.py`
and `PLUGIN_NAME` in `frontend/src/utils/toast.ts`. Inside a user-facing **sentence** it stays literal text —
interpolating a constant into prose costs readability and buys nothing. A **heading** is not a sentence: a headline and
the rule under it are one thing, so the headline is interpolated and the underline derived from its length.

The identifier has **three** homes, separate because they answer three questions that must stay free to disagree:

- `APP_DIR_NAME` (`domain/user_data_location.py`) — the name every directory the program derives for itself carries.
- `PACKAGE_NAME` (`domain/identity.py`) — the recovery root and the `User-Agent`, both through bootstrap, and nothing
  else.
- `SESSION_BREADCRUMB_KEY` (`frontend/src/utils/sessionManager.ts`) — the `localStorage` key naming the open-session
  breadcrumb, so a rename orphans every row written under the old one.

Two homes have gone rather than moved, and both for the same reason. The folder a release unpacked into was Decky's
question, asked because Decky derived its four per-plugin directories from it
([ADR-0035](docs/adr/0035-the-release-builds-no-decky-artifact.md)). The folder EARLIER releases unpacked into was the
other half of the same story — what a start-up migration searched, which is a search nothing performs any more
([ADR-0036](docs/adr/0036-the-backend-hosts-itself.md)). Hosting the backend ourselves derives nothing: the directories
come from the environment, so neither question has an asker left. Why the three stay apart is argued once, in
`backend/domain/identity.py`'s module docstring.

_Avoid_: "the plugin name" for either, since it names neither; and reading "a machine parses it" as "so it is the
identifier" — the display name is machine-read too, by the frontend that puts it in the QAM header.

### The program's directories

The seven directories this program reads and writes, resolved **once from the environment** by the entry point and
handed to `bootstrap()`, which derives none of them (`domain/app_directories.py`). The ladder is `TENDER_*` first — what
an installer resolved and wrote into the service unit — then the XDG variables, then built-in defaults; the back two
rungs are for a start by hand.

- **config root** — user-intent configuration: the settings file and its siblings.
- **data root** — what cannot be fetched again: the database, the single-instance lock beside it, and the legacy
  `save_sync_state.json` the settings fold still reads. This list is the one inventory of that root; everywhere else
  names it rather than repeating it.
- **cache root** — what can: the cover and artwork caches.
- **state root** — the log file.
- **runtime root** — the port file, in a directory the session clears at logout.
- **code root** — where the program itself sits; the launcher it ships is copied out of here.
- **bin root** — where a user's own executables go; the launcher is installed here, and every shortcut's `exe` names it.

The data/cache split is the load-bearing one: a system that clears caches must be able to clear one and not the other.
All but the code root and the bin root are named after `APP_DIR_NAME` — the code root is wherever the program was
installed, and the bin root is shared with every other program the user installed for themselves, so neither carries a
name of ours. The bin root has no XDG variable either: the basedir spec names the path, so its ladder is
`TENDER_BIN_DIR` and then the built-in default. Every reader takes the seven off the one `AppDirectories` the entry
point resolved, which reaches `bootstrap()` as an argument and the services as `WiringConfig.directories`; nothing
composes a directory of its own from a home or a folder name. `resolve_directories` has exactly one caller (`main.py`),
and `config_root` / `data_root` have none left.

_Avoid_: **Decky-assigned directory** — those were named after the plugin's own folder, which is what made the data move
on a rename, and nothing derives a directory from a folder name any more. Also avoid: plugin directory, install
directory. "XDG directory" is now only half wrong — the XDG variables ARE read, as the ladder's second rung, but they
are not where the answer comes from on an installed system.

### Standalone bundle / coexistence bundle / React bootstrap

The three files `pnpm -C frontend build` produces, and the names to use for them.

- **Standalone bundle** — `dist/index.js`. The panel with `@decky/ui` bundled inside it. For a machine where Decky
  Loader is not running.
- **Coexistence bundle** — `dist/index-coexistence.js`. The same panel, taking `@decky/ui` from the copy Decky Loader
  has already loaded, through the `DFL` global. For a machine where Decky is running: a second bundled copy re-executes
  Steam's module registry underneath a Decky that is already rendering from it, and takes the Big Picture window down.
- **React bootstrap** — `dist/globals.js`. Installs `SP_REACT`, `SP_REACTDOM` and `SP_JSX`, which Steam does not define
  and Decky's loader otherwise would. Its own file because `@decky/ui`'s component half reads React internals while its
  modules evaluate, so it cannot be in the import graph of the module that creates the globals.

The two panel bundles differ in exactly one thing and are told apart by NAME — nothing inside either is read to choose
between them. Which one is loaded is the **injection**'s decision, taken from the machine rather than from the window.
Each does carry a build-time stamp of which of the two it IS, read in one place — `frontend/src/boot/searchingCopy.ts` —
to name the copy of `@decky/ui` that ran a search that missed. That answer is printed twice: on the start-up check's
fallback page and in the log line beside it.

_Avoid_: **DFL build** / **bundled build** — both name the mechanism rather than the situation, and the situation is
what the choice turns on. Avoid **the bundle** unqualified once more than one exists. Avoid calling the React bootstrap
a "shim" or a "polyfill": it installs Steam's own React, not a stand-in for it.

### Start-up check

The question `frontend/src/boot/steamModules.ts` asks before anything mounts: did every search into Steam's own
interface find something? Almost everything the panel renders is a search predicate over Steam's minified bundle, and
one Steam has moved past returns `undefined` with nothing thrown.

Each name checked states what its **absence costs**: the `panel`, which is every name's status quo, only its
`appearance`, or — where the name is read by a `diagnostic` and nothing else — a debug line naming a class instead of
the class. On a miss that costs the panel, the panel does not mount at all — the factory returns a **fallback page**
instead and registers nothing. The page distinguishes SOME searches missing from ALL of them (the React bootstrap never
ran, or Steam's registry was read before it was complete, neither of them `@decky/ui`'s doing), which is the first fact
that leads to a repair.

A **miss the panel survives** costs neither the panel nor anything the panel acts on: it mounts as usual and the log is
the only place the miss is reported. That line answers the same question the page does — whose copy of `@decky/ui` ran
the search — rather than naming a repair of its own, because the two costs below the panel do not all belong to the same
program: `ControllerGlyph` is a search Tender runs itself, and `playSectionClasses` is a `@decky/ui` export whose search
is Decky's in the coexistence bundle. _Avoid_ **cosmetic miss** for the pair: it is true of the glyph and false of
`playSectionClasses`, whose absence nothing draws in the first place.

The second is the **searching copy**: which installed copy of `@decky/ui` ran the predicate that went stale, since the
coexistence bundle runs Decky Loader's rather than ours. It is read ONCE per start and worded by both surfaces, which is
what stops the page and the log line from answering it differently. It gives SOME four answers instead of one — Tender's
own copy searched and missed (update Tender); Decky's copy searched and missed (update Decky Loader, whose own interface
and other plugins are affected the same way); Decky's copy does not export a name Tender asks it for, which is two
separately installed programs disagreeing about the package (bring both to current); or what missed spans both kinds,
which still settles Decky's copy — it carries every name asked of it and its searches for them came back empty all the
same — and leaves the rest to whichever kind of name that is. The **package disagreement** is asked before the mixed
answer because a name Decky's copy does not export is a fact about the two installs, where a name it exports with an
empty value is a search result whose cause is inferred.

Asked before them all: four of the names checked are not `@decky/ui` lookups at all — the three `SP_*` React globals and
`ControllerGlyph`, which Tender reaches with a predicate of its own — so a miss confined to those belongs to NO copy of
the package and names none. The **fallback page** then names no repair either, because a global can be among them and
who installed it on a machine running both programs is not this page's to decide. The log line is not in that position:
a global's absence costs the panel, so it never reaches one, and the names that do reach it in this answer are searches
Tender runs itself — so there the repair is named, a newer Tender. (That is this answer's scope, not a universal:
`playSectionClasses` reaches the log line too, and its search is the package's.)

_Avoid_: **health check** — it asks one question at one moment and is not a recurring probe. Avoid **degraded mode**:
the panel is whole or it is absent, and a half-mounted panel acting on what it cannot see is the thing the check exists
to refuse. A miss the panel survives is not degraded mode: what is absent is a decoration the one place that draws it
renders without, or a name only a debug line reads — never something a page acts on.

### Injection

Loading the panel into Steam's renderer from outside it: the backend attaches to Steam's CEF debugger and evaluates one
expression in the `SharedJSContext` target. There is no plugin loader in this path, nothing is copied anywhere, and no
manifest anybody reads.

The **marker** is the global that expression leaves behind (`window.__tender_panel__`), and it is the whole of how a
context says it already carries the panel. It is wiped by a **JS-context rebuild** — Steam building its renderer's
JavaScript world again, which is also what `Page.domContentEventFired` announces — and survives everything short of one.

_Avoid_: **deploy** and **install** for this, which name what #1902 will do with a service and a unit; injection is
about one Steam session and leaves nothing behind. Avoid **hot reload**: nothing here watches a file, and a rebuilt
bundle reaches Steam only when the context is rebuilt.

### Load-failure card

The small card the injected expression draws into Steam's own document when the panel bundle did not load — Tender's
version, Steam's build, the log path, where releases are listed, and the reason the import gave. Plain nodes and inline
styles: it uses neither React nor `@decky/ui`, because its own subject is that those may be exactly what is missing.

It carries **one action** — stop trying until Tender restarts — which reaches the backend through a **debugger binding**
and nothing else, and takes the card off the screen. The restart meant there is the **backend's own process**, the same
way back the crash state has. Checking for an update is an address printed as text rather than an action, because
nothing in this program updates itself yet.

**It is not the start-up check's fallback page** and the two are never called by the same name. The fallback page is a
React component rendered INSIDE a panel that did mount, when a search into Steam's interface came back empty; this card
exists because nothing mounted at all. The issue that asked for it called it a fallback page, which is the wording this
entry replaces.

### Crash record

The file the injection opens before it evaluates anything and closes once Steam's interface is still there some seconds
later (`<state_dir>/injection-guard.json`). A record found still **open** at the next attempt is a crash that already
happened; two in a row stop the injection, and a change in the **fingerprint** — Tender's version, a digest of the
bundle bytes, Steam's client build — drops the count so it starts trying again by itself.

An attempt the record says nothing about is **not counted**: the debugger stopped answering, the backend is shutting
down, nothing but the renderer was open, or a second injection began before the check that answers for the first could
run — in each of those there was no collapse to observe.

_Avoid_: **crash counter**, which is what this replaced — the crash leaves the marker standing, so within one Steam
session it can happen at most once and counting inside a session counts to one for ever.

### Persistence boundary (settings.json / SQLite)

Where a piece of persisted state lives is a deliberate decision driven by what the data _is_, not which file it
historically lived in. Per [ADR-0003](docs/adr/0003-json-sqlite-persistence-boundary.md), three buckets, and the bucket
picks the store:

1. **User-intent config** — flat, no relationships, the user sets it → **`settings.json`**. Includes credentials, scalar
   toggles, the sync-selection lists (`enabled_platforms`, `enabled_collections`), the save-sync feature toggles
   (`save_sync_enabled`, `sync_before_launch`, `sync_after_exit`, `default_slot`, `autocleanup_limit`), and
   `device_name`.
2. **Observed / derived-from-an-external-source** — read, not set → **read live by default**; persisted (in `kv_config`)
   only as a last-seen marker for cross-run change detection. The RetroDECK home path and save-sort settings markers
   live here.
3. **Synced / derived relational state with real invariants** — per-ROM groups, history, caches of remote data →
   **SQLite aggregates**.

`device_id` now lives in the `kv_config` table; bucket-3 save state lives in SQLite aggregates. `save_sync_state.json`
is a dead store — never written, read exactly once at bootstrap for the one-time legacy settings fold. As of #822 it no
longer held the save-sync toggles or `device_name` (those moved to `settings.json`, settings schema v4).

### Cutover

The hard cut from JSON state files to SQLite ([#784](https://github.com/danielcopper/romm-tender/issues/784)) has
landed. "Hard" means **SQLite started empty** — the JSON state was not migrated into it, and the JSON-era domain classes
(`SaveSyncState`, `PluginState`) and `domain/save_state.py` were deleted in the same wave. Bucket-1 config
(`settings.json`) and the change-detection markers were unaffected; only the relational save/library/playtime state was
re-derived from scratch (re-synced from RomM, re-pruned against disk).

### kv_config

A key-value table for small singleton configuration values that don't justify their own aggregate or table. One row per
key.

Residents (per [ADR-0003](docs/adr/0003-json-sqlite-persistence-boundary.md)): the RetroDECK home path marker
(`retrodeck_home_path` + its pending-migration `_previous`), the save-sort settings markers (`save_sort_settings` +
`_previous`), `device_id` (server-issued identity), and `platform_names` (platform_slug → display_name cache). The
schema version is **not** a `kv_config` key — it lives in `PRAGMA user_version`.

**Not** a dumping ground: anything with its own lifecycle, invariants, or repeat-row potential gets its own aggregate.
`kv_config` is for the truly small, the truly singleton, and the truly miscellaneous.

### Custom header

An extra HTTP header the user configures for their own RomM front door, so a server behind an authenticating reverse
proxy (Pangolin, Cloudflare Access, Authelia, Authentik forward-auth) lets the plugin's requests through. It is
user-intent config (`settings.json` `romm_custom_headers`, a list so the entry order survives), and its value is a
**credential**: write-only to the frontend, absent from every log line and refusal message.

Bound to the **destination**, not to a request's purpose: the plugin attaches them to every request it issues to the
configured RomM origin, sign-in included, and to no other request it issues. Attachment is the claim — a redirect the
server answers with is followed by urllib, which carries them onward whatever the new host is (#1889). A custom header
never carries a name the transport sets itself — that would not add a header but replace one. _Avoid_: "auth header"
(that is the RomM bearer), "proxy settings", "header override".

### Rom (aggregate) vs ROM (file) vs RomM (server)

Three things spell similarly; distinct meanings:

- **Rom** — the aggregate / domain entity owned by this plugin (`domain/rom.py`). Represents one ROM as the plugin
  tracks it locally: identity, the denormalized `platform_slug`, sync metadata, the Steam shortcut binding.
- **ROM** (or "ROM file") — the actual playable game file on disk (e.g. `.iso`, `.cue`, `.gba`). What `RomInstall`
  records once a `Rom` has been downloaded.
- **RomM** — the upstream self-hosted server. The source of truth this plugin syncs _from_.

Convention: always write `Rom` (PascalCase) when referring to the aggregate. Write "ROM file" when referring to the
on-disk artifact.

### Version (RomM: sibling) vs Region / Languages

A **version** is one concrete released dump of a game — its own RomM `rom_id`, its own file, its own save universe
(saves never carry across versions; matches RomM's per-ROM saves and RetroArch's per-content save naming). All versions
of one game form a **sibling group** (RomM's `sibling_roms`: same matched metadata id, per platform; unmatched ROMs are
solo groups). Key derivation: `domain/sibling_group.py`, persisted as `roms.sibling_group_key`
([ADR-0021](docs/adr/0021-sibling-group-one-shortcut-binding-active-version.md)).

- **Active version** — the sibling currently bound to the group's Steam shortcut (`roms.shortcut_app_id`); the one that
  installs, launches, and syncs saves. Binding = active version (ADR-0021).
- **Default version** — RomM's per-user `is_main_sibling` ("SET DEFAULT" in RomM's Switch-version UI). Optional;
  respected read-only as a preselect, never written back.
- **Switch version** — the user-facing control (named after RomM's) that changes the active version within a group;
  rendered only when the group has more than one version (#1297/#1298).
- **Reachable** — a version a reader can get to from Steam: any member of a group that holds a binding, since the
  group's one shortcut opens the game's page and **Switch version** reaches the rest from there. Distinct from
  **bound**, which is the active version alone. A count of reachable ROMs is what a surface states about how much of a
  platform arrived in Steam; a count of bindings is what it states about shortcuts, and the two are only equal where
  every group **that holds a binding** is a single version — a group with no binding raises neither count
  (`reachable_count` vs. `count`, `services/library/reporter.py`).

**Region** and **Languages** are **attributes of a single version**, parsed from its filename tags: `(Spain)` → where
that release shipped; `(En,Fr,De,Es)` → the languages contained in that one dump. A multi-language version is still one
version. UI rule: Region/Languages render on the game-detail page for the active version only — they are never a version
list and never the switch mechanism.

### file_path vs rom_dir (RomInstall paths)

The two path fields on `RomInstall` (`domain/rom_install.py`) answer different questions and must not be conflated:

- **`file_path`** — the **launch file**: the single file that is the ROM's launch identity. Present for every ROM;
  save-path resolution, ES-DE core resolution, and the displayed filename all derive from it. It is the **default**
  launch target baked into the Steam shortcut's `launch_options` (`flatpak run … "<file_path>"`, run by the
  `tender-rom-launcher` exec wrapper per [ADR-0009](docs/adr/0009-launcher-pure-exec-wrapper-baked-launch-options.md),
  which superseded the dynamic SQLite read of [ADR-0005](docs/adr/0005-launcher-resolves-path-from-sqlite.md)) — but the
  baked launch **target** may be **overridden at bake time** without rewriting `file_path`: a multi-disc pin bakes the
  selected disc's path
  ([ADR-0014](docs/adr/0014-per-game-disc-selection-in-db-applied-as-bake-time-launch-path-override.md)), and a
  folder-boot system (PS3/RPCS3) bakes the game **directory** rather than the nested launch file
  ([ADR-0019](docs/adr/0019-folder-as-launch-target.md)). `file_path` stays the launch **file** anchor in every case.
- **`rom_dir`** — the **dedicated per-ROM directory**, present only for folder-backed (multi-file) ROMs. **NULL for
  single-file ROMs**, which live as a bare file directly in the shared `<roms>/<system>/` directory and own no dedicated
  folder.

Single-file vs multi-file is **read from `rom_dir` presence** — never re-derived from the file's parent directory, never
stored as a separate boolean ([ADR-0008](docs/adr/0008-rom-install-launch-file-and-rom-dir.md)). Migration moves
`rom_dir` whole when set, else the file; uninstall removes `rom_dir` whole when set, else the file. A future per-file
`RomFile[]` model — one row per physical file, each tagged with a RomM `category` (`game` / `dlc` / `update` / `mod` /
…) — is the planned shape for the multi-file features in [#140](https://github.com/danielcopper/romm-tender/issues/140)
/ [#129](https://github.com/danielcopper/romm-tender/issues/129); it is an additive 1:N child of `rom_installs`
(deferred until those land), and `file_path` + `rom_dir` are its forward-compatible projection.

### Launchable install / no launch target

A downloaded ROM whose recorded **launch file** is something the system cannot act on — a PS3 `.pkg` installer (the game
is still sealed inside the package), a bare disc track `.bin`. `detect_launch_file` ends in "largest file by size", so a
download none of its format rules recognise still yields a `file_path`; `RomInstall.launchable` records whether that
path is a real launch target, decided once at download-complete against the system's live ES-DE accept-list
([#1652](https://github.com/danielcopper/romm-tender/issues/1652)).

"No launch target" is a statement about the **launch command**, never about the download. The files stay on disk, the
install row is written, and uninstall works normally — installing the package by hand in the emulator is the documented
RetroDECK procedure and a refusal that discarded the download would leave the user worse off. What is withheld is the
baked `launch_options`: an unlaunchable install resolves to the empty path in the `DiscLaunchResolver` seam, so every
bake site emits the same empty launch command an **un-downloaded** ROM's shortcut carries.

Unlaunchable is always a **proven** verdict, never the absence of one: an unreadable accept-list, an unknown system, and
a pre-migration row all read as launchable. The foil is a **folder-boot** install, whose `file_path` extension is
irrelevant because the baked target is the game directory.

### Adopt

To take something already present into the plugin's records without having produced it. The object varies — a local save
file becomes a tracked baseline (`adopt_baseline(...)`), a play session survives a frontend reload, an identity-only
claim picks up the debris of an interrupted removal
([ADR-0027](docs/adr/0027-claim-discipline-follows-the-recovery-bundle.md)), a ROM already on disk becomes an install —
but the rule does not: the recorded state derives from what was found, not from what the plugin did. _Avoid_: claim
(reserved for the removal machinery's authorization), import, link, register.

### Adopted install

A `rom_installs` row for content the plugin did not download. Indistinguishable from a downloaded one in every respect,
**including deletion authority**: uninstall and removed-game cleanup delete an adopted ROM's files exactly as they would
a downloaded ROM's. The protection therefore sits at the moment of adoption — proving the content is the ROM the row
will claim — never in a downstream exemption, because a second class of install row would need a second branch in every
consumer (uninstall, cleanup, home migration, version switch).

### Adoption candidate

An entry at the top level of the platform's ROM directory that no `rom_installs` row accounts for, whose shape matches
what the server serves, and whose **normalized name** equals the ROM's. "Candidate" carries the uncertainty
deliberately: a name says nothing about content, so until it is verified this is a guess — and what ranks one candidate
above another is only cheap evidence (a single-member archive's CRC32 from the ZIP index, an exact size), never proof.
The verification (CRC32/MD5, read from a ZIP's central directory where the content is archived) is always
user-triggered, never a wait imposed before the plugin will say anything.

### Entry kind

What an entry on disk **is**, judged by its own type without following it: `file`, `dir` or `link`. There is no fourth
value — anything else a filesystem can hold (a FIFO, a socket, a device node) gets no kind at all, because "file or
directory" has no truthful answer for one and inventing one is what let a named pipe be offered as a game. One rule
answers this for every read of the filesystem, and the two kinds of read differ only in what they do with a kindless
entry: a **directory listing** leaves it out, while **describing one named path** reports it with the kind absent —
something that is there must never come back as nothing, or the next write goes over it in silence. A `link` is its own
kind rather than whatever it resolves to: an install row must be removable, the uninstall path refuses a symlink, so a
link is never adoptable however well its target resolves. _Avoid_: "shape" for this — **shape** is the
single-file-vs-folder question the server answers, and the two are different judgements about different things.

### Unusable namesake

An entry whose **normalized name** equals the ROM's but which cannot become its install: the other **shape** — a
directory where RomM serves a single file, or a loose file where it serves a folder — or any `link`. It is deliberately
**not** an adoption candidate: nothing about it can be taken over, so it is never offered, ranked or renamed. It is
still reported, because the alternative is a download that silently produces a second copy of the game beside it. The
dialog's two exits are download-anyway (the server's copy lands beside it, under the server's name) and cancel; neither
touches the entry. The two reasons are not one reason: a wrong shape is wrong only against what the server sends, so
that refusal names the served shape, while a `link` is unusable on its own terms and naming a shape beside it would
suggest the other shape would have taken it. _Avoid_: mismatched candidate, unusable candidate — the whole point is that
it is not one.

### Normalized name

A ROM filename reduced to the game it denotes: extension removed, bracketed groups `(...)` / `[...]` dropped with their
contents, every run of non-alphanumerics collapsed to one space, lowercased, trimmed.
`Example Quest - Second Journey (Rev 1) (USA).zip` → `example quest second journey`. It is the candidate search's only
match key, applied identically to both sides. An empty normalization (a name that is nothing but tags) matches nothing
rather than everything. _Avoid_: fuzzy match, similar name — the comparison is exact equality of the normalized strings,
and no edit distance or token scoring is involved.

### platform_slug (denormalized)

The RomM platform identifier (e.g. `gba`, `psx`) carried as a plain string on the rows that need it (`roms`,
`rom_installs`, `downloaded_bios`, `firmware_cache`). There is **no local `Platform` aggregate** — that was proposed in
ADR-0001 and **dropped by [ADR-0003](docs/adr/0003-json-sqlite-persistence-boundary.md)** on YAGNI grounds. Platform
display names resolve live from RomM; sync exclusion is the user-intent `enabled_platforms` config in `settings.json`,
not per-platform local state. A `Platform` aggregate is reintroduced only when a concrete need lands (the
standalone-emulator roadmap), not speculatively.

### Emulator override vs default core vs launching emulator

Three distinct notions in core selection, kept separate because they have different owners and lifetimes:

- **Default core** — the emulator RetroDECK declares for a platform (in `es_systems.xml`) — see **default emulator** for
  the precise selection rule, which may resolve to a RetroArch core **or** a standalone emulator. RetroDECK-owned; it
  can change on a RetroDECK update. The plugin reads it live and **never stores** it — a stored copy would go stale.
- **Emulator override** — a deliberate user choice to deviate from the default core, at **per-game** or **per-platform**
  scope. The plugin owns the override and stores **only the deviation**; the absence of an override means "follow the
  default." A core the user picks inside ES-DE's own UI is _not_ an emulator override in this sense — it is ES-DE's
  state, which the plugin does not own.
- **Launching emulator** — the one emulator a ROM actually launches with: the per-game **emulator override** when one
  exists, else the **platform pick**, else the **default core** — and **whatever kind it is**, a RetroArch core or a
  standalone emulator, which is why it is not named after either. One resolver answers it for both the launch and every
  read consumer (BIOS requirement, save path, game-detail badge), so the launched emulator never diverges from what
  those reads assume. On the wire the pair is still spelled `active_core` / `active_core_label`, and `active_core` there
  is the **emulator identity** rather than a core file: `core_so` is absent for every standalone emulator, so a reader
  reaching for it answers `None` for one and falls back to every declaring emulator — the degradation the identity work
  removed. _Avoid_: **active core** for this, and "required by active core" for the row axis, which is **required by the
  launching emulator** — the field it expands is spelled `required_by_active` and carries no "core" at all, so the
  expansion added the one word that is wrong over a standalone pick. The identifiers `active_core`, `active_core_label`
  and `required_by_active` keep their spellings; the prose around them does not expand them into a core.
- **Platform pick** — the same resolution asked of a PLATFORM rather than a ROM, so with no per-game layer to apply: the
  per-platform override when its label still names a bakeable emulator, else the default emulator. It is one pick with
  two projections — the name a surface displays, and the **emulator identity** its BIOS answers key on — and resolving
  those separately is what let one pane name an emulator and judge by another.
- **Emulator identity** — which emulator an answer is about, in the spelling its own launch command uses: a libretro
  entry's core file basename (`dolphin_libretro.so`), a standalone entry's own command name (`DUCKSTATION`, `PCSX2`).
  The resolver states it on both the emulator catalogue and the firmware answer, which is what lets a pick be matched
  against the firmware answer it should be judged by. It is not the **label** — ES-DE lists one `pcsx2_libretro.so` as
  both `LRPS2` and `PCSX2`, so a label names a launch row and two rows can be one emulator — and it is not `core_so`,
  which no standalone emulator has. Absent where the resolver could not identify the emulator behind a row, and nothing
  may be scoped to it then.

### Wanted (firmware): needed / optional / not needed / unknown

What the machine says about one firmware file on a platform's list. The first two are the resolver's per-file answer —
an installed emulator will not run without it (**needed**), or can use it and will run without it (**optional**). The
last two are not properties of the file at all but of the **reading**, and the reading is scoped to the ONE emulator the
game will launch with: it stated what it wants and did not name this file (**not needed**), versus it could not be asked
(**unknown**) — a core shipping without its declaration, a standalone emulator the resolver has no card for, an
**emulator identity** it could not establish, or a configuration it could not read. An emulator the platform also offers
and this launch does not use never makes the answer doubtful.

Keeping the last two apart is the whole point of the vocabulary — "nothing wants this" is a finished answer and "nothing
could be established" is the absence of one, and a single boolean called both _not required_. **Wanted** is a property
of the platform's own emulators and does not move with which of them the user picked; the launch-scoped question is
**required by the launching emulator**, which is what the missing-BIOS badge counts — beside **system image**, the
console's own demand, which no count carries and which raises that same badge. The foil to **BIOS level** (the
platform-wide readiness verdict: unknown / ok / partial / missing).

A wanted file need not be one the RomM library holds — the two sets overlap without either containing the other, and a
platform's list is their **union**. A row the library does not hold is marked **not on server**: it counts towards
readiness, because it is a real prerequisite that is really absent, and never towards a download or a progress ratio,
because nothing in the plugin can fetch it.

### Row verdict (firmware): met / unmet / withheld

Whether one row's requirement is met, and the axis the **readiness** counts key off. It is **not** whether something is
at the destination: for a **folder declaration** the two come apart completely, since what satisfies the core is a file
_inside_ the folder and RetroDECK links LRPS2's `pcsx2/bios` onto the BIOS root, so the folder is there on every
install. Every statement below is scoped to a **required by the launching emulator** row first — a row that emulator
does not require moves no count whatever its verdict — and the library's own held/offered ratio is a third axis again,
counting what the RomM library holds rather than what is met.

- **met** — raises `required_downloaded`. For a declared folder, the resolver listed it and an image inside passes the
  core's own content check. For a declared file it is presence at the destination, answered by the resolver wherever it
  placed the file under the BIOS root and by the plugin's own probe for the rest — a library file no core declares, and
  a file an emulator keeps in its own tree.
- **unmet** — counted as a file shown to be absent, so it reads red and raises the play row's **BIOS badge**. A folder
  the resolver listed and found no image in is exactly this, and so is one that is not there at all.
- **withheld** — the reading established neither. It raises neither count, and while a required row carries one the
  **BIOS level** declines to `unknown` rather than claiming either. The rows below keep their own answers; only the
  one-line verdict declines.

The CAUSE of a withheld verdict is read off the resolver's **caveat codes** carried on the row — a folder read that
could not finish, a candidate the identity table and the core's own header check disagree about, a directory obstructing
a destination the emulator opens as a file — because the verdict is deliberately the answer alone and carries none of
it. What the verdict does decide is which family of codes can apply, and what a surface says when none of them is
recognised.

For a declared **file** the cause is the **byte reading** below, which the row carries beside the verdict.

Not every unestablished thing is withheld here. A declared file whose _bytes_ were never verified is not — and for
almost every such file the reason is simply that nobody asked: the verified question is put to one core at a time and
only where a folder row is open, so on a platform whose cores declare no folder nothing is verified at all. Where it
_is_ asked, it verifies that core's declared files too — each one the packaged identity table covers at a matching size,
and no others — and their **verdicts** are dropped unread. Either way, reading an unasked content question as a withheld
verdict would decline readiness for every row on every platform. The foil to **not on server**, which is a settled
absence and does count towards readiness.

### Byte reading (firmware): what became of a row's bytes

What the reading DID to a declared file, carried on the row beside its **verdict** in the resolver's own vocabulary and
never re-derived here. It answers a different question: the verdict says whether the requirement is met, this says what
was done to establish it.

It is needed because a withheld verdict has several causes and one of them is not a withholding at all. A file the
emulator **read and does not recognise** was checked — DuckStation boots such an image and calls it an unknown BIOS — so
the verdict stays withheld while "could not be checked" is simply untrue of it. Beside that sit a file whose **bytes did
not come back**, which is a statement about the plugin's own read and no evidence the launch cannot read it, and one the
emulator **refuses** on its size before reading a byte, which arrives with the verdict already unmet and needs the
reason rather than a verdict. Everything else — the bytes matched, they did not, nothing asked — leaves the row to the
axes it always had.

Where each of the three is worded is `frontend/src/utils/biosFileNote.ts`, the one place both surfaces derive a row's
note from. The mark in the platform pane's `On disk` cell stays the verdict alone and says only that nothing was settled
either way.

### System image (firmware): held / absent / unsettled / not demanded

Whether the core a game launches with has the firmware image its **console** cannot start without — a requirement no
libretro declaration can express. A `.info` marks each file **needed** or **optional** and nothing else: no way to say
"one of these", and no way to say the console will not boot without one. An author who knows it will not has two lossy
moves and the deployed catalogue takes both — SwanStation marks all five of its PlayStation images optional, Beetle PSX
marks three of its own required — so the file counts alone read a green "this core requires none of the files it names"
under the one core and three separate prerequisites under the other, over a system on which no game starts either way.
The console's own answer is world knowledge rather than a reading of the machine — the resolver keeps a source-cited
table of it, per system.

It is a **disjunction**, and that is what keeps it out of the counts. The console asks for _one_ of the images the core
declares, not for each of them, so it is a single requirement over the whole list rather than one requirement per file.
Folded into **required by the launching emulator** it would report every image the core declares as required —
`0 / 5 required files ready` under SwanStation, which declares five; carried as its own axis it is worded "at least one"
and never as a ratio, nor as a pointer at the file list, most of whose rows cannot answer it. The twenty in the same
page's `(0/20 RomM library files)` is a different set again: the **library inventory** below.

- **held** — one of the images is at its destination. Which one is not asked: any of them answers the whole requirement.
- **absent** — the console needs one and every row the launching emulator declares was established to be absent. The
  **BIOS level** goes to `missing`, tested ahead of the declines so a demonstration outranks a platform nothing could be
  established for. A withheld required row cannot hold with it: that row is one of the rows the disjunction is read
  over, so it leaves the answer **unsettled** instead.
- **unsettled** — the console needs one and whether it is there could not be established. It can turn a green verdict
  grey and nothing else: where the counts already read `partial` or `missing`, something is known to be absent and a
  doubt about one further file does not unsay it.
- **not demanded** — the axis makes no claim, and the file rows speak for themselves. Four recordings reach it: the core
  carries its own substitute (PCSX ReARMed's HLE BIOS), the console was established to start with nothing present, the
  question is recorded as open, or **nothing is recorded about the console at all**. The last is an unasked question and
  may never be read as "this console needs no firmware" — the same rule that keeps **unknown** apart from **not needed**
  one axis over.

The same table answers **per core**, and a file row carries that answer on each core's own entry beside that core's
`required` flag: what the core's `.info` says about this file, and — where that core states the demand as a
**disjunction** — how many files it is spread over. Two speakers, so a core marking the file _optional_ while its
console will not start without one of the five images it declares is the informative pair rather than a contradiction —
and neither half is ever rewritten into the other.

A core states the demand as a disjunction only where it marks **nothing** required: that is the one shape in which "one
of these" is the whole of what the core says. A core whose console needs an image and that does mark files required —
Beetle PSX marks three of the same five — says what it has to say through those rows being **required by the launching
emulator**, so its entries carry no count and its rows are not marked. The **system image candidate** flag is the same
answer read for the launching emulator onto the row: this row is one of the images that would start the console on its
own.

That candidate set is deliberately **narrower** than the set the **system image** value is read over, which is every
image the launching emulator declares whatever it called it. The two answer different questions — one is the console's
verdict, the other is which rows a surface may mark as ways to reach it — so widening the flag to every image-demanding
core would put a second mark on a requirement already stated, and narrowing the verdict to the marked rows would stop
answering for the cores that state required files.

Scoped to the **launching emulator** — the scope **required by the launching emulator** shares and **wanted** does not:
one unchanged PlayStation reads `absent` under SwanStation, whose five declared images the console needs one of, and
`not demanded` under PCSX ReARMed, which carries its own substitute.

### Declaration register (firmware): read / packaged

How the emulator that supplied a firmware row's **description** stated what it wants — the resolver's own word, carried
verbatim onto the row. **read** is a libretro core's own description file, read off the machine beside it, and its prose
is the packager's LABEL for the file: `(PS1 JP BIOS)` on a row named `scph5500.bin`, a region the name never states.
**packaged** is the card the resolver keeps for an emulator shipping no declaration of its own, and its prose is the
resolver explaining the REQUIREMENT in whole sentences. One field, two kinds of writing, and only the first is shown on
a row: the second is a paragraph on a line sized for a label, and it broke off mid-sentence on the platform pane and
filled the row on the game page.

It is the register of the ONE entry the description came from — a file several emulators declare carries the prose of
the first of them — and never a property of the file, the row or the emulator behind it. The resolver states three
further words (`absent`, `unreadable`, `unsupported`), none of which can reach a row: an entry in one of those states
carries no requirement, so it declares no file. A row nothing declared states no register at all, and its description is
its own file name.

### Library inventory (firmware): offered / held

What the RomM library has for a platform, and how much of it is on this machine — the third counted set beside the
**required by the launching emulator** files and the console's own **system image** demand. **offered** (`server_count`)
is every firmware file the library holds for the platform; **held** (`local_count`) is how many of those the plugin
found at their destination. Both are counted over the library's files alone, so a row the library does not have — **not
on server** — is in neither, however required it is. The code calls the pair the **held/offered ratio**.

It is a progress bar over a set the user can finish, not a readiness claim, which is why nothing about it keys off a
**row verdict**: `held` answers whether something is at the destination, and for a **folder declaration** that is
precisely what a verdict is not. Both surfaces render it behind the readiness sentence as `(1/20 RomM library files)`,
naming its set because the sentence in front counts another (`docs/architecture/qam-panel.md`, BIOS files).

### Safely-bakeable

An ES-DE `<command>` the plugin can bake into a Steam shortcut's `-e` override: a real emulator invocation that **ends
in `%ROM%`** and carries none of the forms the bake can't carry — no `%INJECT%` sidecar (that is _needs-setup_, not
launchable from Steam until ES-DE has run it once), no `%ENABLESHORTCUTS%` / `%EMULATOR_OS-SHELL%` shortcut-script form,
no embedded quoting (`"` or `\;`), no `%STARTDIR%` (RetroDECK's `run_game.sh` parses-but-drops it), and no placeholder
outside the known whitelist. The classifier `domain/emulator_commands.py` decides this per command; anything not
safely-bakeable is surfaced in the picker as **disabled** with a reason, never silently offered
([ADR-0020](docs/adr/0020-live-es-systems-emulator-resolution.md)). The foil to **default emulator** (the first
safely-bakeable command a system falls back to).

### Default emulator

The emulator a ROM launches with absent any per-game or per-platform **emulator override**: the **first
_safely-bakeable_ command in a system's `es_systems.xml` document order**. ES-DE lists a system's emulators in
preference order, so ES-DE's own preference picks it — the plugin adds no curation. It may be a **RetroArch core** or a
**standalone emulator** (PCSX2, RPCS3, Dolphin, …), whichever ES-DE lists first that the plugin can bake. Resolved live
from the sole source (`es_systems.xml`) — there is no bundled snapshot (the curated `core_defaults.json` was deleted in
\#1210). **Document order** here is the order the file DECLARES, never the order ES-DE would run: a gamelist
`<altemulator>` / `<alternativeEmulator>` promotes an entry for ES-DE and moves nothing here
([ADR-0030](docs/adr/0030-the-emulator-catalogue-is-read-by-the-vendored-resolver.md)). When no command is
safely-bakeable (or the catalogue can't be read) there is no default emulator and the ROM plain-launches, letting
RetroDECK resolve its own command ([ADR-0020](docs/adr/0020-live-es-systems-emulator-resolution.md)). The foil to
**emulator override** (a user deviation from this default).

### Disc

The launchable unit of a multi-disc ROM: a single-disc **container** file the emulator opens directly — a `.cue`, a
`.chd`, or an `.iso` (`DISC_IMAGE_EXTENSIONS`, `domain/disc_formats.py`). A disc is **not** its `.bin` sidecar (raw
track data a `.cue` references, never launched directly) and **not** the `.m3u` playlist (which points at several
discs). When a bin/cue PS1 game ships both files, the disc is the `.cue`, never the `.bin`. The foil to **disc-image
format** (the file-shape category) and **selected disc** (the user's per-game pick among the discs).

### Disc-image format

The format-semantic, emulator-independent category "this file shape is a launchable disc image" — the hardcoded set
`{.cue, .chd, .iso}` (`DISC_IMAGE_EXTENSIONS`). It answers _is this a disc image?_, which es_systems cannot: es_systems
is a flat per-system accept-list with no per-token role metadata, so it can say a system _accepts_ `.cue` but never that
`.cue` is a disc while `.bin` is its sidecar. The set is intersected with the system's **live** es_systems accept-list
at enumeration time, so the hardcoded knowledge supplies disc _identity_ and es_systems supplies per-system _capability_
— two different questions with two different owners
([ADR-0014](docs/adr/0014-per-game-disc-selection-in-db-applied-as-bake-time-launch-path-override.md)).

### Selected disc

A user's per-game pick of **which disc launches** for a multi-disc ROM, stored as the disc's **basename** on
`roms.selected_disc` (nullable). It mirrors the per-game **emulator override** exactly: NULL means "follow the default"
(the install's `.m3u` when `file_path` is one, else the first enumerated disc), only `pin_selected_disc` /
`clear_selected_disc` write it, it is **excluded from the sync UPSERT** so a re-sync never resets it, and it anchors on
`roms` so it survives uninstall/reinstall and home migration. Applied as a **bake-time launch-path override** — it
changes only the path baked into the shortcut's `launch_options`, never `RomInstall.file_path`, structurally identical
to how the override changes the invocation without touching `file_path`
([ADR-0014](docs/adr/0014-per-game-disc-selection-in-db-applied-as-bake-time-launch-path-override.md)). A stale pin (the
disc no longer present) degrades to the default with a WARNING, never fatal.

### Save answer

What one ROM's save consists of, where the emulator keeps it, and whether this plugin may carry it —
`domain.save_answer.SaveAnswer`, read live off the machine by the vendored resolver through `adapters/atlas_saves.py`.
It replaced a per-system extension table the plugin maintained by hand.

An answer is about one **ROM** and one **emulator**, never a platform (see
[Save scope](#save-scope-per-rom-and-per-emulator-never-per-platform)), and it names the files, their directory, their
roles, the holes left in any name, and the resolver's caveat codes. A file whose role is the emulator's
**configuration** rather than the player's **progress** — Saturn's `.smpc` — is named on the answer and **not carried**;
a directory move still moves it, because splitting one save across two directories breaks the game. Each named file
carries a `carried` flag, named for the RULE and not for the file: it says save sync carries this name, which is a
different claim from "this file is in sync".

**An answer is not always about a file that exists.** A ROM the library holds but has not installed is asked about the
path it WOULD occupy, because `roms.fs_name` carries the extension the answer turns on; every name in such an answer is
a prediction. `content_installed` says which it is, and a surface that renders the names without reading it tells a user
their uninstalled game already has save files.

### Save state: per-game files / shared / inside the content / hole / not established

The five values a save answer classifies a ROM into, **exactly one of which holds**. Only the first is a save this
plugin can carry; the other four **refuse** — no path is probed, no sync state is written, and the sync returns the
benign-skip shape rather than a failure.

- **per-game files** — the answer names concrete files with no hole. Sync as usual, any number of files.
- **shared** — the emulator's granularity is a shared card or a shared file, so one file holds many games' progress and
  a per-game sync would carry another game's save onto this ROM's record.
- **inside the content** — the save is written into the game file itself. There is nothing separate to carry.
- **hole** — the shape is known, but part of the path or the name is the game's own identity, which nothing here
  supplies. Flycast needs a `save_id` in the filename; Dolphin needs the `region` in the directory.
- **not established** — nobody established what this emulator writes, or the directory is known and the names in it are
  not, or no question reached the resolver at all. **These are three shapes, `nothing_established`, `directory_known`
  and `not_asked`, and they stay apart**: they are different sentences to a reader, and collapsing them claims ignorance
  about a folder we can point at, or claims a refusal where nobody was ever asked.

Detail, including which systems land where on a stock RetroDECK, is in
[Save sync coverage](docs/architecture/save-sync-coverage.md).

### Save scope: per ROM and per emulator, never per platform

A save answer is about **one ROM** and **the emulator that would launch it**, and means nothing without both.

The emulator half: PS2 is not unsupported — **standalone PCSX2** is, because it keeps two shared memory cards, and a
libretro core for the same platform can answer per-game.

The ROM half: the answer turns on the **content file's own extension**. An Amiga `.adf` keeps its save inside the disk
image, an Amiga `.lha` states a directory whose file names PUAE does not list, and an `.hdf` establishes nothing; a Sega
CD `.chd` is a shared BRAM card where a `.bin` is a per-game `.srm`. So every question carries the ROM's real content
path — `RomInstall.file_path` when installed, the path built from `roms.fs_name` when not — and a synthetic stem is
never an acceptable stand-in, because it answers a different question in a shape that looks like an answer to this one.

So a save state is never reported for a platform, and whatever carries one names the emulator it is about.

This is why the question goes to the **catalogue entry** the plugin resolved for this ROM (the label
`ActiveCoreResolver` produced, which is the label the launch bakes) rather than to a bare core: a standalone emulator
answers for itself.

### Save-sync slot

A named channel for a ROM's saves (e.g. `default`). **Every slot is a real, addressable name** — the active slot for a
ROM is recorded on its `RomSaveSyncState`, and `default_slot` (a `settings.json` config value, per #822) is the slot a
newly-tracked ROM starts on. Slots let the same ROM carry distinct save sets without clobbering one another. Confirming
a slot (`confirm_slot(...)`) is an explicit user/flow decision that requires a real slot name — the plugin never
silently adopts a foreign slot, and it never confirms a ROM onto the legacy `slot:null`. The legacy `slot:null` is
**retired as a confirmable target** (#1276 / ADR-0017): it survives only as a one-time migration **source** — the Slot
Setup Wizard migrates those pre-slot saves into a named slot — and is never a ROM's active slot.

### Baseline

The last-synced reference point recorded for a tracked save file — captured in a `FileSyncState` value object
(filename + hash + `tracked_save_id`). The newest-wins matrix diffs the current local and remote state against the
baseline to detect drift on the next sync. Adopting a baseline (`adopt_baseline(...)`) is how a file becomes tracked.

### Newest-wins matrix

The save-sync conflict-resolution model (`services/saves/sync_engine/`). For each tracked file it evaluates local vs
remote vs **baseline** and resolves to whichever side is newer, rather than blindly mirroring one direction. The
"matrix" is the per-file evaluation table that drives the actual upload / download / no-op dispatch for a ROM's sync
run.

### SyncRun

One library-sync operation modelled as a first-class aggregate (`domain/sync_run.py`, `sync_runs` table) — a `running` →
`completed` / `cancelled` / `interrupted` / `paused` / `errored` state machine carrying `started_at` / `finished_at`,
the planned platform/ROM counts, and the lists of platforms/collections actually completed. The terminal transition
happens exactly once and is irreversible, and the four stopped terminals are deliberately distinct: `cancelled` is the
user's own Cancel, `interrupted` an external death (the frontend stopped responding), `paused` a deliberate
session-budget stop at a chunk boundary, and `errored` a failure. Replaces the scattered JSON scalars `last_sync`,
`sync_stats`, `last_synced_platforms`, `last_synced_collections`. One row per sync (inserted at apply-start, finalized
at the end). The "how many ROMs" figure is **not** stored on it — that is a live `len(registry)` count computed at read
time.

### Active session

One play session the frontend is currently tracking: `{appId, romId, startMs}` in `sessionManager`'s map, keyed by the
Steam app that opened it. There is one entry **per running app**, not one overall — two RomM games at once are two
active sessions, each finalizing on its own app's exit. An active session is opened by a game-start notification or by
reload-adoption, and its durable counterpart is the `last_session_start` marker on the ROM's `rom_playtime` row plus the
`romm-tender:active-session` breadcrumb that lets a reload adopt it. "Active" is about the frontend's tracking, not
about foreground/focus — a backgrounded game's session is still active.

### Unbind / stale / prune

Three deliberately-distinct ROM-removal notions (see [ADR-0007](docs/adr/0007-rom-retention-identity-anchor.md)):

- **Unbind** — drop a ROM's Steam-shortcut binding (`shortcut_app_id` → NULL, via `Rom.unbind_shortcut()`) while keeping
  its `roms` row and all per-ROM state (install, metadata, playtime, saves). What removing a shortcut does.
- **Stale** — a ROM still in local state but no longer returned by RomM on a sync. Triggers an **unbind**, never a
  delete: a stale signal may be a transient server blip or a reversible RomM change, and local playtime/saves must
  survive.
- **Prune** — an explicit, opt-in purge that `DELETE`s the `roms` row, cascading every per-ROM child away atomically.
  The **only** thing that deletes rows. **Clean Up Removed RomM Games** performs it only after fresh exact-ID 404s,
  explicit options, and any enabled recovery bundle have passed their final guards.

### Recovery bundle

A checksum-verified, atomically sealed pre-mutation snapshot created by explicit Prune. It records the affected local
aggregate state and selected/mandatory recoverable files for manual recovery; it is not a database export or an
automatic restore point. Finalization revalidates both the sealed bytes and their source state before deleting anything.

### Action token

A one-run, one-action lease for a frontend-owned Steam operation during Prune. The frontend must claim the exact token
before touching Steam and complete that same token afterward. Duplicate, stale, unknown, and late unclaimed tokens are
non-authoritative and cannot mutate Steam.

### Game-detail store

The single holder of the state one Steam game page shares across its surfaces (`frontend/src/utils/gameDetailStore.ts`):
the bound `rom_id`, install state, save-sync status, BIOS level and core selection, plus the reads that produce them.
One entry per appId — the first surface to subscribe opens it, the last to unsubscribe closes it, so a page that is off
screen holds nothing and listens to nothing. The `romm_data_changed` DOM bus keeps carrying the notifications and
carries no state: one handler per appId folds an event into the entry, and every subscriber renders from that one fold
rather than from its own copy. Overlapping reads for one appId share a single request, so a payload-less notification
costs one `get_save_status` round-trip however many surfaces are mounted. The foil to a surface's **own** state — the
play row's PLAYTIME / LAST PLAYED pair, which nothing else reads and which stays in the component.

### Collection kind (Standard / Smart / Virtual)

The axis on which a RomM collection is classified for syncing — the internal literal `standard` / `smart` / `virtual`
(the `enabled_collections` bucket keys, `WorkUnit.collection_kind`, and `CollectionSyncState.collection_kind`), whose
values match RomM's own kind labels (Standard / Smart / Virtual); RomM's code calls the first kind `regular`.
**Standard** is a manually-created collection (one of RomM's two owned kinds — its ROM membership is hand-picked; the
auto-managed favorites collection is a Standard one); **Smart** is a saved-search whose membership resolves at query
time, and the other owned kind; **Virtual** is an ownerless grouping RomM derives from game metadata, of which the
plugin syncs two `virtual_type`s — `franchise` and `collection` (an IGDB collection). Only Standard and Smart are
stampable for the incremental skip. Internal-name history: the first kind was called `user` until #1539 renamed it
`standard` (display "My" → "Standard"). What the QAM calls the kinds is decided in `docs/architecture/qam-panel.md` §
Library, not here, and the literals stay as they are whatever it calls them.

### Collection owner-scope

Whether other users' collections on a shared RomM server are part of this device's sync — the `collection_owner_scope`
setting valued `all` (every collection RomM lists to the signed-in user: their own, and other users' public ones) or
`own` (only their own). **A sync scope, not a filter**: under `own`, a foreign standard or smart collection is hidden
from the list **and** left out of the sync, even one switched on earlier — once the signed-in user's id is known, since
until then no collection counts as foreign. Orthogonal to the collection kind axis: it decides by owner (`is_own` /
`user_id` vs `romm_user_id`), not by kind, and virtual collections have no owner so it never touches them. The stored
values are `own` / `all` whatever the QAM calls them. How the QAM presents it is decided in
`docs/architecture/qam-panel.md` § Library. _Avoid_ calling it a filter.

### Collection naming mode (merge / by_label)

How the Steam-collection **name** is formed when RomM collections share a display name across kinds — the
`collection_naming_mode` setting valued `merge` (default) or `by_label`. Under **`merge`**, same-named collections union
into one `RomM: [<name>] (<host>)` Steam collection (#1503). Under **`by_label`**, same-named collections of different
types stay separate Steam collections, told apart by a **type label** appended to the name, and the type label follows
what the QAM calls the kind; the labels a known type gets are listed in `docs/architecture/steam-non-steam-shortcuts.md`
§ Collection naming mode. For a virtual collection the type label names its virtual type rather than the kind —
franchise and IGDB-collection are both `kind="virtual"`, distinguished by `virtual_type` (see the **Collection kind**
entry above) — except one of no known type, which gets the kind's label. Computed backend-side at the reporter's union
key (`domain/collection_label.py`), so the wire payload stays name→appIds and the frontend needs no change; the mode
flip is applied by the ordinary complete-set reconcile on the next normal sync (no Force Full Sync). Same-named
collections that share a type label still union.

### Surface (bigpicture / desktop)

One of the two UIs the plugin draws, each with its own directory under `frontend/src/`: **bigpicture** is the gamepad
surface — the QAM panel and the patch into Steam's game-detail route (`frontend/src/bigpicture/`) — and **desktop** is
the keyboard-and-mouse client (`frontend/src/desktop/`, which holds a README and nothing else so far). They are **peers,
not layers**: the two share data and logic and almost nothing visual, so neither may import from the other, and anything
that turns out to belong to both moves _down_ into `api/`, `utils/` or `types/` rather than sideways —
`import-x/no-restricted-paths` in `frontend/eslint.config.js` enforces both directions.

The word predates the directories and the older use is still current: `docs/architecture/qam-panel.md` and
`frontend/src/utils/gameDetailStore.ts` call the components subscribed to one game page's **game-detail store** that
page's **surfaces** — an open-ended set ("however many surfaces are mounted"), never a fixed number (see **Game-detail
store**). Every one of them lives inside `bigpicture/`, so in the sense above they are viewers of one surface, and only
the scope tells the readings apart: a surface of the plugin is a directory, a surface of a game page is a component
subscribed to that page's game-detail store. _Avoid_: platform, target; _frontend_ (the whole of `frontend/src/` — both
surfaces and everything below them).

### Quick Access entry / entry marker / tab glyph

The **Quick Access entry** is Tender's own row in Steam's Quick Access tab strip — the glyph in the rail, the panel
behind it, and the **entry's heading** Steam draws above that panel from what the entry hands it. It is added by
patching the two renderers Steam draws the menu with and pushing an entry into the tab array they hand back, so it
composes with Decky Loader's entry rather than going through it, and it appears whether or not Decky is running.

The **entry marker** is the property the entry carries to say it is ours (`tender`), and it is what makes a render pass
over an array the entry is already in add nothing. **It is not the injection's marker** — that one
(`window.__tender_panel__`, → Injection) says a JS context already carries the panel, and the two are never called by
the same name.

The **tab glyph** is what the strip draws: the mark reduced to one tone with no disc, the sync ring levelled and
thickened for the size the strip draws it at, and the body as the button bars, solid and with nothing marking the four
button positions — at the size the glyph asks for a bar is too narrow to hold a second shape. It is generated from the
mark's own drawing routines (`scripts/logo/tabicon.py`) rather than drawn by hand, and it is **static** — it reads no
state and has none, because the motion it shipped with cost roughly 29% of one core for as long as the menu was open (→
`docs/architecture/qam-panel.md`, The glyph).

_Avoid_: **tab** on its own for the entry, which is also Steam's word for the L1/R1 views inside a wide page (→ QAM
page); **plugin entry** and **Decky tab**, neither of which this is; **the tab's heading** and **the panel's heading**
for the entry's heading, which is one thing and takes one name.

### QAM page / Main / wide page

What the plugin's Quick Access Menu panel shows at one time, chosen by the panel's router (`Page` in
`frontend/src/types/navigation.ts`). Exactly one page is mounted at a time; navigating to another unmounts it. **Main**
is the page the panel opens on: notices, status, the conditional slot, the download summary and the menu. A **wide
page** is a page that widens the panel from 348 px to 854 px for as long as it is mounted — the full screen width on the
Deck, whose Big Picture viewport is 854 CSS px across. The width belongs to the page, not to a view inside it, and it
collapses again when the page unmounts, the QAM tab changes, the panel closes or the plugin is dismounted. Every page is
one or the other, and the page table in `docs/architecture/qam-panel.md` is where each page's width is decided. _Avoid_:
sub-page, screen, route (a **route** is a Steam page outside the QAM, such as the game detail page).

### List and detail

The layout of a wide page whose entries each carry a detail: the list on the left, the focused entry's detail on the
right. **Focus selects** — moving through the list changes the detail at once; A operates the control in the row (a sync
toggle), never the selection. The two regions scroll independently. _Avoid_: master/detail, sidebar.

### Inventory row (Data Management)

A row on the **Data Management** page. It names a **population** — a set of things this device holds — and never an
operation; which populations the page carries is decided in `docs/architecture/qam-panel.md`, not here. The row states
that population's numbers and no verb: the verb lives on the button in the detail pane, which is what lets a row exist
that offers no action at all (recovery bundles are listed there and deleted nowhere). **Gone from RomM is a population
too** — the versions this device still keeps that the server no longer has — which is what lets it sit beside the others
instead of being an errand; removing them is still a **Prune** (→ Unbind / stale / prune), and that word names the
operation, never the row. A count that costs a server round trip or a backend scan is not fetched on arrival: the row
reads `scan` until the reader asks for it. _Avoid_, as a name for one of these rows: operation, action (an **action
token** is a different thing entirely), and **danger zone** — the narrow panel's name for the page, which described the
risk rather than the contents.

### Preview

The answer to "what would a sync change" — the delta the backend computes without applying it, holds for **30 minutes**,
and hands over as a `SyncPreview`: the library-wide counts, the per-platform split, the added and removed collection
names, and the id an apply names it by. It is a **held** thing, not a screen: the backend stages one snapshot at a time
and the frontend keeps whatever it was handed in a module store, so it outlives the panel that asked for it. A preview
ends exactly three ways the reader chooses, all of them on the Sync page — **applied**, **cancelled**, or **refreshed**
(discarded and replaced by a fresh one) — and it expires on its own if none of them happens. A fourth ending is the
page's own: a successful **Force Full Sync** discards the state the preview was worked out against, so the preview goes
with it, discarded on both sides exactly as Cancel discards one. An expired preview is still shown, so nothing the
reader was told disappears behind them; what goes is the offer to apply it. _Avoid_: dry run, plan (a **plan** is the
run's own work queue, one unit per platform or collection), diff.

### Conditional slot

The one row under Main's status rows that can be pressed, present only while the Sync page has something to report — a
run in flight, or a preview waiting to be answered — and absent otherwise. It states coarsely — a short label, and
beside it the run's step counter or the preview's counts, which can be a phrase carrying no number at all — and opens
the Sync page, exactly as the menu's Sync entry does. The status rows above it state and do nothing, and the slot is the
single exception; it is not the panel's only door besides the menu, but it and a notice's own button are there only
while their condition is, which is what leaves the menu the navigation that is always in the same place. _Avoid_: status
card, banner (a **notice** names a condition that needs the user; this states what the Sync page is doing), button.

### Notice / home

A **notice** is Main's standing statement of a condition that needs the user (settings were reset, the RetroArch input
driver is wrong, a sync paused on the session budget). Most are cards and the input-driver one is a row; the shape is
not what makes it a notice. They do not all sit at the top: three lead the panel above the status rows, and three more
sit inside the status block, below the conditional slot — `docs/architecture/qam-panel.md`'s Main section has the order.
The **home** of a condition is the one page where it is acted on. A notice names the condition and jumps to its home;
the action exists only there, never on the notice — with one exception today, the RetroArch input driver, whose **Fix**
still applies in place behind a confirmation until Settings (#1816) gives it a home. A condition answered **once and for
all** — the user picks between named outcomes, and answering ends the condition for good — has no page to return to, so
its home is a modal opened from the notice; that modal _is_ the home, not a second exception to the rule. A condition
with no home in the plugin stays a notice without a jump, with Dismiss where there is a sensible end to it. _Avoid_:
banner (component names only), warning, alert.
