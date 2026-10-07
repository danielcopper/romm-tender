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
2. **Cross-cutting invariants** — rules that span files, so no single diff shows the whole rule.
3. **Workflow** — how we work here; not derivable from the code.

Everything else is topic depth: `docs/` for humans, `.claude/rules/` for path-scoped coding conventions. A rule with a
mechanical check needs only its one-line statement here (CI carries the enforcement); a rule without one needs its full
statement here or in the rule file that owns its area, because nothing else will catch it.

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
  start-up check — [frontend-bundles.md](docs/architecture/frontend-bundles.md)
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
  is the injected `DebugLogger`. Neither is checked. A converted module (on the shape check's list) refuses by raising
  `Refused`; the check catches a failure dict built there, not a missing raise.
- `python-conventions.md` — Protocol suffixes by shape, `do_<verb>` vs. `_<verb>_io`, docstrings stating the contract
  rather than the behavior, and when a subfolder is justified. **No mechanical check exists for any of it.**
- `adapters-domain.md` — adapters own I/O, domain is pure, aggregate mutations are verb-named after the event
  (`adopt_baseline`, not `update_baseline`). The field-assignment ban is checked; the naming is not.
- `romm-http.md` — an unproven 404 must never become `RommNotFoundError`, which is deletion authority downstream: the
  entity proof is the default and only the three byte-stream fetches opt out. Tests pin both directions; nothing else
  does. Also owns the transport's reachability state: a new request method sends through `_urlopen` (checked) and passes
  `romm_origin=False` if it does not talk to RomM (not checked).
- `bootstrap-wiring.md` — the `main.py` / `bootstrap/` split, which module of `bootstrap/` new wiring belongs in, what
  the `Application` and `Endpoints` each hold, and why `run()` is synchronous.
- `host.md` — the process that hosts this backend (`backend/host/**`): the transport-vs-endpoint failure shapes, the
  token's one deliberate exception, the order of the three admission checks, claim-bearing events, where the size cap is
  judged, the served root, and what the injected expression may carry. **None of its seven rules has a mechanical check;
  each fails green** — the redaction one did exactly that, and only an assertion on stderr's own output caught it.
- `endpoints.md` — the `{success, reason, message}` failure shape and its two carve-outs, checked; and what makes a
  method an endpoint; which endpoints must be `def` is **not checked**.
- `vendored-assets.md` — `_vendor/` and `native/` are checksum-pinned upstream copies — verbatim, or verbatim plus a
  documented local patch — and every vendored tree carries its own manifest. The checksums are checked; the reflex to
  fix the upstream artifact instead of the copy is not. `defaults/` holds no vendored artifact since the BIOS registry
  left.
- `testing-backend.md` — test tiers, gate tests, vendored conformance vectors.
- `testing-frontend.md` — the backend-event harness, that `api/host` is stubbed suite-wide so no socket is ever opened,
  non-vacuous catch assertions.
- `comments.md` — a comment, docstrings included, is the exception: only an outside-world fact, a road not taken, or a
  constraint the code cannot express. A fact has one home, and how a value was arrived at goes in the commit rather than
  the file. Re-read the comment on the line you touch — a stale one is worse than none, because it is believed and
  nothing in the toolchain contradicts it. **No mechanical check exists.**

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
  things the panel takes from it go over the backend's WebSocket (`endpoint` and the event pair); `definePanel` sits
  beside them and opens no socket. `toaster` pushes into Steam's own notification store and draws its entries itself,
  chained behind whatever already patches Steam's toast renderer (`docs/architecture/frontend-bundles.md`, "Talking to
  the backend"). `@decky/api`'s `routerHook`, Decky Loader's route installer, has no counterpart here: Tender's section
  reaches Steam's game page through a seam of its own — `frontend/src/bigpicture/patches/installGamePagePatch.ts`,
  documented at `docs/architecture/frontend-bundles.md`, "Tender's section on Steam's game page". The toaster does not
  reach Decky's loader API when one is present, and what decides that is not purity: it was the loader's own, and one
  that borrowed the loader's wherever it found it would behave differently on a machine with Decky from one without —
  which is the difference this program exists not to depend on. **The reference machine runs the loader** (measured:
  `plugin_loader.service` active and enabled, `127.0.0.1:1337` listening), so that borrowing would show up there rather
  than hide. `definePanel` is no longer inert beside them: `index.tsx` hands the factory it answers with to
  `qam/installEntry.tsx`, which calls it once and mounts the panel behind Tender's own Quick Access entry.
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
- **RomM minimum version**: Requires RomM >= 5.3.0, hard-rejected in `test_connection()` (`MIN_ROMM_VERSION` in
  `domain/identity.py`) — Tender is inert until the server is updated.
- **User-Agent on outgoing HTTP**: SteamGridDB **and** RomM behind Cloudflare Tunnel reject the default `Python-urllib`
  UA with 403, and GitHub's API refuses a request with no UA at all. The three adapters that talk to a server off this
  machine (`adapters/romm/http.py`, `adapters/steamgriddb.py`, `adapters/github_releases.py`) take a `user_agent: str`
  ctor param; bootstrap threads `<package name>/<version>`, both halves from `domain/identity.py` — no hardcoded name
  and no hardcoded version at the format site, so the UA and the recovery root come from that one module rather than
  from two literals that could drift (the root additionally through `sanitize_package_name`, which is the identity for a
  name shaped like this one). Those two are everything `PACKAGE_NAME` reaches; the folder the program ships as is not
  decided by it. **The UA has no fallback and no failure mode**: both halves are constants, so a UA naming anything but
  this program is a code change, never a deployment accident. `VERSION` is machine-stamped by release-please
  (`x-release-please-version` on its line) and never edited by hand. `adapters/renderer_gc.py` also speaks HTTP — to
  Steam's debugger on `localhost` — and takes none.
- **Large payloads**: two caps, and they fail differently — `host/dispatch.py` refuses an encoded answer over ~12 MiB as
  an ordinary error for that one call, while `host/connection.py` closes the socket on a frame over 16 MiB, which
  rejects every call in flight with it. So a bulk payload is chunked rather than sent: per-item endpoints, and bulk
  lists paged (the library apply emits shortcuts in batches; the metadata cache loads page-by-page). Those numbers are
  ours and were chosen — the reference library's largest cover is 5,869,834 bytes, which base64 turns into 7,826,448
  (7.46 MiB), so a 4 MiB cap would have refused it silently.
- **No `BIsModOrShortcut` bypass**: the bypass counter was removed deliberately. Shortcuts return `true` (natural
  state); we own the game detail UI. Do not reintroduce a bypass.
- **`instanceof` against a DOM global is false in QAM code**: the panel's code runs in the **SharedJSContext** window
  while the nodes it renders belong to the QAM view's own document — two realms, confirmed live (the two documents do
  not share a URL, and neither can see the other's elements). What is **measured** is the `instanceof`: such a test is
  false for **every** node such code will ever see, so a guard written that way rejects everything and the feature is
  simply inert. Whether a constructor that takes the node as an argument — `new ResizeObserver(...)`,
  `new MutationObserver(...)` — also misbehaves across realms is **not** established here in either direction; those are
  named because taking the constructor from the node costs a property read, so the question need not be answered. Take
  the constructor from the node — `el.ownerDocument.defaultView` — as `WidePage` and `ScrollRegion` do. **The frontend
  suite cannot see this**: happy-dom has one realm, so the wrong global and the right one are the same object and every
  test passes.
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
- **Lint**: `mise run lint` (import-linter, the `scripts/check_*` gates, markdownlint). Ruff and basedpyright run only
  inside `mise run gate`.
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

Format: **invariant** — tier — enforced by. Each entry here is the binding statement of its rule; the long form of every
entry — why the rule exists, what breaks without it, and where it lives — is on
[docs/architecture/invariants.md](docs/architecture/invariants.md), in the same order.

- **Endpoint failures use `{success, reason, message}` (never `error` / `error_code`); a module on `CONVERTED_MODULES`
  builds no failure shape — a service there raises its refusal, which `Endpoints` answers; anything an endpoint raises
  but a refusal or a `RommApiError` stays a transport error** — check + test + prompt-only —
  `scripts/check_failure_shape.py --check` (returned dicts in `services/`, and every literal, `error_response` call or
  refusal-helper spread in a listed module), `tests/test_endpoints_translation.py` (on every route, a raised `Refused` /
  `DomainRefused` answers that shape and any other exception stays `backend_exception`; on one `def` and one `async def`
  route, a `RommApiError` answers `classify_error`'s reason and a returned `PartialFailure` is serialized into it; every
  route is the translating wrapper). Prompt-only: no entry is ever taken off `CONVERTED_MODULES`, and a further type
  joins `main._TRANSLATED` only by decision
- **A definitive 404 is `not_found`, never `server_unreachable` — a catch-all `except Exception` in `services/` may not
  bind a verdict key (`reason` / `status` / `recommended_action`) to a hardcoded `SERVER_UNREACHABLE`; route the
  exception through `classify_error`, or peel the 404 off with a sibling `except RommNotFoundError` where the verdict is
  a partial-success flag** — check — `scripts/check_404_not_unreachable.py --check`
- **A 404 becomes `RommNotFoundError` only when RomM's entity layer is proven to have answered it; only the three
  byte-stream fetches opt out** — test + prompt-only — `TestNotFoundDiscrimination` plus the per-call-site
  `test_generic_route_404_still_raises_not_found` trio in `tests/adapters/romm/test_http.py`; a fourth byte-stream
  fetch's opt-out is prompt-only — `.claude/rules/romm-http.md`
- **Every RomM request goes out through `RommHttpAdapter._urlopen`, the single point that clears the known-unreachable
  state** — check — `scripts/check_urlopen_choke_point.py` (AST call sites: an aliased `urlopen` or a `getattr` slips
  past it); unchecked: which requests may skip the retry ladder, and which pass `romm_origin=False` because they do not
  talk to RomM (`.claude/rules/romm-http.md`)
- **Frontend↔backend endpoint parity (names + arity)** — check — `scripts/check_endpoint_parity.py`
- **Every backend `emit` event name has a frontend listener, and vice versa** — check — `scripts/check_event_parity.py`
- **`settings.json` is written only by its owner (`adapters/persistence.py`)** — check —
  `scripts/check_settings_owner.py`
- **The backend never writes or removes `update-failure.json`; only the installer does — `install.sh` writes it on an
  automatic rollback and, with `"kind": "check"`, when its pre-install check refuses an update's new version, and
  removes it after an update whose new version answered** — test + prompt-only —
  `tests/adapters/test_update_failure.py::TestOnlyTheInstallerWritesTheRecord`: no backend module but
  `adapters/update_failure.py` and `domain/update_outcome.py` names `UPDATE_FAILURE_FILENAME` or the literal, and the
  adapter calls nothing named like a write, move or removal and opens nothing with a writing mode, nor with a mode or
  flags it cannot read off the call. Unseen by it: a call in `domain/update_outcome.py`, which may name the record and
  whose calls it does not read; a write under a name not on its list; a runtime mode passed as `Path.open`'s first
  argument; a record path assembled from pieces or handed in from elsewhere; a write through a helper in another module;
  a call reached through `getattr`; and a subprocess
- **`update-attempt.json` is written and removed by the backend alone, through `adapters/update_attempt.py`; the
  installer never touches it** — test + prompt-only —
  `tests/adapters/test_update_attempt.py::TestTheBackendIsItsOnlyWriter`: no backend module but
  `adapters/update_attempt.py` and `domain/update_install.py` names `UPDATE_ATTEMPT_FILENAME` or the literal, and
  neither `install.sh` nor a file under `scripts/` (`*.sh`) or `bin/` spells the literal. Unseen by it: a record path
  assembled from pieces or handed in from elsewhere, a write through a helper, and a subprocess. Prompt-only: only
  `UpdateInstallService` calls the adapter's `write` and `remove`
- **No journal line reaches the panel with an admission token in it: every line read back from the journal is shown
  through `domain/update_output.py`'s `output_section` or `installer_section`, which replace the value of every
  admission-token spelling they know with `[hidden]`** — test + prompt-only —
  `tests/domain/test_update_output.py::TestHideToken` pins the three spellings (a query's `token=…`, the same
  percent-encoded after `%3F` or `%26`, a JSON or Python mapping's `"token": "…"`) over a line in the start-up address
  line's shape, and `TestInstallerSection::test_the_token_is_still_hidden` that the installer's part hides it too; one
  service case (`TestAfterARollback::test_answers_the_installer_s_run_and_what_the_failed_version_printed`) and one
  contract case (`test_after_a_rollback_both_runs_are_answered_with_the_token_hidden`, the only one end to end) assert
  `[hidden]` in the failed version's part. Unseen by it: a token printed in any other shape — under another name,
  encoded twice, split across lines, or holding a character outside `secrets.token_urlsafe`'s alphabet. Prompt-only: a
  new reader of journal text answers through `output_section`, `installer_section` or `hide_token`, never with the raw
  `JournalEntry.message`
- **The pre-install check (`backend/check.py`) never builds under a live root: its code root is the tree being checked,
  every other root and the runtime directory are absent or empty when it starts, neither copy lands where it is copied
  from, and the live database is read without a file created or removed beside it, and with no write to one but a
  reader's marks in the WAL index** — test + prompt-only — `tests/test_check.py` (`TestItRefusesToBuildOnALiveRoot`, and
  the live home and runtime directory compared by names, modes and bytes around a check that builds and around one that
  does not), `tests/adapters/test_live_data_copy.py`, and
  `tests/scripts/test_install_sh.py::TestTheNewVersionIsCheckedFirst::test_the_check_runs_the_staged_tree_with_every_root_its_own`.
  Unseen by them: a constructor that writes only where RetroDECK's or Steam's own paths exist, which the test home has
  neither of; a write the build makes by `user_home` or an absolute path rather than under a root it was handed; and a
  write the copy's change detection cannot see (`adapters/live_data_copy.py` names it)
- **From the press that starts an install attempt until it fails, everything a pending RetroDECK migration refuses is
  refused with `blocked_by_update`, and so is the migration itself, asked ahead of every other rule — at every `hold` /
  `hold_start` call that names the migration rule, at `migrate_retrodeck_files`, and at every direct migration check
  outside the rules** — test + prompt-only — `tests/contract/test_conflict_refusals.py`
  (`test_every_call_site_naming_the_migration_rule_names_the_update_rule`,
  `test_the_update_rule_stands_without_the_migration_rule_only_where_pinned`,
  `test_every_direct_migration_check_is_answered_by_the_update_rule_too` with its control
  `test_the_read_behind_the_direct_check_sees_the_checks_it_is_about`,
  `test_an_update_in_progress_refuses_the_endpoint`, `test_an_update_in_progress_answers_before_every_other_condition`)
  and `tests/lib/test_conflict_rules.py`. Unseen by the source readers: a check reached under another name, through an
  alias or a helper. Prompt-only: the rule is taken in the same loop turn as the check the press passed, and given back
  only by an attempt that failed while this process runs, never on a guess while the installer may run
- **Where this program's directories are is resolved once from the environment, and every consumer reads them off
  `AppDirectories`** — prompt-only — `domain/app_directories.py` is the pure ladder (`TENDER_*`, then XDG, then the
  built-in defaults); `main.run()` resolves it once and hands it to `bootstrap()`, which derives nothing, and
  `RuntimeBundle` carries no directory. Re-derivable data goes under `cache_dir`, data that is not re-derivable under
  `data_dir`, and nothing under `bin_dir` is ours to remove. Nothing mechanical tells its seven `str` fields apart. A
  second read of `TENDER_CODE_DIR` is deliberate: `domain/update_release.py::resolve_update_source` asks whether the
  variable was SET, which `AppDirectories.code_dir` has already erased
- **The identifier's four homes are never derived from one another — in particular `APP_DIR_NAME`
  (`domain/user_data_location.py`) is never read from `PACKAGE_NAME` (`domain/identity.py`), and the database's file
  name `DB_FILENAME` (`bootstrap/adapters.py`) from neither** — test + prompt-only —
  `tests/domain/test_identity.py::TestTheIdentifierStaysInSeparatePlaces`, which requires `APP_DIR_NAME` and
  `DB_FILENAME` to be string literals (a transform over `PACKAGE_NAME` fails too); the reverse fold through an aliased
  import and the third home, the frontend's `SESSION_BREADCRUMB_KEY`, are prompt-only. The four homes are listed in
  `backend/domain/identity.py`'s module docstring
- **No name in the code calls Tender a plugin; a third-party name that must keep the word is excepted by name, whole,
  with its reason** — check — `frontend/eslint.config.js` (`no-restricted-syntax` over every identifier in every file
  ESLint lints, tests and config files included; exceptions `NAMES_NOT_ABOUT_TENDER`), kept live by
  `frontend/src/eslintNoPluginNames.test.ts`, and `tests/domain/test_identity.py::TestNoNameMisnamesTender` (every name
  the Python syntax tree of `backend/` less `_vendor/` and `native/`, `tests/` and `scripts/` binds, reads, declares or
  imports; exceptions `_NAMES_NOT_ABOUT_TENDER`); on either side an exception nothing carries any more fails too. Unseen
  by both: string literals (the `"plugin_version"` manifest key stays; a Python string annotation too), comments and
  docstrings, shell, file names, a name built at run time, and an excepted name put to a new use for Tender
- **Sync run-lifecycle (`sync_state` / `current_sync_id`) written only via `LibrarySyncStateBox` verbs** — check —
  `scripts/check_sync_lifecycle_owner.py`
- **A library-sync seam is held only by the module owning the job it belongs to — in `services/library/`, `active_core`
  / `disc_resolver` / `emulator_sources` by `shortcut_launch_resolver.py`, `renderer_rss` / `renderer_gc` by
  `session_budget.py`, and `artwork` by `cover_preparer.py` **and** `reporter.py`, the one confinement with two owners,
  never a third. The `service.py` façade may **pass** a seam on (as a call's keyword-argument value, or as a
  seam-annotated field declared on `LibraryServiceConfig`) and may not **use** one; reading that field in the façade is
  a finding** — check — `scripts/check_seam_owner.py` (an aliased seam, a `getattr`, or a seam passed positionally into
  a helper slips past it); unchecked: `shortcut_launch_resolver`'s install-path read UoW is never held across the
  `active_core` seam's calls (`check_uow_seam_nesting.py` catches only the inline form)
- **The resolver's installations are detected in one place, `adapters/emulator_sources.py`, through the one
  `RealMachine` the process keeps; every other adapter asks it, and a game's questions go to the source
  `domain/emulator_sources.py::answering_source` names** — test + prompt-only —
  `tests/adapters/test_emulator_sources.py::TestOnlyTheHolderDetects` (no other backend module imports `detect`,
  `every_installation`, `RealMachine` or an installation class from the resolver, star-imports it, imports the
  resolver's package as a module, or reads one of those names off it) and, for the last clause,
  `tests/domain/test_emulator_sources.py::TestAnsweringSource::test_retrodeck_answers_even_when_another_source_is_first_in_the_order`
  and `test_retrodeck_answers_with_emudeck_first_in_the_order` in each of the three adapters' tests —
  `tests/adapters/test_atlas_catalogue.py::TestWhichSourceAnswers`,
  `tests/adapters/test_atlas_firmware.py::TestDegradation` and
  `tests/adapters/test_atlas_saves.py::TestWhichSourceAnswers`. Unseen by the scan: a name reached through `getattr` or
  `importlib`. Prompt-only: no adapter keeps a handle or an answer past the reading it came through
- **A module declared read-only calls no repository write — `services/library/local_library_reader.py` to start** —
  check — `scripts/check_read_only_module.py` (the file's own calls only: a write behind a helper, a write passed as a
  bound method (`run_in_executor(None, uow.roms.save, …)`), an aliased handle, a `getattr`-reached repository, and a
  repository name not in its list pass green), with `tests/scripts/test_check_read_only_module.py` pinning its
  name-shape read/write split for every repository method; "repository writes are named as writes" is unchecked: a write
  named like a read (`get_or_create`) passes the gate
- **An emitted `sync_progress` frame stops a run (`running: False`) only with a terminal stage, and a terminal stage is
  only ever emitted with the run stopped** — test — `tests/services/library/test_terminal_frame_contract.py` (all of
  `backend/services`, `services/artwork.py` included; a helper-assembled frame or an aliased emitter slips past it)
- **The KIND of run a `sync_progress` frame belongs to is stated on it (`runKind`), never inferred from it — and a frame
  that states none is rendered as neither of the two answers** — test + prompt-only — `tests/services/library/`'s
  `test_sync_orchestrator.py::TestRunKindOnTheWire` and `test_state.py::TestRunKind`,
  `frontend/src/utils/syncRunView.test.ts` and `frontend/src/bigpicture/MainPage.test.tsx`. Prompt-only: every frame
  builder carries the key, every start path stamps the kind it is, and no reader treats a missing `runKind` as `preview`
  or `apply`
- **A press that starts a run clears the previous run's per-unit rows — unless that press is a RESUME, the one start
  they are still true for** — test + prompt-only — `frontend/src/bigpicture/SyncPage.test.tsx` ("a previous run's rows
  at the next press") and `frontend/src/utils/runUnitsStore.test.ts`; prompt-only: every start path in `useSyncPage`
  clears, and a resume is told apart only by `syncResumeState(stats).canResume` at the press
- **Every reader that decides or predicts a platform skip — the skip gate, the plan estimate and the preview's
  `restamp_platform_count` — reads the stamp through `domain/platform_sync_state.py::stamp_for_skip`, which answers a
  revoked stamp with none; the resume offer's `PlatformSyncStateRepository.has_any` applies the same rule in its query;
  removed-game discovery, the prune canaries, `reachable_count` and the recovery snapshot read the stamp raw; the
  end-of-run stale removal revokes a skip only on a platform outside `processed_platform_slugs`** — test + prompt-only —
  `tests/domain/test_platform_sync_state.py::TestStampForSkip`; the skip side by
  `tests/services/library/test_fetcher.py`'s `TestIncrementalSkipFromPlatformStamp::test_a_revoked_stamp_never_skips`
  and `TestPlanEstimates::test_a_revoked_stamp_predicts_no_skip_and_no_collapsed_count`,
  `test_sync_orchestrator.py::TestPreviewRestampPlatformCount::test_a_platform_whose_skip_was_revoked_is_counted`,
  `test_reporter.py::TestGetSyncStatsResumeInputs::test_a_platform_stamp_whose_skip_was_revoked_is_no_resume` and
  `tests/adapters/repositories/test_platform_sync_state.py::TestHasAny::test_a_revoked_stamp_is_not_counted`; the raw
  side by
  `tests/services/prune/test_preview.py::test_a_platform_whose_skip_was_revoked_still_discovers_its_dropped_rows`,
  `tests/services/prune/test_registry.py::TestCanaryRomIds::test_a_revoked_skip_still_offers_what_the_fetch_returned`,
  `tests/contract/test_prune.py::test_preview_discovers_on_a_platform_whose_skip_was_revoked` and
  `test_reporter.py::TestRegistryPlatformsReachableCount::test_a_revoked_stamp_still_rules_out_what_its_fetch_did_not_return`;
  the stale removal by `test_reporter.py::TestFinalizePerUnitRun` and
  `test_sync_orchestrator.py::TestPlatformTurnedOffAndBackOn`. Prompt-only: a new skip-side reader calling
  `platform_sync_state.get` raw, the recovery snapshot's raw read (no test pins it), and `has_any`'s query agreeing with
  `stamp_for_skip` — the query and the helper are each pinned alone and nothing joins them
- **A firmware answer nothing could establish is `unknown`, never `not_needed` — and the distinction survives every
  layer it crosses** — test + prompt-only — `tests/adapters/test_atlas_firmware.py`,
  `tests/domain/test_firmware_wants.py` and `tests/services/test_firmware.py::TestCheckPlatformBiosUnknown`;
  prompt-only: no caller folds the two values back together, and the reading is scoped to the launching emulator —
  `reading_complete_for` refuses `None`, an unread emulator the platform also offers deliberately does not withhold the
  answer, `declaration="packaged"` with an empty requirement list counts as unread, and a `retroarch-foreign-core` entry
  declares nothing and so adds nothing, while any other entry with no identity still contributes its declared files,
  unowned
- **A firmware row the RomM library does not hold (`on_server: False`) counts towards readiness, and never towards a
  download affordance or a progress ratio** — test + prompt-only — `tests/services/test_firmware.py` and
  `frontend/src/bigpicture/library/PlatformsTab.test.tsx`; prompt-only: the three axes stay apart — readiness
  (`domain/bios_status.py::count_required`, every required row), the progress ratio
  (`services/firmware/status.py::_bios_aggregates`, `on_server` rows only) and the download affordance (`isFetchable`,
  `frontend/src/utils/biosFetchable.ts`). The platform detail and the game page's BIOS tab call `isFetchable`, never a
  copy, while the game page's row-visibility rule stays its own; display-only readers (the On-disk `⊘` mark,
  `rowBelongsOnThisPage`) neither count nor gate
- **No BIOS answer outlives the page that asked for it** — test + prompt-only —
  `tests/services/test_game_detail.py::TestGetCachedGameDetailCarriesNoBiosAnswer` and the two contract cases in
  `tests/contract/test_game_detail_read.py`; prompt-only: no stored or cached BIOS answer is added back (`BiosChecker`
  keeps its one method)
- **Tender attaches a configured custom header to a RomM-origin request and to no other request it issues, and never
  over a header the adapter sets itself** — test + prompt-only — `tests/domain/test_custom_headers.py` and
  `tests/adapters/romm/test_http.py::TestCustomProxyHeaders`; prompt-only: every RomM-origin request method calls
  `_apply_origin_headers` and `download_external` never does, and the adapter adds its own headers after
  `_apply_origin_headers` — nothing pins that order. `Host` and `Content-Length` are refused only by `RESERVED_NAMES`. A
  cross-host redirect still carries the header and the bearer to the foreign host
- **`dist/globals.js` is never evaluated into Steam where Decky Loader is serving, and what decides that is read from
  the MACHINE rather than from the window** — test + prompt-only — `tests/host/inject/test_bundles.py` and
  `tests/host/inject/test_injector.py::TestBesideDeckyLoader`; the standalone panel carries the same sweep and is absent
  there too. Prompt-only: the file list comes only from `host/inject/bundles.py` over `host/inject/machine.py`'s answer,
  never from another caller or a window probe; the gap between the loader's server answering and Decky rendering stays
  on the safe side — "Tender loads first" is never used to close it
- **Where the globals bundle is loaded, its installer is CALLED between the import that defines it and the panel import,
  and the panel is not imported unless the report says every global it names is installed** — test + prompt-only —
  `tests/host/inject/test_bootstrap.py::TestItRunsUnderNode`, `test_bootstrap.py::TestTheInstallersName` and
  `test_bundles.py`; prompt-only: the stub report's keys (`installed`, `steamReady`) agree with `GlobalsReport` in
  `frontend/src/boot/steamGlobals.ts`
- **An injection that could not be observed is never counted as a crash, and this process answers for its own record
  before it reads one** — test + prompt-only — `tests/host/inject/test_watchdog.py` and
  `tests/host/inject/test_injector.py::TestDidTheInterfaceSurviveIt` and
  `::TestWhatThisBackendDoesToSteamIsNeverACrash`; prompt-only: `judge` writes its resolution back before it answers, an
  injection answers for this process's record before `judge` reads one, and the alive check closes a record without
  counting it whenever nothing was established (the debugger stopped answering, the backend is shutting down, only the
  renderer was open, a second injection began first, or this process took the interface down itself since arming)
- **This machine takes Steam's interface down to replace a stranded panel at most twice in ten minutes, across backend
  starts; a reload Steam does not refuse and the fallback's SIGTERM both count** — test + prompt-only —
  `tests/host/inject/test_reload_limit.py` and `tests/host/inject/test_injector.py::TestAcrossBackendStarts`;
  prompt-only: a new path that takes the interface down goes through `recovery.py`'s `_may_take_the_interface_down`
  first and calls `ReloadLimit.record` once it is under way
- **Tender's Quick Access entry composes with Decky's rather than going through it, and everything it binds to the Quick
  Access window is bound from inside that window's React tree** — test + prompt-only —
  `frontend/src/qam/quickAccessEntry.test.ts`; finding and patching the renderers is device-only. Prompt-only: marker
  and key are `tender`, never `decky`; nothing writes `window.__TABS_HOOK_INSTANCE` or calls its `add()`, and neither it
  nor a `decky`-marked entry is read as a sign Decky is present; no tab array held, no unpatch added; the entry moves to
  the tab array's end on every render pass; nothing binds to the Quick Access window at module scope
- **Tender's section reaches Steam's game page through the ROUTE component's `renderFunc`, and never through the page
  component's own `type`** — test + prompt-only — `frontend/src/bigpicture/patches/gamePageSeam.test.ts`; the install
  (`installGamePagePatch.ts`) is device-only. Prompt-only: the install patches every memo export whose `type` is a
  function, and the start-up check's `AppDetailsRoute` and `appDetailsClasses` cost `feature`, never `panel`, and no
  panel name moves to `feature` beside them (that renders a hole)
- **Aggregate state mutated only via verb-named methods (no field assignment)** — check —
  `scripts/check_aggregate_field_assignment.py`
- **No UoW-opening seam (ActiveCoreResolver, RelaunchOptionsResolver, uow_factory) is called while a UoW is open on the
  same path** — check — `scripts/check_uow_seam_nesting.py`, first seam family (a seam reached as a bound method,
  through a helper, a local alias or a nested `def`/`lambda`, inside a UoW opened through a factory attribute not ending
  in `uow_factory`, a `__call__`-only seam, and a seam whose implementation later grows a UoW open slip past it)
- **No file-I/O seam is called while a UoW is open — a Unit of Work wraps database reads and writes, never file or
  server I/O** — check — `scripts/check_uow_seam_nesting.py`, second seam family (`IO_SEAM_METHODS`, which carries each
  `__call__`-only seam's bound attribute); one `# pragma: no uow-check` suppresses the line for both families.
  Unchecked: a seam that list does not name — `SteamConfigStore.grid_dir()` and `check_retroarch_input_driver()` today —
  or whose implementation later grows a file read; a seam passed as a bound method (how `read_shortcut_exes` is
  reached), behind a helper or a local alias, or inside a nested `def`/`lambda`; a UoW opened through a factory
  attribute not ending in `uow_factory`
- **A shell function whose value is taken with `$(...)` never reaches `exit` — it answers, and its caller aborts** —
  check — `scripts/check_shell_answer_functions.py` over `install.sh`, `scripts/package.sh`, `bin/tender-rom-launcher`
  and every `*.sh` under `scripts/` and `bin/`; its blind spots are listed in its docstring
- **Services never call clocks / sleep / uuid / random directly (inject the Protocol)** — check —
  `scripts/check_cosmic_call_bans.sh`
- **No module in `services/`, `bootstrap/`, `adapters/`, `domain/`, `lib/` or `models/` crosses the ~1000-LOC
  decomposition threshold, and the ones already over it may not grow** — check — `scripts/check_module_size.py` (which
  also fails until a module that shrinks under the threshold leaves the list); a grandfathered ceiling goes up only for
  a change that adds no code, with the reason at its `ALLOWLIST` entry, and no entry is ever added to `ALLOWLIST`
- **Service-independence contract list stays complete** — check — `scripts/check_service_independence_contract.py`
- **Layer import direction (services ↛ adapters, adapters ↛ services, …)** — check — `.importlinter` (`lint-imports`)
- **Frontend direction: `frontend/src/utils/` and `frontend/src/api/` never import either surface
  (`frontend/src/bigpicture/`, `frontend/src/desktop/`) or `frontend/src/shared/`; `shared/` never imports either
  surface; the two surfaces never import each other; and no `frontend/src/` module takes part in an import cycle** —
  check — `frontend/eslint.config.js` (`import-x/no-restricted-paths`, `import-x/no-cycle`), kept live by
  `frontend/src/eslintBoundaries.test.ts`; code both surfaces need moves DOWN — UI into `shared/`, the rest into `api/`,
  `utils/` or `types/` — never sideways; type-only imports are not edges
- **No bare `# type: ignore` / blanket suppressions** — check — `scripts/check_no_bare_ignores.sh`
- **A transport failure and an endpoint's own failure never arrive in the same shape, on either end** — test +
  prompt-only — `hostSocket.test.ts` for the frontend half (`frontend/src/api/hostSocket.ts`); the backend half is
  `.claude/rules/host.md`, the vocabulary `backend/host/protocol.py`. Prompt-only: the Python and TypeScript spellings
  of `connection_lost` must agree — nothing holds them equal
- **The standalone panel bundle carries `@decky/ui` and the coexistence one carries none of it** — check —
  `frontend/scripts/check-bundle-shape.mjs`, over the built artifact; which bundle the injector loads
  (`backend/host/inject/bundles.py`) it does not see
- **Every third-party package a bundle carries has a budget of its own in `frontend/package-budgets.json`, and a package
  without one, a package over its budget, or a budget for a package the bundle no longer carries fails the package
  check** — check — `frontend/scripts/check-package-budgets.mjs` (`pnpm -C frontend check:packages`), over the record
  the build writes to `frontend/bundle-packages.json`, refused when it is older than `dist/`; the panel's own code has
  no budget of its own and only `frontend/.size-limit.json`'s total watches it
- **Tender's three React globals are spelled exactly the way Decky Loader spells them** — test —
  `frontend/src/boot/steamGlobals.test.ts` against the pinned `decky-globals-block.txt`; the pinned copy itself is held
  by hand, its provenance header naming the upstream commit
- **Every value the panel imports from `@decky/ui` is classified by the start-up check** — test —
  `frontend/src/boot/steamModules.test.ts`, which fails on an imported name in none of the four lists (`STEAM_LOOKUPS`,
  `UNVERIFIABLE`, `ASKED_LIVE`, `PACKAGE_OWN`) and derives `ASKED_LIVE` through
  `frontend/src/test-utils/jsFunctionScanner.ts`. Every entry of the check reads the module registry or a bootstrap
  global, never what Steam has mounted or focused. Unchecked: whether any other classification is true — a real search
  is never parked in `UNVERIFIABLE` to quieten the check — and a runtime-state reader reached through an arrow export, a
  re-export, another module's helper or a nested function can sit in `STEAM_LOOKUPS` unflagged
- **Whether every search answered and whether the panel may MOUNT are two questions, and a miss that costs less than the
  panel never takes the interface off the air** — check + test + prompt-only — the type (`SteamLookup.absenceCost` is
  required), `frontend/src/index.test.tsx`, and `frontend/src/boot/steamModules.test.ts`, which requires a non-blocking
  name `@decky/ui` does not export to be one Tender resolves itself (so the `SP_*` globals stay blocking). Prompt-only:
  `checkSteamModules` derives `panelMayMount`, and `index.tsx` gates the fallback page on it and otherwise logs
  `describeSurvivedMiss`, which names no repair of its own; `absenceCost` is read only as `!== "panel"`; the notice on
  Main that toasts are unavailable comes from `notificationsMissing` over `NOTIFICATION_LOOKUPS` names, never from a
  `feature` cost; a name leaves `panel` only once its every consumer is read
- **The start-up failure page names the copy of `@decky/ui` that actually ran the search that missed, and the repair
  that follows from it** — check + test + prompt-only — `frontend/scripts/check-bundle-shape.mjs` checks the stamp the
  build puts on each bundle (`BUNDLE_KIND`, standalone or coexistence) and that `globals.js` carries none; every
  `SEARCH_OWNERS` verdict's sentence is pinned by `frontend/src/boot/steamModules.test.ts` and
  `StartupFailurePanel.test.tsx`. Prompt-only: `rollup.config.js` serves the stamp, `boot/searchingCopy.ts` reads it,
  `boot/steamModules.ts` words it, and `index.tsx` resolves the answer once for both the log line and the page; the copy
  is read off the stamp, never a runtime probe; a miss confined to names `@decky/ui` does not export names no copy and
  no repair, which leaves `ControllerGlyph`'s repair unnamed — restoring it takes a third axis, never a reworded answer;
  `in DFL` is never asked about a name `@decky/ui` never exported; reading `DFL` never throws, and an unreadable one is
  nothing established, not an absence; the version beside the name is enrichment only — every sentence is complete
  without `_versionInfo.current`, and `remote` is never consulted
- **A coverage exclusion names a property of the code, never a place: every frontend-scoped entry stands in BOTH
  `frontend/vitest.config.ts`'s `coverage.exclude` and `sonar-project.properties`' `sonar.coverage.exclusions`, every
  file entry carries its reason as a `// coverage-exempt:` marker in the file's own first lines, and every marked file
  is listed** — check — `scripts/check_coverage_exclusions.py`; unchecked: only type declarations and test utilities go
  into `frontend/src/types/` and `frontend/src/test-utils/` (a real module there is exempted by its place, unmarked),
  and a marker's sentence must be true
- **Every pinned version in a lock satisfies its `.txt` source constraint (`requirements-dev.*` at the root,
  `docs/requirements.*` beside the docs)** — check — `scripts/check_lock_sync.py`
- **Every local markdown link in tracked docs resolves (file target + heading/attr-list anchor)** — check —
  `scripts/check_markdown_links.py`
- **Every RomM minimum stated for a reader matches the enforced `MIN_ROMM_VERSION`** — check —
  `scripts/check_romm_min_version.py`, over the statements its `CLAIMS` list names; unchecked: a restatement not yet
  added there, and the examples above the floor (`5.3.1-beta`, `5.4.0-alpha.1`) beside those statements. ADRs are out of
  scope: frozen history
- **Every tree under `backend/_vendor/` is pinned by the `<pkg>.SHA256SUMS` beside it: every manifest entry under
  `<pkg>/` matches the vendored file's digest, the vendored file set EQUALS the manifest's set restricted to that
  prefix, and a package directory with NO manifest is a failure** — check — `scripts/check_vendored_trees.py`;
  unchecked: the manifest itself, regenerated only as the last step of a deliberate re-copy and never to answer a
  failing gate; unseen by the check: a single-module file under `_vendor/` and files below a symlinked directory.
  `backend/native/` is pinned by its own `sha256sum -c`
- **The release tarball is what the installer expects — one top-level `romm-tender/`, the files an install starts from
  plus the version file, the installer an installed tree rolls back with and the licence texts a distributed copy
  carries, nothing the packager prunes, a sidecar `sha256sum -c` accepts** — check — `scripts/check_release_tarball.py`,
  in CI's build job and in the release job; it reads names, modes and digests and starts nothing (its docstring states
  what that misses)
- **A one-time step — an installer move such as the covers' move into the cache root, a backend backfill behind a
  `kv_config` marker, a rung of the database's `user_version` ladder or of the settings' version ladder — stays safe to
  run again and stays in every later release, so an update that skips releases still gets it; one leaves only
  deliberately, with that release's notes naming the oldest version it can be updated from directly** — test +
  prompt-only — `tests/scripts/test_install_sh.py::TestAnUpdateThatSkipsARelease` proves the covers move runs on every
  update, over data left in the older layout; every backend step, and the removal of any step in a later release, is
  prompt-only
- **Server-supplied path components pass `safe_join` (`lib/path_safety.py`)** — test + prompt-only — traversal tests per
  path builder; new call sites are prompt-only
- **A firmware row's presence comes from the resolver wherever the resolver declared it; Tender's own filesystem probe
  covers only three leftovers** — prompt-only — `services/firmware/demand.py::FirmwareDemand.is_downloaded` is the
  single crossing point, and nothing enforces it. The three leftovers: a library file with no placement in the
  platform's catalogue, a placement Tender cannot honour, and the already-there check before a download (the batch and
  the per-row fetch). `present is None` reads as absent. A withheld verdict is not an absence: its cause is read off the
  row's caveat codes and a declared file's `checked`, never off the verdict, and nothing checks that a consumer keeps
  `checked`'s values apart — a file the emulator read and did not recognise is never worded "could not be checked", and
  `refused` is not a withheld verdict
- **A firmware row's verdict is `BiosFileEntry.satisfied`, and for a folder declaration it is what the folder HOLDS —
  never that the folder is there** — test + prompt-only —
  `tests/services/test_firmware.py::TestAFolderRequirementIsAnsweredByItsContents`,
  `tests/domain/test_firmware_wants.py` and `tests/adapters/test_atlas_firmware.py`; prompt-only: no consumer reads
  `downloaded` for a folder row or folds a `None` verdict into `False`. With no check at all: a folder declaration
  (`declared_kind`) is never offered as a download — `FirmwareDownloader.download_firmware(firmware_id)` does not refuse
  one yet
- **The console's own firmware demand is a value of its own (`system_image`) and is never folded into a count, and the
  resolver's `system_firmware: null` reaches it as a claim about nothing; it answers only for a launching emulator that
  states no one-of group** — test + prompt-only — `tests/domain/test_bios_status.py` (`TestClassifySystemImage`,
  `TestTheVerdictOverTheSystemImage`), `tests/services/test_firmware.py::TestTheConsolesOwnFirmwareDemand`,
  `frontend/src/bigpicture/BiosTab.test.tsx`, `frontend/src/bigpicture/library/PlatformsTab.test.tsx`, and the wording
  locks `frontend/src/utils/biosSummary.test.ts` / `frontend/src/utils/biosHeldRatio.test.ts` over
  `frontend/src/test-utils/componentSources.ts`. Prompt-only: demand comes from the resolver's packaged table
  (`system_firmware`), presence from our rows, never from `requirements_met`; every surface words these states through
  `frontend/src/utils/biosSummary.ts`, sole holder of their order (`"absent"` before `bios_level` `unknown`; given
  `required_withheld` and `"unsettled"` both, the withheld row); `PlatformDetail`'s `nothingEstablished` excludes
  `"unsettled"` as it does `required_withheld`, and decides wording alone; the play-row badge
  (`frontend/src/utils/playSection.ts::extractBiosInfo`) rises on `"absent"`, never on `"unsettled"`; a new wording, or
  one in a `.ts` helper (`frontend/src/bigpicture/panelState.ts`), escapes the locks. Where the launching emulator
  states a one-of group, `classify_system_image` answers `not_demanded` and the group speaks instead
- **A one-of group is ONE requirement, judged by the regions its options serve — and an option never makes its row
  required** — test + prompt-only — `tests/domain/test_firmware_groups.py` (with an invented console in
  `TestAnyConsoleIsJudgedTheSameWay`), `tests/adapters/test_atlas_firmware.py::TestOneOfGroups`,
  `tests/domain/test_bios_status.py::TestAOneOfGroupIsOneRequirement`, `tests/services/test_firmware.py`
  (`TestAOneOfGroupIsOneRequirement`, the group cases of `TestOnePlatformOneEmulator` / `TestOneRomOneEmulator` /
  `TestDownloadRequiredFirmware`), and `frontend/src/utils/biosSummary.test.ts` / `biosGroup.test.ts` /
  `playSection.test.ts` pin that `required` comes off plain declarations only, that a group counts once and only `met`
  counts as held, that the badge leaves `required_partial` out, that `Download required`'s count and its download are
  one set (`fetched_as_required`), that the game page judges the ROM's own console regions (`console_regions_of`), and
  that an invented console is judged by the same rules. Prompt-only: each of those at a NEW site — a consumer reading an
  option's `need` as `required`, a second download rule, a judging that names a console, an emulator or a region; the
  state never travels as a colour; `unknown` is never read as `met` or `unmet`; which caveats name a group's other
  regions is `adapters/atlas_firmware.py::_UNCHECKED_REGION_CODES`'s. Unseen by every test: a region whose every name
  the resolver refused carries neither an option nor such a caveat, so a group can read `met` over it (emu-atlas#556)
- **Which emulator a set of answers is about is ONE pick per scope — a platform's, and a ROM's — and every answer in
  that scope is a projection of it** — test + prompt-only — `tests/services/test_firmware.py`
  (`TestOnePlatformOneEmulator`,
  `TestDownloadRequiredFirmware::test_it_fetches_what_the_platforms_own_pick_calls_required`,
  `TestTheAnswerNamesTheEmulatorItJudgedBy`, `TestOneRomOneEmulator`); prompt-only: every platform site resolves through
  `domain/emulator_commands.py::resolve_platform_option`, `.label` and `.emulator` come off one call, the key is the
  emulator identity and never `.core_so`, and both game-page services ask `ActiveCoreReader.active_emulator_for_rom`,
  never `active_core_for_rom`; the one-of groups an answer judges are that same pick's (`groups_for(identity)`). Tests
  on the bare `FakeCoreInfoProvider` exercise a weaker default than the live adapter
- **A platform's BIOS answer is asked for one platform at a time, and a row that has not got one yet is never rendered
  as a row nothing could be established for** — test + prompt-only —
  `frontend/src/bigpicture/library/PlatformsTab.test.tsx` and `tests/contract/test_firmware_status_read.py`;
  prompt-only: no state-bearing field returns to the overview payload, no rendering path reads `firmware === null`
  instead of `firmwareState`, a later failure never takes back an answer already held (`firmwareStale` beside the
  state), and the `alive` guard in `usePlatformsPage`'s `accept` (no write after unmount) is unpinned
- **The whole-machine firmware inventory is never asked with content verification, and the per-platform reading is never
  asked without it** — prompt-only — `firmware_inventory()` (`FirmwareResolver`, `AtlasFirmwareAdapter`) unverified,
  `firmware_for_system(<system>)` (`FirmwarePlatformResolver`, `AtlasPlatformFirmwareAdapter`) with `verify=True`;
  nothing detects either direction
- **No sentinel objects on the wire — explicit JSON-representable tagged values only** — prompt-only — nothing
  mechanical detects a new one
- **Every destructive op has backup-or-confirm; never delete data that exists nowhere else** — test + prompt-only —
  save-file removals route through the `.romm-backup` funnel (`MatrixExecutor.quarantine_local_file`; the removed-game
  cleanup's claimed variant is `PruneSaveSupport.quarantine_prune_saves`); every other delete path carries the rule
  unmechanized. Two deletes take the confirm leg: the removed-game cleanup's unselected installed ROM content
  (per-candidate opt-in stated in the dialog; the row goes only after a fresh 404), and the adopt dialog's **replace**
  exit for the ROM (both sides shown, a content check offered, a second confirmation); an adoption's Overwrite of save
  and savestate files, and a collision while following a moved save directory (`services/saves/save_directory.py`), take
  the backup leg through the same funnel. The installer's two rollbacks: one by hand takes the backup leg — the database
  files and `settings.json` it replaces are copied to `rollback-backup/` under the data root first, and a copy that
  cannot be made refuses it — while the automatic one discards only what a version never seen to answer wrote
  (`tests/scripts/test_install_sh.py`, `TestRollingBackByHand` and `TestAnUpdateThatDoesNotStart`). A settings file
  Tender does not read (older than version 13, or without a usable version) is written over with the defaults and takes
  neither leg: 1.0.0 is a breaking release, and everything it held can be entered again. An install record goes only by
  something the user does (Uninstall, Uninstall all ROM files, "Forget this download", a download or adoption of another
  version of the game, Clean Up Removed RomM Games), never by the start-up step, which only reports a missing file;
  Forget deletes no file and refuses while the file or folder exists
  (`tests/services/test_startup_healing.py::TestReportMissingInstalls`, `tests/contract/test_missing_download.py`).
  Prompt-only: a new path that deletes `rom_installs` rows is one the user starts
- **A BIOS file is deleted only where a `downloaded_bios` record names it under one of the platform's firmware slugs,
  and only at the path that record holds** — test + prompt-only —
  `tests/services/test_firmware.py::TestDeletePlatformBios`, `::TestDeleteOneBiosFile` and
  `TestGetFirmwareStatusDeletableCount`; prompt-only: a delete iterates the records, never a status list, and is never
  authorised by `downloaded` or `on_server`; `deletable_count` (per platform and per row, distinct paths) counts records
  still on disk and alone gates the Delete offer, and a folder row's counts the distinct recorded files beneath it; the
  three buttons share `PlatformBiosDeleter._delete_recorded_io`, never a copy of it
- **Every read-mutate-write of a `RomSaveSyncState` runs under `SyncEngine.rom_lock(rom_id)`** — prompt-only — sync
  paths, `get_save_status`, and the three slot mutations hold the lock; mechanize via a `rom_save_sync_states.save`
  call-site audit
- **Which files a game's save consists of is the EMULATOR's answer, read live, and four of its five states refuse the
  sync — no probe, no state written** — test + prompt-only — `tests/adapters/test_atlas_saves.py`,
  `tests/domain/test_save_answer.py` and `tests/services/saves/test_save_shape_gate.py`, where each absence test stands
  beside a control that asserts the probe does happen, and every per-system pin names the extension it asked with
  (`(system, extension)`). The answered save directory is not sync state: its own table (`answered_save_directories`)
  may record it for a refusing answer. Prompt-only: every per-ROM entry point refuses through
  `sync_engine/_shape_refusal.py` and every sync path crosses the `MatrixExecutor.sync_rom_saves` backstop; the five
  write paths (`slots/switching.py`, `copies.py`, `versions.py`, `slots/setup.py`, `sync_engine/rollback.py`) each
  refuse on the answer's `sync_directory`, never on `saves_dir` — `test_save_shape_gate.py` pins those five, not the
  list; the sync, write, delete, count and status paths that `docs/architecture/save-file-sync-architecture.md` lists
  call `SyncEngine.follow_save_directory` before they look at local files; configuration-role files are held back
  through `SaveAnswer.synced_files` by the denial `CONFIGURATION_ROLES`, never an allow-list, a directory move carries
  `owned_files`, and nothing reads `components` directly; a rendering reads `unestablished`'s shape and
  `content_installed` beside the state; the question carries the ROM's real content path, and system and path are
  decided only at `RomInfoService._installed_answer`, `._uninstalled_answer` and `services/rom_adoption/renamer.py`;
  nothing caches an answer
- **Per-slot server reads/deletes go through `domain/save_slot.py` (legacy omits `&slot=`, client-filters)** —
  prompt-only — `get_slot_saves` / `get_slot_delete_info` / `delete_slot` / `list_file_versions` / `rollback_to_version`
  use `slot_query_param` + `save_in_slot`; a legacy delete is refused up-front
- **Every save-sync decision comes from `compute_sync_action` (via `list_saves`), never the `negotiate` op list; every
  automatic upload POSTs `overwrite=false` (409-backstopped); `overwrite=true` only from an explicit `keep_local`** —
  test + prompt-only — `tests/adapters/test_gavel_native_decision_table.py`,
  `tests/adapters/test_gavel_native_property.py` and the contract 409 tests (`tests/contract/`); new upload/dispatch
  call sites are prompt-only
- **Both save-sync decisions run in the compiled gavel core, reached only through the `ComputeSyncActionFn` /
  `ResolveUploadConflictFn` seams; `domain/sync_action.py` holds only the `SyncAction` vocabulary the core answers in,
  so a change to either decision is a contract change carried by re-copied gavel vectors** — test — both vendored vector
  families run against the core (`tests/adapters/test_gavel_native.py`,
  `tests/adapters/test_gavel_native_table_vectors.py`); the `.so` and the vectors are pinned to one upstream release tag
  and bumped together
- **`applied_launch_options` is written only by the six recorded-state writer sites (sync ack-commit, download-complete,
  adopt-complete, uninstall, home-migration, version-switch), each recording the exact command the frontend wrote;
  excluded from the sync UPSERT; the only sanctioned reset is Force Full Sync's clear-to-NULL** — test + prompt-only —
  each writer site carries a value-exact test; new launch-options write paths are prompt-only — mechanize via a
  `set_applied_launch_options` / `record_applied_launch_options` call-site audit. Download- and adopt-complete share one
  code site, `RomInstallRecorder.do_record_applied_launch_options`; the uninstall site —
  `RomRemovalService._drop_install_record` for one ROM, `_uninstall_all_roms_io` for the bulk run — also serves "Forget
  this download" (`forget_download`), which is therefore not a seventh writer site
- **An abandoned-chunk stash's whole-unit apply staging (`pending_sync` / `pending_all_roms` / `pending_cover_sources`)
  is never mutated while the stash is pending (box IDLE) — every run-entry path passes `try_begin_run`, which clears the
  stash before any staging write** — prompt-only — mechanize via a staging-writer call-site audit
- **An apply chunk's ack identity — `active_unit_id` / `active_chunk_index` and a fresh `unit_complete_event` — is
  stamped on the box BEFORE that chunk's `sync_apply_unit` is emitted, with nothing awaited in between** — test +
  prompt-only — `tests/services/library/test_chunk_dispatcher.py::TestAckIdentityPrecedesTheEmit` pins the ordering
  inside `ChunkDispatcher`; the round trip through `services/library/_state.py` and `SyncReporter.report_unit_results`
  is prompt-only
- **Every path on which the user answers the preview question leaves a live snapshot on neither side — the
  pending-preview store (`frontend/src/utils/pendingPreviewStore.ts`) and the backend's `pending_delta`** — prompt-only
  — Apply (`applyPreview`), Cancel (`cancelPreview`), Refresh (`computePreview(true)`) and a successful Force Full Sync
  (`forceFullSync`) clear the store and tell the backend; a cancel landing just after a preview was staged discards
  server-side. Every answer path lives on the Sync page, where the change table is; Main answers no preview. Nothing
  mechanical can tell that a handler is an answer path
- **A prune run's claim reservation and its refusal of every conflicting endpoint happen in one atomic hold of the prune
  conflicts' lock (the preview rebuild does not), and frontend-owned Steam work holds a heartbeated lease through every
  continuation's final write, and a continuation with an owner is tombstoned by its owner's teardown** — test +
  prompt-only — prune service and prune conflicts race tests + contract endpoint-entry matrix
  (`tests/contract/test_conflict_refusals.py`), and `frontend/src/utils/pruneLease.test.ts` for the heartbeat and the
  owner's tombstone; new conflicting entry points are prompt-only
- **A removed-game cleanup's run claim is registered on the prune conflicts before the start's reservation is given
  back, so the two windows overlap and no conflicting endpoint runs in a gap between them** — test + prompt-only —
  `tests/services/prune/test_service.py::test_a_started_run_holds_its_run_claim_until_it_ends` (registered by the time
  `start_prune` returns) and
  `tests/contract/test_prune.py::test_a_cleanup_refuses_conflicting_endpoints_from_its_start_to_its_end` (refused across
  a real start); the reservation is taken by `hold_start` in `PruneService.start_prune`, around the whole start.
  Prompt-only: the start's body is reached only through that `hold_start`
- **A prune frontend action mutates Steam only after atomically claiming its exact run/token/discriminant/binding;
  repeats are idempotent and an outcome lost in transit is ambiguous, never success** — test + prompt-only — prune
  service claim tests + `frontend/src/utils/pruneActions.test.ts`; new action kinds are prompt-only
- **Every installed-content mutation is authorized by a descriptor-relative no-follow claim (root identity, descendant
  identities, and — where a bundle exists — regular-file hashes) revalidated immediately before it, never by a path
  re-lookup; refusal, partial mutation and ambiguity are reported, never rewritten into success. The hashes follow the
  **bundle**, not the caller: a source a sealed bundle holds consumes the bundle's digest-bound claim, a source it did
  not capture seals a fresh content-bound one, and a removal with no bundle seals identity-only
  (`claim_source(..., digest=False)`) — the only claim that may adopt interrupted `.{basename}.romm-prune-*` staging,
  and only where a surviving install row proves the path. Content-bound and identity-only claims alike keep staging
  rename, mount checks, no-follow traversal and exact-identity revalidation under writer exclusion across each unlink; a
  content-bound removal leases the whole tree up front, an identity-only directory per unlink, so a writer mid-loop
  yields a reported partial removal** — test + prompt-only — descriptor-path, recovery-adapter, real RomRemovalService,
  and prune contract tests; new mutation adapters are prompt-only
- **Every prune frame carries its originating preview ID; only a matching pending preview may adopt a run, and an
  accepted contiguous terminal result seals it against every later frame** — test + prompt-only — prune service frame
  tests + `frontend/src/utils/pruneStore.test.ts`; new prune frame types are prompt-only
- **Every destructive RomM proof is bound to one canonical server-origin/token-origin/user namespace from preview
  through every exact-ID request; a namespace change is uncertainty, never a 404 deletion authority** — test +
  prompt-only — prune service namespace-race tests; new destructive RomM proof paths are prompt-only
- **Every write into per-rom detail state that crosses an `await` is bound to a rom identity — the store
  (`frontend/src/utils/gameDetailStore.ts`) via `writerForRom`, or the answer's own `rom_id` in `applySaveStatus`; the
  panel's modules `frontend/src/bigpicture/panelState.ts`, `frontend/src/bigpicture/panelEvents.ts` and
  `frontend/src/bigpicture/panelTabContent.tsx` via `RomBinding` (`bindRom`, `bindRomInState`);
  `frontend/src/bigpicture/AchievementsTab.tsx` by its rom-id key. Two answers for the SAME rom are ordered by a
  sequence taken at issue (`loadSeq`, `takeReadTicket`). Ordered but unbound: the two identity writes (the store's
  `loadDetail`, the panel's `loadData`) and the panel's lazy SAVES-tab slot load
  (`frontend/src/bigpicture/panelSlotsLoad.ts`, raw setter, `slots` ticket); neither bound nor ordered: the store's
  `cached.bios_status` fold. The panel's `handleBiosChange` is bound and ordered (`bios` ticket). The play button is NOT
  covered** — test + prompt-only — the panel's bound sites each carry a version-switch test
  (`frontend/src/bigpicture/RomMGameInfoPanel.test.tsx`); the store side and every new write site are prompt-only
- **Every row a reader must be able to reach on a QAM page is a row Steam can focus — a toggle, a button, or a
  `Focusable` declaring a stop of its own, including a table row with no action of its own; text between two stops of
  the same card is reached through them and is no stop of its own (an emulator source card's folder and health lines,
  between its arrows and its switch)** — check + prompt-only — `tender/qam-focusable-row` for the syntactic slice — it
  passes a row with an unknown spread, any opaque child, or a browser-focusable descendant (`tabIndex`, `button`), so
  those rows are prompt-only too; prompt-only: focus order, runtime reachability, edge revelation (`ScrollRegion`'s
  `revealEdge`, whose decision `frontend/src/bigpicture/layout/ScrollRegion.test.tsx` pins), scrolling geometry and
  controller behaviour, and that a page's controls sit above its long focusable lists, not below
- **A list-and-detail page opens on the row it was opened WITH, not on its first row** — test + prompt-only —
  `ListDetail.test.tsx` and `WidePage.test.tsx`, each over a non-first row, as any test of it must be; the press itself
  is device-only. Prompt-only: `bigpicture/layout/WidePage.tsx` places entry focus through `pageEntryStop`
  (`utils/entryFocus.ts`), never `firstBodyStop`; `bigpicture/layout/ListDetail.tsx` puts the `ENTRY_STOP_ATTR` mark on
  the `display: contents` wrapper around the selected row, never on the row; a wide page that opens on a non-first row
  without `ListDetail` places the mark itself, the same way

When a change applies a guard / sanitize / backup / grouping pattern, sweep for sibling sites of the same pattern — the
register is what that sweep checks against.

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
