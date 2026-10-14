# Steam Non-Steam Shortcuts

Technical reference for how Tender creates, manages, and launches non-Steam shortcuts. This covers the
`SteamClient.Apps.AddShortcut` API, VDF format details, and app ID handling.

## AddShortcut API Behavior

### Signature

```typescript
SteamClient.Apps.AddShortcut(name: string, exe: string, startDir: string, launchOptions: string): Promise<number>
```

Returns the new shortcut's `appId` (a number), or `0`/`null` on failure.

### What it actually does

Despite accepting four parameters, `AddShortcut` **ignores `startDir` and `launchOptions`**. This was confirmed by the
[MoonDeck plugin](https://github.com/FrogTheFrog/moondeck) developers. Only `name` and `exe` are used during creation.

To set all shortcut properties reliably, wait for Steam to register the new app's **overview** before the `Set*` calls:

```typescript
const appId = await SteamClient.Apps.AddShortcut(name, exe, "", "");
// Poll appStore.GetAppOverviewByAppID(appId) (~100ms cadence) until the overview
// exists, with a 1000ms fallback; on timeout, proceed anyway.
await waitForAppOverview(appId, 1000);

SteamClient.Apps.SetShortcutName(appId, name);
SteamClient.Apps.SetShortcutExe(appId, exe);
SteamClient.Apps.SetShortcutStartDir(appId, startDir);
// An empty launch_options (uninstalled placeholder) needs no write or confirm —
// a fresh shortcut's launch options are already empty, so skip both. A non-empty
// command takes the confirmed write (setLaunchOptionsConfirmed).
if (launchOptions !== "") await setLaunchOptionsConfirmed(appId, launchOptions);
```

Steam must finish registering the new app internally before the `Set*` calls land, or they silently fail. Rather than a
fixed worst-case wait, Tender polls `appStore` for the new overview (readiness) — the common case proceeds in ~100ms
instead of a blind 500ms, and the 1000ms ceiling keeps the old wait's safety net when the overview is slow. Skipping the
launch-options write for the (majority) uninstalled case also avoids `setLaunchOptionsConfirmed`'s
`RegisterForAppDetails` poll, which forces Steam to load and cache a fat `AppDetails` object per call.

### Where the exe points

Every shortcut's `exe` is `<bin root>/tender-rom-launcher` — `~/.local/bin/tender-rom-launcher` by default, the place
XDG names for a user's own executables. Not in the directory the program is installed in, because a shortcut's `exe` is
the one thing about it this program cannot repair from inside: an update that replaced its own install directory would
leave every game pointing at a file nothing puts back. Not under the data root either, which holds the database and
nothing executable ([ADR-0038](../adr/0038-the-launcher-lives-in-local-bin.md), which supersedes
[ADR-0032](../adr/0032-shortcuts-are-rewritten-in-place.md) on the home; the roots themselves are
[ADR-0036](../adr/0036-the-backend-hosts-itself.md)). The backend installs this release's launcher there unconditionally
on every start.

Shortcuts of ours that are not at that home are repointed once, at panel load. The **backend** decides which: it parses
`shortcuts.vdf` and returns the app IDs whose `exe` ends in `/bin/tender-rom-launcher` but is not the launcher's home,
plus the `exe` and `startDir` to write (`get_shortcut_relocation`). The frontend writes exactly those and reports; it
records nothing (`frontend/src/utils/launcherRelocation.ts`). The transition is stamped by the NEXT start's own reading
of that file, once Steam has written its in-memory shortcuts out — after which no start reads it again. An app
**overview** carries no `exe`, so the frontend's own route to the same fact would be a `RegisterForAppDetails` per
shortcut at every start.

Three properties of that path are load-bearing:

- **It ends in `/bin/tender-rom-launcher`.** Ownership is decided by that suffix and nothing else
  (`isRomMShortcutDetails`, `domain/shortcut_data.py::select_shortcuts_to_relocate`, and
  `backend/services/prune/requests.py`), so a launcher kept under any other last two components makes every shortcut
  written before that change stop being recognised as ours.
- **One ending is the whole of ownership.** A shortcut naming a launcher an earlier version of this program wrote is
  foreign here: not recognised, not repointed, not counted, not pruned. A user coming from 0.33 removes all non-Steam
  shortcuts and syncs again ([ADR-0038](../adr/0038-the-launcher-lives-in-local-bin.md)).
- **The app id in the file is signed.** `shortcuts.vdf` stores it as a signed int32 (`to_signed_app_id`) while every
  `SteamClient.Apps.Set*` takes the unsigned form, so anything reading ids back out of the file converts them
  (`to_unsigned_app_id`). A negative id names no shortcut and fails silently.

### Exe quoting

**Do NOT pass quoted exe paths to `AddShortcut` or `SetShortcutExe`.** The API handles quoting internally. Passing
`"\"path/to/exe\""` (pre-quoted) results in double-quoting, which causes launches to fail with "file not found."

Pass the raw path:

```typescript
SteamClient.Apps.SetShortcutExe(appId, "/home/deck/.local/bin/tender-rom-launcher");
```

### Launch options pass through a shell

Steam runs a non-Steam shortcut's launch options through a shell, and the launcher receives what that shell made of
them. Measured on the reference device: a `$HOME` inside double quotes in the launch options reached the program
expanded, and a `$(…)` inside them was run. Double quotes therefore do not keep a file name literal — `$`, backticks and
`$(…)` in it are still expanded.

So `build_launch_options` (`backend/domain/shortcut_data.py`) puts the ROM path in **single** quotes, writing a `'`
inside it as `'\''`; nothing inside single quotes is expanded, and the launched program receives the path exactly as the
file is named. Only the path is quoted that way: the invocation before it is Tender's own text, and its `-e "…"` is
meant for the shell as written. `TestTheShellHandsThePathOverAsNamed` in `tests/domain/test_shortcut_data.py` runs built
launch options through `sh -c` and checks the arguments the program receives.

### Updating existing shortcuts

Steam **assigns** a shortcut's `appId` when `AddShortcut` creates it, and that `appId` is **stable for the shortcut's
lifetime**. Tender never computes it: it records Steam's assigned id in `roms.shortcut_app_id` and detects ownership by
the exe path (`…/bin/tender-rom-launcher`), not by re-deriving the id. (The historical "`appId` is
`CRC32(exe + appName)`" formula does not hold on current Steam — see [App IDs and Artwork](#app-ids-and-artwork).) Two
consequences follow:

- **`launchOptions` and `startDir` are appId-safe.** Changing either on an existing shortcut keeps the same `appId`, so
  the shortcut's identity, artwork, collection membership, and `roms.shortcut_app_id` binding all survive.
  `SetAppLaunchOptions` on an existing shortcut is **reliable** — confirmed on hardware in
  [#827](https://github.com/danielcopper/romm-tender/issues/827) across in-session writes, a Steam restart, and
  removal-churn re-syncs. Tender uses it directly to bake the launch command in at download-complete and to re-resolve
  paths after a RetroDECK-home migration.
- **`exe` is appId-safe too, and that is now measured.** Every one of a 826-shortcut library had its `exe` and
  `startDir` rewritten in one pass — the relocation 0.33 ran at start-up, into the launcher home of
  [ADR-0032](../adr/0032-shortcuts-are-rewritten-in-place.md) that ADR-0038 has since superseded (see
  [Where the exe points](#where-the-exe-points)) — and the appId set afterwards was identical to a `shortcuts.vdf`
  backup taken before it: 0 new, 0 lost, names unchanged, and the 1,652 `Set*` calls (one `SetShortcutExe` and one
  `SetShortcutStartDir` per shortcut) cost 12 ms of renderer time. Earlier revisions of this page said an `exe` change
  had to be applied by delete + recreate; that rested on the CRC derivation disproven in
  [App IDs and Artwork](#app-ids-and-artwork), and this measurement replaces it.
- **The display name is the one nobody has measured.** The sync writes it in place as well — `rewriteShortcutIdentity`
  sets name, exe, start dir and launch options together for a rom that already holds a binding — and nothing has
  established what a `SetShortcutName` does to the appId, in either direction. Do not read the `exe` measurement above
  as covering it: it says nothing about the name, and no delete + recreate path exists for one to fall back on.

Because `SetAppLaunchOptions` returns `void` with no success signal, Tender **fires the set then polls**
`RegisterForAppDetails` until the read-back `strLaunchOptions` matches (`setLaunchOptionsConfirmed`). That report can
take longer than the 2 s the poll waits — right after a Steam start, Steam has already written the new value to
`shortcuts.vdf` while the report is still outstanding — so when the wait runs out, Tender reads the shortcut's details
once more (`getAppDetails`, bounded by the same 2 s) and counts the write as confirmed if they show the value — a
confirm only the re-read gave is logged at INFO, a first-report confirm is not. A re-read can also answer with a value
other than the one written while the file already holds the written one: on the reference device it did so for two
shortcuts, each written back to a value it had held before, and which value the re-read showed was not recorded (#2348).
So when the re-read does not show the value either, Tender asks the backend what `shortcuts.vdf` holds for the shortcut
(`get_shortcut_launch_options`, which reads the file and never writes it), and the written value there confirms the
write, logged at INFO with how long this confirm waited for the reading (in a batch, the wait for the batch's shared
reading, not its parse). The reading waits at most 5 s — an endpoint call otherwise waits out a backend that is down —
and one that does not answer in time counts as a file that could not be read. The file's answer is sound because on the
reference device Steam rewrote the file about 0.1 s after each write measured in #2348 (four writes, two shortcuts),
while the file is read 2–4 s after the write. What it cannot tell apart is a write that did not land of a value the file
still holds from before — possible only while Steam holds a different value in memory that it has not yet written out —
and that prompt rewrite makes such a window negligible. Where many confirms run together, as in the reconcile, each
batch of them reads the file once, at its first confirm that needs it, rather than once per shortcut
(`batchConfirmLaunchOptions`). Only a confirm the file does not settle either — it shows another value, holds no such
shortcut, or could not be read — is a failed confirm, and its warning names what the re-read and the file each showed;
every caller of `setLaunchOptionsConfirmed` gets that answer, so a write that did not land is still reported where its
caller reports one. Setting `""` — the placeholder an uninstalled ROM carries until it is downloaded — is valid and
confirms against an empty read-back.

The real hazard is not the set: heavy removal-churn can corrupt Steam's in-memory shortcut state. A Steam restart clears
it. The sync engine processes removals before additions to minimise churn.

See: `frontend/src/utils/steamShortcuts.ts`

### Recovery after a server switch / re-import

Switching the RomM server URL — or re-importing on the same server — reissues `rom_id`s while the Steam shortcuts are
**not** deleted (their assigned appIds persist) and the `roms` rows survive (ADR-0007 retention) with the binding in
`roms.shortcut_app_id`, reverse-lookupable via `get_app_id_rom_id_map()`. Because Steam **assigns** the appId at
creation (the `CRC32(exe + name)` derivation is disproven — see [App IDs and Artwork](#app-ids-and-artwork)), Tender
never re-derives an id to "find" the old shortcut. It keeps a game's shortcut alive across the `rom_id` churn through
three lanes:

- **Stable sibling-group keys → the rebind lane.** An unchanged game whose `sibling_group_key` is the same after the
  re-import (same IGDB/… identity) rides `collapse_sibling_groups`'s **rebind lane**: the group's only fetched member is
  the fresh `rom_id`, the old bound `rom_id` has vanished, so collapse emits one entry keyed to the vanished sibling —
  the frontend reuses its existing shortcut by `rom_id` — carrying `bind_rom_id` → the fresh representative. The
  shortcut, its artwork, its collection membership, and its playtime all survive; only the DB binding moves onto the new
  `rom_id`, and no duplicate is minted. _(The domain behavior is unit-pinned in `tests/domain/test_sync_diff.py`;
  on-device confirmation of the end-to-end path is still pending.)_
- **Changed / absent group keys → delete + create.** If the re-import changes or drops the `sibling_group_key` (the game
  rematches to a different metadata entry, or its identity is lost), the group no longer resolves to the old bound
  sibling, so the fresh `rom_id` takes the **new** lane: a fresh `AddShortcut` with a **new** appId, and the old
  shortcut is torn down by the stale path. Artwork and collection membership are re-established for the new shortcut.
  This is honest degradation — the identity link the rebind lane needs is gone, so the shortcut cannot be preserved.
- **Untracked orphans → adoption at create time.** A live RomM-owned shortcut (exe ends `/bin/tender-rom-launcher`) that
  carries **no** DB binding — a crashed run's uncommitted in-flight shortcut, or a zombie left after a DB reset — is
  invisible to both the rebind lane (no `roms` row) and the stale path (nothing to unbind), so a naive create would
  leave a duplicate (`#1366`). When such a ROM reaches the create path, the frontend **adopts** the orphan instead: it
  matches the built entry's display name against a once-per-run pool of live-but-unbound RomM appIds
  (`getLiveRomMShortcutAppIds()` minus the run's already-bound appIds, resolved to names via
  `appStore.GetAppOverviewByAppID`), reuses the matched appId, and rewrites its identity + launch bake (the same `Set*`
  writes as an update). The pool is built **lazily** on the first create candidate (a pure-update run pays no scan
  cost); each orphan is adopted at most once per run; a name collision adopts the lowest appId deterministically; a
  `null` live scan (store unreadable) disables adoption for the run rather than guess. An adoption counts as "added" in
  the post-sync toast (a game came under management) but skips the `AddShortcut` a duplicate would cost. See
  `resolveShortcutAppId` in `frontend/src/utils/syncManager.ts`.

Two guards keep a re-import from wiping a freshly-bound shortcut (`#1036`):

- **One appId, one bound row.** `SqliteRomRepository.save()` unbinds any sibling row holding the appId before the
  per-`rom_id` UPSERT, and migration `003`'s partial unique index on `shortcut_app_id` enforces it (see
  [Database Design](database-design.md)). A re-import never leaves two bound rows sharing one appId.
- **Stale-removal excludes appIds bound this run.** The finalize stale pass flags bound rows whose `rom_id` wasn't
  synced this run — which includes the old `rom_id` the rebind lane just superseded.
  `domain/sync_diff.py:select_stale_removals` removes any candidate whose appId is in the run's `committed_app_ids`
  (every appId bound this run, across both the happy-path and the heartbeat-timeout late-ack commit paths), so the appId
  the run just re-bound onto the new `rom_id` is never emitted for removal. The `get_by_app_id` reverse lookup orders
  `rom_id DESC LIMIT 1` so it resolves the live (newest) binding for any pre-migration edge state.

### A bound ROM RomM holds no file for

RomM keeps a ROM row whose file has left its disk, and lists a physical game that never had one. Its payload's
`has_file_on_disk` is false for both, and RomM answers a content download of either with a 404; `missing_from_fs` and
`is_physical` are not read on their own. The sync stores the flag on the row (`roms.has_file_on_disk`, see
[Database Design](database-design.md)), and the representative chain reads it
([Backend Architecture — group-aware sync](backend-architecture.md#libraryservice-decomposition-serviceslibrary)).

A shortcut bound to such a member that is not installed is moved by the next complete-view sync (a platform unit, or the
preview) onto a sibling with a file whose `fs_name_no_ext` equals the bound member's, through the same rebind entry the
re-import case uses: keyed by the bound row, so the appId, artwork, collections and playtime survive, and carrying
`bind_rom_id` → the sibling. An equal stem means the file was replaced in place — a rescan that turns `Game (USA).zip`
into a folder `Game (USA)`. A sibling whose name differs in any part (language, region, version, a hack or a patch)
never takes over: it is another version the user did not choose, and RomM parses some of those dimensions, such as
`(v1.1)`, without sending them. The target must hold no shortcut of its own (grandfathered duplicates), and two bound
members never move onto one target; of several candidates the chain's order picks. Otherwise the shortcut stays on the
member without a file, and its play button says **No file on RomM**.

`classify_roms` counts an entry carrying `bind_rom_id` as changed — this rebind and the re-import one alike — because
the apply moves a binding for it even when the shortcut's name and launch command stay the same. Without that, a rebind
alone left the preview at "Everything is up to date" with no Apply to press. The exception is an entry keyed by a row of
a unit the run skipped: the apply moves no binding for a skipped unit, so the preview counts none.

### Retained-row availability in the version picker

ADR-0007 keeps a `roms` row after RomM stops returning that id because the row can anchor local-only saves, playtime,
and an installed ROM. Retention does not imply that RomM still offers the row as a playable version. The Game Page's
lazy `get_version_list` load therefore recomputes availability every time; there is no liveness column, migration,
cache, or persisted verdict. (The `no_file` marker below is a different thing: a fact the last sync stored, not a
liveness verdict.)

The bound id's own read (`get_rom_simple`, `GET /api/roms/{id}/simple`) supplies its own answer and RomM's current
direct `sibling_roms` view. The bound id and every local id positively present in that view are live. A local group
member absent from the direct view is only a suspect because RomM sibling membership can be transitive, so each suspect
is checked by an exact-id `get_rom_once` request. These checks fan out concurrently on the worker executor and use the
short timeout with no retry, keeping them off the event loop and out of the initial Game Page render.

Only a typed `RommNotFoundError` from an exact id marks that entry `vanished`. A successful response is live; timeout,
transport, authentication, server errors, and malformed or empty data all fail open and leave it available. If the bound
read itself 404s, `bound_vanished` is true, `server_query_failed` stays false, and every other local member is checked
individually. That entity-specific 404 is not fed into the global connection store; a genuine explicit
server-unreachable result still is. Cover and save endpoints are not liveness authorities.

Vanished rows stay visible with their active and downloaded markers, but cannot be switched to — selecting one opens its
cleanup — and are excluded before the existing default-resolution kernel runs. `vanished` does not change `switchable`
or `domain.sibling_group.target_in_sibling_group`: availability and sibling membership remain separate verdicts. This
lets a shortcut still bound to a vanished id show the retained context while the user selects a live alternative. The
Saves tab likewise skips positively vanished inactive installs before checking local drift, then continues through later
live candidates.

The list verdict is advisory UI state, not authority for a later write. Immediately before `switch_version` moves the
binding onto an already-local target, it checks that exact target id again through the same three-second, single-attempt
`get_rom_once` path. The request runs on the worker executor outside the write UoW and after the save-stranding guard
permits the attempt. Consequently an initial unsynced-save warning makes no target request, while both `Sync now` and
`Switch anyway` retries are protected; `allow_stranded` never bypasses liveness. A typed target 404 refuses with
`version_vanished` without changing the binding or any recorded launch state. Every other optional-probe outcome fails
open, so a local switch remains fast when RomM is uncertain or offline. The active-target no-op does not probe because
it moves no binding.

A server-only target already requires its full RomM detail for membership validation and row construction. That fetch
keeps its normal retry policy, doubles as the liveness verdict, and receives no second probe. Its typed 404 produces the
same `version_vanished` refusal; other RomM errors keep their ordinary classified reason. Network I/O remains outside
the short write UoW, whose fresh membership and bound-elsewhere checks still decide SQLite races. This leaves an
unavoidable cross-system interval after a successful response: the liveness check reduces stale-list risk but is not a
transaction with RomM.

A version RomM still lists but holds no file for carries `no_file` — for a local row the column the last sync stored,
for a server-only one the fetched detail — and the row reads **No file on RomM**. It is never the Default, and unlike a
vanished row it offers no local cleanup, since a rescan in RomM can bring back a file that was moved or renamed. The row
is selectable only where that version is installed here, and `switch_version` refuses any other switch to it with
`no_file_on_romm`. A download refuses the same way on RomM's live answer, read when it starts, so a file a rescan
brought back since the last sync downloads, and one that left since does not.

## Explicit cleanup of vanished versions

Automatic sync remains unbind/retain-only. Deleting retained local state is a separate confirmed workflow under **Danger
Zone → Clean Up Removed RomM Games**, also reachable by activating a synced vanished version's own picker row (which
carries a trash affordance, the menu row being the focusable unit) or as a focused button for a synced singleton
vanished binding. Candidate discovery is not deletion authority: the backend freshly probes each exact RomM id, and only
typed 404s can proceed.

For a vanished bound version with a live sibling, the default-on repoint action reuses the version switch independently
of the row-removal option — through `switch_version_unchecked`, which checks no conflict rule and takes no lease, since
the cleanup switches from inside its own run — then the frontend confirm-writes the returned exact launch options.
Cover/cache publication and the `version_switched` event are deferred until terminal prune completion. Before emitting a
terminal result that needs repoint publication, the backend acquires a continuation lease while the old run is still
active; the frontend registers that token immediately and holds it across both release acknowledgement and the final
artwork write. Another prune therefore cannot enter between the old claim and publication. Publication uses the same
path as VersionPicker. Repoint changes neither shortcut name nor exe and never calls `AddShortcut`, so the assigned
appId, collections, and Steam playtime remain attached. Unsynced-save stranding can be overridden only after enabled
recovery has sealed.

For a fully vanished bound game, whole-game cleanup is its own confirmation option, default-on and paired with the
default-on recovery bundle that keeps the shortcut rebuildable. With recovery enabled, the root frontend handler
captures complete shortcut details, available Steam playtime fields, and every collection id/name or fails closed if
that JSON is over the snapshot's byte cap. The backend resolves the active account from Steam's login identity once,
stores that identity in the recovery handle, and adds only that user's grid artwork, both per-app Steam Input roots, and
relevant controller setting. Cleanup must use those exact captured roots even if the active account changes later.

Every action event is deduplicated and serialized. Before any Steam mutation, the frontend claims its token from the
backend, re-reads the live shortcut, and requires the appId to exist with an exe ending in `/bin/tender-rom-launcher`.
Only then does shortcut removal capture a fresh complete snapshot, compare it with the sealed snapshot, call
`RemoveShortcut(appId)` once, and poll the live store until absence. Identical claim retries are idempotent. Completion
reporting uses bounded retries of the same payload without repeating the Steam operation. If every completion report is
lost, the claimed lease expires as an ambiguous partial and retains source data. A `RemoveShortcut` call followed by an
unreadable store or settle timeout is also explicitly attempted-but-unconfirmed, never reported as an unchanged failure;
a later run can confirm the appId is already absent and reconcile the binding without calling removal again. An
unreadable/foreign store before mutation, stale claim, or unclaimed timeout is failure, and no row/source finalization
follows an uncommitted action.

The same reciprocal exclusion covers ordinary frontend Steam continuations outside an action event. SGDB fetch results
carry leases through hero/logo/grid/icon writes; `sync_complete` keeps one shared lease until launch-option, collection,
playtime, and overview-metadata branches all settle; and bulk shortcut removal clears its collections before
acknowledging and releasing the removal lease. Leases live in one frontend registry, renew only for a bounded active
continuation, and receive a cooperative cancellation signal before their owner releases them. A continuation with an
owner captures that owner's mount generation before backend waits, so a token arriving after the owner's teardown is
released without admitting old continuation work even if a new owner has since mounted. Each non-empty `sync_stale`
frame owns a lease through its paced tail; successful `sync_complete` processing overlaps that lease while joining the
same tail. A backend emit failure rolls back a token the frontend never received.

Recovery records the Steam-assigned appId and playtime, but there is no automatic restore and Steam cannot currently
reattach those values to a newly created shortcut.

## Sync-start reconcile of Steam-UI-deleted shortcuts

A user can delete a RomM shortcut through **Steam's own UI** (remove from library), which Tender never observes. The
`roms` row keeps its now-dead `shortcut_app_id`, so `get_app_id_rom_id_map` keeps serving it (playtime writes and
launch-options bakes aim at a Steam app that no longer exists) and the **incremental skip never recreates it**: the skip
counts bound `roms` rows, not live Steam shortcuts, so the platform reports "unchanged" forever. The game stays gone
until a server-side change or a Force Full Sync (`#1046`).

The fix is a **frontend-assisted reconcile at sync start**, because only the frontend can read Steam's shortcut store.
It runs **before** the sync builds its work queue — so the unbind lands before the incremental-skip decision — on both
the skip-preview (`start_sync`) and preview (`sync_preview`) paths:

1. `scanShortcutOwnership()` (`frontend/src/utils/steamShortcuts.ts`) scans Steam's live shortcuts and answers two sets
   of raw appIds, regardless of any backend binding: `owned`, every RomM-owned shortcut (exe ends with
   `/bin/tender-rom-launcher`), and `unresolved`, every entry whose details Steam did not answer for before
   `getAppDetails` timed out. It returns `null` when the store was **unreadable** (`collectionStore` absent) versus two
   empty sets when the scan **ran and found none** — a load-bearing distinction.
2. `reconcileStaleShortcuts()` (`frontend/src/utils/syncManager.ts`) skips the reconcile on a `null` scan (reconciling
   against "couldn't look" would unbind every binding), and otherwise calls the `reconcile_shortcuts` endpoint with
   `owned` and `unresolved` together. An unresolved entry is one nothing was established about, so it keeps its binding:
   a binding that should have gone is corrected by the next run that can read the entry. Unbinding a live shortcut
   instead sends its platform back through a full fetch and the ROM through the apply, which adopts the unbound shortcut
   by its display name when the apply's own scan identifies it as ours (the orphan-adoption pool in `syncManager.ts`).
   The Steam-side state on it — its collections, and the appId playtime and artwork hang off — is lost when that scan
   cannot identify it either, or when the shortcut's display name no longer matches the game's name — a game renamed in
   RomM; in both cases the apply creates a second shortcut beside it. It is best-effort: a scan or backend failure is
   logged and swallowed, never blocking the sync.
3. `ShortcutRemovalService.reconcile_live_shortcuts` unbinds every bound `roms` row whose `shortcut_app_id` is **not**
   in the live set — clearing only the binding (`Rom.unbind_shortcut`, ADR-0007), never deleting the row or its per-ROM
   children. An empty live set is the correct "they're all gone" signal and unbinds every binding.

Once a row is unbound, the fetcher's incremental baseline (`_read_incremental_baseline`, which reconstructs only rows
with a non-NULL `shortcut_app_id`) no longer counts it, so `unit.rom_count == registry_count` fails and the platform
falls through to a full fetch that recreates the shortcut. The unbind is reversible by design — the next sync re-binds.

This is **eager (sync-start) reconciliation of the Steam-shortcut binding**, distinct from `#951`'s lazy on-access
reconciliation of the `rom_installs` (on-disk install) view: a different aggregate, a different cost driver, and —
unlike installs — one the backend physically cannot reconcile lazily, since no per-game backend seam observes Steam's
shortcut store.

## BIsModOrShortcut

Non-Steam shortcuts return `BIsModOrShortcut() = true` by default. This is their natural state — Steam uses this flag to
determine how to render and launch an app.

An earlier version of Tender used a "bypass counter" pattern (inspired by MetaDeck) to temporarily return `false` from
`BIsModOrShortcut()` so that Steam would render metadata sections (description, developer, etc.) on the game detail
page. This approach was **dropped in Phase 5.6** because it caused launch failures — Steam skips the shortcut launch
path when `BIsModOrShortcut()` returns `false`.

The current approach owns the entire game detail UI via custom React components (`RomMPlaySection`, `RomMGameInfoPanel`,
`CustomPlayButton`) injected into the render of Steam's own game-page route — the seam, and why it is that one, is in
[frontend-bundles.md](frontend-bundles.md#tenders-section-on-steams-game-page). This avoids fighting Steam's internal
rendering logic.

See: `frontend/src/bigpicture/patches/gameDetailPatch.tsx`, `frontend/src/bigpicture/RomMPlaySection.tsx`

## Overview metadata mutations (readiness-gated)

Beyond the custom UI, Tender writes three fields directly onto each RomM shortcut's `SteamAppOverview` so the shortcut
presents like a native Steam game: `controller_support = 2` (the "Full Controller Support" badge — important so Game
Mode doesn't flag the controller-driven RetroDECK launch), `metacritic_score` (from RomM's `average_rating`), and
`m_setStoreCategories` (RomM's `steam_categories`).

Steam rebuilds `appStore` from scratch on every `SharedJSContext` mount, so these in-memory mutations are lost on each
reload and must re-apply per mount. `registerMetadataPatches` builds the appId→romId map; `applyAllMetadata` then
applies the mutations with a **readiness retry** (the same `[0, 1s, 3s, 5s]` ladder as `applyAllPlaytime`). Without the
retry the pass runs before `appStore` is populated and silently no-ops on a cold boot, so the badge/rating/categories
never appear until a later mount (#1203). The mutations are idempotent, so retries are safe.

The pass also re-runs on **`sync_complete`**. A sync adds or re-keys ROMs whose metadata the init-time pass never saw,
so `onSyncComplete` re-fetches the full paged metadata cache + appId map (`fetchMetadataCachePages`, shared with init),
re-registers via `registerMetadataPatches` with the fresh data, and re-applies — mirroring the playtime re-apply beside
it. It runs on every `sync_complete`, cancelled runs included (a partial run's committed units still carry fresh
metadata, and the pass is idempotent), in its own detached block with its own error handling so a re-fetch failure never
touches the toast, collections, or playtime paths (#1207). The backend commits metadata per unit during the sync (before
the terminal emit), so this re-fetch always sees the new ROMs.

See: `frontend/src/utils/metadataPatches.ts` (which appId gets which value, and the retry ladder),
`frontend/src/utils/steamOverview.ts` (`writeMetadataFields`, where all three assignments onto the overview are made),
`frontend/src/utils/metadataCache.ts` (paged fetch), `onSyncComplete` in `frontend/src/index.tsx`

## VDF Format Notes

Shortcut creation and every field update go through the frontend `SteamClient.Apps.AddShortcut()` / `Set*` API —
`AddShortcut` returns the real `appId` directly, so Tender never computes app IDs itself and never edits `shortcuts.vdf`
while Steam is running (Steam holds the file in memory and rewrites it from memory, silently clobbering external writes
— see [shortcuts.vdf is memory-authoritative](#shortcutsvdf-is-memory-authoritative)). The backend `SteamConfigAdapter`
(`adapters/steam_config.py`) still lays down artwork **files** in the grid directory, including the icon PNG. It reads
`shortcuts.vdf` for three questions — which shortcuts the one-time relocation still has to repoint
(`read_shortcut_exes`), what a shortcut's launch options are when Steam's reports did not confirm a write
(`read_shortcut_launch_options`), and which shortcuts the shortcut artwork job still has to give an icon
(`read_shortcut_icons`) — and its whole-file `shortcuts.vdf` helpers (`read_shortcuts`, `write_shortcuts`) are on no
live path since the icon write moved to `SteamClient.Apps.SetShortcutIcon`.

### shortcuts.vdf structure

Steam stores non-Steam shortcuts in a binary VDF file at:

```text
~/.local/share/Steam/userdata/<user_id>/config/shortcuts.vdf
```

Each entry has these key fields:

| VDF Field       | Format       | Notes                                                                                                                                                                                                                                                                           |
| --------------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `AppName`       | string       | Display name                                                                                                                                                                                                                                                                    |
| `Exe`           | string       | Executable path. `AddShortcut`-created entries store it **unquoted** (on-device inspection) — the API handles any quoting internally                                                                                                                                            |
| `StartDir`      | string       | Start directory. Stored **unquoted** for `AddShortcut`-created entries                                                                                                                                                                                                          |
| `LaunchOptions` | string       | The full launch command the `bin/tender-rom-launcher` exec wrapper runs, e.g. `flatpak run --nosocket=wayland net.retrodeck.retrodeck '/path/to/game.iso'` — or `""` (placeholder) for an uninstalled ROM. No `romm:<id>` marker; ownership is detected by the exe path instead |
| `appid`         | signed int32 | Assigned by Steam when `AddShortcut` runs; stored as the signed int32 form (`to_signed_app_id`)                                                                                                                                                                                 |
| `icon`          | string       | Icon path or hash                                                                                                                                                                                                                                                               |
| `tags`          | object       | Steam collection tags. Tender manages collections via `collectionStore` (machine-scoped names like `RomM: N64 (steamdeck)`), not by writing this VDF field.                                                                                                                     |

### shortcuts.vdf is memory-authoritative

While Steam is running, `shortcuts.vdf` is authoritative **in Steam's memory**: Steam rewrites the file from memory
mid-session and on exit, so any external write to it while Steam runs is silently clobbered. Tender therefore creates
and mutates shortcuts only through the `SteamClient` API (`AddShortcut` / `Set*` / `SetShortcutIcon`), never by editing
`shortcuts.vdf` directly. Pass raw, **unquoted** paths through those APIs — the API adds any quoting internally, and
on-device inspection confirms `AddShortcut`-created entries are stored unquoted; pre-quoting double-quotes the path and
breaks launches (see [Exe quoting](#exe-quoting)).

See: `backend/adapters/steam_config.py`

## Collection management

Steam collections are managed entirely on the frontend via `collectionStore`, not by writing the shortcut's `tags` VDF
field. Tender owns machine-scoped collections named `RomM: <platform> (<hostname>)` for platforms and
`RomM: [<name>] (<hostname>)` for synced RomM collections. The `sync_complete` event carries `platform_app_ids` and
`romm_collection_app_ids` maps; `onSyncComplete` (`frontend/src/index.tsx`) creates/updates the collections for the maps
it receives and then runs a **stale-collection cleanup** that deletes any `RomM: …` collection for this machine whose
platform/collection name is absent from those maps.

The cleanup is **gated on a completed (non-cancelled) sync** (`!data.cancelled`). On a cancelled run the maps are
**partial** — they list only the platforms the run reached before the cancel (empty if the cancel fired before the first
unit), because the backend builds `platform_app_ids` from the cross-unit accumulator of reached platforms. Treating a
partial map as the authoritative active-set would delete the collections for unreached platforms — an early cancel would
wipe the entire library organization. The additive create/update path stays ungated, so the platforms that did complete
still get their collections; only the destructive deletion is skipped on cancel. Steam collections are not backed up, so
the safe behavior on a partial/cancelled run is to delete nothing.

### Collection naming mode — `merge` vs `by_label` (#1539)

The `romm_collection_app_ids` wire payload is `name → appIds` **only** — kind/virtual_type are collapsed away before the
frontend sees them, and the frontend simply wraps each key as `RomM: [<key>] (<hostname>)`. So the Steam-collection name
is decided **entirely by the reporter's dict key**, which `SyncReporter._resolve_collection_memberships`
(`services/library/reporter.py`) takes from `domain/collection_label.py::steam_collection_key` under the
`collection_naming_mode` setting:

- **`merge`** (default) — the key is the bare collection display name. Same-named RomM collections of any kind union
  into one `RomM: [<name>]` Steam collection (RomM permits same-named collections across kinds/users, #1503).
- **`by_label`** (opt-in) — a standard collection (a favorites collection included) keeps the bare name as its key;
  every other kind's key is `"<name> (<Label>)"`, where the label comes from the pure `domain/collection_label.py`
  kernel. The rule: the label is a short form of the kind's row name on the Collections page — `Smart` (Smart
  collections), `Franchise` (Franchises), `IGDB Collection` (IGDB collections). A virtual collection of no known type
  falls back to its group heading's word, `Autogenerated`; the sync never writes it, since it fetches only the franchise
  and IGDB-collection types. So a hand-picked collection, a franchise and an IGDB collection that share a name become
  `RomM: [<name>]`, `RomM: [<name> (Franchise)]` and `RomM: [<name> (IGDB Collection)]` — three Steam collections.
  Collections whose keys match still union: two of the same name and kind, and — accepted, since it takes a name
  spelling out another kind's label — a standard collection named `"Foo (Smart)"` with a smart one named `"Foo"`.

`IGDB Collection` keeps its capital C: Steam tells collection names apart without regard to case, and Tender finds an
existing collection the same way (below), so a change of case alone would not rename a collection that already exists.

The Collections page does not quote the label strings. Besides the kernel, its test and this section's own examples,
they are spelled in these places, and a label change is a change in each:

- the user guide's "Collections that share a name" (`docs/user-guide/syncing-your-library.md`);
- the description of the Steam Library setting that turns the mode on
  (`frontend/src/bigpicture/settings/LibrarySection.tsx`), which gives two as examples;
- [qam-panel.md](qam-panel.md) § Library, which quotes the three a synced kind gets and the fallback;
- the format example in `SyncReporter._resolve_collection_memberships`' docstring
  (`backend/services/library/reporter.py`), and the `virtual_type` comment in `backend/domain/work_unit.py`.

`tests/services/library/test_reporter.py` asserts the union keys the reporter builds, so a label change fails there on
its own. The reporter needs the kind/virtual_type at its union key, so `WorkUnit.virtual_type` and
`CollectionMembership.kind` + `CollectionMembership.virtual_type` thread that identity through the fetcher →
orchestrator → reporter.

**Label-format constraint:** the reconcile parses the collection name with `/^RomM: \[([^\]]+)\]/i`
(`frontend/src/index.tsx`), so a label must contain **no** `]` character — it sits inside the single existing bracket
pair. Parens (`(Franchise)`) are safe; a bracket would truncate the parsed name and orphan the collection. Every
produced label is bracket-free (asserted in `tests/domain/test_collection_label.py`).

**No Force Full Sync on a mode flip.** Because the create-name and the reconcile's `activeNames` both derive from the
same `romm_collection_app_ids` keys, flipping the mode is applied by the ordinary **complete-set reconcile** on the next
normal sync: the reporter re-emits the complete set of enabled collections under the new keys, `onSyncComplete` creates
the new-named collections and deletes any old-named collection absent from the new complete set. No skip-state
invalidation is involved (same mechanism owner-scope reshaping uses). A change to a label reaches Steam the same way: a
sync that reaches the collection creates it under its new name, and the first sync that finishes in full (its
`sync_complete` carries no `cancelled`) deletes the old one — a new Steam collection, not the old one renamed.

**The preview names collections by the same key.** A completed run records the reporter's keys as its
`collections_completed`, and the preview's `collection_diff` builds its current set with the same
`steam_collection_key`, so an unchanged set of Steam collections shows no collection change in either mode, and the
Added and Removed names the Sync page lists are those keys, the name-part of each Steam collection (`Filter (Smart)`
under `by_label`). It compares them without regard to case ("Name identity is case-insensitive", below). A mode flip is
a real rename: the preview lists each labelled collection's new name as added and its old name as removed, except a name
another collection already carries on that side; this holds until a run completes. Under `by_label` the same holds for a
standard collection over a run recorded while standard collections still carried a `(Standard)` label.

**Name identity is case-insensitive (#1569).** Steam collapses collection names by a **case-insensitive** identity — two
collections whose display names differ only in case (`RomM: [7 up]` vs `RomM: [7 Up]`) are the same Steam collection, so
creating the second silently overwrites the first and loses its games. To match, Tender folds both names wherever it
asks whether two names are one Steam collection, by one rule on both sides: lower case, then upper case, then lower case
again — `fold_collection_name` (`backend/domain/collection_name.py`) on the backend, `foldCollectionName`
(`frontend/src/utils/collectionName.ts`) on the frontend, both tested against the names in
`tests/domain/collection_name_folds.json`. It is that chain rather than Python's `str.casefold()`, which JavaScript has
no counterpart for, and rather than a single lower-casing, which keeps `Straße` apart from `STRASSE`. Python and
JavaScript compute the chain identically on every code point both assign, and it joins each code point with everything
`str.casefold()`, `toLowerCase()` or `toUpperCase()` joins it with — plus one pair `str.casefold()` keeps apart, a
dotless `ı` and `i` (measured: CPython 3.13 / Unicode 15.1 against Node 24 / Unicode 17). For grouping and for the
create/find, joining more is the safe direction: what loses games is a pair Steam holds as one and Tender keeps apart.
On the delete matchers, joining more means deleting more.

Where the fold applies: the reporter groups both `romm_collection_app_ids` and `platform_app_ids` by the folded key,
keeping the first-seen original casing for display (which casing wins is irrelevant — every match folds both sides); the
preview's `collection_diff` and `platform_collection_diff` compare folded names the same way; and the frontend
create/find (`createOrUpdateCollections` / `createOrUpdateRomMCollections`), the cleanup matchers
(`clearPlatformCollection` / `clearAllRomMCollections`) and the `onSyncComplete` stale-delete (`removeStaleCollections`,
`frontend/src/index.tsx`) fold the whole name, the `RomM:` prefix and the host suffix included — so a case variant of
one of this machine's names matches as that name does, while one carrying another machine's suffix does not — a hostname
differing from this one only by case counts as this machine, as it does for Steam. This is safe because Steam's identity
is case-insensitive: two collections differing only by case can never coexist, so there is never an ambiguous match to
disambiguate. The DB is unaffected — `collection_sync_state` is keyed by `(collection_id, collection_kind)`, never by
name — so there is no migration.

## App IDs and Artwork

`SteamClient.Apps.AddShortcut()` returns the real `appId`, so Tender does **not** compute shortcut app IDs itself —
there is no app-ID generator in the codebase. Steam **assigns** the `appId` at creation and it is stable for the
shortcut's lifetime, which is why mutating `launchOptions` or `startDir` keeps the same `appId` (see
[Updating existing shortcuts](#updating-existing-shortcuts)) while delete + recreate yields a new one.

> **Errata (2026-07): the appId is not `CRC32(exe + appName)`.** Earlier docs described the `appId` as
> `CRC32(exe + appName)`. On-device inspection of 68 live shortcuts Tender created matched **none** against any CRC32
> candidate (exe/name variants, quoted/unquoted, with/without a trailing NUL, top bit set), and the live appids are
> uniformly spread across `[0x80000000, 0xFFFFFFFF]` — consistent with random assignment at creation (and with the
> community observation that delete + re-add yields a different appid). The load-bearing facts are unchanged: the appId
> is stable for the shortcut's lifetime (so `launchOptions` / `startDir` edits are appId-safe), delete + recreate yields
> a new appId, and Tender's identity model never computes appIds — it records Steam's assigned id in
> `roms.shortcut_app_id` and detects ownership by the exe path. Only the derivation _mechanism_ was wrong.

The frontend stores the returned `appId` and the backend persists it as `shortcut_app_id` on the ROM's `roms` row (the
synced-ROM registry; reverse-lookupable by `shortcut_app_id`). The frontend resolves rom_id ↔ appId through the
backend's `get_app_id_rom_id_map()` endpoint, which reads that binding.

The signed-int32 helper `to_signed_app_id(app_id)` remains in `backend/domain/sgdb_artwork.py` (alongside the SGDB
endpoint/asset-type maps) for the `shortcuts.vdf` record format, but no longer has a production caller now that the icon
write goes through `SteamClient` rather than editing the VDF.

### Artwork file naming

Grid artwork is stored at `userdata/<user_id>/config/grid/`, keyed by the shortcut's real `appId`:

| Suffix             | Artwork Type           |
| ------------------ | ---------------------- |
| `<appId>p.png`     | Portrait grid (cover)  |
| `<appId>_hero.png` | Hero banner            |
| `<appId>_logo.png` | Logo overlay           |
| `<appId>.png`      | Wide grid / horizontal |
| `<appId>_icon.png` | Icon                   |

Each form also occurs with a `.jpg` / `.jpeg` extension. On shortcut removal Tender deletes the **full** suffix ×
extension set for the removed appId (`ArtworkService.remove_artwork_files`), so companion art (hero/logo/icon/wide)
never outlives its shortcut. Files a removal missed historically are reclaimed by Data Management's **Grid images**
cleanup (`cleanup_orphaned_grid_images`): candidates are only grid-image-named files whose appId sits in the
non-Steam-shortcut range (`[0x80000000, 0xFFFFFFFF]` — see the errata above; store-game custom art is out of range and
never touched) and whose appId belongs to no live shortcut in the frontend's full scan; if any bound
`roms.shortcut_app_id` is missing from that scan the cleanup refuses and deletes nothing.

`ArtworkService` (cover staging/finalisation, renaming the staged cover to `{app_id}p.png`) and `SteamGridService` (SGDB
hero/logo/grid/icon) own the artwork flow. The icon is a two-step write: `SteamGridService.save_shortcut_icon` writes
the icon PNG into the grid dir via `SteamConfigAdapter.write_shortcut_icon` and returns its `icon_path`; the frontend
then points the live shortcut at it with `SteamClient.Apps.SetShortcutIcon(appId, icon_path)`. The backend no longer
edits the `shortcuts.vdf` `icon` field — Steam is memory-authoritative and clobbered that write, so pointing the
shortcut must go through `SteamClient` (see
[shortcuts.vdf is memory-authoritative](#shortcutsvdf-is-memory-authoritative)).

Covers are applied per created shortcut through Steam's own artwork API during the apply, so tiles show their real cover
in-session with no client restart. Right after a newly created shortcut resolves its `appId` in the per-item apply loop
(`applyCoverArtwork` in `syncManager.ts`), the frontend fetches the cover bytes for that ROM
(`get_artwork_base64(rom_id)`) and hands them to `SteamClient.Apps.SetCustomArtworkForApp(appId, base64, "png", 0)`.
Steam decodes the image, owns the tile, and writes the file itself as `{app_id}p.png` in the grid dir — the same path
the backend also writes — so the cover appears as the shortcut is created and Steam refreshes the tile in-session.

The key properties:

- **Creates only.** A cover is applied only when the item is a fresh create. An updated or rebound shortcut keeps its
  existing grid file (in-session cover refresh on a version/metadata change is tracked separately, `#1386`).
- **One cover per item, under the session-budget gate.** The cover is fetched and applied _inside_ the existing 50
  ms-paced per-item loop — never prefetched or batched. Decoding many covers resident at once is exactly the CEF heap
  overflow (`#797`) that crashed `SharedJSContext` on large libraries; the session-budget gate (see
  [ADR-0024](../adr/0024-session-budget-rss-gate.md)) prices each create at its permanent cost plus the cover's
  transient peak and pauses the run before the renderer nears its heap cliff.
- **Fail-soft.** A cover that can't be fetched (`base64: null`) or applied (a throwing `SetCustomArtworkForApp`) is
  logged and never fails the shortcut — the shortcut is already created, and the backend's commit-time grid write is the
  durability net.

The backend also writes each `{app_id}p.png` grid file at commit (`SyncReporter._finalize_cover_path` →
`ArtworkService.finalize_cover_path`). That copy costs no renderer heap and is the durability net: it lands the grid
file even if a per-item API call failed, so a residual gray tile resolves the next time the game's page is opened or on
the next client restart.

### Game page artwork

The hero, the logo and the wide grid come from SteamGridDB, and Steam has to be handed them in-session through
`SteamClient.Apps.SetCustomArtworkForApp`: a file written into the grid directory shows only after Steam restarts. The
first visit to a game's page in a session sets each of the three **only where the slot is empty** (`fillEmptyArtwork` in
`utils/artwork.ts`). **Refresh Artwork** and the SteamGridDB game picker are an explicit request and still replace every
slot (`applyArtwork`), and so does a version switch: once artwork was applied for one ROM of an app id, the newly bound
ROM's is applied in the replacing mode, an image set by hand included. The replacing mode records the wide grid and the
logo it set, or Steam refused, under the ROM now bound, as the fill does below: `get_sgdb_artwork_base64` answers a wide
grid or logo with its image id, the hash of its bytes and the ROM's SteamGridDB id for the report. The shortcut artwork
job fills the wide grid and the logo in the background by the same rules ([Shortcut artwork](#shortcut-artwork)), so a
first visit usually finds them filled; the hero is the game page's alone, and the icon keeps its own rules, under the
same section.

**When a slot is filled** (`domain/artwork_slot.py`, `is_slot_filled`). A slot already holding an image is left alone,
whoever set it — an image set by hand is never replaced. It is filled when Steam's grid directory holds a regular file,
links followed, named exactly the slot's name plus one extension: `<appid>` for the wide grid, `<appid>_logo`,
`<appid>_hero`. Any extension counts but `.json`, where Steam keeps the logo's position: which image formats Steam reads
there is not documented beyond PNG and JPEG, and missing an image set by hand would put a second one beside it. Nothing
else counts: not `<appid>p`, the cover; not a link that leads nowhere; not a file named by the 64-bit game id that other
tools write. `SteamConfigAdapter.grid_file_names` lists the regular files; the rule reads the names.

**One slot at a time.** For each slot the frontend asks `get_sgdb_slot_artwork(rom_id, app_id, slot)`, which answers
`skip` for a filled slot or one a record rules out, `none` when SteamGridDB's list is empty or it does not know the game
(a 404), or the ROM has no SteamGridDB id, `failed` for an unsuccessful answer, a list with no usable entry or a
download that failed, or the image's base64 with SteamGridDB's image id, the SHA-256 of its bytes, the ROM's SteamGridDB
id and an `sgdb_artwork` lease. The frontend sets the image under that lease, waits for it, and only then asks for the
next slot, so one image at a time is in the renderer. For the wide grid and the logo it then reports what Steam made of
it through `report_sgdb_slot_artwork` — `set`, or `refused` when `SetCustomArtworkForApp` failed — passing the fetch's
answer back; the backend cannot see Steam's answer itself.

**What was found is recorded** per ROM and slot, for the wide grid and the logo, in `artwork_slot_outcomes`
([database design](database-design.md)): `set` with the image id and the hash, `none`, or `refused`, the ROM's
SteamGridDB id at that time, and when. A `none` or `refused` row keeps the slot from being asked again for 7 days
(`RULED_OUT_SECONDS` in `domain/artwork_slot_outcome.py`): SteamGridDB may have an image tomorrow, and a week costs one
request per slot instead of one at every first visit in a session and at every run of the shortcut artwork job, which
starts after each sync. The row stops counting at once when the ROM's SteamGridDB id changes, and **Refresh Artwork**
deletes the game's rows (in `get_sgdb_resolution`, before it resolves the id). A `set` row never rules a slot out: a
slot whose file is gone is asked again. Rows are keyed by ROM rather than app id, because a re-created shortcut gets a
new app id. The hero is not recorded.

**Which image** (`domain/sgdb_artwork.py`, `choose_candidate`), in the one download path the game page, Refresh Artwork,
the picker and the shortcut artwork job share (`SteamGridService._download_sgdb_artwork_io`). Images are chosen among
SteamGridDB's candidates by their stated size and never scaled locally — decoding in pure Python takes seconds for a
large logo and reads no JPEG or WebP. The wide grid and the logo are asked as static images only (`types=static`), the
wide grid in its two sizes only, since `/grids` also lists the portrait covers. The wide grid prefers 920×430, Valve's
own library header size, then 460×215, then the first candidate; the logo prefers the first candidate between 640 and
2048 pixels wide, then the first candidate: the band keeps SteamGridDB's top-ranked logo unless it is tiny or many times
larger than Steam draws it. SteamGridDB lists an asset's candidates in its website's "Highest Score (Beta)" order and
reports a score of 0 for every image, so the first candidate is the best-rated one (checked for one game against the
website by the maintainer). A slot never stays empty for want of a preferred size.

**The artwork cache** keeps a downloaded wide grid or logo as `{rom_id}_grid_{image_id}.png` /
`{rom_id}_logo_{image_id}.png`: the cache is re-derivable and may vanish at any time, so which image it holds lives and
dies with the file rather than in the database. A cached wide grid or logo named without an id is not counted as cached;
it is downloaded again under the rule above and replaced, once per slot. The hero and the icon keep `{rom_id}_hero.png`
and `{rom_id}_icon.png`.

### Shortcut artwork

Every shortcut Tender manages gets its SteamGridDB icon, wide grid and logo in the background, never inside the apply
loop: there a cold icon fetch cost about a second per minted shortcut, almost all of it SteamGridDB's image CDN missing
its edge cache (measured by the maintainer on a Steam Deck, Steam client version 1788652215).

**What Steam shows**, measured on a Steam Deck LCD in Desktop Mode, in the desktop client, Steam client version
1788652215 (2026-10-05):

- A shortcut with **no** icon shows a newly set one only after Steam restarts — through `SetShortcutIcon` or by hand in
  Properties alike. **Replacing** an icon a shortcut already has shows at once, and so does an icon set right after
  `AddShortcut`, and new bytes written to the same file followed by `SetShortcutIcon` with the same path.
- Steam rewrites `shortcuts.vdf` soon after a shortcut's creation and after an icon is set, not only on exit. The
  maintainer measured the first on the same client: five shortcuts added 50 ms apart were all in the file 0.15 s after
  the last `AddShortcut`.
- Steam's app overview carries no icon for a shortcut that had none when the session began: `icon_hash`, `icon_data` and
  `icon_data_format` read `undefined` before and after `SetShortcutIcon`. So the file is where the icons are read from.

**The placeholder.** The apply loop gives every shortcut it mints the placeholder icon right after its cover
(`applyPlaceholderIcon` in `syncManager.ts`): `tender-icon-placeholder.png`, a transparent 1×1 PNG of 68 bytes
(`domain/shortcut_icon.py`) that the backend writes into the grid directory and rewrites when it is missing; each
`sync_apply_unit` frame names its path as `icon_placeholder_path`. The list shows an empty spot rather than a grey box,
and the job's icon is then a replacement, which shows at once. A shortcut the sync adopts (an untracked orphan) gets
none, since it may carry an icon somebody set by hand. Without a SteamGridDB API key no job would replace the
placeholder, so the frame names none and a minted shortcut keeps Steam's own grey box. A shortcut that had no icon
before — one minted without a key included — shows its first one after one Steam restart.

**Why 64×64.** In the desktop client the library list draws icons at 20 px (40 px at 2×), SteamGridDB's are often 512 or
1024 px, and Steam holds a shortcut's icon in the renderer as base64 for the whole session once it has one: a 1 MB PNG
becomes 1.35 MB of `icon_data`. Both are the maintainer's measurements (Steam Deck, client 1788652215). 64 px, the size
the maintainer chose, leaves room above the largest size drawn. So every SteamGridDB icon Tender writes into the grid
directory, the game page's included, is downscaled to fit 64×64 with its aspect kept (`adapters/icon_image.py`); the
full-size file stays in the artwork cache. Where the vendored `png` does not load, icons are written full size and that
is logged once.

**The job** (`services/shortcut_artwork.py`) gives every bound shortcut its icon by the rules above, and its SteamGridDB
wide grid and logo where the slot is empty, so a library looks complete without opening each game. The hero stays with
the game page.

- **Worklist:** every bound shortcut whose `icon` in `shortcuts.vdf` is empty or a file named like the placeholder
  (`domain/shortcut_icon.py`, `icon_worklist`) — by name, so a placeholder minted under another Steam account's grid
  directory still counts — and every bound shortcut's wide grid and logo that no file in Steam's grid directory fills
  and no recorded outcome rules out (`domain/artwork_slot_outcome.py`, `slot_waits`; the rules are those of
  [Game page artwork](#game-page-artwork)). Derived at every run and never stored, so a stopped run leaves exactly the
  rest. The logo icon, or any other icon path — an icon somebody set by hand — is left alone. A bound shortcut the file
  does not hold yet is counted in the log and its icon left for the next run; its wide grid and logo do not depend on
  the file. Where the grid directory cannot be listed, no wide grid or logo is asked for, and the icons still are.
- **Two passes:** first each shortcut's icon and wide grid together, shortcut by shortcut, then the logos. A sync may
  run in the desktop client, where the icon shows in the library list, or in Big Picture, where the wide grid shows in
  search and among recent games, and neither should wait for the other; a logo shows only on the game page, which fills
  an empty one itself on opening. The number of requests is the same in any order.
- **One icon:** `SteamGridService.do_fetch_shortcut_icon` serves it from the artwork cache first. A ROM with no
  SteamGridDB id stored answers "no icon" without a request; no name search runs, since picking from one needs a person.
  The fetch answers the icon downscaled, and the job writes it as `{app_id}_icon.png`. A file the downscale cannot read
  — a page in place of an image, a truncated PNG — is a failure and leaves the artwork cache, so the next run downloads
  it again; the game page writes such a file as it came, with a warning.
- **One wide grid or logo:** `SteamGridService.do_fetch_slot_artwork` puts SteamGridDB's image into the artwork cache by
  the rule the game page uses ("Which image", above) and answers that it is ready; no bytes leave the backend here. A
  ROM with no SteamGridDB id stored gets a `none` record without a request; a game SteamGridDB has no candidate for, or
  does not know (a 404), gets one too. The record keeps the slot off the worklist for a week. A failure records nothing,
  so the next run asks again.
- **No icon:** a game SteamGridDB has no icon for, one with no SteamGridDB id stored, or one whose id SteamGridDB does
  not know, gets Tender's logo — `tender-icon.png`, shipped in `defaults/` and copied into the grid directory. The job
  does not ask again; **Refresh Artwork** does, and so does the game page's passive artwork apply the first time the
  page is opened in a session, for a game whose SteamGridDB id is known. Where the logo cannot be read, that is logged,
  and the shortcut keeps its placeholder and stays on the worklist. A timeout, an answer that says it failed, or any
  other failure is not "no icon": the shortcut stays on the worklist and the next run asks again.
- **Pace:** four shortcuts at a time. A 429 from SteamGridDB's API or its image CDN (`SgdbRateLimitedError`) pauses
  every worker: 30 s, twice as long at each 429 in a row, at most 15 minutes, then on, with only what the 429 met asked
  again; every run starts the pause over. A 401 or 403 from the API — a key SteamGridDB does not accept — ends the run
  with one warning; what was written before it is still handed over.
- **Hand-over:** what is finished goes to the frontend as the `shortcut_artwork` event in batches of up to 25 entries,
  each batch under one prune lease (`emit_under_lease`): `icons`, `(app_id, icon_path)` pairs, and `artwork`,
  `(app_id, rom_id, slot)` triples that carry no image. `applyShortcutArtwork` (`utils/shortcutArtwork.ts`) sets each
  icon, then each wide grid and logo one at a time: it fetches the image's base64 through `get_sgdb_slot_artwork` —
  which skips a slot that has filled since — sets it with `SetCustomArtworkForApp`, waits, reports the outcome through
  `report_sgdb_slot_artwork`, and only then fetches the next (`setJobSlotArtwork` in `utils/artwork.ts`). It gives the
  lease back after the last, and stops once the lease is cancelled. The job hands the next batch over only once that
  lease has ended — given back, disowned by a panel that mounts, or expired — so one image at a time is in the renderer
  across batches too, an image too large fails only its own call, and what is set is what the cache holds at that
  moment. That holds across a stop too: a stop does not wait for the frontend, so a run stopped while it waits leaves
  its batch out, and the next run waits for that batch before it reads the renderer and hands one over. It waits at most
  10 minutes (`_BATCH_WAIT_SECONDS`), past the frontend's 5-minute bound on a batch and the lease's 300 s expiry, and
  ends the run when a batch outlasts that. A batch no panel heard — Steam restarted, as it does at every switch between
  Game Mode and Desktop Mode — ends the run, and the job runs again once a panel is connected.
- **Steam's memory:** before each batch, so after the batch before it has been set, the job reads the renderer's memory
  through the sync's own `SessionBudgetMonitor` (`LibraryService.renderer_above_ceiling`; at or above `GC_SKIP_BELOW_KB`
  Steam collects its garbage first, bounded at about 5 s). At or above the session budget's ceiling the run ends without
  handing the batch over; only a Steam restart frees that memory, so the job waits for the panel to go and a panel to
  connect again — polled every 5 s — and the next sync asks for it too. What was not handed over stays on the worklist.
- **Triggers:** the end of every sync run — after an apply, 10 s later, so Steam has rewritten its file with the
  shortcuts the run minted — backend start once a panel is connected, the end of a removed-game cleanup and the end of a
  shortcut removal (one whose lease expires, or is released as orphaned when a panel mounts, leaves it to the next
  trigger). Without a SteamGridDB API key it does nothing.
- **Yielding:** the job yields to a sync in flight, a removed-game cleanup and a shortcut removal (from the call that
  names what to remove until its report, or until its lease expires or is released as orphaned). It asks before each
  fetch, write and hand-over; once it has yielded, the run ends, and what it wrote but did not hand over waits for the
  next run, which the end of what it yielded to asks for. A removal stops the run under way once its claim is held
  (`stop_for_removal`), and so does its report, which may arrive after its lease is gone and a run has started. A
  cleanup's start stops it (`stop_for_cleanup`) before it reserves its start, and no run starts until the cleanup
  resumes it (`resume_after_cleanup`). What either stop waits for, and what a fetch it abandons may still do, is
  `ShortcutArtworkJob`'s (`services/protocols/cross_service.py`). The job is no endpoint, so it takes no conflict-rule
  `hold`; the lease on each batch is a claim like any event's, and a cleanup pressed while the frontend holds one is
  refused ([Prune conflicts](removed-game-cleanup.md#prune-conflicts)).
- **A pick on the game page wins:** `save_shortcut_icon` and the job's write take turns under one lock per app id, and
  the job writes only while the page has saved no icon since it read that app's generation; the hand-over leaves out a
  shortcut whose generation has moved since, so a logo icon queued before a pick is left out; and the frontend skips a
  shortcut whose artwork apply is under way (`isArtworkApplyInFlight`), and sets no wide grid or logo once a game page's
  apply for the shortcut has started since it began fetching it.

## Pre-launch launch-options confirmation

Both launch funnels (the game-detail Play button and Steam's direct-launch watcher) re-fetch the selected ROM's resolved
command and confirm-write it immediately before `RunGame`. Ordinary fetch or Steam-write failures remain best-effort and
the launch proceeds. A three-second endpoint timeout is different: the already-cancelled launch remains blocked, while
the unresolved endpoint call stays observed so a lease token returned later is released without a Steam write. The Play
button's launch captures its component generation before gate and modal waits; the button's unmount makes that admission
stale, and even an immediate remount cannot let the old chain write launch options or invoke `RunGame` under the new
generation. The watcher's launch has no component owner, so no teardown short of a JS-context rebuild stops its chain;
the timeout above still does.

## Key Files

| File                                                  | Purpose                                                                                                                                                                                                                                                                                                                                        |
| ----------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `frontend/src/utils/steamShortcuts.ts`                | `addShortcut()`, `removeShortcut()`, `getExistingRomMShortcuts()`, `getLiveRomMShortcutAppIds()` (raw live appId scan for the sync-start reconcile) — frontend shortcut CRUD. The existing-shortcut scan emits a sync heartbeat every 10s between batches so a large library can't stall the run past the backend's per-unit heartbeat timeout |
| `frontend/src/utils/syncManager.ts`                   | Listens for sync events, orchestrates shortcut creation/removal, artwork application, collection management. `reconcileStaleShortcuts()` runs the sync-start reconcile of Steam-UI-deleted shortcuts. Caches the existing-shortcut scan per run (keyed by the `sync_apply_unit` `run_id`) so it scans Steam once per run, not once per unit    |
| `backend/services/shortcut_removal.py`                | `ShortcutRemovalService` — resolves shortcut-removal sets, unbinds removed ROMs, and runs `reconcile_live_shortcuts` (the sync-start reconcile of Steam-UI-deleted bindings)                                                                                                                                                                   |
| `frontend/src/utils/collections.ts`                   | Machine-scoped Steam collection management                                                                                                                                                                                                                                                                                                     |
| `frontend/src/bigpicture/patches/gameDetailPatch.tsx` | The tree patch for Steam's game page — injects RomMPlaySection for custom game detail UI. Installed on the route component's render by `installGamePagePatch.ts` ([frontend-bundles.md](frontend-bundles.md#tenders-section-on-steams-game-page))                                                                                              |
| `frontend/src/utils/metadataPatches.ts`               | Store patches for description, associations, categories, release date display                                                                                                                                                                                                                                                                  |
| `backend/adapters/steam_config.py`                    | `SteamConfigAdapter` — VDF read/write, grid dir, shortcut icon write, Steam Input config                                                                                                                                                                                                                                                       |
| `backend/services/library/`                           | LibraryService — builds shortcut data, drives per-unit sync apply                                                                                                                                                                                                                                                                              |
| `backend/domain/sgdb_artwork.py`                      | `to_signed_app_id`, SGDB asset-type/endpoint maps and their queries, the image choice by stated size (`choose_candidate`), the artwork cache's file names (`cached_artwork_name`, `cached_image_id`)                                                                                                                                           |
| `bin/tender-rom-launcher`                             | Pure `exec "$@"` wrapper invoked by Steam — runs the full launch command baked into the shortcut's launch options; owns no state, no path resolution, no emulator knowledge. Shipped here, **run from `<bin root>/tender-rom-launcher`**: `bootstrap()` installs this copy there at every start (ADR-0038)                                     |

## Common Pitfalls

### Quoting exe breaks launches

Pre-quoting the exe path in `AddShortcut` or `SetShortcutExe` causes double-quoting. Steam tries to execute
`""/path/to/exe""` and fails with "file not found." Always pass raw paths through the SteamClient API.

### Empty Set* params after AddShortcut

Calling `Set*` methods too quickly after `AddShortcut` (before the new app's overview is registered) results in the
properties not being saved. The shortcut appears in the library but with wrong or missing exe/startDir/launchOptions.
Launches fail or open the wrong thing. Tender gates the `Set*` calls on an overview-readiness poll
(`waitForAppOverview`, 1000ms fallback) rather than a fixed delay.

### Removal-churn can corrupt shortcut state

`SetAppLaunchOptions` on an existing shortcut is reliable (validated in
[#827](https://github.com/danielcopper/romm-tender/issues/827); see
[Updating existing shortcuts](#updating-existing-shortcuts)) — the historical "property updates may not persist" warning
has been narrowed. The remaining hazard is **removal-churn**: adding and removing many shortcuts in one pass can corrupt
Steam's in-memory shortcut state. A Steam restart clears it. Two things keep churn down. The sync engine processes
removals before additions, and every launch-options write uses the fire-then-poll `setLaunchOptionsConfirmed` so a
silently dropped write is observable rather than assumed. And **mass removals are awaited and chunk-paced** through the
shared `removeShortcutsPaced` helper (`frontend/src/utils/shortcutRemoval.ts`, over `pacedForEach`,
[#977](https://github.com/danielcopper/romm-tender/issues/977)): every bulk removal path — Library › Platforms' removal,
Data Management's "Remove all shortcuts" with its live-orphan sweep, and its non-Steam-games removal, **and** the
sync-run stale-shortcut cleanup (`sync_stale`, fired at run finalize) — awaits each `removeShortcut` in sequence and
yields a 50ms breather every 25 removals, so the CEF renderer never blocks and thousands of removals can't stack as
fire-and-forget promises. (The `sync_stale` handler records its "removed" delta for the terminal toast up front, before
the first breather, so the paced removal can't leave the count partial when `sync_complete` interleaves.)

### AddShortcut / RemoveShortcut timing between shortcuts

Bulk shortcut loops corrupt Steam's internal store if driven too fast — added shortcuts may silently fail to register,
and removals churn the in-memory state (above). Both cadences live in one place: the shared paced loop `pacedForEach` in
`frontend/src/utils/pacedOps.ts`, which iterates awaiting each item and yields a breather between chunks (no trailing
delay). The two callers differ only in chunk size:

- **Add** (`syncManager.ts` — `processUnitShortcuts`, `processCoverRefreshes`) paces **one item at a time**: a 50ms
  breather after every `addShortcut()` / cover apply, plus the per-unit heartbeat + cancel hooks.
- **Remove** (`removeShortcutsPaced` in `frontend/src/utils/shortcutRemoval.ts`, shared by the page removals above and
  the `sync_stale` cleanup) paces in **25-item chunks with a 50ms breather** between them. A removal is a single cheap
  call, so chunked yielding keeps a 5000-game teardown at ~seconds of overhead instead of the ~4 minutes strict
  50ms/item would cost, while still letting the renderer breathe. RomM-shortcut and `sync_stale` removals hold renewable
  prune conflict leases for the complete paced loop even though the backend does not await the frontend's stale removal.

### The apply is chunked; a heartbeat timeout must not discard a chunk's delivered bindings

A unit's emitted shortcuts are split into fixed-size chunks (200,
[ADR-0023](https://github.com/danielcopper/romm-tender/blob/main/docs/adr/0023-chunked-per-unit-apply.md)); the pipeline
emits one `sync_apply_unit` per chunk (carrying `chunk_index` / `chunk_count` / `chunk_offset` / `unit_total`,
`shortcuts` = the chunk slice), then waits for the frontend's `report_unit_results` ack — echoing the `chunk_index` back
— and commits that chunk's `roms` rows durably before emitting the next. A mid-unit crash, cancel, or timeout forfeits
only the in-flight chunk; every chunk committed before it stays committed. See
[Backend Architecture — per-unit apply](backend-architecture.md) for the full loop.

If the frontend stops heartbeating for longer than the per-chunk timeout (`_UNIT_HEARTBEAT_TIMEOUT_SEC`, 60s — e.g. a
chunk slow enough that real heartbeats lag), the wait gives up. But by then the frontend has **already created that
chunk's Steam shortcuts** and will still fire its late `report_unit_results`. Dropping that ack is data loss: the
bindings are never written to `roms`, so `get_app_id_rom_id_map` doesn't know about the shortcuts, and the next sync
re-creates them as **duplicates** (an unmapped exe-detected shortcut takes the `addShortcut` branch).

So a heartbeat **timeout** is handled differently from a **user cancel** (#1052 / #1367):

- **User cancel** — the in-flight chunk is intentionally discarded. The orchestrator clears the staging and nulls
  `unit_complete_event`, so a stray late ack can't commit a cancelled chunk.
- **Heartbeat timeout** — the orchestrator moves the abandoned chunk into an `abandoned_chunk` stash on
  `LibrarySyncStateBox` (`stash_abandoned_chunk`): its run/unit/chunk identity plus **this chunk's** ROMs (only the
  abandoned chunk), while keeping the whole-unit staging live for the commit to read and clearing the dispatch identity.
  The stash lives **outside** the run-lifecycle state and deliberately survives the run's teardown (`finish_run` nulls
  `current_sync_id`), because in production the late `report_unit_results` arrives **after** the run has wound down —
  the window an earlier design missed, where the active-unit ack check could no longer match and the recovery was
  unreachable (#1367). The late ack matches the stash **by identity** (`take_abandoned_chunk`) and drives
  `commit_unit_results` itself over the stashed rows (binding + metadata), never stamping a timed-out platform complete.
  Bounded lifetime: the next run's `try_begin_run` clears an unacked stash.

The committed binding self-heals the duplicate hazard: a bound `roms` row is mapped by `getExistingRomMShortcuts` next
sync, so `resolveShortcutAppId` takes the update branch. The orchestrator does **not** add active orphan deletion — a
Steam shortcut is the sole record of its tile (the "never delete data that exists nowhere else" invariant).
