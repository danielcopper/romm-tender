# Tender — RomM library into Steam

## What This Is

Syncs a self-hosted RomM library into Steam as Non-Steam shortcuts. Games launch via RetroDECK. The QAM panel handles
settings, sync, downloads, and BIOS management.

The backend runs as **its own process** and hosts the panel itself over a loopback port
([ADR-0036](docs/adr/0036-the-backend-hosts-itself.md)); it was a Decky Loader plugin up to 0.33. It also LOADS the
panel, into Steam's renderer over the CEF debugger — so `mise run dev` is now "build, restart Steam, then run the
backend", and the Decky-shaped deploy tasks are gone. **It shuts the running Steam down**, because a rebuilt bundle
reaches Steam only in a fresh JS context; which tasks do that, and which window they come back into, is under
Development below. A user installs it with `install.sh`, which writes a systemd **user** unit and resolves every root
once into it; the launcher every Steam shortcut starts through lives at `~/.local/bin/tender-rom-launcher` and is not
under any root this program owns.

## What belongs in this file

Three things, and nothing else:

1. **Traps** — where an agent would confidently do the wrong thing and have no reason to go look first.
2. **Cross-cutting invariants** — rules that span files, so no single diff shows the whole rule: the map to their area
   files, and the few whose breach loses data.
3. **Workflow** — how we work here; not derivable from the code.

