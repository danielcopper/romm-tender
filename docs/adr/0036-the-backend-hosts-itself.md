# The backend hosts itself

## Status

Accepted.

## Context

Everything that is not this plugin's own code has, until now, been Decky Loader's: the process, the Python it ran on,
the directories it read and wrote, the root logger, the bridge that carried a call from the panel to a method and an
event back, and the decision about when to start and stop. That was a real service, and the price of it was
proportionate — but every one of those is now a constraint rather than a convenience.

The specific costs, measured rather than assumed:

- **The runtime is frozen.** The loader is a PyInstaller build, so `sys.executable` is not an interpreter. The vendored
  resolver's core probe needs one to spawn, which is why `adapters/atlas_host.py` exists at all.
- **The directories follow a folder name.** Decky derives `plugins/`, `data/`, `settings/` and `logs/` from the
  delivered folder, so packaging decided where a user's library lived — the reason the data roots were moved out from
  under it ([ADR-0031](0031-user-data-lives-outside-the-plugin-directory.md)).
- **The bridge sets the limits.** A 1 MiB payload cap sits on a path this plugin does not control, which is why bulk
  base64 is chunked through per-item callables rather than sent.
- **The lifecycle is a detached task.** `_main` is awaited by nobody, so a start-up repair that raised was harmless —
  and that is exactly what makes hosting dangerous without a change: **six of the nine** start-up routines `_main` calls
  contain no exception handling at all, and under a service manager's restart policy an unhandled failure in one of them
  becomes a restart loop. Counting rule: an AST walk of each of the nine called methods for a `try` carrying any
  handler. The six without one are `detect_retrodeck_path_change`, `prune_stale_installed_roms`,
  `reconcile_orphaned_sync_runs`, `prune_orphaned_cover_cache`, `cleanup_leftover_tmp_files` and
  `detect_save_sort_change`. Only the negative direction is exact — a routine that _has_ a `try` is not thereby shown to
  catch everything it can raise.
- **Installation is Decky's.** A user must install a plugin loader to run this, and a release must be shaped for it.

None of this is a complaint about Decky, which does what it says. It is that the plugin has outgrown being a plugin.

## Decision

**1. A process of our own hosts the backend.** It holds the single-instance lock, runs the schema migration and the
start-up routines, binds a loopback port, serves the panel bundle, and carries calls and events over one WebSocket. It
lives in `backend/host/`, standard library only, and imports nothing from `services`, `adapters`, `bootstrap` or
`domain`; nothing but `backend/main.py` imports it. Both directions are `.importlinter` contracts.

**2. The HTTP server is written here.** The standard library has no asyncio HTTP server — `http.server` blocks — and
this backend is asyncio throughout. What is written is exactly as much as a `GET` and a WebSocket upgrade need: no
`POST`, no body parsing, no directory listings. The two pure halves — the frame codec and the request-head parser — live
in `backend/lib/` and are checked against tables of bytes.

**3. Every message states its own kind.** `call`, `reply`, `error` and `event`, each carrying `type` as a readable word.
Without it every later addition would be a protocol change and a receiver would have to guess from which keys are
present. `error.reason` is the **transport** vocabulary and is kept apart from a callable's own
`{success, reason, message}`, which travels inside `result`.

**4. There is no reply store.** A call whose answer was in flight when the socket went is not redelivered; the caller's
pending register answers it `connection_lost`. Over loopback a connection breaks essentially only when the page itself
goes away, the expensive apply path is already protected by chunking and acknowledgement, and the dangerous calls carry
claims. "No store" means a lost answer fails **visibly**, never that it vanishes quietly.

**5. Two size caps, with two purposes.** A ~12 MiB cap on one call's encoded answer, refused as an ordinary error for
that call; a 16 MiB cap on the connection's frames, judged on the announced length before a byte is buffered, whose
breach closes the socket. They are separate because breaking the second rejects every call in flight, and one oversized
cover image must not cost every other request. The numbers are chosen, not inherited: the reference library's largest
cover is 5,869,834 bytes, which base64 turns into 7,826,448 (7.46 MiB), so a 4 MiB cap would have refused it silently.

**6. Three checks, one order, two entry points.** Host, then Origin, then Token — on the static route and on the
upgrade, from one function. Host first so a DNS-rebinding attempt is logged as one rather than as a bad credential;
Origin second, which is protection against a foreign page and not a login (an absent origin passes; a browser never
omits one); Token last, because it is the only one that authorises. The token is per-process, memory-only, and travels
as part of an address — a module load cannot set a header, and a header of ours would force a CORS preflight.

