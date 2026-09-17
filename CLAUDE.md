# Tender — RomM library into Steam

## What This Is

Syncs a self-hosted RomM library into Steam as Non-Steam shortcuts. Games launch via RetroDECK. The QAM panel handles
settings, sync, downloads, and BIOS management.

The backend runs as **its own process** and hosts the panel itself over a loopback port
([ADR-0036](docs/adr/0036-the-backend-hosts-itself.md)); it was a Decky Loader plugin up to 0.33. It also LOADS the
panel, into Steam's renderer over the CEF debugger — so `mise run dev` is now "build, then run the backend", and the
Decky-shaped deploy tasks are gone. The installer, the user unit and the XDG paths are a separate cut (#1902).

## What belongs in this file

Three things, and nothing else:

1. **Traps** — where an agent would confidently do the wrong thing and have no reason to go look first.
2. **Cross-cutting invariants** — rules that span files, so no single diff shows the whole rule.
3. **Workflow** — how we work here; not derivable from the code.

Everything else is topic depth: `docs/` for humans, `.claude/rules/` for path-scoped coding conventions. A rule with a
mechanical check needs only its one-line statement here (CI carries the enforcement); a rule without one needs its full
statement here or in the rule file that owns its area, because nothing else will catch it.

## Where the details live

Each page below is the current-truth owner of its area, and most carry their own ADR trail. Read the page before working
in the area. **Do not cite ADRs from this file** — an ADR is frozen history (and may be `Proposed` or superseded, which
is invisible at the citation site), so reach it through the page that owns the topic.

- Domain vocabulary — the canonical meaning of project terms — [CONTEXT.md](CONTEXT.md). A glossary, not a spec: use its
  wording in code, issues, and PRs, and add a term there the moment it resolves in discussion.
- Steam shortcuts — appIds, artwork, launch-option writes, removal churn —
  [steam-non-steam-shortcuts.md](docs/architecture/steam-non-steam-shortcuts.md)
- QAM panel — pages and their widths, the wide-page frame, list-and-detail navigation, notices and their homes —
  [qam-panel.md](docs/architecture/qam-panel.md)
- How the panel is BUILT — the three build outputs, the two copies of the panel and why, Steam's React globals, the
  start-up check — [frontend-bundles.md](docs/architecture/frontend-bundles.md)
- How the panel GETS INTO STEAM — the CEF client, which bundle is chosen and from what, the marker, the crash watchdog,
  the load-failure card — [loading-the-panel.md](docs/architecture/loading-the-panel.md)
- Save-file sync — slots, conflict resolution, negotiate transport, version history —
  [save-file-sync-architecture.md](docs/architecture/save-file-sync-architecture.md)
- Save-sync coverage matrix — [save-sync-coverage.md](docs/architecture/save-sync-coverage.md)
- Removed-game cleanup — deletion authority, admission/leases, claims, recovery bundles —
  [removed-game-cleanup.md](docs/architecture/removed-game-cleanup.md)
- Services, adapters, wiring; connection/token and settings-persistence internals —
  [backend-architecture.md](docs/architecture/backend-architecture.md)
- SQLite schema, aggregate roots, migrations — [database-design.md](docs/architecture/database-design.md)
- Emulator and core selection — [core-emulator-selection.md](docs/architecture/core-emulator-selection.md)
- RetroArch/ES-DE config parsing — [config-source-parsers.md](docs/architecture/config-source-parsers.md)
- Steam Remote Play — [steam-remote-play.md](docs/architecture/steam-remote-play.md)
- **End-user-facing behavior and UI** — setup, configuration, syncing, save-sync, BIOS, troubleshooting —
  `docs/user-guide/`
- Dev setup, dependency management, frontend loop — `docs/contributing/`

## Path-scoped rules — `.claude/rules/`

Coding conventions live in `.claude/rules/*.md`, each carrying a `paths:` frontmatter glob. They are plain Markdown, not
a harness-specific format: an agent that supports path-scoped rules gets one loaded when a matching file is read, and
**every other agent must open the file itself**. Either way it arrives a beat late for code you are **creating** rather
than editing, so the entries below lead with what goes wrong unnoticed. Read the rule that owns an area before writing
new code in it.

- `services.md` — a new service takes **one `config: XxxServiceConfig` kwarg** (frozen, all deps inside); debug logging
  is the injected `DebugLogger`. Neither is checked.
- `python-conventions.md` — Protocol suffixes by shape, `do_<verb>` vs. `_<verb>_io`, docstrings stating the contract
  rather than the behavior, and when a subfolder is justified. **No mechanical check exists for any of it.**
- `adapters-domain.md` — adapters own I/O, domain is pure, aggregate mutations are verb-named after the event
  (`adopt_baseline`, not `update_baseline`). The field-assignment ban is checked; the naming is not.
- `romm-http.md` — an unproven 404 must never become `RommNotFoundError`, which is deletion authority downstream: the
  entity proof is the default and only the three byte-stream fetches opt out. Tests pin both directions; nothing else
  does. Also owns the transport's reachability state: a new request method sends through `_urlopen` (checked) and passes
  `romm_origin=False` if it does not talk to RomM (not checked).
- `bootstrap-wiring.md` — the `main.py` / `bootstrap/` split, which half of `bootstrap/` new wiring belongs in, and why
  `Plugin.run` is a synchronous classmethod.
- `host.md` — the process that hosts this backend (`backend/host/**`): the transport-vs-callable failure shapes, the
  token's one deliberate exception, the order of the three admission checks, claim-bearing events, where the size cap is
  judged, the served root, and what the injected expression may carry. **None of its seven rules has a mechanical check;
  each fails green** — the redaction one did exactly that, and only an assertion on stderr's own output caught it.
- `callables.md` — the `{success, reason, message}` failure shape and its two carve-outs. Checked.
- `vendored-assets.md` — `_vendor/` and `native/` are checksum-pinned upstream copies — verbatim, or verbatim plus a
  documented local patch — and every vendored tree carries its own manifest. The checksums are checked; the reflex to
  fix the upstream artifact instead of the copy is not. `defaults/` holds no vendored artifact since the BIOS registry
  left.
- `testing-backend.md` — test tiers, gate tests, vendored conformance vectors.
- `testing-frontend.md` — the backend-event harness, that `api/host` is stubbed suite-wide so no socket is ever opened,
  non-vacuous catch assertions.
- `comments.md` — an inline comment is the exception: only an outside-world fact, a road not taken, or a constraint the
  code cannot express. Re-read the comment on the line you touch — a stale one is worse than none, because it is
  believed and nothing in the toolchain contradicts it. **No mechanical check exists.**

## Documentation

**Docs are updated in the same PR as the code change. This is not optional.** When a change affects architecture, data
flows, feature behavior, or user-facing UI, the relevant page under `docs/` must be updated in the same PR.
Documentation-debt-as-a-separate-follow-up-issue is forbidden — those follow-ups never land. If you're not sure whether
a change needs docs, the default is "yes, it does." Enforced in CI by `.github/workflows/docs-check.yml`.

For genuinely doc-irrelevant PRs (pure refactor with no user-visible change, no architecture shift, no new flow;
tooling/CI changes; dependency bumps), set the `no-docs-change` label on the PR OR include `docs: N/A` (with a one-line
reason) in the PR description. Opting out is an explicit acknowledgement, not a silent omission.

One opt-out is automatic and nobody has to remember it: a PR from a `release-please--*` branch. Its diff stamps
`version.txt` and `backend/domain/identity.py`'s `VERSION` line, so every release PR touches source and would fail the
check forever. It is skipped by BRANCH rather than by exempting those two paths, because `identity.py` is a real source
file whose docstring is load-bearing — exempting it by name would drop the docs requirement from every hand-written
change to it as well.

Docs are Material for MkDocs, published to GitHub Pages by `.github/workflows/docs.yml` on push to `main`. Preview
locally with `mise run docs`.

## Traps — non-obvious rules that bite silently

- **The build output lives at `<repo>/dist/`, not under `frontend/`** — and the frontend package writes one directory UP
  to put it there (`OUT_DIR` in `frontend/rollup.config.js`). `dist/` is the SEAM between the two halves rather than the
  frontend's property: the backend serves it as `os.path.join(directories.code_dir, "dist")` (`backend/main.py`), and a
  host that located its own build output relative to `__file__` would be the only part of the backend that knew the
  repository's layout. Tidying `dist/` into the package it is built by would put the backend's reach inside the
  frontend's internals. Two consequences worth knowing: `frontend/tsconfig.json`'s `outDir` and
  `frontend/.size-limit.json`'s paths point up as well, and **emptying that directory is the `build` script's job**
  (`rm -rf ../dist && rollup -c`) rather than any plugin's.
- **The build produces THREE files, and two of them are the same panel** — `dist/globals.js` (Steam's React installed by
  us), `dist/index.js` (the panel with `@decky/ui` bundled) and `dist/index-coexistence.js` (the panel taking it from
  Decky's `DFL` global). **Which panel bundle gets loaded is the injector's decision**
  (`backend/host/inject/bundles.py`), taken from the machine rather than from the window; the name is the whole of the
  SELECTION mechanism. Each panel bundle does know which of the two it IS — `rollup.config.js` stamps it through
  `virtual:tender-bundle-kind`, and `frontend/src/boot/searchingCopy.ts` is the one thing that reads it. What it reads
  it INTO is the start-up failure answer, whose sentence names a different program to update depending on whose copy of
  `@decky/ui` ran the search that missed — and that answer has two consumers, not one: `index.tsx` resolves it once and
  hands it to both the fallback page and the log line beside it. Importing `@decky/ui` re-executes every module in
  Steam's live webpack registry, and **what makes a second import fatal is a consumer already RENDERING from those
  modules — not the number of sweeps, and not Big Picture.** Measured on the device: a second sweep on the desktop
  client survives; a third with Big Picture open and the Quick Access view mounted survives; starting Decky into that
  same session survives; Big Picture plus Quick Access plus Decky **already rendering** crashes with
  `Minified React error #31`, because a module re-executed underneath something holding its exports leaves an empty
  object where a component was. Steam's own interface is not such a consumer; Decky's is. That is why the pair exists
  and why a runtime `if` cannot replace it: the damage is done at import, and ESM hoists the import above any set-up
  code in the same module. **`dist/globals.js` carries the same sweep** — it imports `@decky/ui/dist/webpack`, whose
  `initModuleCache()` is unguarded at module scope — so the short-circuit inside `installGlobals` protects nothing, and
  that bundle must not be loaded beside a running Decky either. `pnpm -C frontend check:bundle` fails when either bundle
  stops being what it is; `pnpm build` alone would not. Detail:
  [frontend-bundles.md](docs/architecture/frontend-bundles.md).
- **Our three React globals must match Decky's EXACTLY, and the cost of a difference lands on Decky's users** —
  `frontend/src/boot/steamGlobals.ts` installs `SP_REACT`, `SP_REACTDOM` and `SP_JSX`, which Steam does not define and
  Decky's loader otherwise would. Decky skips its **entire** globals block when `SP_REACT` is already set, so when ours
  runs first, **Decky's whole frontend renders through our shape** — a predicate of ours that differs breaks Decky's
  interface, not Tender's. `frontend/src/boot/decky-globals-block.txt` pins upstream's block verbatim with its
  provenance and `steamGlobals.test.ts` holds the two against each other; a failure there is not a test to fix but a
  question about which of the two moved.

- **Shortcuts**: Use `SteamClient.Apps.AddShortcut()` from frontend JS, NOT VDF writes. VDF edits require Steam restart;
  SteamClient API is instant.
- **AddShortcut ignores most params**: `AddShortcut(name, exe, startDir, launchOptions)` ignores startDir and
  launchOptions. Must use `Set*` calls (`SetShortcutName`, `SetShortcutExe`, `SetShortcutStartDir`,
  `SetAppLaunchOptions`) once the new app's overview is registered. Do NOT pass quoted exe paths — the API quotes
  internally.
- **AddShortcut timing**: After `AddShortcut()`, wait for the new app's overview before setting properties — poll
  `appStore.GetAppOverviewByAppID(appId)` (`waitForAppOverview`), never a blind fixed delay. Use 50ms between operations
  in the apply loop.
- **Shortcut appId is assigned, not derived**: Steam assigns it at creation and it is stable for the shortcut's
  lifetime; the plugin records it in `roms.shortcut_app_id` and detects ownership by the exe path. Never re-derive it
  (the `CRC32(exe + appName)` formula is disproven). `launchOptions`, `startDir` **and `exe`** changes are appId-safe,
  and each rests on its own measurement: `launchOptions` on #827's hardware runs, and `exe` on rewriting every one of a
  826-shortcut library and finding every appId still there, against a `shortcuts.vdf` backup taken before it (0 new, 0
  lost, names unchanged). "They are all `Set*` calls on an existing shortcut" is a description of the three, not
  evidence about any of them — it is equally true of `SetShortcutName`, which is the one that has **never** been
  measured. The sync writes the name in place too (`rewriteShortcutIdentity`), and nothing has established what that
  does to the appId; do not read the exe measurement as covering it.
- **Frontend API**: `@decky/ui` for Steam's components, and `frontend/src/api/host.ts` for everything `@decky/api` used
  to give us — same six export names, so a call site reads the same. Four of the six go over the backend's WebSocket;
  **`toaster` and `routerHook` are declared placeholders that do nothing** until #1901, so no toast appears and Steam's
  game page carries no Tender section. Neither reaches Decky's loader API when one is present, and what decides that is
  not purity: those two are the loader's own, #1901 replaces them with Tender's, and a placeholder that borrowed one
  wherever it found one would behave differently on a machine with Decky from one without — which is the difference this
  program exists not to depend on. **The reference machine runs the loader** (measured: `plugin_loader.service` active
  and enabled, `127.0.0.1:1337` listening), so that borrowing would show up there rather than hide, which is the
  opposite of what this note used to say.
- **A callable must be `async def`**: even where the body is synchronous. The set a caller can reach is exactly the
  public `async def` on `Plugin` — `host.dispatch.reachable_methods` resolves it off the loaded class,
  `scripts/check_callable_manifest.py` derives the same set from the source, and `tests/host/test_dispatch.py` asserts
  the two are equal. Two consequences, both silent: dropping `async` makes a callable unreachable, and giving `Plugin` a
  public `async def` that was never meant as wire surface publishes it. `Plugin.run`, the process entry point, is
  synchronous for exactly that reason.
- **RomM API quirks**: Filter param is `platform_ids` (plural). Cover URLs have unencoded spaces (must URL-encode).
  Paginated: `{"items": [...], "total": N}`. List calls page via `lib/romm_paging.py` and append
  `&with_char_index=false&with_filter_values=false` to skip aggregations the server otherwise computes on every request.
- **RomM minimum version**: Requires RomM >= 4.9.0, hard-rejected in `test_connection()` (`_MIN_REQUIRED_VERSION` in
  `main.py`) — the plugin is inert until the server is updated.
- **User-Agent on outgoing HTTP**: SteamGridDB **and** RomM behind Cloudflare Tunnel reject the default `Python-urllib`
  UA with 403. Both adapters that talk to a server off this machine (`adapters/romm/http.py`, `adapters/steamgriddb.py`)
  take a `user_agent: str` ctor param; bootstrap threads `<package name>/<version>`, both halves from
  `domain/identity.py` — no hardcoded name and no hardcoded version at the format site, so the UA and the recovery root
  come from that one module rather than from two literals that could drift (the root additionally through
  `sanitize_package_name`, which is the identity for a name shaped like this one). Those two are everything
  `PACKAGE_NAME` reaches; the folder the program ships as is not decided by it — and is decided nowhere in the tree
  today, the build that decided it having gone with the Decky zip. **There is no fallback and no failure mode left**:
  this used to be a `package.json` read that degraded to `decky-plugin/0.0.0` when the manifest was missing or
  malformed, and a constant cannot be missing — so a UA naming anything but this program is now a code change, never a
  deployment accident. `VERSION` is machine-stamped by release-please (`x-release-please-version` on its line) and never
  edited by hand. `adapters/renderer_gc.py` also speaks HTTP — to Steam's debugger on `localhost` — and takes none.
- **Large payloads**: two caps, and they fail differently — `host/dispatch.py` refuses an encoded answer over ~12 MiB as
  an ordinary error for that one call, while `host/connection.py` closes the socket on a frame over 16 MiB, which
  rejects every call in flight with it. So a bulk payload is chunked rather than sent: per-item callables, and bulk
  lists paged (the library apply emits shortcuts in batches; the metadata cache loads page-by-page). Those numbers are
  ours and were chosen — the reference library's largest cover is 5,869,834 bytes, which base64 turns into 7,826,448
  (7.46 MiB), so a 4 MiB cap would have refused it silently. The plugin loader's own 1 MiB bridge limit is **not** in
  this path any more; do not reason from it.
- **No `BIsModOrShortcut` bypass**: the bypass counter was removed deliberately. Shortcuts return `true` (natural
  state); we own the game detail UI. Do not reintroduce a bypass.
- **`instanceof` against a DOM global is false in QAM code**: plugin code runs in the **SharedJSContext** window while
  the QAM panel's nodes belong to the QAM view's own document — two realms, confirmed live (the two documents do not
  share a URL, and neither can see the other's elements). What is **measured** is the `instanceof`: such a test is false
  for **every** node such code will ever see, so a guard written that way rejects everything and the feature is simply
  inert. Whether a constructor that takes the node as an argument — `new ResizeObserver(...)`,
  `new MutationObserver(...)` — also misbehaves across realms is **not** established here in either direction; those are
  named because taking the constructor from the node costs a property read, so the question need not be answered. Take
  the constructor from the node — `el.ownerDocument.defaultView` — as `WidePage` and `ScrollRegion` do. **The frontend
  suite cannot see this**: happy-dom has one realm, so the wrong global and the right one are the same object and every
  test passes.
- **A vendored package's assumptions about the runtime it loads in are invisible to every check here**: nothing in this
  repo's toolchain runs Decky Loader's frozen Python, so vendoring or bumping anything under `_vendor/` is a device-test
  trigger, and what it risks is not confined to load — the plugin may fail to come up at all, or a question may reach
  the assumption later and do its damage then: the spawn shape the grant below answers took the whole Steam UI down at
  the first save question — [`.claude/rules/vendored-assets.md`](.claude/rules/vendored-assets.md). That assumption is
  answered by a **grant this repo makes and nothing enforces**: `adapters/atlas_host.py` hands the resolver an
  interpreter for its core probe, because frozen, `sys.executable` is the loader binary rather than a Python. Removing
  or forgetting the grant fails nothing — atlas probes no core, so every core it is asked about comes back unknown, and
  the one question this plugin puts that reaches the probe degrades: a **libretro** entry's save answer, which loses the
  core's recorded save behaviour (`core-generation-unestablished`, `core-unqueryable`) and then usually establishes
  nothing and names no file. Usually, not always — an answer that comes from a per-game override still names its files —
  and a standalone emulator's save answer never probes at all, so what is lost is a subset nothing counts. Green suite,
  green gate, quietly poorer answers: `bootstrap/adapters.py` logs which interpreter a probe would run under because
  that line is the only place the **cause** is named — the caveat itself reaches the debug log and the wire, but nothing
  in it separates "no interpreter" from "the core would not load".
- **An empty collection in a resolver answer is a statement about PROVENANCE, never about need** — and Python spells it
  the same as an absence, which is what makes this the trap it is. The verdict on a catalogue entry is
  `requirements_met`, three-valued: `True`, `False`, `None` for "could not be established". It narrows only and is never
  `True` out of ignorance — with `verify=False` an entry whose required files are **present** still answers `None`,
  because presence is not the question that field asks. What an empty `requirements` list means is decided by
  `declaration` and by nothing else: `read` and empty is the only pairing that means "this emulator needs no firmware";
  `packaged` and empty means a card exists and this query established nothing (a card may identify its image by
  **content**, so it names no file until the bytes are read); `unsupported` means the emulator is installed and the
  resolver has no source for what it wants. Reading a length where the verdict was asked produced a green "needs
  nothing" over a console that does not boot without an image, twice in one hour, and the second time with
  `requirements_met: None` already on screen. The same shape recurs across the answer: `system_firmware: None` is
  nothing recorded rather than nothing needed, and `core_so: None` is a standalone emulator's entry rather than no entry
  — which emulator that is comes from `emulator`, the identity field that stands on both kinds and on both the catalogue
  answer and the firmware one; `label` is presentation (one `pcsx2_libretro.so` under two of them) and a caveat's
  `token` is a third vocabulary again. **The answer is entry-shaped**, and `answer.requirements` is a flattening that
  has already discarded `declaration`, `requirements_met`, `caveats`, `unread` and `refused` — so an entry-level
  question answered from it is answered from evidence that was thrown away before the question was put. Related and
  separate: `description` is deliberately outside the resolver's contract (it is the packager's prose from a core's
  `.info`), so it is not a field to render as a row's headline. Nothing mechanical carries any of this; the vocabulary
  overlaps ours almost exactly (`satisfied`, `required`, `present`, `cores`, `description` all exist on both sides and
  name different types), which is what makes a wrong reading look like a correct one.

## Current State

Latest release and shipped features: see `git tag --sort=-v:refname` and GitHub Releases. Roadmap and open work:
[GitHub Projects board](https://github.com/users/danielcopper/projects/2).

## Development

- **Build**: `pnpm -C frontend build` (Rollup -> `dist/globals.js`, `dist/index.js`, `dist/index-coexistence.js`, and
  `@decky/ui`'s licence text beside them)
- **Tests**: backend — `python -m pytest tests/ -q` or `mise run test`; frontend — `mise run test:frontend` (Vitest +
  happy-dom)
- **Coverage**: backend — `python -m pytest tests/ -q --cov=backend --cov-report=term --cov-branch`; frontend —
  `mise run test:frontend:coverage`
- **Lint**: `mise run lint` (import-linter, the `scripts/check_*` gates, markdownlint). Ruff and basedpyright run only
  inside `mise run gate`.
- **Gate**: `mise run gate` (the full CI battery in one command — mirrors every PR check; slow. Run before pushing.)
- **Setup**: `mise run setup` (installs JS + Python dependencies)
- **Release**: release-please, configured as `release-type: simple` (`release-please-config.json`). What it proposes is
  normally computed from `.release-please-manifest.json` plus the commits since — but **not today**:
  `release-as: "1.0.0"` overrides that computation on every run, so every release PR proposes 1.0.0 until the key is
  removed. **Take it out once 1.0.0 has shipped**, or the version stops moving and nothing says so.

  Two files carry the version and each is written by a different mechanism: `version.txt` at the repository root is the
  `simple` strategy's own version file, and `backend/domain/identity.py`'s `VERSION` line is an extra file, found by the
  `x-release-please-version` marker on that line (the `generic` entry reaches only `identity.py`). Both are OUTPUTS of
  the release run and neither is edited by hand. `version.txt` ends with a newline because that is what release-please
  writes (`DefaultUpdater.updateContent` returns `this.version + '\n'`); stripping it makes the next release PR diff a
  line nobody touched.

  A third reader exists outside the repository: README's release badge is a shields `dynamic/json` badge over
  `.release-please-manifest.json`, because the JS manifest it used to read moved under `frontend/` and deliberately
  carries no version. The note lives here rather than beside the badge: the badge sits inside a centred HTML block, and
  `deno fmt` puts blank lines around an HTML comment, which would end that block and unalign the row.
- **Run it**: `mise run dev` (build the panel, then run the backend, which serves `dist/` and loads the panel into
  Steam). Needs `~/.steam/steam/.cef-enable-remote-debugging`. **There is no hot reload** — a rebuilt bundle reaches
  Steam when its JS context is rebuilt; `mise run dev:bpm-reset [display]` gives a fresh one. `mise run dev:bpm` opens
  windowed Big Picture on a display; `mise run dev:ui-scale` forces the Deck's metrics. Guide:
  `docs/contributing/frontend-dev-loop.md`
- **Tooling**: mise manages node, pnpm, python, uv; venv auto-creates at `.venv`. Python deps are pinned in two
  lock/source pairs — `requirements-dev.lock` at the root, for `backend/`, `tests/` and `scripts/`, and
  `docs/requirements.lock` beside the documentation it builds — each compiled from the `.txt` next to it by
  `uv pip compile`; regenerate with `mise run lock-update` after editing a source or bumping a pin.
- **Pre-commit hook** (`.githooks/pre-commit`): formats staged files — `ruff format` + `ruff check` (Python),
  `prettier --write` (TS/TSX), `deno fmt` (Markdown). Stays fast (<2s); heavy validation is CI-only. Do not re-introduce
  heavy checks here. It re-stages what it formatted, but **only for a file with no unstaged changes**: `git add` stages
  the whole worktree file, so re-adding one you staged in part with `git add -p` would commit the hunks you left out.
  Such a file is named on stdout and its index entry left alone, which means the commit can carry unformatted content —
  loudly, and CI says so, where the other direction was silent.

## Code Quality

CI runs SonarCloud (Quality Gate: 80% coverage on new code, 0 bugs, 0 vulnerabilities), Ruff, basedpyright,
import-linter, pytest-cov branch coverage, and the repo's `scripts/check_*` gates. **The per-rule checks and what each
one enforces are listed in the invariant register below** — that table is the single inventory; do not duplicate it
here.

## Invariant register — cross-cutting safety rules

The audit's clearest pattern: every rule with a mechanical check held; every rule that lived in prose or in a reviewer's
head drifted. This register is the single inventory of the cross-cutting safety rules — the ones that span files, so no
diff-scoped review (human or agent) sees the whole rule — plus the current enforcement tier of each. It is a **map of
the enforcement surface, not the enforcement itself**: a `check`/`test` rule is enforced by the named artifact; a
`prompt-only` rule is not yet mechanized and is injected here so review carries it verbatim until a check exists. The
moment a `prompt-only` rule gets a mechanical check it moves to the `check` tier — a rule is never weakened to stay
green, and a real drift is a finding to triage, never an exemption. `[ours]`

Format: **invariant** — tier — enforced by.

- **Callable failures use `{success, reason, message}` (never `error` / `error_code`)** — check —
  `scripts/check_failure_shape.py --check`
- **A definitive 404 is `not_found`, never `server_unreachable` — a catch-all `except Exception` in `services/` may not
  bind a verdict key (`reason` / `status` / `recommended_action`) to a hardcoded `SERVER_UNREACHABLE`; route the
  exception through `classify_error`, or peel the 404 off with a sibling `except RommNotFoundError` where the verdict is
  a partial-success flag** — check — `scripts/check_404_not_unreachable.py --check`
- **A 404 becomes `RommNotFoundError` only when RomM's entity layer is proven to have answered it; only the three
  byte-stream fetches opt out** — test + prompt-only — `TestNotFoundDiscrimination` plus the per-call-site
  `test_generic_route_404_still_raises_not_found` trio in `tests/adapters/romm/test_http.py`; a fourth byte-stream
  fetch's opt-out is prompt-only — `.claude/rules/romm-http.md`
- **Every RomM request goes out through `RommHttpAdapter._urlopen`, the single point that clears the known-unreachable
  state — a request method calling `urllib.request.urlopen` itself leaves every retry ladder degraded to one attempt
  until an unrelated path happens to succeed, and nothing else fails** — check — `scripts/check_urlopen_choke_point.py`
  (structural, AST call sites — an alias or a `getattr` would slip past it. Which requests may skip the ladder, and
  which pass `romm_origin=False` because they do not talk to RomM at all, stays prompt-only in
  `.claude/rules/romm-http.md`)
- **Frontend↔backend callable parity (names + arity)** — check — `scripts/check_callable_manifest.py`
- **Every backend `emit` event name has a frontend listener, and vice versa** — check — `scripts/check_event_parity.py`
- **`settings.json` is written only by its owner (`adapters/persistence.py`)** — check —
  `scripts/check_settings_owner.py`
- **Where this program's directories are is resolved once from the environment, and every consumer reads them off
  `AppDirectories`** — prompt-only — `domain/app_directories.py` is the ladder (`TENDER_*`, then XDG, then the built-in
  defaults) and it is pure: the environment is handed in, so every rung is checkable against a table. `Plugin.run`
  resolves it once and hands it to `bootstrap()`, which derives nothing, and `RuntimeBundle` carries no directory at all
  — it used to carry two, and that is how a question about a plugin loader's own layout came to sit beside a question
  about the user's data as two plain `str` fields on structs the composition root passes around. **Counting rule** (an
  AST walk for an attribute in `{config_dir, data_dir, cache_dir, state_dir, runtime_dir,
  code_dir}` whose base ends
  in `directories`): **20 reads over three modules**, `main.py` and `bootstrap/`'s two — `code_dir` 6, `data_dir` 6,
  `cache_dir` 4, `state_dir` 2, and one each for `config_dir` and `runtime_dir`. Re-derive it rather than trusting the
  number. **Two fields are read in `main.py` alone** and nowhere else: `state_dir`, which the logging setup opens and
  which the injection's crash record lives under, and `runtime_dir`, which the port file lives in. `config_dir` has
  exactly one reader, `PersistenceAdapter`. The pairing that matters is `cache_dir` against `data_dir` — covers, artwork
  and the SGDB artwork cache on the first because they are re-derivable from the server, the database and the launcher
  on the second because they are not; a system that clears caches must be able to clear one and not the other. The
  launcher's home is the read whose mix-up a user would see rather than the next start only, since
  `launcher_path(directories.data_dir)` is carried on as `ShortcutLauncher.path` and baked into every shortcut's `exe`.
  Nothing mechanical tells the six apart: they are six `str` fields on one frozen struct, so a read of the wrong one is
  a rename away and fails silently in whichever direction it happened to point
- **The identifier's three homes are never derived from one another — in particular `APP_DIR_NAME`
  (`domain/user_data_location.py`) is never read from `PACKAGE_NAME` (`domain/identity.py`)** — test + prompt-only — the
  three homes and the question each answers are enumerated in `backend/domain/identity.py`'s module docstring.
  `APP_DIR_NAME` and `PACKAGE_NAME` spell the same string today, so `APP_DIR_NAME = PACKAGE_NAME` reproduces every
  current path exactly and every value comparison stays green — the two are still equal after the fold, which is what
  makes it invisible; the cost arrives at the next package rename, which then moves every user's library on the
  following start with nothing failing and nothing said.
  `tests/domain/test_identity.py::TestTheIdentifierStaysInTwoPlaces` therefore asks the module what it ASSIGNS rather
  than what it resolves to: it parses `user_data_location.py` and fails unless `APP_DIR_NAME` is a string literal, which
  is the one answer that cannot be another constant's — a fold through a transform (`PACKAGE_NAME.lower()`) is a call
  node and fails too. The reverse fold, `PACKAGE_NAME = APP_DIR_NAME`, is caught by asserting `domain.identity` has no
  `APP_DIR_NAME` attribute, and **that half is the weaker one**: importing it under an alias evades it. The THIRD home
  is unchecked entirely — `SESSION_BREADCRUMB_KEY` is frontend TypeScript and no test on either side relates it to the
  other two. The rule is also stated at `APP_DIR_NAME` itself, because a diff that folds it opens neither the docstring
  nor this file
- **Sync run-lifecycle (`sync_state` / `current_sync_id`) written only via `LibrarySyncStateBox` verbs** — check —
  `scripts/check_sync_lifecycle_owner.py`
- **A library-sync seam is held only by the module owning the job it belongs to: `active_core` / `disc_resolver` by
  `services/library/shortcut_launch_resolver.py`, `renderer_rss` / `renderer_gc` by
  `services/library/session_budget.py`, and `artwork` by `services/library/cover_preparer.py` (the apply path's covers)
  **and** `services/library/reporter.py` (commit-time cover-path finalisation) — the one confinement with two owners,
  because those are two different questions a unit asks at two different points; two owners is a named pair, not a
  licence for a third, and the orchestrator that used to be the third holds no `ArtworkManager` at all. The `service.py`
  façade is not a co-owner — it may **pass** a seam on and may not **use** one, which the check reads structurally: a
  seam attribute standing as a call's keyword-argument value, and a seam annotation on a field of
  `LibraryServiceConfig`, are wiring; anything else in the façade is a finding** — check — `scripts/check_seam_owner.py`
  (AST over attributes named after a seam and over annotations naming its Protocol, resolved per seam rather than per
  module — owning one grants nothing about another. It sees the injection, not every later use: a seam aliased to a
  differently-named attribute or local, one reached through `getattr`, one passed positionally into a helper that holds
  it, a quoted string annotation, and a Protocol imported under an alias all slip past it — the last two only on the
  annotation half, because the constructor read that unpacks the config is flagged whatever the field is called). What
  the confinement buys is that each module's transactions stay separable: `shortcut_launch_resolver` holds a read UoW
  across its install-path scan while the `active_core` seam opens its own `BEGIN IMMEDIATE` per ROM, so folding the two
  into one pass deadlocks — on a real device only, since `FakeUnitOfWork` shares no connection. **Nothing mechanical
  stands behind that half.** `check_uow_seam_nesting.py` catches the fold only in its inline form (the seam method named
  inside the `with` block); the peer-call form — `do_build_core_overrides` invoked from inside the install-path readers'
  own UoW, which is what putting the three methods on one class makes cheapest — is that gate's documented blind spot
  and passes green. On the budget side the confinement holds `session_budget`'s stated promise that no renderer-RSS
  reading is taken anywhere else in the package
- **A module declared read-only calls no repository write — `services/library/local_library_reader.py` to start** —
  check — `scripts/check_read_only_module.py` (AST over the declared file's own calls, matching the two-attribute
  `<...>.<repo>.<method>` shape against the eleven repositories the UoW exposes). Read or write is decided **by the
  name's shape** — `get` / `get_*` / `iter_*` / `count` plus the explicitly listed reads — and
  `tests/scripts/test_check_read_only_module.py` re-derives every method name from `services/protocols/repositories.py`
  and pins its classification, so a new repository method fails there until it is classified. **The two directions are
  not symmetric.** A read named outside the shapes is called a write: loud, safe. A **write named like a read is called
  a read and passes in silence** — `uow.roms.get_or_create(...)` is green today, which is the very accident class this
  entry is otherwise about, so "repository writes are named as writes" stays a prose rule the gate does not carry. It
  also sees only calls the file itself makes, and only as calls: a write behind a helper it calls, a write **passed as a
  bound method** (`run_in_executor(None, uow.roms.save, rom)` — an attribute, not a call, and the exact idiom this
  module's own reads are invoked through), an aliased handle (`repo = uow.roms`), a `getattr`-reached repository, and a
  repository name not in its list all pass green. What it does catch is the plain `uow.roms.save(...)` dropped into a
  read module because a UoW was already open there. The declaration earns its keep by making the boundary checkable
  rather than descriptive: these reads open their own short UoW and are offloaded to an executor at points chosen for
  cheapness, so a write among them would land at a moment nobody picked. The platform stamp's DELETE stayed in
  `sync_orchestrator.py` on those grounds — it is a write, so a read-only module cannot hold it, and it is cohesive with
  the half of the apply pipeline that performs it: the DELETE stayed with `_sync_one_unit`, which builds a unit's delta,
  while the re-stamp went to `ChunkDispatcher._build_final_platform_stamp` with the final chunk whose commit UoW it
  rides, so the stamp's two ends now sit in two modules and each names the other. **Why the DELETE sits exactly where it
  sits in that pipeline is a property of its call site, not of its module** — after the fetch, after the artwork, after
  the cancel guard, before the first chunk (ADR-0023 / #1025) — and that argument lives in full at the call site's own
  comment, which is the only place a move could not have carried it away from
- **An emitted `sync_progress` frame stops a run (`running: False`) only with a terminal stage, and a terminal stage is
  only ever emitted with the run stopped** — test — `tests/services/library/test_terminal_frame_contract.py`
  (structural, AST call sites and dict literals across all of `backend/services` — it covers the error paths a
  behavioural test would have to provoke one at a time, but a frame assembled by a helper it cannot follow, or emitted
  through an aliased callable, slips past it; its own scope tests pin the producers and the root it reaches, so a
  narrowing fails rather than shrinking the rule in silence. Frame producers are not confined to `services/library/`:
  `services/artwork.py` emits through an injected `emit_progress`). The QAM panel derives "a run is in flight" from
  `running` and keys the run's end — the status line, the live-ETA teardown, Main's stats re-read and the Sync page's
  three — on the stage, so a stopping frame with a non-terminal stage would collapse the in-progress rows while ending
  nothing. The panel cannot defend against it: a bare `running: false` is exactly what the Sync page's own retraction of
  an optimistic start looks like. Since #1814 the frontend's frame store reads the same discrimination for a rule of its
  own — a run whose stopping frame carried a terminal stage AND a run id can never be put back in flight, which is what
  stops the apply loop's next item from resurrecting a run that has already ended — so a stopping frame emitted without
  a terminal stage would record no ending there either, and the freeze that rule removes comes back
- **The KIND of run a `sync_progress` frame belongs to is stated on it (`runKind`), never inferred from it — and a frame
  that states none is rendered as neither of the two answers** — test + prompt-only — the backend half is pinned end to
  end by `tests/services/library/test_sync_orchestrator.py::TestRunKindOnTheWire` (every frame of a preview run and of
  an apply run, both terminal frames, and the `get_sync_status` snapshot) and
  `tests/services/library/test_state.py::TestRunKind` (claimed with the run slot, cleared with it); the frontend half by
  `frontend/src/utils/syncRunView.test.ts` and the slot's three labels in `frontend/src/bigpicture/MainPage.test.tsx`.
  **Nothing joins the eleven sites it passes through**, counted one per site at the granularity this list names them:
  `LibrarySyncStateBox` holds it with the slot, three separate backend frame builders carry it (`emit_progress`,
  `_finish_sync`'s CANCELLED terminal, and the per-unit ERROR dict literal in `sync_orchestrator.py`), three frontend
  start paths stamp it themselves on the optimistic frame they show before the first real one arrives (`useSyncPage`'s
  `computePreview` as `preview`, its `applyPreview` and `startRunDirectly` as `apply`), `SyncProgress.runKind` and
  `useSyncRunView` pass it through, and `MainPage` both seeds it from the `get_sync_status` snapshot onto the store at
  mount and maps it to the slot's label. **Nothing mechanical stands behind the seam between them**: a fourth frame
  builder that omits the key, a fourth start path that stamps the kind it is not, or a reader that spends the absent
  case on one of the two answers — a `runKind ?? "preview"`, a `=== "preview"` where the neutral branch was — goes
  green, because each test above pins one half and none of them pins the join. The failure is silent and worst exactly
  where the frontend cannot help itself: after a plugin reload mid-run the store starts empty, the snapshot is the only
  thing that can say what the run is doing, and Main then tells the reader a real apply run is merely checking for
  changes. Why the kind cannot be derived at all is stated at `domain/sync_run_kind.py` and in
  `docs/architecture/qam-panel.md`'s Main section; do not restate it here
- **A press that starts a run clears the previous run's per-unit rows — unless that press is a RESUME, the one start
  they are still true for** — test + prompt-only — `frontend/src/bigpicture/SyncPage.test.tsx`'s "a previous run's rows
  at the next press" pins all three start paths in both directions, and `frontend/src/utils/runUnitsStore.test.ts` pins
  the clear itself. **The rule spans three modules and nothing joins them.** `utils/runUnitsStore.ts` holds the rows and
  offers `clearRunUnits`; `useSyncPage` decides, at each of the three presses that write an optimistic frame
  (`computePreview`, `applyPreview`, `startRunDirectly` — the same three the entry above names); and `index.tsx`'s
  `sync_plan` listener is the only OTHER thing that ever replaces the rows, which is what makes the press the moment
  that matters. The plan arrives after `build_work_queue()` on the two apply paths and never at all on the preview path,
  so a fourth start path that forgets the clear leaves the previous run's `done` rows — with its apply results, and with
  the unit it died in dressed as running by this run's frames — standing over the new run for the length of a work-queue
  build, or for the whole of it. The store's own guards cannot help: they REFUSE a foreign frame, and refusing is not
  clearing. The discriminator can be nothing but the frontend's `syncResumeState(stats).canResume` at the press, because
  a resume is a new run with a new id and the backend has no resume concept at all — no frame, kind or id tells the two
  apart. Both directions fail in silence: forget the clear and another run's rows read as this run's progress, clear on
  a resume and the one start whose rows are true loses them
- **A firmware answer nothing could establish is `unknown`, never `not_needed` — and the distinction survives every
  layer it crosses** — test + prompt-only — `tests/adapters/test_atlas_firmware.py` pins the adapter's degradation (a
  raising resolver, a missing installation, an answer with no root all come back with `resolved` clear, never as an
  empty catalogue reading "nothing needed"), `tests/domain/test_firmware_wants.py` pins the classification, and
  `tests/services/test_firmware.py::TestCheckPlatformBiosUnknown` pins the same listing answering `not_needed` under a
  whole reading and `unknown` under a partial one. The rule spans four modules and no diff-scoped review sees it whole:
  the adapter decides whether the reading happened, `domain/firmware_wants.py` holds the two values apart,
  `services/firmware/status.py` scopes the doubt to the emulator the platform launches with, and both frontend surfaces
  render them as different sentences. **Nothing mechanical stands behind the scoping half.** A future caller that folds
  the two values back together — a truthiness test on a placement, a `wanted != "needed"` bucket, a default of
  `not_needed` where the catalogue is silent — goes green: the collapse is the upstream defect this swap removed, and it
  is one careless `or` away from returning. The scope is the second half, and since #1821 it is ONE emulator rather than
  a platform's whole list: `reading_complete_for` takes the launching emulator's identity and refuses `None` — an
  unresolved pick, or one the resolver could not identify. Read as complete, a `None` is a finished reading of nobody:
  every server file classifies `not_needed`, `required_count` is 0, and the platform reports a green "Nothing required"
  over firmware the emulator will not boot without. **An unread emulator the platform also offers no longer withholds
  the answer**, which is deliberate — it says nothing about a launch that does not use it — and the cost is that
  switching a platform's emulator can move it from a finished answer to a withheld one. `declaration="packaged"` with an
  EMPTY requirement list counts as unread and is the shape most likely to be folded back the wrong way: a card may
  identify its image by content, so it names no file until the bytes are read, and reading the empty list as "wants
  nothing" puts a green all-clear on a PlayStation launching DuckStation
- **A firmware row the RomM library does not hold (`on_server: False`) counts towards readiness, and never towards a
  download affordance or a progress ratio** — test + prompt-only — `tests/services/test_firmware.py` pins the row's
  shape (`id` absent, `on_server` clear), that it raises `required_count`, and that it stays out of `server_count`;
  `frontend/src/bigpicture/library/PlatformsTab.test.tsx` pins that the buttons key off the fetchable set. **The three
  axes live in three places and nothing joins them.** `domain/bios_status.py::count_required` is readiness and counts
  every required row; `services/firmware/status.py::_bios_aggregates` scopes `server_count` / `local_count` to
  `on_server` rows; the download buttons' condition is `isFetchable` (`frontend/src/utils/biosFetchable.ts`), called
  from `frontend/src/bigpicture/library/PlatformDetail.tsx` — and since #1815 the per-row Download button reads the same
  filtered set, so a fourth reader of the axis now exists in that one file. A fifth reads it in the same file for the
  On-disk cell's second mark (`⊘`), and that one is display alone: it neither counts nor gates, which is what keeps it
  out of all three folds below. **A sixth reader is the game page's BIOS tab** (`BiosTab.tsx`'s `rowBelongsOnThisPage`),
  and it is display alone in the same sense: it decides whether a row gets a LINE — a file no page can fetch, that this
  launch does not require and that is not there, is nothing that page can act on — and gates no count and offers no
  action, so it belongs to none of the three folds below either. What holds the two surfaces together is that they call
  one predicate rather than spelling the three clauses twice: a second copy would let the game page point at a download
  button the platform page does not offer, or leave off a row it does. What must NOT be shared is the game page's rule
  around it — required for this launch, the console's own image, present, fetchable, or unjudged — which is that page's
  alone; the platform detail has no such rule, and a shared "visibility" module would invent a notion only one surface
  has. Each fold has its own quiet failure: drop the row from readiness and a platform reads ready while a required file
  is absent; add it to the ratio and a SNES page reports `0 / 26 files, 26 missing` for twenty-six optional files no
  core wants; add it to the buttons and the page offers a download that cannot succeed. `on_server` is the one field all
  three read; the row's `id: None` is an honest absence with no consumer at all, so nothing breaks if it is filled in
  and nothing is guarded by leaving it empty
- **No BIOS answer outlives the page that asked for it** — test + prompt-only —
  `tests/services/test_game_detail.py::TestGetCachedGameDetailCarriesNoBiosAnswer` and the two contract cases in
  `tests/contract/test_game_detail_read.py`. `get_cached_game_detail` carries none and says so (`bios_status_unknown`,
  plus an unconditional `bios` stale field); the live `get_bios_status` fills it in. What holds the rule is an absence —
  `BiosChecker` has one method, so there is no cheap cached twin to reach for — and an absence is exactly what a future
  change restores without noticing. Re-adding a stored answer would look like a performance win and would put a previous
  page open's requirement on this page
- **The plugin attaches a configured custom header to a RomM-origin request and to no other request it issues, and never
  over a header the adapter sets itself** — test + prompt-only — `tests/domain/test_custom_headers.py` pins the
  validation in both directions (every reserved name case-insensitively, a CRLF in a value, and what the persisted
  reading skips), and `tests/adapters/romm/test_http.py::TestCustomProxyHeaders` pins the three attachment points, the
  one exclusion, and that a hand-planted `Authorization` / `Host` still loses. **Nothing joins them.** The rule spans
  `domain/custom_headers.py`, the transport's `_apply_origin_headers` and its three callers — `_apply_default_headers`
  (every authenticated route), `unauthenticated_post_json` (the pairing-code exchange) and `basic_auth_request` (the
  token mint) — plus the one place that must NOT call it, `download_external`. Both directions fail in silence and each
  one is worse than it looks. A fourth request method that forgets the helper works perfectly for the user who has no
  proxy and 403s for the user who has one, on that path only. Adding it to `download_external` hands the user's proxy
  credential to a third-party metadata CDN, which no test would notice because the fetch still succeeds. **What the
  plugin issues is the whole of the claim**: `_urlopen` uses the default opener, so urllib's redirect handler follows a
  30x by copying every header but `content-length` / `content-type` onto the next request with no same-origin test —
  measured, not read: a cross-host 302 delivers both the configured header and the RomM bearer to the foreign host. That
  is the transport's behaviour and predates this rule (the bearer always travelled it), which is why the invariant is
  worded about attachment rather than about arrival; #1889 holds the gap. The reserved set is held by **two**
  mechanisms, not three, and they are not equally strong. One is validation — `_name_refusal` against `RESERVED_NAMES`,
  reached from both `resolve_custom_headers` (the wire) and `stored_custom_headers` (every request, so a hand-edited
  `settings.json` cannot route around it). Those are two call sites of ONE frozenset: drop a name from it and both gates
  open in a single edit. The other is attachment ORDER, and it covers only a name the adapter itself re-adds after
  `_apply_origin_headers` — `User-Agent`, `Authorization`, `Content-Type`, and conditionally `Range` / `If-None-Match` /
  `If-Modified-Since`. It covers `Host` and `Content-Length` **not at all**, because the adapter sets neither:
  `http.client._send_request` suppresses its own derived `Host` when the caller supplied one, so a configured `Host`
  retargets every request's virtual host — and for that name, the one this list singles out as dangerous, the frozenset
  is the only defence there is. Nothing pins the ordering leg either: the two tests that look like they do
  (`..._never_displaces_the_bearer`, `..._never_retargets_the_request`) pass because `stored_custom_headers` drops the
  entry long before `add_header` is reached, so both stay green if the ordering is reversed. Detail:
  `docs/architecture/backend-architecture.md` → "the headers every RomM-origin request carries"
- **`dist/globals.js` is never evaluated into Steam where Decky Loader is serving, and what decides that is read from
  the MACHINE rather than from the window** — test + prompt-only — `tests/host/inject/test_bundles.py` pins both choices
  in both directions (the globals bundle absent beside a serving loader, and the standalone panel absent too, since it
  carries the same sweep), and `tests/host/inject/test_injector.py::TestBesideDeckyLoader` pins it end to end over a
  real socket with something answering on the loader's port. It is the only crash cause ever observed here: that bundle
  carries `@decky/ui`'s module sweep at import scope, and re-running it under an interface already rendering from those
  modules takes the Big Picture window down. **The rule spans three modules and nothing joins them.**
  `host/inject/machine.py` asks whether Decky Loader's server answers, `host/inject/bundles.py` turns that into a file
  list, and `host/inject/injector.py` evaluates it. A fourth caller building its own file list, or one reading the
  window instead, goes green: at the moment the question has to be answered every Decky marker on the window — `DFL`,
  `DeckyPluginLoader`, `DeckyBackend`, `deckyAuthToken`, `deckyHasLoaded` — is still `undefined`, so a window probe
  answers "no Decky" on a machine that has one, picks the globals, and crashes the interface. The gap between "the
  loader's server answers" and "Decky is rendering" is deliberately left on the safe side, and the ordering measurement
  (Tender loading first is safe) may not be leant on to close it the other way: a reconnect puts the same question at a
  moment when Steam has been up for an hour. Detail: `docs/architecture/loading-the-panel.md`
- **An injection that could not be observed is never counted as a crash, and this process answers for its own record
  before it reads one** — test + prompt-only — `tests/host/inject/test_watchdog.py` pins the state machine in every
  direction and which of the three answers settles an armed record;
  `tests/host/inject/test_injector.py::TestDidTheInterfaceSurviveIt` pins every way a record is answered for over the
  real loop — the survival, the collapse that leaves it open, and each way an attempt is closed without being counted.
  Count those by their call: a test in that class that ends with the record `open: false` and `failures: 0` is one of
  them. The crash cannot be counted inside a session — it leaves `SharedJSContext` alive with our marker on it, so the
  injector sees "already injected" and never tries again — so what is counted is a record left open at the NEXT attempt,
  and two in a row stop the injection. **Three things have to hold together and nothing checks that they do.** `judge`
  writes its resolution back before it answers, or one open record counts once per attempt for ever. **The ORDER is the
  second**: an injection answers for the record this process already holds before `judge` reads one, because a
  JS-context rebuild inside the alive window starts the next injection while the last record is still open and
  unanswered — ordinary during start-up settle, and what a Steam restart produces — and read the other way round it is a
  crash that never happened, twice over on a machine where nothing was wrong. A real crash is unaffected: the check that
  saw it marks its reading taken (`stays_open`) and the record stays open for the next `judge` to find. The third is
  that the alive check closes the record without counting whenever nothing was established — the debugger stopped
  answering, the backend is shutting down, nothing but the renderer was open when the panel was loaded, or a second
  injection began before the check that answers for the first could run — because the signature is specific (every other
  page target goes at once while the debugger keeps answering), and a run in which that could not be observed says
  nothing. A close that counted any of those would stop the panel loading over a user closing Steam, and the failure is
  silent in both directions: too lenient and a crash loop is never stopped, too strict and the panel disappears with
  only a log line to say why. The way back is not inside Steam (the interface is what is gone): the fingerprint —
  Tender's version, the bundle bytes, Steam's client build — drops the count on its own, and `TENDER_INJECT=force` is
  the switch the refusal line names

- **Aggregate state mutated only via verb-named methods (no field assignment)** — check —
  `scripts/check_aggregate_field_assignment.py`
- **No UoW-opening seam (ActiveCoreResolver, RelaunchOptionsResolver, uow_factory) is called while a UoW is open on the
  same path** — check — `scripts/check_uow_seam_nesting.py` (the first of the **two** rules that script carries, over
  one shared matcher; the file-I/O rule below is a different hazard with its own seam list and its own failure message,
  and neither entry is evidence about the other)
- **No file-I/O seam is called while a UoW is open — a Unit of Work wraps database reads and writes, never file or
  server I/O (CONTEXT.md → Unit of Work, ADR-0006)** — check — `scripts/check_uow_seam_nesting.py`, second seam family
  (`IO_SEAM_METHODS`). The list is **the seams this checker can see and has been told about, never an inventory of the
  I/O seams that exist**: `DiscResolver.enumerate_discs` / `.resolve_for_install` (a recursive walk of the ROM's install
  directory), the three `CoreInfoProvider` reads — `get_active_core`, `get_default_emulator`, `get_emulator_options` —
  which are answered by the vendored resolver's live read of ES-DE's catalogue (a system's first read opens it and the
  adapter's per-system cache is what a second one hits; `get_emulator_options` additionally globs each **bakeable
  standalone** option's emulator install through the find rules on **every** call, uncached so a component installed
  mid-session is seen), `SandboxLauncherFn` (re-probes the flatpak roots for `es_find_rules.xml` and re-stats it before
  it may use the parse cache), `SystemResolver` (parses the plugin's **own** bundled `config.json`, not RetroDECK's
  `retrodeck.json`, and does no network work despite living on the RomM HTTP adapter), `SystemSupportedExtensionsFn` /
  `SystemKnownFn` (two more questions to the same catalogue, through the same adapter cache),
  `SteamConfigStore.read_shortcut_exes` (parses Steam's whole `shortcuts.vdf` — 315 KB and 828 entries on the reference
  machine — for the one-time shortcut relocation. **Listing it changes nothing at its only call site**: the service
  reaches it through `run_in_executor` as a bound method, which is this checker's documented blind spot, so the entry is
  a statement of the rule rather than an enforcement of it. It is also not the store's only real I/O — `grid_dir()` is
  called from `services/artwork.py` (six sites), `services/shortcut_removal.py` and `services/library/reporter.py`, and
  `check_retroarch_input_driver()` from `services/settings.py` — those are unlisted, and their being unlisted is a gap,
  not a judgement), `FirmwarePlatformResolver` (reads what one system's emulators want WITH content verification: it
  opens each candidate in a declared folder and reads it the way the emulator does — 64-318 ms per system on the
  reference machine) and its whole-machine sibling `FirmwareResolver`, the save answer — `resolve_save_answer` and the
  saves package's own `save_answer` wrapper, 170 ms warm and 490 ms cold per ROM, which makes it the most expensive
  entry in the list — the two path resolvers — `MigrationFileStore.realpath` (one walk per stored RetroDECK-home marker,
  a directory that may sit on the SD card the marker is pending a migration away from) and `ResolvedPathFn` (the same
  walk, but on **both** sides of a comparison, so a call site costs what the rows it checks cost, not what it checks
  them against) — and the `RetroDeckPaths` getters that answer with a root: `bios_path`, `roms_path`, `saves_path`,
  `states_path` and `retrodeck_home`, five of the Protocol's six path getters, each resolving on every call. The sixth,
  `config_path`, stays out because it resolves nothing — it is `os.path.join` over the user home, so calling it costs no
  I/O. Those two timings are the only entries a cost was measured for; every other one is listed from reading its
  implementation. One other real I/O seam was weighed and kept out — the reason is in the script's docstring, and it is
  not an exemption; nor is it an inventory of what else touches the disk. **"It's only a read" is the reasoning this
  rule exists to refuse**: `SqliteUnitOfWork.__enter__` issues `BEGIN IMMEDIATE`, so even a read-only UoW takes the
  write lock. The database is in WAL, so readers are unaffected — but every other **writer** waits on the lock for up to
  `busy_timeout=5000` and fails with `SQLITE_BUSY` if it is still held then, and `FakeUnitOfWork` shares no connection,
  so no unit test notices. Six call sites had drifted across the rule before anything looked (#1779), for the reason the
  check exists: nothing at a call site reveals that an injected seam touches the disk. **The rule and the gate come from
  reading code — no measurement of how long any of those transactions actually held the lock exists, and nothing here
  should be read as one.** What the check sees is the deadlock rule's matcher unchanged — an **attribute** call naming a
  listed seam, lexically inside a `with <...>uow_factory()` block in the same function scope — so it inherits every
  blind spot of that half: a seam behind a helper one level down, an alias to a local, a factory attribute whose name
  does not end in `uow_factory`, a nested `def`/`lambda` (which resets the scope by design), a seam **passed as a bound
  method** (`run_in_executor(None, self._disc_resolver.enumerate_discs, install)` — an attribute, not a call, and
  `run_in_executor` is exactly how `disc.py` and `cores.py` reach their `_io` bodies; the same shape
  `check_read_only_module.py` records for its own gate), and the hand-maintained list itself, which cannot notice a seam
  whose implementation _grows_ a file read later. Matching only attribute calls is deliberate: the pure
  `domain.disc_selection.enumerate_discs` shares a name with the seam and does no I/O — it is safe because its call site
  imports it bare, not because of the name. The call-shaped blind spot is shared with the deadlock rule and only this
  family closes it: for each `__call__`-only seam the list carries the attribute it is bound to — by convention rather
  than by construction, and only while such a name means one thing, which is exactly what keeps `_list_files` out.
  **Count them by their leading underscore**, which is what marks an entry as a holding attribute rather than a method
  name: six today (`_resolve_system`, `_sandbox_launcher`, `_system_extensions`, `_system_known`,
  `_firmware_folder_verdicts`, `_resolve_path`), and the number is re-derivable from `IO_SEAM_METHODS` rather than
  remembered. Two of those six are listed a second time under their implementation's own method name, for a peer holding
  the object rather than the bound method — `RommHttpAdapter.resolve_system` beside `_resolve_system`, and
  `EsFindRulesAdapter.resolve_sandbox_launcher` beside `_sandbox_launcher`. The first pair happens to be the attribute
  minus its underscore and the second plainly is not, which is the point: a twin exists where the implementation has a
  method name a peer could write, and it is read off the implementation rather than derived from the attribute. The
  other four have no such twin. The deadlock rule's own call-shaped seams stay open. `SystemResolver` is the odd one out
  for a second reason: the adapter memoises its map for the life of the process, so exactly one call ever opens the
  file, and the entry earns its place because that one call can land inside a UoW. One `# pragma: no uow-check` covers
  both families — it suppresses the line, and no seam is in both lists, so where a line does name two seams it silences
  both
- **Services never call clocks / sleep / uuid / random directly (inject the Protocol)** — check —
  `scripts/check_cosmic_call_bans.sh`
- **No module in `services/`, `bootstrap/`, `adapters/`, `domain/`, `lib/` or `models/` crosses the ~1000-LOC
  decomposition threshold, and the ones already over it may not grow** — check — `scripts/check_module_size.py` (the
  modules that predate the gate are grandfathered at their exact size. A ceiling goes up only for a change that adds no
  code — a rename, a reformat — and only with the reason recorded at its `ALLOWLIST` entry; a raise taken silently has
  retired the gate. Entries only ever come out, when the module drops back under the threshold. `main.py`, `_vendor/`,
  `tests/`, `scripts/` and `frontend/src/` are out of scope, each for a reason recorded at `SCOPE_DIRS`)
- **Service-independence contract list stays complete** — check — `scripts/check_service_independence_contract.py`
- **Layer import direction (services ↛ adapters, adapters ↛ services, …)** — check — `.importlinter` (`lint-imports`)
- **Frontend direction: `frontend/src/utils/` and `frontend/src/api/` never import either surface
  (`frontend/src/bigpicture/`, `frontend/src/desktop/`); the two surfaces never import each other; and no
  `frontend/src/` module takes part in an import cycle** — check — `frontend/eslint.config.js`
  (`import-x/no-restricted-paths`, `import-x/no-cycle`). The surface pair is a peer rule, not a layer rule: the two
  share data and logic and almost nothing visual, so anything that turns out to belong to both moves DOWN into `api/`,
  `utils/` or `types/`, never sideways. These rules go inert rather than loud when misconfigured: `import-x/extensions`
  ships as `['.js']`, so until it names `.ts`/`.tsx` the plugin resolves an import but never opens the target to read
  its imports, and `no-cycle` reports nothing on any codebase. `frontend/src/eslintBoundaries.test.ts` lints known-bad
  fixtures through the real config and fails if any of the seven stops reporting — a green `pnpm lint` alone proves
  nothing. Type-only imports are not edges (erased at runtime), which is why the `api/backend.ts` ⇄
  `utils/cachedGameDetailStore.ts` back-reference is not a cycle
- **No bare `# type: ignore` / blanket suppressions** — check — `scripts/check_no_bare_ignores.sh`
- **A transport failure and a callable's own failure never arrive in the same shape, on either end** — test +
  prompt-only — `backend/host/protocol.py` states the vocabulary and `.claude/rules/host.md` holds the backend half; the
  frontend half is `frontend/src/api/hostSocket.ts`, which THROWS `HostTransportError` for an `error` message and
  resolves only a `reply`, so a transport reason cannot reach a reader of `{success, reason, message}`.
  `hostSocket.test.ts` pins both directions. **Nothing joins the two ends**: `connection_lost` is the one reason no
  backend ever sends — the caller's own register answers with it — and it is spelled once in Python and once in
  TypeScript with no check that the two agree. A frontend that spelled it differently would go green, and the divergence
  would surface only to whoever eventually matched on it
- **The standalone panel bundle carries `@decky/ui` and the coexistence one carries none of it** — check —
  `frontend/scripts/check-bundle-shape.mjs`, over the built artifact rather than a bundler setting (nine strings that
  exist only in the package's implementation, plus the `DFL.` read count, in both directions; the licence file the
  standalone build owes and each bundle's own build stamp are asserted there too). Both failures are silent in CI and
  land on a device: a standalone bundle that lost the package throws on its first `DFL.` read where no `DFL` exists, and
  a coexistence bundle that gained it re-executes the modules a rendering Decky is rendering FROM, and takes the Big
  Picture window down. **The check sees the artefacts and not the decision**: which of the two the injector loads is
  `backend/host/inject/bundles.py`'s, and nothing here would notice the wrong one being served
- **Tender's three React globals are spelled exactly the way Decky Loader spells them** — test —
  `frontend/src/boot/steamGlobals.test.ts`, which reads `steamGlobals.ts` and the pinned `decky-globals-block.txt` as
  TEXT and compares the four search predicates, which global each answer is assigned to, and the JSX stand-in's keys and
  aliasing. The cost of a difference lands on DECKY's users, not ours: its loader skips its entire globals block when
  `SP_REACT` is already set, so when ours runs first, Decky's whole frontend renders through our shape. **The pinned
  copy is the half nothing can check** — it is upstream's file, held still by hand, so a refresh that is wrong reads as
  agreement; the provenance header names the commit it was taken at so the question can be re-asked rather than trusted
- **Every value the panel imports from `@decky/ui` is classified by the start-up check** — test —
  `frontend/src/boot/steamModules.test.ts`, which sweeps every non-test module under `frontend/src/` and fails on a name
  that is in none of the three lists (a search it asks, a name it cannot answer for, the package's own code). The swept
  set is derived rather than listed, because a file missing from such a list carries no lock at all. **What it cannot
  see is whether a classification is TRUE**: three names sit in the unverifiable list because they are wrappers the
  package always defines, and moving a real search there to quieten the check would pass green and leave the panel
  rendering a hole where the check reported everything resolved
- **Whether every search answered and whether the panel may MOUNT are two questions, and a miss that costs less than the
  panel never takes the interface off the air** — check + test + prompt-only — the type carries the first half:
  `SteamLookup.absenceCost` is required, so a new entry does not compile until it states which of the three its absence
  costs — the `panel`, only its `appearance` (`ControllerGlyph`, whose only consumer `layout/WidePage.tsx` already draws
  `‹ Back` in its place), or only a `diagnostic` (`playSectionClasses`, read nowhere but `gameDetailPatch.tsx`'s
  one-shot `dumpTree`, which already prints `UNDEFINED` in its place) — and there is no default to arrive in.
  `frontend/src/index.test.tsx` pins both factory branches — the panel mounts with everything registered, and the miss
  reaches the log. **Blocking is the status quo and staying there costs no evidence: nothing here is a claim that every
  other name was judged**, only that moving one OUT needs its every consumer read, one name at a time. **The join is
  prompt-only and spans three places**: `checkSteamModules` derives `panelMayMount` from the costs, `index.tsx` gates
  the fallback page on it and logs `describeSurvivedMiss` on the other side, and that sentence answers whose COPY of
  `@decky/ui` ran the missed searches rather than naming a repair of its own — it used to say "a newer Tender"
  unconditionally, which held only while nothing reaching it was a name the package exports, and `playSectionClasses` is
  one. What `frontend/src/boot/steamModules.test.ts` locks is the property the line's remaining own answer rests on — a
  non-blocking name `@decky/ui` does NOT export must be one Tender probes for itself (`findModule`, swept from the
  source) — so the three `SP_*` globals, which the frontend cannot attribute to a program from inside the page, fail
  there the moment one is made non-blocking, instead of shipping a repair aimed at whichever program did not install
  them. Both directions fail quietly: call a real dependency cosmetic and the panel mounts and renders a hole, which is
  the fault the whole check exists to tell apart from a backend that is not running; call a decoration blocking and one
  missing glyph costs the user their entire interface, which is what this entry removed
- **The start-up failure page names the copy of `@decky/ui` that actually ran the search that missed, and the repair
  that follows from it** — check + test + prompt-only — the artefact's stamp is checked
  (`frontend/scripts/check-bundle-shape.mjs`, per bundle and on `globals.js`, which must carry none), and the sentence
  behind every verdict in `SEARCH_OWNERS` is pinned in `frontend/src/boot/steamModules.test.ts` and
  `StartupFailurePanel.test.tsx` with both bundle values exercised — the test iterates that list rather than a count, so
  a verdict added without a sentence on each surface fails instead of going unworded. **The join is prompt-only and
  spans four places**: `rollup.config.js` serves the stamp, `boot/searchingCopy.ts` reads it and Decky's namespace,
  `boot/steamModules.ts` words it, and `index.tsx` resolves it ONCE for the log line and the page — two resolutions
  could disagree with each other. The predicates belong to `@decky/ui` and the coexistence bundle runs DECKY's copy, so
  a page that blamed Tender in both would send a user after the wrong program while Decky's own interface and its other
  plugins broke beside it. **A miss confined to the four names `@decky/ui` does not export names NO copy and offers NO
  repair** — `SP_REACTDOM` is the only one that reaches that state alone, `ControllerGlyph` only ever beside a global
  (on its own it is cosmetic and brings no page up at all, per the entry above), and `describeFailure` answers it before
  it asks whose copy ran anything. Naming a copy would blame Decky for a predicate of ours; the silence about a repair
  is right for the three globals and a real loss for the glyph, and only the second half of that is easy to forget. For
  the globals no repair follows: who installed them on a machine running both now HAS an answer — the injector loads
  `globals.js` only where Decky Loader is not serving, so beside a serving Decky they are Decky's — and **this branch
  does not read it**, because it keys on whose COPY ran the search rather than on which program installed a global. In
  the standalone bundle the answer would not settle it anyway: a missing `SP_REACTDOM` there is `globals.js` not having
  run OR our own ReactDOM predicate in `boot/steamGlobals.ts` having gone stale — two repairs behind one symptom.
  `ControllerGlyph` is reached by a `findModule` predicate of ours in BOTH bundles, so a newer Tender IS its repair and
  this branch cannot say so; restoring it here would take a third axis (whose PREDICATE, not whose copy), never a
  reworded answer. What bounds that cost is only that the glyph's absence costs appearance, so it never brings the page
  up alone and `describeSurvivedMiss` prints its sentence into the log whenever it is the whole of the miss. **It is NOT
  bounded to the company of a global**: beside a blocking `@decky/ui` name the verdict is `mixed` and the glyph is that
  answer's unnamed rest, with no global anywhere in the miss — `steamModules.test.ts`'s "leaves the glyph in the unnamed
  rest with no global anywhere in the miss" is that case. Four quiet ways back: a runtime probe instead of the stamp
  (`typeof DFL !== "undefined"` is true of a standalone bundle loaded beside a running Decky), asking `in DFL` about a
  name `@decky/ui` never exported (`SP_*`, `ControllerGlyph` — a package disagreement reported on every miss, which is
  what `SteamLookup.deckyUiExport` and its sweep-derived lock exist to prevent), reading an unreadable `DFL` as an
  absence rather than as nothing established, and letting the reading THROW at all — `definePlugin`'s factory reads it
  before it returns anything, so an unguarded `window.DFL` or `name in DFL` costs the page AND the log line and leaves
  the blank panel the check exists to tell apart from a dead backend. The version beside the name is an enrichment only
  — `_versionInfo.current` is internal, guarded, and every sentence is complete without it; `remote` beside it is the
  PUBLISHED version and is never consulted
- **A coverage exclusion names a property of the code, never a place: every frontend-scoped entry stands in BOTH
  `frontend/vitest.config.ts`'s `coverage.exclude` and `sonar-project.properties`' `sonar.coverage.exclusions`, every
  file entry carries its reason as a `// coverage-exempt:` marker in the file's own first lines, and every marked file
  is listed** — check — `scripts/check_coverage_exclusions.py`. **The two lists spell a shared entry differently and
  that is not drift**: Sonar runs from the repository root and Vitest from `frontend/`, so `src/types/**` there is
  `frontend/src/types/**` here, and the gate normalises before comparing. An entry spelled repo-relative on the Vitest
  side excludes nothing at all — Vitest would resolve it to `frontend/frontend/...` — so it is reported by name rather
  than normalised into agreement with Sonar's identical-looking copy. (A folder entry is admitted only from the script's
  `FOLDER_ENTRIES`, where membership in the folder IS the property; the backend/config entries are Sonar-only and the
  frontend test glob Vitest-only, each declared there with its reason so the asymmetry is stated rather than tolerated).
  Two accidents it removes, both silent: `src/patches/**` excluded a FOLDER, so a file's coverage obligation changed
  when it was moved out of the folder and nothing said so; and `steamShortcuts.ts` sat on Sonar's list and not on
  Vitest's under a comment claiming the two were aligned. **What the check cannot see is MEMBERSHIP in a folder entry's
  directory** — the two admitted folders are checked to exist and their contents are never read, so a real module filed
  into `frontend/src/types/` or `frontend/src/test-utils/` is exempted by its PLACE, with no marker asked for and
  nothing failing: the very accident above, still live for those two directories. It is declared rather than mechanized
  on purpose — "is this really only a type declaration" is not a cheap check, and a half-check would exempt on a
  property nobody stated while reading as enforcement. **Nor can it see the marker's SENTENCE** — a false reason passes
  green, which is what the three stated reasons this cut found were: "no logic to assert" over a file with four passing
  tests, "no isolated logic to assert" over 88.65% line coverage, and "thin plugin-entry shim" over 89.09%. Only the
  first was replaced by a truer marker; the other two files lost their exclusions outright, which is also how their list
  drift was settled
- **Every pinned version in a lock satisfies its `.txt` source constraint (`requirements-dev.*` at the root,
  `docs/requirements.*` beside the docs)** — check — `scripts/check_lock_sync.py`
- **Every local markdown link in tracked docs resolves (file target + heading/attr-list anchor)** — check —
  `scripts/check_markdown_links.py`
- **Every stated RomM minimum version matches the enforced `Plugin._MIN_REQUIRED_VERSION`** — check —
  `scripts/check_romm_min_version.py` (ADRs excluded: frozen history)
- **Every tree under `backend/_vendor/` is pinned by the `<pkg>.SHA256SUMS` beside it: every manifest entry under
  `<pkg>/` matches the vendored file's digest, the vendored file set EQUALS the manifest's set restricted to that
  prefix, and a package directory with NO manifest is a failure** — check — `scripts/check_vendored_trees.py` (the
  manifest is discovered, never named in the script, so the next vendored package is guarded by default rather than when
  someone remembers — the hole this closed was a second tree, `vdf`, sitting unpinned beside a pinned `atlas` while the
  gate reported OK. The set-equality half is the part `sha256sum -c` cannot do at all: the plain form fails on a correct
  copy, because a wheel's manifest lists release artifacts and dist-info files we never vendor, and `--ignore-missing` —
  the flag that makes it green again — exits 0 after a vendored file is deleted). **What the check cannot see is the
  manifest itself.** For `atlas` it is upstream's own release manifest, so the digests additionally prove identity with
  the tagged release; for a patched copy like `vdf` it is our own digest of the tree we ship, so a manifest regenerated
  to bless a hand-edit passes green and only review catches it — which is why regenerating one is the last step of a
  deliberate re-copy and never the answer to a failing gate. The licence assertion is data-driven, not package-driven:
  the sibling `<pkg>.LICENSE` is checked exactly where the manifest carries a dist-info licence entry, and a sibling the
  manifest carries no entry for is reported as pinned by nothing — otherwise regenerating a wheel's manifest from its
  own tree would delete the licence check in the same step, silently, while the file stayed. Deliberately outside it:
  `backend/native/` is pinned by its own `sha256sum -c` over one `.so`, and `__pycache__` is ignored wherever it
  appears, on both sides of the comparison. **Two things are outside it by accident of shape, and neither is loud.** The
  gate sees **directories only** (`package_dirs` filters on `entry.is_dir()`), so a single-module dependency dropped in
  as `_vendor/six.py` is never asked for a manifest — and there is no shape here that could pin one: dropping a
  `six.SHA256SUMS` beside it makes the gate fail with "it pins no vendored tree", so the guarded-by-default half is a
  property of package DIRECTORIES alone. And `rglob` does not descend into a symlinked directory, so every file below
  one is invisible to the set comparison whatever the per-file symlink guard does; git records the link itself as mode
  120000, which is what makes it a review question rather than a silent one
- **Server-supplied path components pass `safe_join` (`lib/path_safety.py`)** — test + prompt-only — traversal tests per
  path builder; new call sites are prompt-only
- **A firmware row's presence comes from the resolver wherever the resolver declared it; the plugin's own filesystem
  probe covers only three leftovers** — prompt-only — `services/firmware/demand.py::FirmwareDemand.is_downloaded` is the
  single crossing point and states the boundary: the probe answers for a library file no core declares, for a placement
  whose location the plugin cannot honour, and for the already-there skip in the download batch. Everything else reads
  the resolver's `present`, which follows symlinks the plugin would have to re-implement — the PS2 folder is one
  directory reached through two spellings. `present is None` reads as absent, the safe direction, because the row then
  shows work outstanding rather than a readiness nobody established. **Nothing enforces the crossing point.** A fourth
  status builder calling `_firmware_file_store.exists(dest)` directly would go green, and its rows would silently answer
  from the weaker source — `os.path.exists` on a path the plugin assembled, which is what this cut removed after it
  rendered a satisfied requirement as missing. `services/firmware/status.py` holds that store itself, for
  `_stamp_deletable`'s records-still-on-disk probe, so the wrong probe is one line away from every row builder that
  should be asking `FirmwareDemand`. Related and separate: presence is not the row's verdict (CONTEXT.md → Row verdict),
  and a withheld verdict is not an absence — its cause is read off the row's caveat codes and, for a declared FILE, off
  its `checked` (CONTEXT.md → Byte reading), never off the verdict itself. Three of that vocabulary's eight values sit
  behind one withheld verdict and are three different statements: a file the emulator READ and does not recognise was
  checked, so wording it "could not be checked" is untrue; `refused` is not withheld at all, arriving with the verdict
  already `false`. Nothing checks that a consumer keeps them apart — `checked` is a plain string on the row beside a
  `satisfied` that reads like its summary
- **A firmware row's verdict is `BiosFileEntry.satisfied`, and for a folder declaration it is what the folder HOLDS —
  never that the folder is there** — test + prompt-only —
  `tests/services/test_firmware.py::TestAFolderRequirementIsAnsweredByItsContents` pins all three answers end-to-end,
  `tests/domain/test_firmware_wants.py` pins the fold the service asks through, and
  `tests/adapters/test_atlas_firmware.py` pins each folder answer and which codes those rows carry. The rule spans three
  modules and no diff-scoped review sees it whole: the adapter carries `declared_kind` and the folder verdict — settled
  in the same verified per-platform reading the rest of the row comes from, so there is no second question to keep in
  step — `domain/bios_status.py::_row_verdict` decides the row's answer, and both frontend surfaces colour and word the
  row off it. **Nothing mechanical joins those three**, which is what a consumer reading `downloaded` for a folder row
  breaks — an `if row.downloaded` beside the verdict, a count that spends presence as readiness. RetroDECK links LRPS2's
  `pcsx2/bios` onto the BIOS root, so such a consumer reports "All required ready" over a PS2 install with no BIOS file
  at all; that was the state before #1807 declined the verdict, and this cut replaced the declining with a real answer,
  so the same field access brings it straight back. The same holds for the third value: a required row answered `None`
  takes the level to `unknown`, and folding it into `False` claims an absence nothing established. `declared_kind`
  carries a second rule with **no check at all**: a folder declaration is never offered as a download — the emulator
  lists that name, so there is no file to fetch into it. Three places refuse it today (`PlatformDetail.tsx`'s fetchable
  filter, `FirmwareDownloader._download_firmware_batch`, and `FirmwareDownloader.download_platform_firmware_file`, which
  answers one named file and so refuses with a reason where the batch simply passes the row over);
  `FirmwareDownloader.download_firmware(firmware_id)` still does not. It is the DECLARATION's kind, so it survives an
  absent folder, which is exactly the case a presence check would let through
- **The console's own firmware demand is a value of its own (`system_image`) and is never folded into a count, and the
  resolver's `system_firmware: null` reaches it as a claim about nothing** — test + prompt-only —
  `tests/domain/test_bios_status.py::TestClassifySystemImage` pins all four answers and the precedence over them,
  `::TestTheVerdictOverTheSystemImage` pins what the level and the token do with each, and
  `tests/services/test_firmware.py::TestTheConsolesOwnFirmwareDemand` pins the PlayStation case end to end including
  that the overview and the game page stamp one answer. The frontend halves are pinned per surface
  (`frontend/src/bigpicture/BiosTab.test.tsx`, `frontend/src/bigpicture/library/PlatformsTab.test.tsx`). **The rule
  spans eight modules and nothing joins them** — counted one per file the answer passes through, four backend and four
  frontend: the adapter (`adapters/atlas_firmware.py`) carries `CoreFirmware.system_firmware` and `requirements_met` per
  core, `domain/firmware_wants.py::CoreFirmwareVerdict` holds the four spellings apart from the absence,
  `domain/bios_status.py::classify_system_image` decides, `services/firmware/status.py` stamps it beside the counts,
  every frontend surface that words it does so through ONE module (`frontend/src/utils/biosSummary.ts`; which components
  those are is answered by reading them, not by a tally kept here), and a fourth reads it without wording it (below). A
  libretro `.info` can mark a file required or optional and nothing else — no way to say "one of these", none to say the
  console will not start without one — so an author who knows it will not has two lossy moves, and the deployed
  catalogue takes both: SwanStation marks all five of its PlayStation images **optional**, Beetle PSX marks three of its
  own **required**. Which is why no count can be relied on to carry this: it is ONE requirement over the whole list, and
  putting it in `required_count` reports every image the core declares as required —
  `0 of 5 files SwanStation requires are in place` under the SwanStation this was observed on. The twenty in that page's
  own `0/20 files held` is the library's inventory for the platform, a different set again, and reading the two as one
  is how the wrong ratio gets written. Each fold fails its own way and all of them silently. Fold it into the counts and
  the page states a ratio over the wrong set. Read `system_firmware: null` as "this console needs nothing" — a
  truthiness test, a `!= "runs-without-firmware"` bucket, a default — and the plugin claims an all-clear over a console
  nobody has looked at, which is the collapse the `unknown`/`not_needed` entry above is about, one axis over. **The
  demand comes from the table and the presence from our rows, and `requirements_met` is not consulted at all** — weigh
  the two against each other and you have made the misreading that field exists to prevent, because ignorance there is
  always `None` and a `False` is therefore a demonstrated statement rather than a disagreement. Its two causes (a
  DIFFERENT required file absent, or one present with the wrong bytes) each leave one of our own required rows unmet, so
  the counts already report them by name; the second needs a content check to arise, and the inventory is asked
  **unverified** (the entry below), so it cannot occur here. What the presence half actually resolves to — a row's
  `satisfied` is presence, `null` in two shapes, and both read as not held — is written once, at
  `classify_system_image`, because an outside reader took that field for the resolver's usability answer and drew a
  false finding from it. And on the frontend, `system_image: "unsettled"` joins `required_withheld` on the side
  `PlatformDetail`'s `nothingEstablished` excludes: its rows were answered, so the pane has a file list to point at
  rather than only a place to put files by hand. Since #1821 that flag decides WORDING alone — the download affordances
  are built off the fetchable set and read the verdict nowhere, because what the resolver could establish is the
  emulator's demand and what is fetchable is what the library holds; the two further inputs they do read
  (`required_by_active`, and the library's own finished ratio) are demand and inventory, not readiness gates. **A fourth
  frontend reader is the play row's BIOS badge** (`frontend/src/utils/playSection.ts::extractBiosInfo`), where
  `"absent"` is a second established absence beside the required count. Whether the count sees the same thing is the
  core author's choice, which is why the badge may not be left to it: under SwanStation every image is optional,
  `required_count` is 0 and the comparison beside it is vacuously false, while under Beetle PSX three of the same images
  are required and the count raises the badge by itself. One console, one BIOS folder, two answers — and `"absent"` is
  the same under both. `"unsettled"` deliberately raises no badge, the same reading a withheld required row gets: the
  badge claims a file is NOT THERE, and nothing established that. **Where BOTH ignorances hold** — a console needing an
  image whose required folder row could not be judged, the LRPS2 shape and a reachable one — `biosSummary` names the
  withheld ROW rather than the console. They are not two gaps over two different file sets: a `required_by_active` row
  always carries the active core, so it is always one of the rows the disjunction is read over. It is always one of the
  unjudged rows that verdict is read over rather than a finding beside it — the decline needs at least one such row, and
  this is one — and need not be the only one, since another image the core declares can be unjudged too; it is the only
  half of the pair that can name a file, and naming it points at the file list, where its caveat explains itself.
  **`"absent"` is tested BEFORE the level's decline**, and the pair never arrives at all today because the backend lands
  `absent` on `missing`. Since #1863 that order lives ONCE, in `biosSummary`, which is what every wording surface reads
  — `PlatformsTab.tsx`'s row tooltip last, since it kept a copy of the order and an older spelling of the states for a
  cut longer and described one platform in two vocabularies a keypress apart. **The module's own drift lock is a test
  that reads components as SOURCE** (`biosSummary.test.ts`, over the phrase list the module builds its answers from,
  with the ratio's twin in `biosHeldRatio.test.ts`) — and since #1866 it SWEEPS the set it searches rather than naming
  it (`frontend/src/test-utils/componentSources.ts`, every non-test `.tsx` under `frontend/src/bigpicture`), because the
  naming is what failed: both locks listed two components while three rendered these states, and a surface missing from
  such a list carries no lock at all and cannot be told from one that never drifted. Deriving the set from who IMPORTS
  the module would be worse than the list — a surface wording a state for itself is exactly one that does not import it.
  **What neither lock can catch is a component inventing a NEW wording for one of these states**: only a copied phrase
  is searchable, so a green run there is evidence about copied sentences and about nothing else. Two limits of the
  sweep, both deliberate: it is `.tsx` only, so a wording helper extracted into a `.ts` beside its component is
  unsearched (`frontend/src/bigpicture/panelState.ts` is such a file and quotes BIOS prose today), and
  `frontend/src/utils` is out of scope because that is where the phrases legitimately live **A narrower form of the same
  answer is read PER CORE onto every row** (`FirmwareCatalogue.emulators_needing_one_of_their_files` →
  `build_file_entry`'s `cores[<emulator>]["needs_one_of"]` and the row's own `system_image_candidate`, worded by
  `BiosTab.tsx`'s `coreLineSuffix` and marked by `library/PlatformDetail.tsx`'s `diskMark`), and there the rule is that
  the two keys on that entry are two SPEAKERS: `required` is the core's own `.info`, the other is the packaged table
  about that core's console counted over the core's whole declaration, and `optional` beside `needs_one_of: 5` is the
  informative pair rather than a contradiction to resolve. Rewriting the declaration off the demand — printing
  "required" where the core said optional — puts words in the emulator's mouth and loses the only fact the row had to
  add; folding the pair the other way loses the demand. **A core is in that narrower answer only where it marks NOTHING
  required**, which is deliberate and is the second thing nothing checks: a core whose console needs an image and that
  does state required files says so through those rows' `required_by_active`, so annotating its optional rows too states
  one requirement twice — it put "the console will not start without one" under `ps1_rom.bin`, which Beetle PSX marks
  optional while hard-requiring three other images. The same narrowing makes `system_image_candidate` a strict subset of
  the rows `classify_system_image` weighs, and widening either to match the other is the fix that reintroduces one of
  those two defects. Nothing checks any of it: `needs_one_of` is a plain int-or-null on a dict a surface may read either
  key of, and the candidate flag is a plain bool beside a `required_by_active` that reads like its sibling
- **Which emulator a set of answers is about is ONE pick per scope — a platform's, and a ROM's — and every answer in
  that scope is a projection of it** — test + prompt-only —
  `tests/services/test_firmware.py::TestOnePlatformOneEmulator` asserts the two surfaces AGREE across every way a
  platform arrives at an emulator (no pick, each of the three ES-DE offers, a pin naming an emulator the catalogue no
  longer lists, a pin whose command cannot be baked) rather than pinning today's value, because a value test would pass
  for a third resolution that diverges on some other configuration;
  `::TestDownloadRequiredFirmware::test_it_fetches_what_the_platforms_own_pick_calls_required` holds the download button
  to the same pick. The pick is `domain/emulator_commands.py::resolve_platform_option` — the per-platform override
  (`settings.json` `platform_cores`) when its label still names a bakeable emulator, else the es_systems default — and
  it is the read-path precedence `ActiveCoreResolver` applies minus the per-game layer. Three call sites read it today:
  `FirmwareStatusReader._platform_emulator` (which serves BOTH the overview's `active_core` / `active_core_label` and
  `check_platform_bios`'s `launching_emulator=None` fallback) and `FirmwareDownloader._platform_emulator_identity`.
  **Nothing joins them**, and a fourth resolution is exactly what this entry is about: the pane displayed a just-picked
  PCSX ReARMed and judged the platform by the libretro system default beside it, so one PlayStation read `not_demanded`
  / `ok` on the game page and `absent` / `missing` on the pane, and the write's own response carried the wrong verdict.
  `.label` and `.emulator` must come off ONE call — two calls agree by coincidence, which is what the old pair did until
  an override was set. **One seam now carries the pick rather than a projection of it**: `BiosChecker` takes a
  `LaunchingEmulator` (`domain/emulator_commands.py` — `emulator` and `label`, both read-only), so the per-game caller
  hands over the whole resolution and `check_platform_bios` reads both projections off that one value. A mismatched pair
  is not representable there, which is why the answer may state its own `active_core_label`: it is the label half of the
  pick those very counts were filtered by, and the game page's BIOS headline names the emulator from it
  (`TestTheAnswerNamesTheEmulatorItJudgedBy`, which hands the check two picks differing only in label and holds the name
  to moving while the judgment does not). That covers this seam and no other — the remaining sites still pair by
  discipline. **The key is the emulator IDENTITY, not `.core_so`** (#1821): the identity names a standalone pick as
  readily as a libretro one, where `core_so` is `None` for every standalone emulator and sent the rows back to "every
  declaring emulator". Reaching for `.core_so` here again restores that degradation silently, because the field is still
  there and still right for the picker payload beside it. `CoreInfoProvider.get_active_core` — the "first libretro
  entry, bakeable or not" reading these sites used — has no production caller left. **The ROM scope is the same rule one
  layer in, over a different pair of modules**: the game page is assembled by two services that each ask
  `ActiveCoreReader.active_emulator_for_rom` for themselves — `services/cores.py::get_platform_core_info` names the pick
  in the picker, `services/game_detail.py::get_bios_status` scopes the BIOS question to it — and
  `::TestOneRomOneEmulator` asserts they agree across every way a ROM arrives at an emulator (nothing pinned, the
  platform's pick, a per-game override, the override over a platform pick naming something else, a standalone pick, a
  stale pin that degrades). They read one seam today and nothing says they must; the picker reaching for
  `active_core_for_rom` — the `.so`-space projection right beside it — would answer `None` for every standalone pick and
  send the BIOS rows back to the platform's own, which is the platform-scoped defect above, per ROM. **What the ROM
  sibling cannot pin is the fixture's own default**: `FakeCoreInfoProvider.get_default_emulator` builds its invocation
  from the `active_core` tuple, which carries no identity, so a test on the bare fake resolves an unpinned ROM to a
  `None` where the live adapter resolves it to an emulator — `_DeclaredDefaultCoreInfo` in that file renders the
  declared default the way `AtlasCatalogueAdapter` does, and every other fixture on the bare fake still exercises the
  weaker resolution
- **A platform's BIOS answer is asked for one platform at a time, and a row that has not got one yet is never rendered
  as a row nothing could be established for** — test + prompt-only —
  `frontend/src/bigpicture/library/PlatformsTab.test.tsx` pins the four halves that can be seen from a test: the two
  renderings apart (an outline dot and "Checking…" against the solid grey dot and "Nothing is known"), the focused row
  asked ahead of the rows above it, the walk stopping at unmount, and a read issued before a core change not overwriting
  the one issued after it. Each was mutation-checked. **The JOIN between the two calls is pinned once**, in
  `tests/contract/test_firmware_status_read.py`: it composes them over the real wiring and holds the result against the
  key set the single whole-page call answered with, which is the one thing the service tier cannot do — its ~25
  whole-page tests compose through a local helper that would reproduce a composition bug rather than catch it. **The
  rule spans three frontend modules and one backend split, and nothing joins them.** `services/firmware/status.py`
  answers `get_firmware_status` (which platforms the page can speak for) and `get_platform_firmware_status` (one
  platform's whole entry — 106-486 ms each against 4.6 ms for the overview, measured); `usePlatformsPage` owns the walk,
  the per-slug ordering counter and the four-valued `firmwareState`; `PlatformsTab` draws the dot; `PlatformDetail`
  words the pane. Every failure here is silent and looks like an answer. A state-bearing field creeping back onto the
  overview payload gets rendered over a platform nobody has asked about yet. A fifth rendering path reading
  `firmware === null` instead of the state says "nothing could be established" about most of the list for the first
  seconds of every visit — which is the confusion this cut exists to remove, restored by a truthiness test. An answer
  already held is not taken back by a later failure (`firmwareStale` beside the state, never instead of it), and "the
  overview did not name this platform" is one of the two ways to hold one. **Two halves no test reaches**: the `alive`
  guard in the hook's `accept` is unobservable under React Testing Library, which drops a write to an unmounted tree
  itself — what a test can see is the walk stopping, so the guard states the rule rather than being held to it; and
  whether an 8px outline reads as "not yet" against a filled dot is device-only, like everything else about this list's
  legibility
- **The whole-machine firmware inventory is never asked with content verification, and the per-platform reading is never
  asked without it** — prompt-only — `firmware_inventory()` (`FirmwareResolver`, `AtlasFirmwareAdapter`) is asked
  unverified: `verify=True` there sweeps every unclaimed file under the BIOS root plus each declared file the packaged
  identity table covers at a matching size, and its two callers — the home migration's untracked-BIOS sweep and
  `download_firmware(firmware_id)` — need only where a file GOES. `firmware_for_system(<system>)`
  (`FirmwarePlatformResolver`, `AtlasPlatformFirmwareAdapter`) is asked WITH it, and that half is the one a reader is
  likely to "optimise": drop the flag and two answers go silent rather than loud — a packaged card that identifies its
  image by content names no file at all (DuckStation comes back `declaration="packaged"` with an empty list and no
  system recording), and every folder declaration's verdict falls to `None`, which takes each platform holding one to
  `unknown`. Measured on the reference machine: 64-318 ms per system verified, against 248 ms for one unverified
  whole-machine sweep — the per-system read performs no unclaimed sweep at all, which is what bounds it. Nothing detects
  either direction: `verify` is one keyword argument on each call and every test stays green
- **No sentinel objects on the wire — explicit JSON-representable tagged values only** — prompt-only — no sentinel
  survives on the wire today (`NO_MIGRATION` retired with #1004, legacy `slot:null` confirmation with #1276), so the
  rule now guards reintroduction; nothing mechanical detects a new one
- **Every destructive op has backup-or-confirm; never delete data that exists nowhere else** — prompt-only — save-file
  removals route through the `.romm-backup` funnel (`MatrixExecutor.quarantine_local_file`; the removed-game cleanup's
  claimed variant is `PruneSaveSupport.quarantine_prune_saves`); every other delete path carries the rule unmechanized.
  Removed-game cleanup takes the **confirm** leg for one case deliberately: installed ROM content the user did not
  select for the recovery bundle is deleted with its row. The ROM is re-downloadable from RomM where a save is not, the
  per-candidate opt-in and its consequence are stated in the confirmation dialog and the user guide, and the row cannot
  be removed at all without a fresh 404 — so this is a disclosed choice, not an exception that drifted in. The adopt
  dialog's **replace** exit is the second such case, and it does **not** rest on that justification: the premise is that
  the content is the user's own — a different rip, a patch, a romhack — which is exactly what the server cannot hand
  back. What carries it instead is that the user is shown both sides, offered a content check, and chooses between two
  named outcomes behind a second confirmation ([ADR-0028](docs/adr/0028-adopted-install-is-an-install.md)). That
  reasoning covers the **ROM** only: an adoption's Overwrite also replaces save and savestate files, and those take the
  **backup** leg through the same `MatrixExecutor.quarantine_local_file` — every argument ADR-0028 gives for not
  quarantining a ROM (gigabytes, no sensible retention, re-fetchable from RomM) inverts for a save, and a savestate is
  synced nowhere at all. It is the first caller to hand that funnel a directory outside the saves root: it takes the
  directory it is given, so a savestate's backup lands in `<states>/.romm-backup/`
- **A BIOS file is deleted only where a `downloaded_bios` record names it under one of the platform's firmware slugs,
  and only at the path that record holds** — test + prompt-only —
  `tests/services/test_firmware.py::TestDeletePlatformBios` and `::TestDeleteOneBiosFile` pin every direction
  end-to-end: an emulator-shipped file survives, a hand-placed file under a server file's name survives, our own
  download is still removed once RomM no longer holds it, a download whose placement has since moved is unlinked where
  it was written rather than where the placement now points, and a per-row delete takes only the record it names. What
  makes the destructive-op rule (the `backup-or-confirm` entry) concrete for BIOS files is that authority to delete
  comes from having placed the file, and the record is the only evidence of that, because `BiosFile.mark_downloaded` is
  written in the download path and nowhere else. So the records are the delete's whole input: it iterates them, not a
  status listing, which is also what keeps a download RomM has since dropped deletable instead of gated behind a file
  list that no longer names it. The authorisations a reader reaches for instead are wrong in opposite directions.
  `downloaded` is `os.path.exists` and nothing more: authorise on it and Delete BIOS destroys firmware RetroDECK ships
  with its own components, which no RomM library holds and nothing here can fetch back — it did exactly that to
  `<bios>/dolphin-emu/Sys/codehandler.bin` on a real device. `on_server` describes what the library holds _now_, not who
  wrote the file: authorise on it and a file dropped from RomM after we downloaded it is stranded on disk with nothing
  in the UI able to remove it. The PATH has its own version of the same trap: a status row's `local_path` is recomputed
  from today's placement, so for a file fetched before an emu-atlas bump moved it the name still matches our record
  while the path names whatever now occupies the new destination — RetroDECK's own `codehandler.bin`, in the case that
  motivated this. The count the UI offers is bound to the same set: `deletable_count` on the
  `get_platform_firmware_status` payload is records-still-on-disk, counted as distinct paths, because `local_count` is
  the library's progress ratio and is wrong in both directions — it hid the button entirely for a platform whose
  downloads had all left the library. **Since #1815 the same field is stamped per ROW** (`_stamp_deletable`), and the
  frontend authorises a destructive action on it: a row's Delete is offered where `deletable_count` is non-zero and
  nowhere else, and a folder row's counts the distinct files our records name underneath it, because a folder is never a
  download but what we put inside one is still ours — and two records naming one path are one unlink, the platform
  count's own rule read one layer in. That is a wire field a page reads to decide whether to offer a delete, so deriving
  it from `downloaded` — the same substitution as below, one layer out — puts the button on `codehandler.bin`;
  `TestGetFirmwareStatusDeletableCount` pins the row's answer for a file the plugin did not place. **Three buttons now
  reach one removal loop** (`PlatformBiosDeleter._delete_recorded_io`, under a record predicate per button): a second
  copy of that loop is the shape this rule is about, because the copies would drift silently. **Nothing mechanical
  stands behind any of this.** A delete path looping a status list on `downloaded` alone would go green — which is
  exactly the shape this one had when it destroyed that file
- **Every read-mutate-write of a `RomSaveSyncState` runs under `SyncEngine.rom_lock(rom_id)`** — prompt-only — sync
  paths, `get_save_status`, and the four slot mutations hold the lock; mechanize via a `rom_save_sync_states.save`
  call-site audit
- **Which files a game's save consists of is the EMULATOR's answer, read live, and four of its five states refuse the
  sync — no probe, no state written** — test + prompt-only — `tests/adapters/test_atlas_saves.py` pins the five states
  and every way the question cannot be put, `tests/domain/test_save_answer.py` pins the precedence that makes "exactly
  one" well defined, and `tests/services/saves/test_save_shape_gate.py` pins the absences **each beside a control that
  asserts the same probe DOES happen for a syncable answer** — without those controls a service that had stopped probing
  entirely would pass. The rule spans seven modules and no diff-scoped review sees it whole: `AtlasSaveLocationAdapter`
  reads the machine, `domain/save_answer.py` decides what the reading means, `RomInfoService.save_answer` turns it into
  names, `SyncEngine`'s three per-ROM entry points refuse on it through `sync_engine/_shape_refusal.py`, which holds the
  reading and the skip shape, `MatrixExecutor.sync_rom_saves` is the backstop every sync path crosses, and
  `services/saves/status/service.py` puts it on the wire. **Four halves have no mechanical check at all.** (1) The
  refusal is enforced at four call sites — the three per-ROM entry points, which report the skip via
  `sync_engine/_shape_refusal.py`'s `live_save_answer` / `save_shape_skip`, and `MatrixExecutor.sync_rom_saves` (reached
  through `SyncEngine.do_sync_rom_saves`), the backstop that covers the whole-library sweep, whose single result has no
  room to name the ROM it passed over. A fifth entry point added without either goes green, and its failure is silent
  because a per-game probe for a shared card finds nothing and reports "no saves". The backstop is pinned by the ABSENCE
  of a server round-trip, because everything downstream of it is redundantly safe — a refusing answer carries no names,
  so nothing is probed or grouped even without it. (2) A configuration-role file is excluded by
  `SaveAnswer.synced_files` and included by `owned_files`, which is what a directory move must carry — a caller reading
  `components` directly gets neither rule, and syncing Saturn's `.smpc` overwrites the console settings the user chose
  on the other device. Saturn is the only example that actually reaches the rule on a stock RetroDECK: MAME states a
  per-game `.cfg` too, but its answer classifies as not-established, so the sync refuses before any role is consulted.
  The rule is a DENIAL — `CONFIGURATION_ROLES` names what to hold back — and turning it into an allow-list of the roles
  known today is the one change here that fails in silence and in the expensive direction: the resolver's own `unknown`
  role (a file on the machine no declaration describes) and a component with no role at all are both carried today, and
  an allow-list drops them, along with every role upstream names next. `tests/domain/test_save_answer.py` and
  `tests/adapters/test_atlas_saves.py` pin both directions; nothing else would notice, because a dropped file is simply
  a file the page does not mention. (3) The two axes a rendering must read alongside the state are single fields nothing
  forces a consumer to touch. `SaveAnswer.unestablished` holds three shapes, and a truthiness test on
  `state == "unestablished"` collapses "nobody has audited this core" into "the folder is known and the names are not"
  and into "the question was never put". `content_installed` is worse, because ignoring it is invisible: an uninstalled
  ROM answers with a state, a directory and a full file list, every name a prediction about the path the game WOULD
  occupy, so a surface that renders them tells a user their uninstalled game already has three save files. The wire flag
  beside each name is `carried`, not `synced`, for the same reason — it names the RULE applied to a file, never that
  file's sync state. (4) **The question must carry the ROM's REAL content path**, because the answer turns on the
  content file's own EXTENSION — PUAE answers `save-inside-content` for an Amiga `.adf` and establishes nothing for an
  `.hdf`; Genesis Plus GX answers a shared `scd_*.brm` for a Sega CD `.chd` and a per-game `.srm` for a `.bin`. Within
  `RomInfoService` the system and the path are decided in exactly two places — `_installed_answer` for a ROM on disk and
  `_uninstalled_answer` for one the library only knows about — so ADR-0010's slug leak has two sites to guard there
  rather than one per caller. A **third** site exists outside it: `services/migration/save_sort.py` asks the resolver
  directly for each ROM its walk sees, taking both the system and the path off the install record, which is the source
  ADR-0010 says to use — so it cannot leak the slug, and it is a site the same rule has to hold at. A synthetic stem
  passed anywhere else answers a different question in a shape that looks like an answer to this one, and nothing would
  say so. It is also why every per-system pin in `tests/adapters/test_atlas_saves.py` is keyed by `(system, extension)`:
  a pin that does not name the extension it asked with is pinning nothing, which is how two independent measurements of
  the same systems produced contradictory fact lists. **Every path asks live and nothing caches an answer** — only the
  installation handle is memoised — because the user changes a core's options in the emulator's own quick menu between a
  launch and the next sync; a display cache added without invalidating it on every sync entry is the one change that
  makes this rule fail silently and expensively. Detail: `docs/architecture/save-sync-coverage.md`, CONTEXT.md → Save
  state / Save scope
- **Per-slot server reads/deletes go through `domain/save_slot.py` (legacy omits `&slot=`, client-filters)** —
  prompt-only — `get_slot_saves` / `get_slot_delete_info` / `delete_slot` / `list_file_versions` / `rollback_to_version`
  use `slot_query_param` + `save_in_slot`; RomM can't address `slot:null` via the param, so legacy MUST omit it + filter
  client-side, and a legacy delete is refused up-front
- **Every save-sync decision comes from `compute_sync_action` (via `list_saves`), never the `negotiate` op list; every
  automatic upload POSTs `overwrite=false` (409-backstopped); `overwrite=true` only from an explicit `keep_local`** —
  test + prompt-only — the hand-enumerated core cases (`tests/adapters/test_gavel_native_decision_table.py`) and the
  core property tier (`tests/adapters/test_gavel_native_property.py`) + contract 409 tests (`tests/contract/`); new
  upload/dispatch call sites are prompt-only
- **Both save-sync decisions run in the compiled gavel core, reached only through the `ComputeSyncActionFn` /
  `ResolveUploadConflictFn` seams; `domain/sync_action.py` holds only the `SyncAction` vocabulary the core answers in,
  so a change to either decision is a contract change carried by re-copied gavel vectors** — test — both vendored vector
  families run against the core (ladder in `tests/adapters/test_gavel_native.py`, decision table in
  `tests/adapters/test_gavel_native_table_vectors.py`); the `.so` and the vectors are pinned to the same upstream
  release tag and are bumped together
- **`applied_launch_options` is written only by the six recorded-state writer sites (sync ack-commit, download-complete,
  adopt-complete, uninstall, home-migration, version-switch), each recording the exact command the frontend wrote;
  excluded from the sync UPSERT; the only sanctioned reset is Force Full Sync's clear-to-NULL (a wrong recorded value is
  the only path to a wrong delta-skip)** — test + prompt-only — each writer site carries a value-exact test; new
  launch-options write paths are prompt-only — mechanize via a `set_applied_launch_options` /
  `record_applied_launch_options` call-site audit. Download-complete and adopt-complete are one site in the code
  (`RomInstallRecorder.do_record_applied_launch_options`) and two in the flow, because an adopted install is an install
  in every respect (ADR-0028)
- **An abandoned-chunk stash's whole-unit apply staging (`pending_sync` / `pending_all_roms` / `pending_cover_sources`)
  is never mutated while the stash is pending (box IDLE) — every run-entry path passes `try_begin_run`, which clears the
  stash before any staging write** — prompt-only — the invariant holds today rather than being aspirational; mechanize
  via a staging-writer call-site audit
- **An apply chunk's ack identity — `active_unit_id` / `active_chunk_index` and a fresh `unit_complete_event` — is
  stamped on the box BEFORE that chunk's `sync_apply_unit` is emitted, with nothing awaited in between** — test +
  prompt-only — `tests/services/library/test_chunk_dispatcher.py::TestAckIdentityPrecedesTheEmit`, which wraps
  `ChunkDispatcher._emit` and records the box at call time over a two-chunk unit. The rule spans three modules and no
  diff-scoped review sees it whole: `ChunkDispatcher` stamps, `services/library/_state.py` holds the fields and their
  verbs, and `SyncReporter.report_unit_results` validates an incoming ack against them (#1041). **What the test pins is
  the ordering inside the dispatcher, not the round-trip** — it observes a mock's call-time state, so a real frontend
  ack racing a real emit is still unexercised, and every other suite lets the emit mock swallow the call. The failure
  mode is why the entry exists rather than being left to the comment: stamp after the emit and a fast ack is rejected as
  stray, the wait then stalls the full 60-second heartbeat window, and the run ends by stashing a chunk the frontend had
  already applied — slow, plausible-looking, and silent (#1052 / #1367)
- **Every path on which the user answers the preview question leaves a live snapshot on neither side — the
  pending-preview store (`frontend/src/utils/pendingPreviewStore.ts`) and the backend's `pending_delta`** — prompt-only
  — three paths clear the store and tell the backend, and all three are the Sync page's: Apply (`applyPreview`), Cancel
  (`cancelPreview`) and Refresh (`computePreview(true)`, which discards before asking for the next one). The fourth is
  the cancel that lands just after a preview was staged, which never adopted it into the store and so discharges the
  rule by discarding server-side alone. A fifth path is not an answer at all and is held to the same rule: a successful
  **Force Full Sync** (`forceFullSync`) discards the state the preview was computed against, so it clears the store and
  tells the backend too — a preview left standing there offers an Apply that would skip exactly what the clear armed a
  re-fetch for. Main holds none of them, and holds none of them for a stronger reason than a division of labour: it
  starts no run and computes no preview, so it never holds one to answer for — its slot opens the page, and its Cancel
  ends a run rather than answering a preview. So the answer is given once, where the change table is. Nothing mechanical
  can tell: an answer path is a page handler, and neither the store nor the backend can know that a call it never
  received was an answer. Forget the store and a table stands over a decision already made; forget the backend and the
  terminal-stage re-ask fetches it back a round trip later
- **A prune run's claim reservation and its refusal of every conflicting callable happen in one atomic gate hold (the
  preview rebuild does not), and frontend-owned Steam work holds a heartbeated, generation-tombstoned lease through
  every continuation's final write** — test + prompt-only — prune service/gate race tests + contract callable-entry
  matrix; new conflicting entry points are prompt-only
- **A prune frontend action mutates Steam only after atomically claiming its exact run/token/discriminant/binding;
  repeats are idempotent and an outcome lost in transit is ambiguous, never success** — test + prompt-only — prune
  service claim tests + `frontend/src/utils/pruneActions.test.ts`; new action kinds are prompt-only
- **Every installed-content mutation is authorized by a descriptor-relative no-follow claim (root identity, descendant
  identities, and — where a bundle exists — regular-file hashes) revalidated immediately before it, never by a path
  re-lookup; refusal, partial mutation and ambiguity are reported, never rewritten into success. The hashes bind a
  deletion to bytes held somewhere else, so they follow the **bundle**, not the caller: a source a sealed bundle holds
  consumes the bundle's digest-bound claim, a source it did not capture seals a fresh content-bound one, and a removal
  with no bundle anywhere — recovery off, or a user-initiated uninstall — seals identity-only
  (`claim_source(..., digest=False)`). An identity-only claim is also the only one that may adopt interrupted
  `.{basename}.romm-prune-*` staging, and only where a surviving install row proves the path. Everything else holds for
  both: staging rename, mount checks, no-follow traversal, and exact-identity revalidation under writer exclusion held
  across each unlink. The one guarantee that differs is all-or-nothing: a content-bound removal leases the whole tree up
  front, an identity-only directory leases per unlink (a whole-tree hold hits `EMFILE` and makes large dumps
  un-uninstallable), so a writer arriving mid-loop yields a reported partial removal instead of a clean refusal** —
  test + prompt-only — descriptor-path, recovery-adapter, real RomRemovalService, and prune contract tests; new mutation
  adapters are prompt-only
- **Every prune frame carries its originating preview ID; only a matching pending preview may adopt a run, and an
  accepted contiguous terminal result seals it against every later frame** — test + prompt-only — prune service frame
  tests + `frontend/src/utils/pruneStore.test.ts`; new prune frame types are prompt-only
- **Every destructive RomM proof is bound to one canonical server-origin/token-origin/user namespace from preview
  through every exact-ID request; a namespace change is uncertainty, never a 404 deletion authority** — test +
  prompt-only — prune service namespace-race tests; new destructive RomM proof paths are prompt-only
- **Every write into per-rom detail state that crosses an `await` is bound to a rom identity — the store
  (`frontend/src/utils/gameDetailStore.ts`) via `writerForRom`, or the answer's own `rom_id` in `applySaveStatus`; the
  panel's state, event and tab-content modules (`frontend/src/bigpicture/panelState.ts`,
  `frontend/src/bigpicture/panelEvents.ts`, `frontend/src/bigpicture/panelTabContent.tsx` — the panel component itself
  holds none of these writes) via `RomBinding`, built by `bindRom` for a read a run of the `[appId]` effect issued and
  by `bindRomInState` for the active tab's panes, whose writer is built during render; the achievements tab
  (`frontend/src/bigpicture/AchievementsTab.tsx`) by construction, its React key being the rom id, so its state cannot
  outlive the identity it was read for. A version switch re-keys without closing, so neither the store's generation
  counter nor the panel's `[appId]` effect sees this class. Binding answers the wrong-rom question only; two answers for
  the SAME rom are ordered instead, by a sequence taken when the read is issued — the store's `loadSeq`, the panel's
  `takeReadTicket` (#1717). Four writes are unbound: the two identity writes install what a binding would compare
  against, so ordering is all they can have, and both have it. The other two have neither — the store's
  `cached.bios_status` fold runs in the same synchronous run as its guard, and the event lane's `handleBiosChange`
  answers for the platform's default core and can overwrite a rom-keyed answer (#1718). The panel's remaining lazy lane
  writes through the raw setter, ordered by the same ticket. The play button is NOT covered (#1714)** — test +
  prompt-only — the panel's thirteen bound sites each carry a version-switch test
  (`frontend/src/bigpicture/RomMGameInfoPanel.test.tsx`); the store side and every new write site on either are
  prompt-only, because a checker scoped to the store's own function bodies would be green on the case this rule was
  written for. The reasons behind the two writer mechanisms live at `writerForRom` and `RomBinding` — do not restate
  them here
- **Every row a reader must be able to reach on a QAM page is a row Steam can focus — a toggle, a button, or a
  `Focusable` carrying an activate handler, including a table row with no action of its own, so the reader can walk the
  table** — check + prompt-only — `tender/qam-focusable-row` checks the narrow syntactic slice where an `@decky/ui`
  `Focusable` in the QAM module map has no self-focus prop, static focusable descendant, opaque child, or unknown
  spread; focus order, runtime reachability, edge revelation, scrolling geometry, and controller behaviour remain
  prompt-only. The frontend suite cannot see those runtime properties: happy-dom has no nav tree, so a page whose rows
  are unreachable renders exactly like one whose rows are not, and a mouse-driven dev loop never meets the problem
  either. A region scrolls only by moving focus — Steam's plain `ScrollPanel` binds no gamepad direction — so an
  unreachable row is also an unscrollable one, and everything below the fold is simply out of reach with a controller.
  The trap is that a bare `Focusable` is a container rather than a focus stop: the base panel sets `focusable` only from
  a caller prop or an `onActivate`/`onOKButton`, and `GetFocusable()` answers `"none"` for a node with neither and no
  focusable children. The rule spans every page the wide frame will host, and the frame cannot carry it: it holds a
  page's content as opaque nodes, never as rows it could check. **What the frame DOES carry is the content outside a
  region's focusable rows, at both ends** — a heading, a counts line or a column header above the first, a legend or a
  total below the last, each unreachable for the same reason and with no neighbour to ride along with — so
  `ScrollRegion` scrolls itself to the top when focus reaches the first stop in it and to its end when focus reaches the
  last (`revealEdge`, over `revealTop` and `revealBottom`). Both halves are pinned by
  `frontend/src/bigpicture/layout/ScrollRegion.test.tsx` over mocked geometry, so what is tested is the DECISION and not
  the scroll: whether the panel and the reader agree about which element is topmost or last stays device-only, like the
  rest of this entry. **Reachable is not near, and the same mechanism decides where a page puts its controls**: focus
  moves one row at a time, so a button under a list of N focusable rows is N presses from the top of the column —
  sixteen, measured on the device for the Cancel that stops a sixteen-unit run. That is why the Sync page's two button
  rows sit ABOVE their tables, which is the reading the layout wants anyway: what you can do, then why. Nothing checks
  that half either, and the suite is blind to it for the same reason — a page whose only control is a library's length
  below the point it opens at renders exactly like one whose control is a press away. Detail:
  `docs/architecture/qam-panel.md`, "Building blocks"
- **A list-and-detail page opens on the row it was opened WITH, not on its first row — because on that layout focus
  selects, so entry focus landing anywhere selects what it lands on** — test + prompt-only — `ListDetail.test.tsx` pins
  the mark and that it moves with the selection, `WidePage.test.tsx` pins that the frame asks for the declared stop, and
  both use a **non-first** row deliberately. **The rule spans three modules and nothing joins them**:
  `utils/entryFocus.ts` owns `ENTRY_STOP_ATTR` and `pageEntryStop`, `bigpicture/layout/WidePage.tsx` places entry focus
  through it rather than through `firstBodyStop`, and `bigpicture/layout/ListDetail.tsx` marks its selected row — plus
  whatever page passes a starting selection at all, `SettingsPage` today. A fourth wide page that opens on a non-first
  row and forgets the mark selects its first row instead, and every test still passes. **The end to end is unreachable
  here**: happy-dom performs no layout and does not reproduce Steam's focus resolution, so what the suite pins is the
  declaration and the finder, never the press. Only a controller confirms it. **What hid this for a whole review round
  is the shape of the failure, not its size**: Main has three notices that name a section, and the one naming the FIRST
  section keeps working, so a reader checking Open Connections sees the feature working while Open Controller and Open
  Save Sync both land on Connections. A check that exercises the first row proves nothing about the rule. The mark sits
  on a `display: contents` wrapper AROUND each row rather than on the row, and that is load-bearing rather than
  stylistic: `pageEntryStop` calls `firstBodyStop(declared)`, which searches DESCENDANTS — a mark on the row itself
  finds no candidate inside it, falls back to the first row, and ships the defect under a comment saying it does not

When a change applies a guard / sanitize / backup / grouping pattern, sweep for sibling sites of the same pattern — the
register is what that sweep checks against.

## Security

- NEVER read or use credentials from settings files (`~/homebrew/settings/`) without explicit user permission
- NEVER pass credentials to agents — if API calls are needed, ask the user to run them and provide output
- NEVER log secrets (passwords, API keys) — mask them in any log output

## Working Style

- **Research before implementing.** When encountering an unknown (how a third-party tool works, where files are stored,
  what APIs exist), STOP and research first. Present findings and agree on an approach before implementation.
- **Discuss architecture decisions.** This is not a vibe coding project. Non-trivial changes require discussion before
  code is written. When you find a problem, explain it and propose options — don't just start fixing.
- **Use agents** for everything beyond trivial single-file edits — research, exploration, implementation. Keep main
  context on architecture and coordination.
- **Sequential agent discipline.** Each agent's prompt MUST include: "When done, report back and wait for shutdown. Do
  NOT pick up other tasks from the task list."
- **Preserve context.** Get alignment first, then implement cleanly in one pass.
- **Sub-issue policy**: epic bodies do **not** carry markdown sub-issue lists — open work is tracked via GitHub's native
  Sub-Issues panel. Link new sub-issues natively; don't add body bullets.
- Roadmap: [GitHub Projects board](https://github.com/users/danielcopper/projects/2).