Everything else is topic depth: `docs/` for humans (the invariant register's entries among them, one file per area),
`.claude/rules/` for path-scoped coding conventions. A rule with a mechanical check needs only its one-line statement —
in its area's register file, or here if it is a trap (CI carries the enforcement); a rule without one needs its full
statement here, in the rule file that owns its area or in its area's register file, because nothing else will catch it.

## Where the details live

Each page below is the current-truth owner of its area, and most carry their own ADR trail. Read the page before working
in the area. **Do not cite ADRs from this file** — an ADR is frozen history (and may be `Proposed` or superseded, which
is invisible at the citation site), so reach it through the page that owns the topic.

- Domain vocabulary — the canonical meaning of project terms — [GLOSSARY.md](GLOSSARY.md). A glossary, not a spec: use
  its wording in code, issues, and PRs, and add a term there the moment it resolves in discussion.
- Steam shortcuts — appIds, artwork, launch-option writes, removal churn —
  [steam-non-steam-shortcuts.md](docs/architecture/steam-non-steam-shortcuts.md)
- QAM panel — pages and their widths, the wide-page frame, list-and-detail navigation, notices and their homes —
  [qam-panel.md](docs/architecture/qam-panel.md)
- How the panel is BUILT — the three build outputs, the two copies of the panel and why, Steam's React globals, the
  start-up check, the list of Steam internals — [frontend-bundles.md](docs/architecture/frontend-bundles.md)
- How the panel GETS INTO STEAM — the CEF client, which bundle is chosen and from what, the marker, the crash watchdog,
  the load-failure card — [loading-the-panel.md](docs/architecture/loading-the-panel.md)
- Save-file sync — slots, conflict resolution, negotiate transport, version history —
  [save-file-sync-architecture.md](docs/architecture/save-file-sync-architecture.md)
- Save-sync coverage matrix — [save-sync-coverage.md](docs/architecture/save-sync-coverage.md)
- Removed-game cleanup — deletion authority, prune conflicts and leases, claims, recovery bundles —
  [removed-game-cleanup.md](docs/architecture/removed-game-cleanup.md)
- Services, adapters, wiring; connection/token and settings-persistence internals —
  [backend-architecture.md](docs/architecture/backend-architecture.md)
- SQLite schema, aggregate roots, migrations — [database-design.md](docs/architecture/database-design.md)
- Emulator and core selection — [core-emulator-selection.md](docs/architecture/core-emulator-selection.md)
- RetroArch/ES-DE config parsing — [config-source-parsers.md](docs/architecture/config-source-parsers.md)
- Steam Remote Play — [steam-remote-play.md](docs/architecture/steam-remote-play.md)
- The focus watcher — which controller layout Steam's 64-bit client is told to load in Desktop Mode, the X connection,
  the pin and its release at stop — [controller-focus.md](docs/architecture/controller-focus.md)
- The desktop surface's changes to Steam's page in the desktop client (dev build only) —
  [desktop-dom-architecture.md](docs/architecture/desktop-dom-architecture.md)
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
  is the injected `DebugLogger`. Neither is checked. A service refuses by raising `Refused`; the shape check catches a
  failure dict built from a literal outside `main.py`, not a missing raise.
- `python-conventions.md` — Protocol suffixes by shape, `do_<verb>` vs. `_<verb>_io`, docstrings stating the contract
  rather than the behavior, and when a subfolder is justified. **No mechanical check exists for any of it.**
- `adapters-domain.md` — adapters own I/O, domain is pure, aggregate mutations are verb-named after the event
  (`adopt_baseline`, not `update_baseline`). The field-assignment ban is checked; the naming is not.
- `romm-http.md` — an unproven 404 must never become `RommNotFoundError`, which is deletion authority downstream: the
  entity proof is the default and only the three byte-stream fetches opt out. Tests pin both directions; nothing else
  does. Also owns the transport's reachability state: a new request method sends through `_urlopen` (checked) and passes
  `romm_origin=False` if it does not talk to RomM (not checked), and a new translation of an HTTP answer keeps its
  status, or a refusal marks RomM unreachable (not checked).
- `bootstrap-wiring.md` — the `main.py` / `bootstrap/` split, which module of `bootstrap/` new wiring belongs in, what
  the `Application` and `Endpoints` each hold, and why `run()` is synchronous.
- `host.md` — the process that hosts this backend (`backend/host/**`): the transport-vs-endpoint failure shapes, the
  token's one deliberate exception, the order of the three admission checks, claim-bearing events, where the size cap is
  judged, the served root, and what the injected expression may carry. **None of its seven rules has a mechanical check;
  each fails green** — the redaction one did exactly that, and only an assertion on stderr's own output caught it.
- `endpoints.md` — the `{success, reason, message}` failure shape, built from a literal only in `main.py` (checked), and
  its two carve-outs; and what makes a method an endpoint; which endpoints must be `def` is **not checked**.
- `vendored-assets.md` — `_vendor/` and `native/` are checksum-pinned upstream copies — verbatim, or verbatim plus a
  documented local patch — and every vendored tree carries its own manifest. The checksums are checked; the reflex to
  fix the upstream artifact instead of the copy is not.
- `testing-backend.md` — test tiers, gate tests, vendored conformance vectors.
- `testing-frontend.md` — the backend-event harness, that `api/host` is stubbed suite-wide so no socket is ever opened,
  non-vacuous catch assertions.
- `comments.md` — a comment, docstrings included, is the exception: only an outside-world fact, a road not taken, or a
  constraint the code cannot express. A fact has one home, and how a value was arrived at goes in the commit rather than
  the file. Re-read the comment on the line you touch — a stale one is worse than none, because it is believed and
  nothing in the toolchain contradicts it. **No mechanical check exists.**
- `desktop-dom.md` — every change the desktop surface makes to Steam's page goes through a restoration ledger, or it is
  left behind once the page is left or the launch question settled; and a selector never names a hashed class. Both are
  in the register's [desktop.md](docs/architecture/invariants/desktop.md), with what checks them; the file's other rules
  — Steam's part hidden only behind a drawn view, every failure logged — have no check.

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

## Where decisions live

- Work starts from an issue. Open questions go under `## To decide`, one checkbox each; an answered question is checked
  off and points to its entry under `## Decisions` (`- [x] … → D1`). A pull request merges only once every question is
  checked.
- New decisions are appended. A decision that changes later is struck through, and the new one follows with its date.
- An epic's decisions live in the epic's body. A sub-issue says "See epic #N" instead of copying them.
- A decision that is hard to reverse, surprising without context, and a real trade-off also becomes an ADR in
  `docs/adr/`; the issue links it.
- Each `## Done when` criterion becomes a test that is seen failing first, or is marked "(device)" when only the owner's
  Game-Mode pass can show it.
- The PR body repeats the final decisions; it becomes the squash commit body.
- Nothing needed to understand a change lives outside this repo and its issues.
- The `decisions` CI check enforces `## To decide` and `## Decisions` in the issue, this section, and the ADR format for
  new ADRs, among other rules listed in the
  [shared check's README](https://github.com/danielcopper/.github#the-decisions-check); everything else here is for the
  review.

## Traps — non-obvious rules that bite silently

- **The build output lives at `<repo>/dist/`, not under `frontend/`** — and the frontend package writes one directory UP
  to put it there (`OUT_DIR` in `frontend/rollup.config.js`). `dist/` is the SEAM between the two halves rather than the
  frontend's property: the backend serves it as `os.path.join(directories.code_dir, "dist")` (`backend/main.py`), and a
  host that located its own build output relative to `__file__` would be the only part of the backend that knew the
  repository's layout. Tidying `dist/` into the package it is built by would put the backend's reach inside the
  frontend's internals. Two consequences worth knowing: `frontend/.size-limit.json`'s paths point up as well, and
  **emptying that directory is the `build` script's job** (`rm -rf ../dist && rollup -c`) rather than any plugin's.
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
  lifetime; Tender records it in `roms.shortcut_app_id` and detects ownership by the exe path. Never re-derive it (the
  `CRC32(exe + appName)` formula is disproven). `launchOptions`, `startDir` **and `exe`** changes are appId-safe, and
  each rests on its own measurement: `launchOptions` on #827's hardware runs, and `exe` on rewriting every one of a
  826-shortcut library and finding every appId still there, against a `shortcuts.vdf` backup taken before it (0 new, 0
  lost, names unchanged). "They are all `Set*` calls on an existing shortcut" is a description of the three, not
  evidence about any of them — it is equally true of `SetShortcutName`, which is the one that has **never** been
  measured. The sync writes the name in place too (`rewriteShortcutIdentity`), and nothing has established what that
  does to the appId; do not read the exe measurement as covering it.
- **Frontend API**: `@decky/ui` for Steam's components, and `frontend/src/api/host.ts` for what the panel gets from its
  host — it replaces `@decky/api`, which is no longer a dependency, so nothing imports that package. Three of the five
  things it replaces from `@decky/api` go over the backend's WebSocket (`endpoint` and the event pair); `definePanel`
  sits beside them and opens no socket. `toaster` pushes into Steam's own notification store and draws its entries
  itself, chained behind whatever already patches Steam's toast renderer (`docs/architecture/frontend-bundles.md`,
  "Talking to the backend"). `@decky/api`'s `routerHook`, Decky Loader's route installer, has no counterpart here:
  Tender's section reaches Steam's game page through a seam of its own —
  `frontend/src/bigpicture/patches/installGamePagePatch.ts`, documented at `docs/architecture/frontend-bundles.md`,
  "Tender's section on Steam's game page". The toaster does not reach Decky's loader API when one is present, and what
  decides that is not purity: it was the loader's own, and one that borrowed the loader's wherever it found it would
  behave differently on a machine with Decky from one without — which is the difference this program exists not to
  depend on. **The reference machine runs the loader** (measured: `plugin_loader.service` active and enabled,
  `127.0.0.1:1337` listening), so that borrowing would show up there rather than hide. `definePanel` is no longer inert
  beside them: `index.tsx` hands the factory it answers with to `qam/installEntry.tsx`, which calls it once and mounts
  the panel behind Tender's own Quick Access entry.
- **An endpoint is what `@route` marks**, `def` or `async def` alike: a caller can reach exactly the public methods on
  `Endpoints` that carry it, and `async` has no bearing on that. `@route` goes topmost, above any other decorator — the
  one placement `scripts/check_endpoint_parity.py` accepts. A public method on `Endpoints` must carry `@route`:
  `main.py` wraps every public method in the refusal translation, and `tests/test_endpoints_translation.py` fails on one
  without it. A stray `@route` is seen only by the gate, as a name the frontend's `endpoint("name")` declarations
  disagree with, so those declarations are the one judge of what should be reachable. The dispatcher's reading is held
  equal to the gate's by `tests/host/test_dispatch.py`.
- **RomM API quirks**: Filter param is `platform_ids` (plural). Cover URLs have unencoded spaces (must URL-encode).
  Paginated: `{"items": [...], "total": N}`. List calls page via `lib/romm_paging.py` and append
  `&with_char_index=false&with_filter_values=false` to skip aggregations the server otherwise computes on every request.
  Every `/api/roms` walk that pages with `offset` also sends `order_by=id`; a walk without it is not stable under a
  concurrent scan (why: `_WALK_ORDER` in `adapters/romm/romm_api.py`).
- **RomM minimum version**: Requires RomM >= 5.4.1, hard-rejected in `test_connection()` (`MIN_ROMM_VERSION` in
  `domain/identity.py`) — Tender is inert until the server is updated.
- **User-Agent on outgoing HTTP**: SteamGridDB **and** RomM behind Cloudflare Tunnel reject the default `Python-urllib`
  UA with 403, and GitHub's API refuses a request with no UA at all. The three adapters that talk to a server off this
  machine (`adapters/romm/http.py`, `adapters/steamgriddb.py`, `adapters/github_releases.py`) take a `user_agent: str`
  ctor param; bootstrap threads `<package name>/<version>`, both halves from `domain/identity.py` — no hardcoded name
  and no hardcoded version at the format site, so the UA comes from that one module rather than from two literals that
  could drift. The UA is everything `PACKAGE_NAME` reaches; neither the folder the program ships as nor the recovery
  bundles' root (`recovery/` inside Tender's folder) is decided by it. **The UA has no fallback and no failure mode**:
  both halves are constants, so a UA naming anything but this program is a code change, never a deployment accident.
  `VERSION` is machine-stamped by release-please (`x-release-please-version` on its line) and never edited by hand.
  `adapters/renderer_gc.py` also speaks HTTP — to Steam's debugger on `localhost` — and takes none.