**7. The order of start-up is what makes the port file mean something.** Lock, then schema and start-up routines, then
bind, then write the port file, then the one start-up step that talks to the network. So "the port file is there" simply
means "the backend is ready", and the draft's waiting mechanism — listen at once, hold calls until a ready flag —
disappears. The lock lies beside the database, because that is what it protects.

**8. A start-up routine's failure is counted, not fatal.** Each runs through `bootstrap/startup.py`, which reports and
continues. One pair has an edge: `prune_stale_installed_roms` runs only after `detect_retrodeck_path_change` has
**succeeded**, because the prune reads the pending homes the detection writes and would otherwise take every install
under the home RetroDECK just left for orphaned.

**9. The directories come from the environment.** `TENDER_*` first, XDG second, built-in defaults last; the back two
rungs are for a start by hand. `bootstrap()` is **told** where things are rather than deriving them. The ladder itself
is `domain/app_directories.py` — pure, with the environment handed in, so every rung is checkable against a table.

## Consequences

- **Nothing in this repository's backend imports `decky` any more.** `Plugin.run` is the process entry point, and the
  four loader constants are gone with it. Two surfaces went at the same time because both asked questions only the
  loader could pose: the start-up data migration, which copied a library forward from a directory the loader had named
  after the delivered folder (decision 9 leaves it nothing to decide), and `LegacyInstallService`, which asked whether
  the pre-rename plugin folder still stood beside ours by taking the loader's own runtime directory's parent. Both had a
  panel surface, so both crossed into the frontend. `typings/decky/` stays until the test files that still import the
  module follow, which is a separate cleanup.
- **Nothing reaches the backend yet.** The injector ([#1900](https://github.com/danielcopper/romm-tender/issues/1900)),
  the frontend transport ([#1899](https://github.com/danielcopper/romm-tender/issues/1899)) and the installer
  ([#1902](https://github.com/danielcopper/romm-tender/issues/1902)) are separate cuts. This one makes the backend
  hostable; later ones make it reachable. The npm package `@decky/ui` in the frontend stays either way — it is Steam's
  UI toolkit, not the loader.
- **A second copy refuses to start** and says where the first one listens — reading the port file, and then trying it,
  because the file is a hint and connecting is the proof.
- **The root logger is ours**, configured once from the entry point: the historical format verbatim, INFO, rotating by
  size rather than by run, plus stderr for the journal. The admission token is redacted on the **file** handler, so the
  one start-up line carrying the whole load address still reaches a terminal.
- **Nothing may ever log to stdout.** The core probe's child is read on stdout as JSON.
- **The interpreter grant becomes conditional rather than unnecessary.** Hosted, `sys.executable` is a real interpreter
  and is the right one; frozen, it is not, and the grant is the only offer there is.

## Alternatives considered

**Keep running under Decky and add a socket beside it.** Two lifecycles, two loggers, two ideas of where data lives, and
the frozen runtime still in the way. It buys nothing the loader was giving us that this does not.

**Use a third-party ASGI server (uvicorn, aiohttp, websockets).** Rejected on the vendoring rule: everything under
`_vendor/` is a checksum-pinned upstream copy, and this would be the largest dependency in the tree by far — for a
server that serves one directory and upgrades one path. The hand-written half is small enough to check against byte
tables, which a framework's would not be.

**Build Decky's reply store: acknowledgement and redelivery after a reconnect.** Rejected for this cut, and the shape
that would be needed is recorded rather than half-built: it is a new message kind and gets its own cut with its own
tests. Two hooks are in place for the honest reason of **traceability**, not of retrofitting — the call number carries
on across a reconnect because it lives in the bundle, and the session identity comes from the **frontend**, once per
bundle instance, travelling in the upgrade address so "newest connection wins" can be decided at connection time. A
backend-assigned identity would be new per connection and would measure nothing.

**Let the start-up routines keep failing loudly.** That is the current behaviour and it is only survivable because
nobody awaits them. Hosted, it is a restart loop caused by an artwork sweep.

**Derive the served directory from `__file__`.** It would make the host the only part of this backend that knew the
repository's layout, and it would be wrong the moment the program was installed anywhere.

## Related

- [ADR-0035](0035-the-release-builds-no-decky-artifact.md) — the release stopped building a Decky artifact, which is
  what this decision is the other half of.
- [ADR-0031](0031-user-data-lives-outside-the-plugin-directory.md) — the user's data had already left the loader's
  directories, which is why hosting moves no files.
- [ADR-0006](0006-narrow-unit-of-work-scope.md) — every unit of work opens with `BEGIN IMMEDIATE`, which is what the
  single-instance lock protects.