- **Large payloads**: two caps, and they fail differently — `host/dispatch.py` refuses an encoded answer over ~12 MiB as
  an ordinary error for that one call, while `host/connection.py` closes the socket on a frame over 16 MiB, which
  rejects every call in flight with it. So a bulk payload is chunked rather than sent: per-item endpoints, and bulk
  lists paged (the library apply emits shortcuts in batches; the metadata cache loads page-by-page). Those numbers are
  ours and were chosen — the reference library's largest cover is 5,869,834 bytes, which base64 turns into 7,826,448
  (7.46 MiB), so a 4 MiB cap would have refused it silently.
- **No `BIsModOrShortcut` bypass**: the bypass counter was removed deliberately. Shortcuts return `true` (natural
  state); we own the game detail UI. Do not reintroduce a bypass.
- **`instanceof` against a DOM global is false in QAM and desktop-client code**: the panel's code runs in the
  **SharedJSContext** window while the nodes it renders belong to another document — the QAM view's own, or, for the
  desktop surface (dev build only), the desktop client's window (#1866). Two realms, confirmed live for the QAM view
  (the two documents do not share a URL, and neither can see the other's elements). What is **measured** is the
  `instanceof`: such a test is false for **every** node such code will ever see, so a guard written that way rejects
  everything and the feature is simply inert. Whether a constructor that takes the node as an argument —
  `new ResizeObserver(...)`, `new MutationObserver(...)` — also misbehaves across realms is **not** established here in
  either direction; those are named because taking the constructor from the node costs a property read, so the question
  need not be answered. Take the constructor from the node — `el.ownerDocument.defaultView` — as `WidePage` and
  `ScrollRegion` do, or from the window the code was handed, as the desktop surface's navigation watcher does. **The
  frontend suite cannot see the `instanceof` half**: two happy-dom windows answer it alike. A `MutationObserver` taken
  from the wrong window it CAN see — each happy-dom `Window` has its own, and a document's `defaultView` is its window —
  but not a `ResizeObserver`, which every happy-dom `Window` shares (`new Window().ResizeObserver` is the same class
  twice), and neither over a document built with `createHTMLDocument`, whose `defaultView` is null.
- **The Python CI runs is not the Python a device runs** — what that makes of a change under `_vendor/` is
  [`.claude/rules/vendored-assets.md`](.claude/rules/vendored-assets.md)'s, which loads only once a file there is read.
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
  answer and the firmware one; `label` is presentation (one `pcsx2_libretro.so` under two of them), a caveat's `token`
  is a third vocabulary again, and a caveat's `core` a fourth — the libretro SHORT name (`pcsx2`), which equals no
  identity. **The answer is entry-shaped**, and `answer.requirements` is a flattening that has already discarded
  `declaration`, `requirements_met`, `caveats`, `unread` and `refused` — so an entry-level question answered from it is
  answered from evidence that was thrown away before the question was put. Related and separate: `description` is
  deliberately outside the resolver's contract (it is the packager's prose from a core's `.info`), so it is not a field
  to render as a row's headline. Nothing mechanical carries any of this; the vocabulary overlaps ours almost exactly
  (`satisfied`, `required`, `present`, `cores`, `description` all exist on both sides and name different types), which
  is what makes a wrong reading look like a correct one.

## Current State

Latest release and shipped features: see `git tag --sort=-v:refname` and GitHub Releases. Roadmap and open work:
[GitHub Projects board](https://github.com/users/danielcopper/projects/2).

## Development

- **Build**: `pnpm -C frontend build` (Rollup -> `dist/globals.js`, `dist/index.js`, `dist/index-coexistence.js`, and
  `@decky/ui`'s licence text beside them)
- **Tests**: backend — `mise run test` (the whole suite across every logical CPU; `python -m pytest tests/ -q` runs it
  in one process, for debugging); frontend — `mise run test:frontend` (Vitest + happy-dom)
- **Coverage**: backend — `python -m pytest tests/ -q -n auto --cov=backend --cov-report=term --cov-branch`; frontend —
  `mise run test:frontend:coverage`
- **Lint**: `mise run lint` (import-linter, the `scripts/check_*` gates, markdownlint). No other mise task runs Ruff or
  basedpyright.
- **Gate**: `mise run gate` (the CI battery in one command; which PR checks it does not run:
  `docs/contributing/development.md`, "Full CI gate"; slow. Run before pushing.)
- **Setup**: `mise run setup` (installs JS + Python dependencies)
- **Package**: `mise run package` (production frontend build, then `scripts/package.sh` → `build/romm-tender-<V>.tar.gz`
  plus its `.sha256`). `bash install.sh --from build/romm-tender-<V>.tar.gz` installs that build the way a release is
  installed; over an existing install that is an update, which rolls back by itself when the new version does not
  answer. `install.sh --rollback` goes back by hand, and `install.sh --uninstall` takes it back out. What an update
  does, in order, is `docs/contributing/development.md`, "Running an installed one". **Neither is ever run against your
  own machine from a test** — every execution in `tests/scripts/` happens under a `tmp_path` HOME with a stub `PATH`.
  CI's build job runs the same two steps on every PR and checks what they produce (`scripts/check_release_tarball.py`),
  so the packager is exercised continuously rather than first at a tag.
- **Release**: release-please, configured as `release-type: simple` (`release-please-config.json`). A second job in
  `.github/workflows/release.yml` builds the tagged tree, packs it with the same `scripts/package.sh`, checks the result
  and attaches `romm-tender-<V>.tar.gz` plus its `.sha256` to the release — it builds from the tag rather than carrying
  an artifact over from CI. What it proposes is normally computed from `.release-please-manifest.json` plus the commits
  since — but **not today**: `release-as: "1.0.0"` overrides that computation on every run, so every release PR proposes
  1.0.0 until the key is removed. **Take it out once 1.0.0 has shipped**, or the version stops moving and nothing says
  so.

  **`main` is always releasable, and there are no hotfix branches**: a fault found after a merge — a database migration
  included, through a new rung — is fixed forward on `main` and ships as a normal patch release.

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
- **Run it**: `mise run dev` (build the panel, restart Steam into the window and display last chosen, then run the
  backend, which serves `dist/` and loads the panel into Steam). Needs `~/.steam/steam/.cef-enable-remote-debugging`.
  **There is no hot reload, and every deploy is a full one** — a rebuilt bundle reaches Steam only in a fresh JS
  context, and a new backend process strands the panel the old one loaded; the backend replaces that panel itself only
  once no app is running, so the tasks do not rely on it and restart Steam, which is what all but the two tasks named
  below do. `mise run dev:bpm [display]` / `mise run dev:desktop [display]` do what `dev` does into windowed Big Picture
  or the desktop client and remember that choice for `dev`; `mise run dev:bpm-reset [display]` /
  `mise run dev:desktop-reset [display]` only restart Steam (no build, the running backend loads the panel) and remember
  too. `mise run dev:backend` and `mise run dev:frontend [display]` are `dev` split in two: the first runs a backend
  against the Steam already there and restarts nothing itself (beside a panel an earlier backend loaded, the backend it
  starts has Steam reload its JS context once no app is running, which closes what is open), the second restarts Steam
  and leaves the running backend alone, so a rebuilt panel reaches a fresh context without a sync in flight being killed
  to get it there. `mise run dev:ui-scale` forces the Deck's metrics onto the window that is open. **Those five building
  tasks first ask who holds the single-instance lock**, and refuse before Steam is touched on the wrong answer: a
  backend already running for the four that start one (the restart would otherwise go ahead and the second backend exit
  one line later, leaving a fresh Steam with no panel), and no backend at all for `dev:frontend`, which starts none and
  would restart Steam into nothing. The two resets ask nothing — they are the way out when a refusal is in your way.
  Guide: `docs/contributing/frontend-dev-loop.md`
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

## Pipeline

- **Gate**: as "Development", "Gate", says (`mise run gate`).
- **Review checks**: `ruff check <files>`, `basedpyright <files>`, `pnpm -C frontend exec eslint <files>` (paths
  relative to `frontend/`)
- **Worktree task**: `mise run worktree-new` (a global mise task from the owner's dotfiles, not in this repository)
- **Agent rules**:
  - Scratch files never go inside the worktree: `lint:md` globs `**/*.md` without honouring `.gitignore`, and `tsc`
    checks all of `frontend/src` (Vitest runs any `*.test.ts` there), so a stray probe breaks the gate or a parallel
    typecheck. Use your scratchpad.
  - Run `mise run gate` once; it takes 8–15 minutes, longer under load. The Bash tool stops waiting after 600 s and
    leaves the run in the background: wait for that run (`tail --pid=<pid> -f /dev/null`) instead of starting another.
  - Before writing code in an area, read every `.claude/rules/*.md` file whose `paths:` covers it; "Path-scoped rules"
    above lists them.
  - Backend tests run under pytest-xdist: a new test shares no file, port or global state with another test.
  - Never raise a bundle size budget (`frontend/.size-limit.json`, `frontend/package-budgets.json`) to make the gate
    pass; report the overrun instead.

## Code Quality

CI runs SonarCloud (Quality Gate: 80% coverage on new code, 0 bugs, 0 vulnerabilities), Ruff, basedpyright,
import-linter, pytest-cov branch coverage, and the repo's `scripts/check_*` gates. **The checks that hold a
cross-cutting rule, and what each one enforces, are listed with the rule in the invariant register's area files** (the
map below) — the single inventory of them; do not duplicate it here.

## Invariant register — cross-cutting safety rules

The safety rules that span files, so no diff-scoped review (human or agent) sees the whole rule, each with its
enforcement tier: a `check`/`test` rule is enforced by the named artifact, a `prompt-only` rule only by review until a
check exists, and moves to the `check` tier once one does. A rule is never weakened to stay green, and a real drift is a
finding to triage, never an exemption. Every entry but the two below stands once, in one file per area under
`docs/architecture/invariants/`; how an entry reads and binds is that folder's
[index](docs/architecture/invariants/index.md). `scripts/check_invariant_register.py` holds this section, every headline
and every area file to a size limit.

**Before implementing or reviewing in an area, read that area's file** — every file whose area a change touches.

- Endpoints and the wire — `backend/main.py`, `frontend/src/types/reasons.ts`, `frontend/src/api/hostSocket.ts` —
  [endpoints.md](docs/architecture/invariants/endpoints.md)
- RomM HTTP — `backend/adapters/romm/`, `backend/lib/errors.py` —
  [romm-http.md](docs/architecture/invariants/romm-http.md)
- Install and update — `install.sh`, `backend/check.py`, `backend/*/update_*.py`, `scripts/package.sh` —
  [install-update.md](docs/architecture/invariants/install-update.md)
- The resolver — `backend/adapters/atlas_*.py`, `emulator_sources.py`, `retrodeck_folders.py`,
  `backend/services/downloads/`, `backend/services/rom_removal.py` —
  [resolver.md](docs/architecture/invariants/resolver.md)
- Library sync — `backend/services/library/`, `backend/domain/sync_diff.py`, `platform_skip.py`, `collection_skip.py`:
  the run, [library-sync.md](docs/architecture/invariants/library-sync.md); skips and stamps,
  [library-sync-skips.md](docs/architecture/invariants/library-sync-skips.md); representatives and removals,
  [library-sync-removals.md](docs/architecture/invariants/library-sync-removals.md)
- Firmware — `backend/services/firmware/`, `backend/domain/bios_status.py`, `firmware_*.py`,
  `services/tender_folder.py`, `frontend/src/utils/bios*.ts`: answers and verdicts,
  [firmware.md](docs/architecture/invariants/firmware.md); files, links and Tender's folder,
  [firmware-files.md](docs/architecture/invariants/firmware-files.md)
- Save-file sync — `backend/services/saves/`, `backend/domain/save_*.py`, `adapters/atlas_saves.py`,
  `adapters/gavel_native.py` — [saves.md](docs/architecture/invariants/saves.md)
- Removed-game cleanup — `backend/services/prune/`, `frontend/src/utils/prune*.ts` —
  [prune.md](docs/architecture/invariants/prune.md)
- The host and loading the panel — `backend/host/`, `*/controller_focus.py`, `frontend/src/utils/runningApps.ts` —
  [host.md](docs/architecture/invariants/host.md)
- Bundles and the start-up check — `frontend/src/boot/`, `frontend/scripts/`, `frontend/rollup.config.js`,
  `backend/domain/steam_internals.py` — [startup.md](docs/architecture/invariants/startup.md)
- The panel — `frontend/src/qam/`, `frontend/src/bigpicture/` — [panel.md](docs/architecture/invariants/panel.md)
- The desktop surface — `frontend/src/desktop/` — [desktop.md](docs/architecture/invariants/desktop.md)
- Code structure — layering, aggregates, Units of Work, tasks, clocks, module size —
  [code-structure.md](docs/architecture/invariants/code-structure.md)
- Cross-cutting — settings, directories, identifiers, naming, coverage, lock files, links, vendored trees, the register
  — [cross-cutting.md](docs/architecture/invariants/cross-cutting.md)

Two rules stand here, because a breach loses user data for good, any area can break them, and no check holds them fully:

- **Every destructive op has backup-or-confirm; never delete data that exists nowhere else** — test + prompt-only — what
  holds it, the deletes it covers and what stays prompt-only:
  [cross-cutting.md, "Backup or confirm"](docs/architecture/invariants/cross-cutting.md#backup-or-confirm)
- **Server-supplied path components pass `safe_join` (`lib/path_safety.py`)** — test + prompt-only — traversal tests per
  path builder; new call sites are prompt-only

When a change applies a guard / sanitize / backup / grouping pattern, sweep for sibling sites of the same pattern — the
area files are what that sweep checks against.

## Security

- NEVER read or use credentials from settings files (`~/.config/romm-tender/`) without explicit user permission
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
