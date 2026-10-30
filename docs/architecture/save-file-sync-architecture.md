# Save File Sync Architecture

## Overview

Tender provides bidirectional save file synchronization between RetroDECK and a self-hosted RomM server. Saves are
uploaded after play sessions and downloaded before game launch, enabling seamless multi-device play.

The scope is **a per-game set of save files that Tender can carry**. Which files those are is not a property of the
platform and never was: it belongs to the **emulator** that opens the game, and it is read live off the machine by the
vendored [emu-atlas](https://github.com/danielcopper/emu-atlas) resolver through `adapters/atlas_saves.py`. Services see
a `domain.save_answer.SaveAnswer` and never a resolver type.

That answer classifies every ROM into exactly one of **seven save states**, and the sync runs in only the first of them
— see [Save sync coverage](save-sync-coverage.md), which owns the states and the reasoning. Every file in a syncable set
syncs **independently against the server save sharing its own canonical target**, so a multi-file set never cross-mixes
extensions.

Tender used to hold its own per-system extension table (`domain/save_extensions.py`, retired). It was written from a
one-pass desk audit and it was wrong in both directions: it searched forever for an Amiga `.nvr` that no core writes,
and it never knew about the version digit in 3DO's `<stem>.0.srm`. Neither failure was visible — the search simply found
nothing.

## RomM Save API

Requires RomM >= 5.3.0 (release or higher core). Pre-releases at the exact floor (`5.3.0-beta.1`, `5.3.0-alpha.1`) rank
below `5.3.0` and are rejected; a higher core with a suffix (`5.3.1-beta`) passes. Tender rejects servers below the
floor with `reason: "version_error"`.

| Endpoint                                                 | Method | Notes                                                                                                                                                                                                                                                                                                  |
| -------------------------------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `/api/saves?rom_id={id}`                                 | GET    | Returns array. Each item now includes `slot`, `file_name_no_tags`, `file_extension`, `content_hash`, and `device_syncs` array.                                                                                                                                                                         |
| `/api/saves/{id}`                                        | GET    | Single save metadata with v4.7 fields                                                                                                                                                                                                                                                                  |
| `/api/saves?rom_id={id}&emulator={emulator}&slot={slot}` | POST   | Creates a new save entry. Slot-aware: `slot=default` causes RomM to append a timestamp to the filename (e.g. `Game.srm` becomes `Game [2026-03-24_15-18-50].srm`). Same filename + same slot = upsert. Different slot = new entry.                                                                     |
| `/api/saves/{id}`                                        | PUT    | Updates file content only. No metadata changes, no new entry created.                                                                                                                                                                                                                                  |
| `/api/saves/{id}/content`                                | GET    | Binary download by save ID (new in v4.7)                                                                                                                                                                                                                                                               |
| `/api/devices`                                           | GET    | List all registered devices for the authenticated user. Returns array of `{id, name, platform, client, client_version, last_seen, created_at, ...}`.                                                                                                                                                   |
| `/api/devices`                                           | POST   | Register a device. Accepts hostname, platform, client info. Returns `device_id` (UUID).                                                                                                                                                                                                                |
| `/api/devices/{id}`                                      | DELETE | Remove a device registration. Returns 204 No Content. PATCH (rename) is not supported (405).                                                                                                                                                                                                           |
| `/api/saves/delete`                                      | POST   | Bulk delete saves by ID. Body: `{"saves": [id1, id2, ...]}`. Returns result dict.                                                                                                                                                                                                                      |
| `/api/sync/negotiate`                                    | POST   | Open a Device Sync session. Body: `{device_id, saves: ClientSaveState[], rom_ids?: int[]}`. Returns `{session_id, operations[], total_*}` — the server's `upload`/`download`/`conflict`/`no_op` verdicts (+ a `reason`). Detects but never resolves; opening cancels this device's prior sessions.     |
| `/api/sync/sessions/{id}/complete`                       | POST   | Close a negotiated session. Body: `{operations_completed, operations_failed}`. Returns `{session}`.                                                                                                                                                                                                    |
| `/api/play-sessions`                                     | POST   | Ingest a batch (max 100) of `{rom_id, start_time, end_time, duration_ms}` under a top-level `device_id`. Additive per-device union; dedup on `(user_id, device_id, rom_id, start_time)`. Playtime, decoupled from save-sync ([ADR-0018](../adr/0018-native-play-session-tracking-additive-ingest.md)). |
| `/api/play-sessions?rom_id={id}`                         | GET    | List a ROM's stored play sessions (needs the `roms.user.read` scope). Summed by `duration_ms` for the reconcile `max()`; degrades to local-only without the scope.                                                                                                                                     |

**New parameters on POST:**

- `slot` — slot name (e.g. `"default"`). If omitted, save has `slot=null` (legacy behavior).
- `autocleanup` — whether RomM prunes old stacked versions. Defaults to **false**.
- `autocleanup_limit` — max save versions retained per slot (default: 10). Inert unless `autocleanup=true` is sent
  alongside it.

The `negotiate` / `complete` endpoints are wired into the adapter (`RommApiAdapter.negotiate_sync` /
`complete_sync_session`), typed by the `models/sync.py` schemas (`ClientSaveState`, `SyncOperation`,
`SyncNegotiateResponse`, …). As of [#1276](https://github.com/danielcopper/romm-tender/issues/1276) /
[ADR-0017](../adr/0017-client-baseline-detection-authoritative-negotiate-is-transport.md) they are the sync
**transport**, not the sync **brain**: for a confirmed non-legacy ROM the run opens a negotiate session (per-device
serialization) but the server's returned `operations` are **discarded**. The client's `compute_sync_action` decides
every action for every ROM, from `list_saves` — including legacy `slot:null` saves, which RomM cannot address through
the negotiate inventory param. See
[Negotiate as transport; one decision kernel](#negotiate-as-transport-one-decision-kernel).

- `device_id` — server-registered device UUID. On `GET /api/saves` it decides what each save's `device_syncs` carries —
  see [`GET /api/saves` writes no sync row](#get-apisaves-writes-no-sync-row).

**New fields on save metadata:**

- `slot` — the slot this save belongs to (string or null)
- `file_name_no_tags` — base filename without timestamp tags (e.g. `Game` from `Game [2026-03-24_15-18-50].srm`)
- `file_extension` — file extension (e.g. `srm`)
- `content_hash` — RomM's content hash of the save: a plain MD5 for a single file, or a per-entry combined MD5 for a
  zipped multi-file save (eliminates the download-and-hash slow path)
- `device_syncs` — array of per-device sync records: `device_id`, `device_name`, `is_current`, `last_synced_at`

Tender reproduces this `content_hash` byte-for-byte so a save's local and server hashes agree and sync converges:
single-file MD5 via `SaveFileStore.checksum_md5`, and the zip per-entry scheme via `SaveFileStore.content_hash` →
`domain.save_hash.combine_zip_entry_hashes` (sorted `name:md5(entry)` lines joined by `\n`, then MD5'd; dispatch is by
`zipfile.is_zipfile`, a content sniff, not the extension). A file that `is_zipfile` accepts but that cannot actually be
read as a zip — a corrupt or truncated archive, an encrypted entry, or a compression method this Python runtime lacks —
falls back to the plain `checksum_md5` rather than raising, so one poison save can never abort the whole sweep (#1470);
RomM's server degrades the same file to `content_hash=None`, so the kernel's truthiness guards reject a `None`-side
identity match and the fallback can never manufacture a false byte-identical match — it only keeps the local drift
baseline working. This hash reproduction is what the sync decision needs
([ADR-0016](../adr/0016-save-sync-hands-detection-to-romm-negotiate.md) /
[ADR-0017](../adr/0017-client-baseline-detection-authoritative-negotiate-is-transport.md) / #1234): the client's
`compute_sync_action` decides `upload` / `download` / `conflict` / `no_op` without a download-and-rehash, for every ROM
— including legacy `slot:null` saves, which RomM cannot address through the negotiate inventory param.

**Server-hash baseline; parity as the no-history fallback (#1468).** The identity question — "is my local file
byte-identical to that server save?" — is answered by a two-route disjunction, used at matrix rows 6d / 8a / 10a / 11a /
12a and in the 409 backstop (row 6d is reached by no Tender path — see
[Where the never-touched rows are reached](#where-the-never-touched-rows-are-reached)):

- **Provenance (primary).** At each sync boundary Tender stores the server's own `content_hash` alongside the local
  baseline, in `FileSyncState.last_sync_server_hash` (the `rom_save_files.last_sync_server_hash` column, added by
  migration 017). While the local file is unchanged since that baseline (`local_hash == last_sync_hash`), identity is
  proven by `last_sync_server_hash == server.content_hash` — two hashes RomM itself produced, so this route holds even
  if Tender's local hashing ever drifts from the server's. This is the robustness the issue buys: identity no longer
  depends on Tender's reimplementation staying byte-for-byte identical to RomM's scheme.
- **Parity (fallback).** A file with **no sync history on this device** (fresh reinstall, copied SD card, second device)
  has no stored server hash, so identity falls back to the direct `local_hash == server.content_hash` comparison — kept
  correct by the hash reproduction above (#1457). Branch 5's no-baseline slice and true fresh installs can only ever use
  this route, by design. Watch item: RomM's zip scheme is undocumented internal behavior and its author has publicly
  sketched a metadata-based variant that does not match the shipped code — re-verify the zip branch of this fallback
  against the RomM source on server version bumps. If it ever drifts, the fallback degrades to spurious conflict prompts
  (never data loss); the stored-server-hash primary route is unaffected.

The baseline pair is written together at the recorded-baseline writer sites (`RomSaveSyncState.record_transfer` on
upload/download; `RomSaveSyncState.adopt_head` for the adopt of an identical head — in the sync, the status read and
keep_local's adopt without an upload; `RomSaveSyncState.adopt_head_with_local_change` for copy to slot's
`upload_failed`) with honest provenance per flow — the upload response's `content_hash` (RomM hashed the bytes it
received), the adopted server save's `content_hash` on download or adopt, and for a failed copy upload the target slot
head's `content_hash` as both hashes. Where a response or save carries none, `record_transfer` and `adopt_head` record
`None` and `adopt_head_with_local_change` drops the file's tracking — never a hash fabricated from a local recomputation
— so a stored server hash always truthfully pairs with its `last_sync_hash`. The negotiate inventory
(`build_save_inventory`) still sends the locally-computed `content_hash`: echoing the stored hash buys nothing while the
computed hash already is RomM's scheme (#1457), and it stays the client's own statement of local content.

Every local hash that reaches this comparison — the matrix's `local_hash`, the post-op baseline (`last_sync_hash`), the
drift check, the slot-switch pending-changes check, and the keep_local adopt-without-upload probe — is computed with
`SaveFileStore.content_hash`, never the whole-archive `checksum_md5` (#1457). For a single-file save the two are
bit-identical, so this is a no-op there; for a zip container the whole-archive MD5 could never equal
`server.content_hash`, so feeding it would silently degrade the byte-identical dedup and adoption to spurious conflicts.
The hash comes from `SaveFileStore.measure`, which opens the file once for its hash, its size and its
[content size](#the-content-size). Within one sync run the same file is measured by several passes (negotiate inventory,
the matrix, the baseline write); `SaveFileStore.hash_memo_scope` memoizes the measure keyed by `(path, mtime_ns, size)`
so it is read once, bounded to that run and discarded on exit.

### Negotiate inventory builder (`SaveService.build_save_inventory`)

The negotiate POST sends this device's local save inventory; `SaveService.build_save_inventory()` assembles it as a
`list[ClientSaveState]`. It walks the `rom_save_sync_states` aggregate and keeps only ROMs **in scope** —
`slot_confirmed` is true **and** `active_slot` is a real (truthy) slot, which excludes both the unset `None` and the
legacy `""` slot. For each in-scope ROM it enumerates the local save files via `RomInfoService.find_save_files` and
emits **one `ClientSaveState` per file** (per-file granularity): a confirmed ROM with no local files contributes
nothing, and a ROM with several local files yields one entry each. Per entry, `content_hash` is always set via
`SaveFileStore.content_hash` — the zip-aware RomM-parity hash above, never the single-file `checksum_md5` — and
`updated_at` is the local file's mtime rendered as a UTC ISO-8601 string (`domain.iso_time.epoch_to_iso`, the round-trip
inverse of `parse_iso_to_epoch`).

`build_save_inventory(rom_id=None)` builds the whole-device inventory (the bulk `sync_all_saves` pre-negotiate); a
concrete `rom_id` scopes it to that one ROM (the single-ROM negotiate trigger). The in-scope predicate is identical
either way. The builder feeds the negotiate transport below (#1234 / ADR-0017). It is single-file-first; the
multi-file-per-slot collision case (several local files mapping to one slot) is a Phase 4 concern tracked in #1235.

### Negotiate as transport; one decision kernel

`SyncEngine._run_rom_sync` runs the **same** path for every ROM — confirmed non-legacy and legacy `slot:null` alike
([ADR-0017](../adr/0017-client-baseline-detection-authoritative-negotiate-is-transport.md), superseding the
detection-handoff of [ADR-0016](../adr/0016-save-sync-hands-detection-to-romm-negotiate.md)):

1. Load the ROM's `RomSaveSyncState`.
2. Fetch the slot's server saves with `list_saves`.
3. Run `compute_sync_action` per file (the decision matrix below).
4. Dispatch each outcome through `_dispatch_sync_action` to the save transfer (`SaveTransfer`,
   `services/saves/save_transfer.py`) — POST / GET plus the `.romm-backup` backup, and the per-file baseline it records
   in the same step. The #1062 shrink guard is part of the decision.

A download (`SaveTransfer.download_save_file`, the one used here and by a restore, a copy to another slot, a conflict
resolution and a slot switch) lands in `<save>.tmp` and replaces the local file only once complete, after the save
backup has moved the old one aside. On any failure up to that replacement it tries to remove the `.tmp` — a removal that
fails is not reported — and raises the download's own failure. A server copy fetched only to compare or to place
(`SaveTransfer.fetch_server_copy`: Keep Local's identity check, the slot-choice migration) names no device to RomM,
records no baseline, and removes its file the same way when the fetch fails.

There is **no** op→action fork any more: the client's kernel is the sole authority, and `StatusService` (the read-only
SAVES-tab path) runs the identical kernel, so the sync run and the game-detail status can never disagree. The wizard
gate still holds — an unconfirmed ROM never syncs automatically — so the "user decides on ambiguity" invariant is
unchanged.

**`negotiate` is the transport, not the brain.** When a ROM has a confirmed non-legacy slot, `_run_rom_sync` opens a
negotiate session around the kernel run and closes it in a `finally`. The session buys two things and only two:

- **Per-device serialization** — `negotiate` cancels this device's prior in-flight sessions server-side, reinforcing the
  in-process single-owner gate (#1202).

Playtime does **not** ride this session: it must be recorded for every ROM on every exit (including save-sync-off and
unconfirmed-slot ROMs), so it ingests through the standalone `/api/play-sessions` endpoint instead
([ADR-0018](../adr/0018-native-play-session-tracking-additive-ingest.md), which narrows the "ingest rides `negotiate`"
aside of ADR-0016/0017). The server's returned `operations` are **discarded** — detection is the client's
`compute_sync_action`. The request names the ROMs it is about as `rom_ids` — the one ROM, or the distinct ROMs of the
whole-device inventory — at most 500; above that the scope is left off and the server evaluates the whole library.
Opening the session is best-effort: a `negotiate` **transport** failure is non-fatal, the run simply proceeds
**without** a session (the kernel still syncs). The **one** exception is RomM's per-device `sync_enabled` switch —
`negotiate` is the only API-mode endpoint that enforces it, answering a 400 with detail
`"Sync is disabled for this device"` when the user has disabled sync for this device server-side. That specific 400 is
translated to `RommSyncDisabledError` and **stops** the run with `reason: device_sync_disabled` (a visible policy stop,
not a silent sessionless degrade); pre-launch treats it like the local save-sync toggle being off and skips silently so
the launch always proceeds (#1489). Scope limit: legacy/unconfirmed ROMs never open a session, so a library with no
confirmed named-slot ROM never reaches the switch. Legacy `slot:null` ROMs open no session at all (RomM cannot address
`slot:null` through the negotiate inventory param) but take the identical kernel path. The `complete` close is likewise
**non-fatal** — a session the server never hears closed times out and is cancelled by the next `negotiate`, so a failed
`complete` never fails the run.

**Cross-device pulls come from `list_saves`, not an op.** A confirmed ROM with a save on the server but **no local file
here** is picked up by the matrix directly: `list_saves` returns the server save, it is grouped by its canonical local
target with no matching local file, and `compute_sync_action` returns `Download` (matrix rows 4 / 5; row 3 is reached by
no Tender path — see [Where the never-touched rows are reached](#where-the-never-touched-rows-are-reached)). No
negotiate `download` op is needed or consulted.

**Upload safety is the 409 backstop, not a server verdict.** Every upload the kernel dispatches on the automatic path is
a POST with `overwrite=false` — see [Upload-time conflicts (the 409 backstop)](#upload-time-conflicts-the-409-backstop)
below. The old in-place PUT is no longer on the automatic path.

On the automatic path there is no PUT-in-place to bump, so the #748 "drop the PUT-bump" work is **moot** for it.
`add_save` (POST) already upserts this device's `device_save_sync` row (`last_synced_at = updated_at`) and serializes it
into the response's `device_syncs`, so `is_current` is true for us the moment the POST returns — the follow-up
`confirm_download` ack is redundant and is **skipped** when the response proves us current (the upload's own
acknowledgement inside `SaveTransfer.upload_save_file`, #1458). When `add_save` answers with a save its content matched
instead of a new version, what that answer reports and when the ack is still sent are under
[POST and PUT both upsert the calling device's sync row](#post-and-put-both-upsert-the-calling-devices-sync-row). The
version-switch flow (`versions.py`) PUTs through the same `SaveTransfer.upload_save_file`, so the same acknowledgement
follows its PUT — the PUT upserts too, so the ack is at most an idempotent re-ack (and is skipped if the PUT response
already proves us current) — and is out of scope here — see [Version Switch Flow](#version-switch-flow-rollback).

## Save Slots

RomM v4.7 introduces **save slots** — named containers for save files. This enables multi-save workflows (e.g.,
different save states per device).

### How slots work

- Each save on RomM belongs to a slot (or has `slot=null` for legacy pre-slot saves)
- Save identity on RomM: `(user_id, rom_id, filename)` **within a slot**
- `POST /api/saves` with `slot=default` causes RomM to append a timestamp to the filename: `Game.srm` becomes
  `Game [2026-03-24_15-18-50].srm`
- Same filename + same slot = overwrites (upsert). Different slot = new save entry.
- `PUT /api/saves/{id}` updates file content only, no metadata changes. No new entry created.
- `autocleanup_limit` parameter controls how many stacked versions are retained per slot

### Our default behavior

- Every game gets a `default` slot (configurable in QAM settings as "Default Save Slot")
- First upload = POST (creates save entry with timestamp filename, server assigns ID)
- Automatic uploads carry `autocleanup=true` together with the user-configured `autocleanup_limit` (QAM "Auto-cleanup
  limit"), so RomM prunes each slot back to the cap as versions stack.
- All subsequent syncs = POST a new version (`overwrite=false`, 409-backstopped), not an in-place PUT (#1276 /
  [ADR-0017](../adr/0017-client-baseline-detection-authoritative-negotiate-is-transport.md)). RomM stacks the versions
  and prunes them to `autocleanup_limit`.
- Single-device flow: one current head per game per slot, with older versions retained up to `autocleanup_limit`.
- Multi-device: newest-wins across devices; each device tracks the current head via `tracked_save_id`.

### Switching slots

`switch_slot` makes the active slot, the local saves directory, and per-file tracking coherent with the chosen slot in
one locked critical section (the per-rom `asyncio.Lock` — see the "Per-rom asyncio.Lock" section). After the pre-checks
pass (sync enabled, a real non-empty target slot name, ROM installed, not a content-dir layout, a save answer a sync
would carry, no un-uploaded local changes on tracked files, server reachable — the first that fails ends the switch with
a failure answer, `pending_uploads` names the changed files in `files`, and a RomM error from the fetch answers
`classify_error`'s reason rather than a refusal):

1. Every local save file the target slot does **not** provide is backed up into `.romm-backup` (never deleted outright)
   and dropped from tracking — so no stale extension (e.g. a `.rtc` left behind when the new slot holds only `.srm`)
   lingers to upload into the new slot, and a never-synced local save is always recoverable (#965, #1058). Before the
   switch's first move — a backup in this step, or a download in step 3 that backs up the local file it overwrites — the
   save backup checks that the folder can take backups (`SaveBackup.prepare_save_backup_folder`: not a link, inside the
   saves directory, created if missing, listable); a switch that will move nothing neither checks nor creates it. A file
   loses its tracking only once it was moved; once every backup went through, every tracked name the new slot does not
   provide loses its tracking too. The local files are those of the save answer the switch took at its start, as for the
   pending-changes check: one switch never reads two save answers.
2. Only then is the active slot flipped in memory — a file the backup could not move would otherwise sit under the new
   slot, and the next sync would upload the previous slot's save into it.
3. For each canonical local target the new slot **does** provide, the **newest** server save by `updated_at` is
   downloaded. Two server saves mapping to one target collapse to the newest, so the on-disk result and
   `tracked_save_id` are deterministic, not server-list-order dependent (#1058). The download backs up the file it
   overwrites through the same save backup.
4. The flipped slot + tracking are persisted once, **regardless of partial download failure**: a failed leg still
   persists this coherent state and answers the partial failure `SlotSwitchIncomplete` (`reason="switch_incomplete"`) so
   the caller can retry — the completed targets are already correct. A target whose acknowledgement failed is one of the
   failed downloads, with its file in place
   ([A download is acknowledged once placed, never marked at the GET](#a-download-is-acknowledged-once-placed-never-marked-at-the-get)).
   On the next sync a failed target with a baseline re-resolves as `Download` (row 10b), or adopts the head when the
   local file is byte-identical to it (row 10a); one with no local file re-resolves as `Download`; a never-synced local
   file at a target's name has no baseline, so the next sync answers a conflict (row 11b), or adopts the head when the
   file is byte-identical to it (row 11a) — [row 11](#why-row-11-splits-into-adopt-11a-vs-conflict-11b) gives why. Saves
   are never carried between slots; the switch only downloads or backs up, never uploads.

A backup that fails in step 1 ends the switch there, with the previous slot still active and nothing downloaded. Where
nothing was moved yet it refuses, and the state is not written: with `backup_folder_unusable`
(`SlotSwitchBackupFolderUnusable`) when the folder check failed, and with `backup_failed` (`SlotSwitchBackupFailed`)
when the first move did. Where some files were already moved, those stop being tracked, the state is persisted with the
previous slot active, and the answer is the partial failure `SlotSwitchStopped` (`reason="switch_stopped"`), whose
`set_aside` names the files now in `.romm-backup`. Nothing is put back: a move back could overwrite what an emulator
wrote in between, and one that failed would leave this answer anyway. A database write that fails after the backups or
downloads is a backend error, as in every other flow.

A **named** target slot with no server saves is just the case where step 3 is a no-op: every local file is backed up,
tracking is cleared, and the slot starts fresh — with every prior save recoverable under `.romm-backup`. (An empty /
whitespace / `None` slot **name** is a different thing entirely: it is rejected up front with
`reason="invalid_slot_name"` — the slot-less legacy bucket is no longer a switch target, see below.)

On the SAVES tab every slot is a collapsible panel, and the active slot's panel starts open. When the active slot
changes — **Activate Slot**, or a copy that makes its target active — the new active slot's panel opens and the old
one's closes, reading its files again when it is reopened. Every other panel keeps its state. Activate Slot and New Slot
both switch through `executeSlotSwitch` in `frontend/src/utils/saveSlotFlow.ts`, which answers the new save status or
the sentence the tab shows. On either partial answer, `switch_incomplete` or `switch_stopped`, it also announces the
ROM's save sync, so the tab reads back the slot and the files the backend saved, and the surface shows the sentence as a
toast rather than inline.

#### Inside `.romm-backup` — naming and retention

Backups live in `<saves_dir>/.romm-backup` and are named `<name>_<ts>[_<n>]<ext>`, where `<ts>` is the `YYYYMMDD_HHMMSS`
backup time. The save backup (`SaveBackup`, `services/saves/save_backup.py`) is the one place a backup is taken; the
name, its time stamp and which copies go are `domain/save_backup.py`'s. When several files of one slot are backed up
within the same second (so the base `<name>_<ts><ext>` name would collide), a `_<n>` counter (`_1`, `_2`, …) is appended
so an earlier backup is never overwritten by a later one. The folder is capped at the **newest 10** backups per save
file (`BACKUP_RETENTION`, which every `back_up_save_file` caller passes as `keep`; the cleanup's
`back_up_claimed_save_file` prunes nothing): each backup prunes the older copies of that same file beyond the cap,
bounding disk use on the Deck while keeping a deep-enough recovery net. The cap is per save file — backups of a
different save file in the same folder are never pruned by another file's backup. One deliberate exception: the backup a
call just wrote is never pruned in that same call, so under sustained same-second churn the folder may briefly hold one
extra copy (11) — honouring the cap by deleting the just-saved file would defeat the backup. A removal of an older copy
that fails is logged as a warning and leaves that copy in place: the file was moved, so the backup still answers moved
to every caller, which would otherwise treat a file already in `.romm-backup` as not moved.

#### Removed-game cleanup and save recovery

Explicit cleanup of a retained RomM 404 starts with the same exact per-ROM save paths used by sync and unions path-safe
filenames already retained in `RomSaveSyncState.files`; it never broad-scans a save directory. This preserves exact
historical filenames after a ROM rename or layout change without guessing. Before a row can be purged, the service
canonicalizes those paths for the purge set and every remaining local ROM row, installed or not — an uninstalled row
that projects onto the same save path is a co-owner. That matters because a vanished row's live replacement is routinely
uninstalled. A current save with any owner outside the purge set is copied into an enabled recovery bundle but left in
the emulator directory. An exclusively owned current save is copied and checksum-verified when recovery is enabled, then
moved through `PruneSaveSupport.back_up_prune_saves` → `SaveBackup.back_up_claimed_save_file` → `rename_claimed`, named
and time-stamped by the same rule as every other backup. This operation deliberately keeps all existing `.romm-backup`
history instead of enforcing the normal newest-ten cap. Cleanup always takes a final no-follow source claim and creates
the backup directory through anchored parents before a directory-durable rename, including recovery-off runs. A
post-rename fsync failure is returned as an actual but durability-ambiguous move rather than disappearing from the group
ledger. After the backup, every exclusively owned current-save path is expected absent, including one that existed
during inventory and was moved successfully. Cleanup collectively rechecks the whole set after later filesystem cleanup
and immediately before deleting the owning aggregate. Any newly created or emulator-recreated save retains the
aggregate, with or without a recovery bundle.

Recovery also copies matching `.romm-backup` files while leaving the originals untouched. If an uninstalled ROM or an
unsupported layout has no resolvable exact path, the manifest preserves known save-sync filenames/state and records a
warning, but physical files are not guessed or removed. Save states are outside this workflow. Save locks are acquired
in ascending ROM-id order, including remaining owners of shared paths, and retried if ownership expands. A symlinked or
out-of-root `.romm-backup` is rejected both during inventory and immediately before the backup. No UoW is open while
lock acquisition waits. Short read UoWs close before filesystem work, network/frontend waits run outside the locks, and
the final stable lock set remains held through the backup and the aggregate cascade so a newer save baseline cannot be
deleted after the lock is released.

### The `none` slot (legacy) — a migration source, no longer a target

- Saves uploaded before v2 (or without the slot parameter) have `slot=null`.
- These are separate entries from `slot="default"` — different slot = different save.
- **Legacy `slot:null` is retired as a confirmable target**
  ([#1276](https://github.com/danielcopper/romm-tender/issues/1276) /
  [ADR-0017](../adr/0017-client-baseline-detection-authoritative-negotiate-is-transport.md)): a ROM can no longer be
  confirmed onto the legacy slot. Every confirmed slot is now a real, addressable name. The Slot Setup Wizard detects
  legacy saves and offers to **migrate them into a named slot**, never to "track legacy in place."
- **`switch_slot` and `delete_slot` reject the legacy bucket too** — not just `confirm_slot_choice`. An empty /
  whitespace / `None` slot name returns `{success: false, reason: "invalid_slot_name", …}` before any lock or I/O, so a
  ROM can never be switched _into_ legacy mode through the slot switcher, and the slot-less bucket — a manual upload or
  an older web player's save — can never be torn down from Tender
  ([#1478](https://github.com/danielcopper/romm-tender/issues/1478)). The SAVES-tab UI matches this: it no longer offers
  a "Use Legacy Mode?" action, and the legacy bucket's panel is **fully read-only** — its saves stay listable and
  expandable, but it has neither an "Activate Slot" nor a "Delete Slot" button. The panel is also visually **demoted**:
  muted styling, sorted last (below every named slot, just above "+ New Slot"), labelled **Manual archive** and a note
  reading "Saves uploaded to RomM without a slot. RomM keeps them as a manual archive and never syncs them between
  devices." Only the _entry_ is closed, not existing state — a ROM already in legacy mode keeps syncing until migration
  `005` re-opens the wizard.
- **Migration `005`** (`005_unconfirm_legacy_slot_confirmations.sql`) un-confirms any ROM previously confirmed in legacy
  mode — `UPDATE rom_save_states SET slot_confirmed=0 WHERE active_slot IS NULL AND slot_confirmed=1` (the pre-rename
  table name migration `005` targets; migration `018` later renames it to `rom_save_sync_states`). No save data is
  touched; the wizard simply reappears for that ROM and the user re-picks a named slot (optionally migrating the legacy
  saves in). `resolve_default_slot` never returns `None` — a blank/unset default coerces to `"autosave"`.
- **Why the bucket is not coming back as an opt-in write target** —
  [ADR-0026](../adr/0026-legacy-bucket-stays-read-only.md) records the decision and what was measured against a live
  RomM 5.0.0 server to reach it (the web player can continue a **named**-slot save, so interop does not require
  slot-less writes; the slot-less bucket has no server-side write-currency gate; the stray-creating player behavior and
  the server-side row/file desync are being fixed upstream).
- Legacy `slot:null` survives **only** as a migration **source**. `domain/save_slot.py` (`normalize_slot`) still defines
  the equivalence class `slot ∈ {null, ""}` (state stores `active_slot=None`, the persisted slots map keys it `""`, and
  the server returns `slot: null`) so those saves can be read and deleted on the wire (below), but they are never the
  active slot of a confirmed ROM.

#### Migrating legacy saves into a named slot (content-based, #1498)

When the wizard's **Track** action carries a game's legacy (`slot:null`) saves into a chosen named slot, the migration
is **content-based**, not filename-based. An older RomM web player wrote legacy saves under timestamped names (e.g.
`Game [2026-07-19 13-41-44-611].srm`) that never equal the canonical local name, so the old filename-equality migration
silently left the target slot empty (the [#1498](https://github.com/danielcopper/romm-tender/issues/1498) defect, part
of the [#1478](https://github.com/danielcopper/romm-tender/issues/1478) triage). Instead, for each **canonical local
target** (`<rom_name>.<ext>`, the same mapping `switch_slot` uses) the migration:

1. Groups the legacy saves by the canonical filename each maps to — independent of the legacy row's own name — and picks
   the **newest** by `updated_at` per target. Two legacy saves that resolve to one target collapse to the newest; the
   older one is neither carried nor deleted.
2. Downloads that save's **content** and classifies it against the local file:
   - **No local file** → the content is written locally under the canonical name and copied into the slot.
   - **Byte-identical** local file (same zip-aware content hash the sync kernel uses) → migrated silently.
   - **Differing** local file → the migration **holds** for an informed confirmation: nothing is migrated and the slot
     is **not** confirmed (a `local_conflict` refusal carrying `needs_conflict_resolution` + a `conflicts` list), and
     the wizard shows both sides' size + timestamp — the comparison that stops a newer local save being buried
     unnoticed. The dialog offers exactly two ways out: _Replace local save_ (the second call, with
     `use_server_on_conflict`), which backs the local file up into `.romm-backup` (never deleted, #965) before writing
     the server content, or _Cancel_, which makes no second call at all — nothing changes, the slot stays unconfirmed,
     and the wizard's start-fresh route (`Use slot '<default>'`) is still open. There is deliberately **no** "keep my
     local save" action: it would produce exactly the same end state as that start-fresh button while re-opening a
     decision the user already made by clicking Track.
3. Copies the content into the slot through the normal upload path (`SaveTransfer.upload_save_file`, `overwrite=false`,
   409-backstopped), adopting the per-file baseline — so the migrated slot is immediately in sync.

The legacy source saves are **never deleted** — a migration copies their content into the slot and leaves the sources in
the read-only legacy bucket. Deleting them would reopen the legacy-write door #1478/#1496 just closed and would yank the
web player's bucket head away from a browser session still on the v1 player.

Failure handling splits on **whether the apply phase began** (#1498 review): the migration first checks its
preconditions (a live device id from `SyncEngine.live_device_id`, which registers or heals the device, and an installed
ROM), then downloads every target to a scratch `.tmp` sibling (never the real save files) and classifies it. A
**wholesale** failure _before_ the apply phase confirms **nothing**: the device/install precheck refuses as
`live_device_id` does (`device_not_registered` or `server_unreachable`) or with `not_installed`, a RomM error from
`list_saves` or a phase-1 download answers `classify_error`'s reason and message, and a save file on this device that
cannot be read or written — the scratch download included, which the RomM adapter raises as `DeviceWriteError` rather
than a RomM error — or a saves folder that cannot be created, refuses with `migration_failed`. The scratch temps are
cleared and the wizard stays open on the message, so the user can simply retry Track. Once the apply phase begins, a
**per-target** upload failure is **counted, not fatal** (`Could not migrate N save(s)`): the slot is still confirmed and
the failed source is left in place, so no save that lives only in the legacy bucket is ever lost. (A migration whose
server had no legacy saves is a no-op that confirms the slot silently; a requested migration can never return
`success: true` with nothing migrated while legacy saves existed.)

The wizard names the target slot on **every** surface — the pre-click explainer under the legacy entry ("the archived
save itself is left untouched"), the confirm modal ("Copy the archived save into 'default'? If a local save differs,
you'll confirm before anything is replaced."), and the completion toast ("Migrated 1 save into 'default'. The archived
save stays in the manual archive.") — never log-only, and the completion copy pre-empts the "why is the legacy save
still there?" confusion.

#### Addressing legacy saves on the wire (#1061)

RomM filters the `slot` query param by **exact string match**, and legacy saves are stored as `slot: null` — which **no
param value can address** (`&slot=` matches only `slot==""`; `&slot=null` matches the literal string `"null"`; both
return `[]`). The **only** way to retrieve legacy saves is to **omit the `slot` param** (the server then returns every
save for the ROM) and **filter client-side** for `slot ∈ {null, ""}`.

This is the core invariant for every per-slot server read that can target legacy (`get_slot_saves`,
`get_slot_delete_info`): legacy → `slot_query_param(...) == None` (param omitted) + `save_in_slot(...)` client filter; a
named slot → `&slot=<name>` (server filters) **and** the same client re-filter (defence in depth). `delete_slot` uses
the same named-slot addressing but **refuses the legacy bucket outright** (#1478): an empty slot name is rejected before
any I/O, so `delete_slot` never reaches the wire for `""` and the slot-less bucket is never torn down from here. Sending
`&slot=` (empty) for a legacy read was the bug: the server returned `[]`, the local tracking was cleared, and the slot
resurrected on the next merge (zombie slot).

The **active-slot matching filter** applies the same funnel from the other direction. The matrix sync run,
`get_save_status`, and rollback narrow the fetched saves through `domain.save_slot.filter_saves_to_slot`, and
`switch_slot` client-filters its fetch the same way — every one keeps only `save_in_slot(save, active_slot)`. So a
legacy `slot:null` save belongs **only** to the legacy slot and is **isolated from every named slot, including
`"default"`** ([#877](https://github.com/danielcopper/romm-tender/issues/877)): it never enters a named slot's
`compute_sync_action` inputs (no spurious download or conflict from a newer legacy head), its status counts, or its slot
listing, and a brand-new named slot is genuinely empty. The legacy saves stay readable through the legacy bucket
(`get_slot_saves(rom, "")` and the Setup Wizard's server-slot grouping) — the null bucket is deliberately separate from
`"default"`, never merged onto or aliased with it. The original bug matched `slot == active or slot is None` under every
named slot (fixed by #1061); routing the switch fetch through the shared `save_in_slot` funnel closes the last
hand-rolled site.

The **upload** side honours the same equivalence (`RomSaveSyncState.upload_slot`): a sync on the legacy slot
(`active_slot=None` with a populated `slots` map) uploads with the `slot` param **omitted**, so the server stores a
`slot: null` save. Only a brand-new ROM (no `slots` yet) seeds the configured default slot for its first sync. Since
\#1276 retired legacy as a confirmable target, this `active_slot=None`-confirmed state is no longer newly created — it
lingers only until migration `005` un-confirms the ROM — but the equivalence stays defined so any residual legacy
tracking still reads and deletes correctly. Returning `"default"` for `active_slot=None` was a sibling of the original
\#1061 bug — a save played on the legacy slot was misfiled into the default slot, so switching back to legacy found
nothing on the server.

### Confirming a slot (`confirm_slot_choice`)

The wizard confirms a slot through `confirm_slot_choice(rom_id, chosen_slot, migrate, migrate_from_slot)`:

- `chosen_slot` **must be a real slot name.** A `null`, empty, or whitespace-only value is rejected up front with
  `{success: false, reason: "invalid_slot_name", …}` before the aggregate is touched — legacy confirm is retired, so
  there is no longer a "confirm legacy mode" branch ([#1276](https://github.com/danielcopper/romm-tender/issues/1276)).
  `confirm_slot` on the aggregate mirrors this: it raises on an empty/`None` name.
- `migrate` is an explicit boolean — the default (`false`) never migrates. When `true`, the content of the saves in
  `migrate_from_slot` (`null` = the legacy `slot:null` **source**) is copied into the named `chosen_slot`, as "Migrating
  legacy saves into a named slot" above describes; no server save is deleted from the source. This is how legacy saves
  reach a named slot now that legacy is no longer a confirmable target.
- **A game whose saves no sync carries is never confirmed**, with or without a migration. Under the ROM's `rom_lock`,
  before anything is confirmed, the call takes one live reading of the save answer and refuses with the refusal a sync
  of that game raises (`refuse_unsyncable`, `services/saves/sync_engine/_shape_refusal.py`): `savefiles_in_content_dir`,
  `save_shape_unsupported` or `saves_root_missing`. A save written beside the game file has no exception: the plain
  confirm writes no file, but a slot set up for a sync that never runs is the thing refused
  ([#2153](https://github.com/danielcopper/romm-tender/issues/2153)). A game already marked as set up keeps its mark;
  every sync refuses it by the live answer, and the slot choice is still there once the emulator's setting changes back.

### Not yet implemented

- Manually selecting a specific save if multiple exist in one slot

## Device Registration

Each machine running Tender registers as a device with the RomM server. This allows RomM to track which device uploaded
each save.

1. Tender registers the device — whether save sync is on or off, since the play-session upload sends the id too — on
   every successful sign-in, when the panel loads and when the settings page opens while signed in, and before the first
   request to RomM of every save operation that names the device (see
   [`DeviceRegistry` owns device identity](#deviceregistry-owns-device-identity)). With no id stored it calls
   `POST /api/devices` with the friendly device label (`name`), platform, client info, and the contents of
   `/etc/machine-id` as the `hostname` fingerprint; with one stored it touches it (`PUT /api/devices/{id}`), and a 404
   there registers afresh
2. Server returns a `device_id` (UUID). Registration writes it to `kv_config["device_id"]` **first** — the `device_id`
   is the authoritative "registered" signal — then writes the device label to `settings.json` as a **best-effort** step
   (the two live in separate stores per [ADR-0003](../adr/0003-json-sqlite-persistence-boundary.md), so the two writes
   can't be one atomic op). A failed label write leaves a fully registered, usable device (valid `device_id`,
   prior/default label) instead of a broken half-state, and logs at debug; the in-memory settings dict is rolled back so
   an unsaved label never lingers
3. This ID is passed to `list_saves` (populates `device_syncs` per save) and `upload_save` / `download_save_content`
   (tracks sync status)
4. `device_syncs` array on each save shows per-device sync status: `device_id`, `device_name`, `is_current`,
   `last_synced_at`
5. `is_current = false` means another device uploaded since our last sync
6. Server returns HTTP 409 on POST when device has stale sync record (additional safety net)

### Why `/etc/machine-id` is the fingerprint

RomM dedupes devices by fingerprint — `mac_address`, OR `hostname` + `platform` — and returns the existing device
instead of minting a duplicate (`allow_existing` defaults true). The `name` field is **not** fingerprinted, so without a
stable fingerprint every local-state wipe (the SQLite reinstall path) would create a fresh duplicate device on each
reinstall.

Tender sends `/etc/machine-id` as the RomM `hostname`: it is machine-derived (survives a reinstall), unique per device
(two Steam Decks stay distinct), and stable. The real OS hostname is deliberately **not** sent — two stock Steam Decks
both report `steamdeck`, so a `hostname` + `platform` fingerprint built from the OS hostname would collide them into one
server device. The friendly OS hostname remains the display-only `name`. When `/etc/machine-id` is unreadable the
`hostname` field is omitted entirely, degrading to no-fingerprint behaviour rather than sending a colliding value.

### RomM account requirement

Save games in RomM are tied to the authenticated user account. Users must use their own RomM account (not a
shared/generic one) so saves are correctly attributed per user, per device.

## Emulator Tags

The `emulator` parameter on RomM save uploads determines the server-side folder path:
`saves/{system}/{rom_id}/{emulator}/`

**Format:** `retroarch-{core}` where core is the libretro core name without `_libretro` suffix, lowercased.

- Examples: `retroarch-mgba`, `retroarch-snes9x`, `retroarch-swanstation`
- Fallback: `retroarch` if core resolution fails (e.g., ES-DE config parse error)

**Important:** Emulator tag is **immutable** on RomM — set on creation, cannot be changed later. This means saves
created before v2 have `emulator=retroarch` and will keep that tag. New saves created with slots get the correct
`retroarch-{core}` tag.

For future standalone emulator support (Phase 9): just the emulator name, e.g. `duckstation`.

## Sync Decision Algorithm

Each sync run picks one action per save file: `Skip`, `Upload`, `Download`, or `Conflict`. The decision is a pure
function of what it is handed — no I/O, no state of its own — so behaviour is fully driven by inputs and is exhaustively
covered by cases and vectors.

### Inputs

- **`local_file`** — `{filename, path, size, content_size, mtime[, size_fixed]}` for a file on disk, or `None` if no
  local file exists for this filename. `content_size` is the file's [content size](#the-content-size). `size_fixed` is
  whether the emulator writes the file at a fixed size (battery-backed SRAM, memory card images), taken from the save
  answer: the resolver states it per group of files, and each synced file carries its group's claim
  (`SaveComponent.size_fixed`). A file is probed in the answer's own directory and takes the claim of the synced file of
  that name there. Where the resolver states none, or no group in that directory lists the file, Tender leaves it out,
  which the core reads as unknown. The sync and the save status's preview both read the name and the claim off the same
  synced files, so they decide a file alike.
- **`server_saves_in_slot`** — RomM save dicts already filtered to the active slot.
- **`files_state`** — the per-filename baseline from the ROM's save state — the `FileSyncState` value object on the
  `RomSaveSyncState` aggregate (persisted in the `rom_save_files` table), may be empty for a never-synced file. Carries
  `tracked_save_id`, `last_sync_hash` (our own hash of the local file at the last sync — the drift baseline),
  `last_sync_server_hash` (the server's own `content_hash` for that same sync — the provenance anchor for the identity
  check, `None` where the server save carried none, and for a baseline recorded before this field existed or by an
  earlier version's hash-only adopt), `last_sync_local_size` and `last_sync_content_size` (the raw size and the content
  size of the local file at that sync), `last_sync_server_updated_at`, `last_sync_local_mtime`, etc.
- **`device_id`** — this device's RomM-server ID (used to find our entry in `server_save.device_syncs`).
- **`local_hash`** — pre-computed RomM-parity `content_hash` of `local_file` (zip-aware: a plain MD5 for a single-file
  save, the per-entry combined hash for a zipped multi-file save — `SaveFileStore.content_hash`, never the whole-archive
  `checksum_md5`), or `None`. It **must** be the same scheme the server stamps so the parity-route byte-identity checks
  against `server.content_hash` (rows 6d / 8a / 10a / 11a / 12a) can match for zip saves too (#1457); the provenance
  route instead compares the stored `last_sync_server_hash` against `server.content_hash` and needs no such agreement
  (#1468).

### The content size

A file's content size is its size less the trailing run of the byte it ends with, when that byte is `0xFF` or `0x00`,
the two fill bytes; a file ending in any other byte has a content size equal to its size, and a file of fill only, like
an empty one, has content size 0 (romm-gavel's `SPEC.md`, "Inputs"). Emulators disagree on how much fill they write: one
writes a 128 KiB file for a game whose save holds 32 KiB, another writes the 32 KiB alone, so the raw size of the same
save can drop to a quarter between two syncs without a byte of the game's data going. Judged by raw size, that is a
truncated file ([row 9b](#why-row-9b-conflicts-instead-of-uploading)); judged by content size, it has kept its size.

`SaveFileStore.measure` takes it over the file's raw bytes — a zip save's too, whose hash is still taken over its
entries — in the same open as the hash (`domain/save_measure.py`). It reaches the core beside the raw size, and it is
recorded in `rom_save_files.last_sync_content_size` at every sync boundary beside `last_sync_local_size`. A baseline
recorded before Tender took it carries none, and the core then compares raw sizes. Where the local file is still the one
that baseline was recorded from (`local_hash == last_sync_hash`), the next sync records its content size from those
bytes (`RomSaveSyncState.backfill_content_size`), so the first edit after the update is judged by content size. A file
already edited since a baseline without one is judged by raw size once; a Keep Local or any other sync boundary records
it. A baseline the copy flow records from the server's size alone carries none either
([Copy a save to another slot](#copy-a-save-to-another-slot)).

### Pick rule and discriminators

Within `server_saves_in_slot`, the algorithm picks the **newest by `updated_at`** as the canonical save and decides
against that one. The pick is deterministic — `max(updated_at)` over the same list always names the same save — so two
devices reasoning about the same slot converge on the same target instead of each choosing a different head. A save
whose `updated_at` cannot be parsed sorts to the bottom, so a garbled timestamp can never beat a readable one into the
head position. Other saves in the slot are ignored — no foreign-save surfacing, no per-save dismiss state.

Three discriminators drive the branch:

1. **Our device's entry on the picked save**: `server.device_syncs[me]` may be missing (on every save when `list_saves`
   went out without a device id, which no Tender path sends — see
   [Where the never-touched rows are reached](#where-the-never-touched-rows-are-reached)), `is_current=true` (server
   claims our last write/read is current), or `is_current=false` (someone else has moved this save forward since we last
   touched it, or we never touched it and RomM answered its placeholder — see
   [`GET /api/saves` writes no sync row](#get-apisaves-writes-no-sync-row)).
2. **Hash divergence vs. baseline**: `local_hash != files_state["last_sync_hash"]` means the local file has been edited
   since the last successful sync. Without a baseline (`last_sync_hash` is missing) we cannot claim divergence.
3. **Size plausibility (upload guard only)**: in the branch that uploads a diverged edit (our device `is_current=true` +
   local diverged, row 9), the local file's size is checked against the recorded baseline — content size against content
   size where both are stated, raw size against raw size otherwise; a file the save answer states fixed-size is also
   held, by raw size, to the recorded content size, or to the recorded raw size where there is none (see
   [row 9b](#why-row-9b-conflicts-instead-of-uploading)). A 0-byte or implausibly-shrunk local is a crash artifact, not
   an edit, and diverts that branch to `Conflict` (row 9b) so a corrupt-looking local is never pushed as the new newest
   save (#1062).

`is_current` is **computed server-side**, not stored — see [RomM Save Sync API Behaviour](#romm-save-sync-api-behaviour)
below. It is also a **snapshot**, not a promise: the whole decision is made against one `list_saves` response, and
another device can move the slot on between that read and the write we derive from it. "Our device is still current, so
nobody else has moved the server forward" is therefore a heuristic re-checked at write time, not something the decision
may rely on — which is why the row-9 upload it licenses POSTs with `overwrite=false` and treats RomM's 409 as the
authority (see [Upload-time conflicts (the 409 backstop)](#upload-time-conflicts-the-409-backstop)).

### Outcomes

| Variant                       | Service behaviour                                                                                                                                                   |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Skip(reason)`                | No transfer. With `adopt_baseline=True` it carries the head (`server_save`): the local file is identical to it and is recorded as its baseline (see below).         |
| `Upload(target_save_id=None)` | POST a new save to the slot (`overwrite=false`, 409-backstopped). Server assigns an ID; we record it in state.                                                      |
| `Upload(target_save_id=int)`  | POST a new version too (`overwrite=false`, 409-backstopped). The id marks the save we supersede, for the status echo — it no longer drives an in-place PUT (#1276). |
| `Download(server_save)`       | GET save content, overwrite local file, update sync state.                                                                                                          |
| `Conflict(server_save)`       | Surface a `SyncConflict` to the frontend. The user resolves via `resolve_sync_conflict(rom_id, filename, server_save_id, action)`.                                  |

An adopting `Skip` names the head it adopts (`server_save`, the caller's own save dict) and says whether this device
must acknowledge it to RomM (`acknowledge`), which the core sets exactly when this device's entry on that head is
`is_current=false`. The adopt records a full baseline from the head — its id, its `content_hash` verbatim, its size and
`updated_at`, and the local file's size, content size and mtime (`RomSaveSyncState.adopt_head`) — and, as it stands in
for a download of the same bytes, seeds the ROM's state as a download does: the system, a first active slot from the
default slot, and the emulator tag already recorded, else `retroarch` (`RomSaveSyncState.reach_sync_boundary`). The
aggregate's verbs decide what is recorded; the save transfer (`SaveTransfer.adopt_server_head`, or `record_server_head`
for an adopt that needs no acknowledgement) measures the file and runs the steps in this order — for the sync and for
Keep Local alike. No core is recorded: an adopt transfers nothing, so which core wrote the bytes is unknown, and the one
the last upload recorded stays for the core-change warning to compare against. An adopt is recorded only if the file
still hashes to what the decision saw; one that no longer does is neither acknowledged nor recorded. An adopt that must
be acknowledged then sends `POST /api/saves/{id}/downloaded` with the device id and the local file's content hash
([A download is acknowledged once placed, never marked at the GET](#a-download-is-acknowledged-once-placed-never-marked-at-the-get)),
through the retry strategy every RomM request in the sync uses, and records the baseline from that measure only after
that succeeded: without it RomM keeps this device not current on the head, and the next local edit would read as one
under a head that moved past it — a conflict nobody caused. Where the acknowledgement still fails, nothing is recorded,
the file is reported as an error of that sync with its reason — not a conflict — and the next sync decides the same way
again.

The mutating sync path (`SyncEngine.do_sync_rom_saves`) records every adopt. The read-only status path
(`StatusService._get_save_status_io`) records only one that needs no acknowledgement, and leaves the other to the next
sync: showing a page never writes to RomM, and the read runs with save sync off too. The alternative — only writing the
baseline from the mutating path — would leave the adopts that need no acknowledgement incomplete forever for users who
only ever open the game-detail panel.

### Implementation

The algorithm runs in the compiled [romm-gavel](https://github.com/danielcopper/romm-gavel) core, reached through the
`ComputeSyncActionFn` seam that `GavelNativeAdapter` provides (see
[GavelNativeAdapter notes](backend-architecture.md#gavelnativeadapter-notes-the-compiled-save-sync-core) for the
marshalling and the no-fallback posture). It answers in the `SyncAction` dataclasses defined in
`backend/domain/sync_action.py` — which is all that module holds, the decision itself living nowhere in Python. The
`SaveService` aggregate (`backend/services/saves/`) calls the seam from two sub-services:

- `SyncEngine.do_sync_rom_saves` (`services/saves/sync_engine/`) iterates local files and server-only-in-slot groups,
  dispatching each action via the matrix executor's `_dispatch_sync_action` (the in-place PUT is retired on this path,
  #1276).
- `StatusService._get_save_status_io` (`services/saves/status/`) runs the same decisions read-only and folds them into
  the `SaveStatus.files[*].status` strings the frontend renders. The only allowed mutation is recording an adopt that
  needs no acknowledgement — pure state hygiene with no network traffic.

What an action moves is not the matrix's. Two building blocks in `services/saves/`, built once by `SaveService` and
handed to every save service that needs them, carry it out:

- `SaveBackup` (`save_backup.py`) moves a local save file into the `.romm-backup` folder beside it before Tender itself
  replaces or removes it — every download over a local file (`SaveTransfer.download_save_file`, whichever flow calls
  it), the slot steps, following a moved save directory, the adoption renamer and the removed-game cleanup all go
  through it. Deleting local saves — a game's or a whole platform's — which the user asks for, removes them outright.
- `SaveTransfer` (`save_transfer.py`) downloads a server save, uploads a local one, acknowledges a head adopted without
  a transfer, adopts an identical head, and fetches a server copy to compare or to place. A download or upload records
  the sync boundary through the aggregate's verbs in the same step, so no caller transfers without recording.

Server-only saves (no matching local file) are grouped by their target local filename (`rom_name.<ext>`) before being
passed to `compute_sync_action`. The algorithm picks the newest in the group, so older stacked versions in the same slot
are not separately surfaced. The same grouping applies to local files: each local file is matrix-evaluated only against
the server saves sharing its canonical target, so a multi-file save set (e.g. `Game.srm` + `Game.rtc`) never
cross-contaminates extensions — `Game.srm` is never resolved against a newer `Game.rtc` server record.

## Decision Matrix

The matrix below enumerates every input combination the decision handles. Rows are derived from the algorithm and
exhaustively cover the cross-product of dimensions. Every row is pinned against the shipped core by at least one case in
`tests/adapters/test_gavel_native_decision_table.py` or one of the vendored gavel `decision-table` vectors, which state
the same contract in upstream's terms.

Dimensions:

- **Local file** — does a `.srm` exist on disk?
- **Server saves in slot** — none, or at least one (algorithm picks newest).
- **Our device entry on picked save** — _never touched_ (no `device_syncs` entry for our id, which only a `list_saves`
  without a device id answers, and Tender sends none — see
  [Where the never-touched rows are reached](#where-the-never-touched-rows-are-reached)), _current=true_, or
  _current=false_.
- **Local vs `last_sync_hash`** — _unchanged_, _changed_, or _no baseline_ (key missing in state). A `local_hash` that
  is `None` or empty is _unknown_: it never proves a change, so the core takes the unchanged exit — rows 7 and 10b, and
  rows 6b / 6e in the never-touched branch, with or without a baseline — and with no baseline it lands on rows 8b and
  11b, since an unknown hash never matches the head. Tender hashes every save file it finds on disk before it asks
  (`MatrixExecutor.iter_matrix_outcomes`), so it never passes an unknown hash itself.
- **Local mtime vs server `updated_at`** — only consulted in the `never touched` branch, and there only when the local
  file is unchanged since the baseline or its hash is unknown (rows 6b / 6e) — the one case where nothing else orders
  the two sides. A time that is missing or unreadable on either side never reads as local-newer, so the head is
  downloaded.
- **Content identity** — whether the local file is byte-identical to the server head: the stored `last_sync_server_hash`
  vs `server.content_hash` while local is unchanged (provenance), else parity `local_hash == server.content_hash`
  (#1468). In the `never touched` branch it is checked first, and a match short-circuits to row 6d before mtime/baseline
  are consulted; in the `current=false` branch a match adopts the head in every baseline case (rows 10a, 11a, 12a); in
  the `current=true` branch it decides only the no-baseline case (rows 8a / 8b). Every hash the comparison touches must
  be present and non-empty on both sides: a missing or empty hash is uncertainty, and uncertainty never reads as a
  match.

| #   | local file | server in slot | our entry     | local vs baseline               | mtime vs server                              | decision                                          | reason                                                                                                                                                                                                                                                                                                                                               |
| --- | ---------- | -------------- | ------------- | ------------------------------- | -------------------------------------------- | ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | no         | none           | n/a           | n/a                             | n/a                                          | `Skip(nothing_to_sync)`                           | nothing local, nothing server                                                                                                                                                                                                                                                                                                                        |
| 2   | yes        | none           | n/a           | n/a                             | n/a                                          | `Upload(POST)`                                    | first push for this save (or recovery after server-side wipe)                                                                                                                                                                                                                                                                                        |
| 3   | no         | ≥1             | never touched | n/a                             | n/a                                          | `Download(picked)`                                | no relation, pull newest                                                                                                                                                                                                                                                                                                                             |
| 4   | no         | ≥1             | current=true  | n/a                             | n/a                                          | `Download(picked)`                                | recovery — server still tracks our last version, local is gone                                                                                                                                                                                                                                                                                       |
| 5   | no         | ≥1             | current=false | n/a                             | n/a                                          | `Download(picked)`                                | server moved forward, nothing local to protect                                                                                                                                                                                                                                                                                                       |
| 6a  | yes        | ≥1             | never touched | no baseline, local hash known   | any                                          | **`Conflict(picked)`**                            | a local save of unknown provenance beside a different head — with no baseline nothing proves which side moved, and mtime is not trusted to decide in either direction; the user does (#1276)                                                                                                                                                         |
| 6b  | yes        | ≥1             | never touched | unchanged or local hash unknown | local mtime < server, or either time unknown | `Download(picked)`                                | no local change is known since the baseline, and nothing shows the local save is the newer one                                                                                                                                                                                                                                                       |
| 6c  | yes        | ≥1             | never touched | changed                         | n/a                                          | **`Conflict(picked)`**                            | baseline held from a prior sync but the picked head is a save we never synced — both sides moved (#1059)                                                                                                                                                                                                                                             |
| 6d  | yes        | ≥1             | never touched | any                             | any                                          | `Skip(synced, adopt_baseline=true)`               | byte-identical to an existing server save (stored server hash — provenance, or `content_hash == local_hash` — parity); adopt it, never POST a duplicate (#1013 / #1468)                                                                                                                                                                              |
| 6e  | yes        | ≥1             | never touched | unchanged or local hash unknown | local mtime ≥ server                         | `Upload(POST)`                                    | the local is not older than the head; reached only without a device id, which Tender never lists with — see [Where the never-touched rows are reached](#where-the-never-touched-rows-are-reached)                                                                                                                                                    |
| 7   | yes        | ≥1             | current=true  | unchanged or local hash unknown | n/a                                          | `Skip(synced)`                                    | steady state                                                                                                                                                                                                                                                                                                                                         |
| 8a  | yes        | ≥1             | current=true  | no baseline                     | n/a                                          | `Skip(synced, adopt_baseline=true)`               | byte-identical to the head (`server.content_hash == local_hash`, parity only) — adopt it as the baseline; RomM already holds this device current, so nothing is acknowledged                                                                                                                                                                         |
| 8b  | yes        | ≥1             | current=true  | no baseline                     | n/a                                          | **`Conflict(picked)`**                            | the local differs from the head, the head carries no `content_hash`, or the local hash is unknown — being current says this device synced the head, not that the file on disk is still it                                                                                                                                                            |
| 9   | yes        | ≥1             | current=true  | changed                         | n/a                                          | `Upload(supersede picked.id)`                     | offline edit (plausible size) — POST our changes as a new version that supersedes the save the server still considers ours                                                                                                                                                                                                                           |
| 9b  | yes        | ≥1             | current=true  | changed                         | n/a                                          | **`Conflict(picked)`**                            | diverged local is 0-byte, below half its recorded size (content size where both sides state one, raw size otherwise), or — for a file the save answer states fixed-size — shorter than the recorded content size (the recorded raw size where the baseline holds none); a crash / full-disk artifact; refuse the upload, let the user decide (#1062) |
| 10a | yes        | ≥1             | current=false | unchanged                       | n/a                                          | `Skip(synced, adopt_baseline=true)` + acknowledge | byte-identical to the head (provenance or parity) — adopt it with no transfer and acknowledge it to RomM                                                                                                                                                                                                                                             |
| 10b | yes        | ≥1             | current=false | unchanged or local hash unknown | n/a                                          | `Download(picked)`                                | another device synced, nothing shows a change of ours, and nothing proves the head holds the same bytes — adopt their version                                                                                                                                                                                                                        |
| 11a | yes        | ≥1             | current=false | no baseline                     | n/a                                          | `Skip(synced, adopt_baseline=true)` + acknowledge | no baseline (so parity only, #1468), but `server.content_hash == local_hash` — byte-identical; adopt the head with no transfer and acknowledge it                                                                                                                                                                                                    |
| 11b | yes        | ≥1             | current=false | no baseline                     | n/a                                          | **`Conflict(picked)`**                            | no baseline and content differs from the server head — cannot prove which side is newer; refuse the silent overwrite (#1276)                                                                                                                                                                                                                         |
| 12a | yes        | ≥1             | current=false | changed                         | n/a                                          | `Skip(synced, adopt_baseline=true)` + acknowledge | both sides moved off the baseline but landed on the SAME bytes (`server.content_hash == local_hash`, parity only) — adopt the head with no transfer and acknowledge it, nothing to reconcile (#1480)                                                                                                                                                 |
| 12b | yes        | ≥1             | current=false | changed                         | n/a                                          | **`Conflict(picked)`**                            | both sides moved to DIFFERENT content — the user decides                                                                                                                                                                                                                                                                                             |

Conflict happens in six rows — #12b (we already hold an entry on the picked save and our local diverged from the
baseline to content that differs from the head), #6c (we hold a baseline from a prior sync but no entry on the picked
head), #6a (we hold neither a baseline nor an entry on the picked head, and our local content differs from it), #11b (we
hold an `is_current=false` entry, have no baseline, and our local content differs from the server head), #8b (we are
current on the picked save but hold no baseline, and nothing proves our local content is the head's), and #9b (we own
the picked save and our local diverged, but the local file is 0-byte / implausibly shrunk). #12b, #6c, #6a and #11b are
all "both sides hold content we never reconciled" situations, and #8b a local of unknown provenance beside a head we
synced; #9b is a different hazard — protecting the server's only good copy from being superseded by a corrupt-looking
local file (#1062). Every other row resolves silently to a Skip, Upload, or Download. An adopt marked "+ acknowledge"
confirms the head to RomM before it records the baseline ([Outcomes](#outcomes)).

### Where the never-touched rows are reached

Rows 3 and 6a–6e need a `list_saves` answer with no entry for this device on the picked save, and RomM gives that answer
only to a request without a device id: with one, a save this device never synced carries RomM's placeholder, which is
not current ([`GET /api/saves` writes no sync row](#get-apisaves-writes-no-sync-row)). **No Tender path reaches them.**
Every save operation takes the device id it sends from `SyncEngine.live_device_id`, which registers or heals the device
first, and `list_saves` takes only that id — see
[`DeviceRegistry` owns device identity](#deviceregistry-owns-device-identity). Where no live id can be had, the
operation lists nothing.

Without a device id every save reads never-touched, including one this device is current on: an edit over its own head,
row 9 with a device id, is row 6c without one. For a save this device holds no sync row on, a device id sends the same
situations to the not-current rows: row 3 on row 5, 6a on 11b, 6b and 6e on 10b (on 11b for an unknown local hash with
no baseline), 6c on 12b, and 6d on 10a, 11a or 12a, depending on the baseline. The never-touched rows stay in the table
as the core decides them; the decision-table tests and the gavel vectors call the core directly.

### Why row 6d adopts instead of posting

Row 6d fires when a local file exists, a server save also exists in the slot, our device has never touched the picked
save, **and** the picked save's RomM-provided `content_hash` equals the local content hash — the bytes on disk are
identical to bytes the server already holds. This is the copied-SD-card / restored-backup / fresh-reinstall case: the
device looks brand-new to the server (no `device_syncs` entry) but the content is the same save. That is how it looks
only on a `list_saves` without a device id; with one, RomM's placeholder sends the same case to row 11a with no
baseline, to row 10a with a baseline the local still matches, and to row 12a with one it has moved off, and the head is
adopted there too, acknowledged to RomM because the placeholder says this device is not current on it. POSTing here
would create a duplicate server save of identical bytes, inflating the slot and churning autocleanup. Instead we adopt
the existing save as the baseline (`Skip(adopt_baseline=true)`, recording it with `last_sync_hash := local_hash`), so
future drift is detected without ever duplicating. The check is pure — no download, no re-hash — because RomM stamps
`content_hash` on every save. **Known fallback gap:** older / migrated server saves may lack `content_hash`; when it is
absent neither identity route can match. A local with no baseline then conflicts (row 6a) and a diverged one conflicts
(row 6c), so only a local unchanged since its baseline — or one whose hash is unknown — can still reach row 6e's upload
of what may be a byte-identical duplicate — a row no Tender path reaches
([Where the never-touched rows are reached](#where-the-never-touched-rows-are-reached)). No slow-path content fetch is
attempted (#1013).

### Why row 6a conflicts instead of going by age

Row 6a fires when a local file exists, a server save also exists in the slot, our device has never touched the picked
server save, the content does **not** match it (or `content_hash` is absent — see row 6d), and there is no baseline
(`last_sync_hash`) while the local hash is known. Nothing then says where the local save came from: no baseline to show
whether it changed since a sync, and no `device_syncs` entry to give us a claim on the picked save. Its modification
time does not settle it either, for the reason [row 11](#why-row-11-splits-into-adopt-11a-vs-conflict-11b) gives, so the
row does not compare times at all: whether the local file is older or newer than the head, the answer is a `Conflict`
the user resolves via Keep Local / Use Server — never a silent `Download` that would overwrite local progress, and no
upload. This is the same "user decides on ambiguity" exit row 11b takes for a save we do hold an `is_current=false`
entry on ([#1276](https://github.com/danielcopper/romm-tender/issues/1276) /
[ADR-0017](../adr/0017-client-baseline-detection-authoritative-negotiate-is-transport.md)).

Rows 6b and 6e still compare times because they are the one never-touched case with nothing else to go on and nothing of
ours at stake: the local file is unchanged since the baseline (or its hash is unknown, which Tender never passes). A
local at or after the head is answered with an upload (row 6e) and an older local, or a time that cannot be read,
downloads the head (row 6b) — rows no Tender path reaches
([Where the never-touched rows are reached](#where-the-never-touched-rows-are-reached)).

### Why row 6c conflicts instead of downloading

Row 6c is the never-touched sibling of row 12b. We hold a baseline (`last_sync_hash`) from a prior sync, but the picked
head is a save we have no `device_syncs` entry for — another timeline became newest while our local diverged from the
baseline (`local_hash != last_sync_hash`). That is the same "both sides moved independently" situation as row 12b, so it
takes the same exit: a `Conflict` the user resolves, never a silent `Download` that would discard the diverged local
progress (whose only surviving copy would be the `.romm-backup`). As in row 12b, byte-identity is checked first — if the
diverged local happens to match the head's `content_hash`, row 6d adopts it instead of conflicting — so 6c fires only on
genuinely-different content. When there is no baseline we cannot claim divergence, and row 6a conflicts on the unknown
provenance instead; when local still matches the baseline, rows 6b / 6e apply and the mtime comparison breaks the tie.

### Why row 8 splits into adopt (8a) vs conflict (8b)

Row 8 is a device RomM holds as current on the head, with a local file and no baseline: the baseline was dropped (a slot
change clears them, a copy whose upload failed drops one) or never recorded. Being current says this device last synced
the head; it says nothing about the file on disk, which may have been edited, restored or replaced since — and with no
baseline there is nothing to show which.

The content hash decides:

- **8a — `server.content_hash == local_hash`.** The file is the head's bytes, so it is adopted as the baseline with no
  transfer. RomM already holds this device current, so there is nothing to acknowledge.
- **8b — anything else**: the content differs, the head carries no `content_hash`, or the local hash is unknown.
  Adopting here would record a file that is not the head as if it were, and its next change would upload as an ordinary
  edit superseding the head, whose progress then survives only as an older version until auto-cleanup prunes it
  (romm-gavel#53). So it is a **`Conflict`** the user resolves via Keep Local / Use Server, the same exit rows 6a and
  11b take for a local of unknown provenance.

Only parity can prove identity here: provenance needs a baseline.

### Why row 9b conflicts instead of uploading

Row 9 is the steady offline-edit path: we own the picked save (`is_current=true`), our local diverged from the baseline,
so we POST the local content as a new version that supersedes the tracked save. Row 9b is the same branch with one extra
guard. A crashed emulator or a full disk can leave a **0-byte or truncated** save on disk — still a valid regular file,
with a valid-but-wrong content hash, so it reads as a "diverged" edit and would take the row 9 upload. Pushing that
corrupt file would make it the newest save in the slot, so newest-wins would then propagate the garbage to every other
device — and once `autocleanup_limit` prunes the older versions, the good copy could age out of recovery. This is the
upload mirror of the [#965](https://github.com/danielcopper/romm-tender/issues/965) backup-or-confirm invariant: the
download-overwrite path already backs the local file up into `.romm-backup` first, but a blind upload of a
corrupt-looking local had no equivalent guard.

The plausibility check is part of the decision (fed the local file's size and [content size](#the-content-size), and the
recorded `last_sync_local_size` and `last_sync_content_size`): a new size of **0** fires unconditionally; otherwise a
content size under **50%** of the recorded one fires as a shrink — the raw sizes stand in where either side states no
content size. The threshold is a hard-coded conservative default — not a setting. Measuring content lets a save whose
emulator writes less trailing fill than the last one upload as the edit it is, while a save that lost data still trips
it; where both content sizes are stated, a non-empty file of fill only trips it whenever the recorded content was larger
than 0. The core has a further rule for a file the save answer states is written at a fixed size (`size_fixed`, see
[Inputs](#inputs)), which conflicts as soon as its raw size is below the recorded content size, whatever the 50%
threshold says. A file shorter only by a trailing run of fill does not trip it, because the recorded content size leaves
that run out. Where the baseline carries no content size ([The content size](#the-content-size)), the fixed rule holds
the file to the recorded raw size instead, so a shorter fixed-size file is a conflict once. When the guard fires, the
decision is `Conflict(picked)` instead of `Upload`, routing through the existing `SyncConflictModal` so the user
decides: **Use Server** downloads the good server copy (backing the bad local up first), **Keep Local** re-uploads the
corrupt file only after an explicit choice. A plausible-size divergent edit (or a save that grew) is unaffected and
still uploads (row 9).

### Why row 11 splits into adopt (11a) vs conflict (11b)

Row 11 is the `is_current=false` sibling of row 6a — local file exists, mtime is whatever, no baseline. The difference
is that our device **does** have an entry on the picked save and the entry says `is_current=false`: either some other
device has moved that save forward since our last interaction, or we never synced it and the entry is RomM's
placeholder. Either way its content is foreign to us. Without a baseline we cannot prove our local has edits that
postdate the foreign write, and mtime is unreliable (filesystem touches, migrations, clock skew).

The content hash breaks the tie:

- **11a — `server.content_hash == local_hash`.** The bytes on disk are already identical to the foreign head, so there
  is nothing to lose: adopt the server save as the baseline with no transfer, and acknowledge it to RomM
  (`Skip(adopt_baseline=true)` with `acknowledge`, [Outcomes](#outcomes)). This is the copied-card / restored-backup /
  fresh-install case landing on the foreign timeline.
- **11b — the content differs.** We genuinely hold different local bytes than the server head, with no baseline to say
  which is newer. The earlier design silently `Download`ed here, backing the local bytes up into `.romm-backup` on the
  bet that another device's work mattered more — a silent overwrite of possibly-newer local progress. Under
  [#1276](https://github.com/danielcopper/romm-tender/issues/1276) this is a **`Conflict`** instead: the user decides
  via Keep Local / Use Server, the same exit rows 6a, 6c, 8b and 12b take. When the server save carries no
  `content_hash` (older / migrated saves) the equality fails closed to this conflict — the safe default. mtime is never
  trusted to break this tie.

### Why row 12 splits into adopt (12a) vs conflict (12b)

Row 12 is the both-sides-moved case where we already hold an `is_current=false` entry on the picked head **and** a
baseline our local has diverged from (`local_hash != last_sync_hash`). The intuition is "two timelines, user decides",
and 12b is exactly that. But the two independent moves can coincidentally land on the **same** bytes — the same change
synced from another device, or an identical backup restored on both sides — and then there is nothing to reconcile.

The content hash breaks the tie, mirroring row 11's split:

- **12a — `server.content_hash == local_hash`.** The bytes on disk already equal the moved-past head, so adopting it is
  risk-free by construction (nothing differs to lose). We adopt it with no transfer; the acknowledgement re-establishes
  `is_current` and the adopt the baseline, so the next sync is a plain `Skip`
  ([#1480](https://github.com/danielcopper/romm-tender/issues/1480) — before it, this collision surfaced a needless
  conflict modal).
- **12b — the content differs.** Both sides genuinely hold unreconciled bytes → **`Conflict`**, the user decides via
  Keep Local / Use Server. Unchanged from before.

The identity check is the same two-route one rows 6d / 10a / 11a use. One subtlety: in this slice the local has
**diverged** from the baseline, and the provenance route requires it to be _unchanged_ since baseline — so that route is
inert here and only parity (`local_hash == server.content_hash`) can prove identity. When the head carries no
`content_hash` (older / migrated saves) or either hash is empty, parity fails closed to 12b — the safe default.

### Why is there no foreign-save modal anymore

Earlier versions surfaced every server save in the slot the user had not authored as a "newer-in-slot" prompt. The
pragmatic newest-wins model used by the official RomM clients (Argosy, Grout) treats the slot as a single timeline:
whichever save has the highest `updated_at` wins, regardless of which device PUT it. We adopted that model in v0.16
because it eliminates ~1500 lines of foreign-tracking code and aligns with the wider RomM ecosystem. Cross-device
uploads are silently adopted unless this device holds local content it never reconciled with them (rows 6a, 6c, 11b,
12b). This is documented behaviour, not a regression.

### Upload-time conflicts (the 409 backstop)

The client kernel is not the only conflict detector — RomM's `POST /api/saves` self-guards. Every upload the automatic
sync path dispatches is a POST with `overwrite=false`, and RomM rejects it with **HTTP 409** when the device is not
current on the slot's newest save (see [The `add_save` POST 409-gate](#the-add_save-post-409-gate) below for the exact
predicate). The adapter maps that 409 to `RommConflictError`, which is non-retryable and propagates on the first
attempt. The backstop is needed because the matrix decided against a `list_saves` snapshot that can already be stale by
the time the POST lands. The server's rejection, not our snapshot, is the authority on what the slot holds; the
resolution below does nothing but map that rejection to an action.

**Dedup-to-non-head (detected client-side).** A second signal routes to the same backstop. RomM's `add_save` content
dedup can early-return an **existing** save whose content matches the upload instead of creating a new version — and
that returned save may not be the slot head. Because the automatic path only POSTs when local content diverged from the
head, a dedup on that path resolves to an **older** save while a newer, different head still leads the slot: no new
version, the foreign head stays authoritative, yet the response looks like a successful upload. Recording it as a synced
baseline would falsely report "synced" while the upload intent (make our content the head) went silently unfulfilled
cross-device. `SaveTransfer.upload_save_file` compares the POST response against the pre-upload `list_saves` snapshot
(`domain/upload_response.py`'s `dedup_returned_non_head`: the response is a pre-existing snapshot member that is not the
newest) and raises `RommConflictError` **before** any baseline / confirm / own-upload write, so the same backstop below
surfaces the true state and nothing stamps currency on the non-head save
([#1482](https://github.com/danielcopper/romm-tender/issues/1482)). An **empty** planned slot has no head to bypass. A
save another device puts there before our POST lands is never what the POST dedups to: RomM's
[409-gate](#the-add_save-post-409-gate) runs first and refuses the POST, since this device holds no sync row on that
save, and the backstop below decides.

On a 409 (or a client-detected dedup-to-non-head) the executor re-fetches the slot, picks the newest save in the
canonical group, and resolves through the `ResolveUploadConflictFn` seam — the core's second entry point, reading the
current local hash, the recorded baseline, and both server hashes:

| Condition                           | Result       | Why                                                                                 |
| ----------------------------------- | ------------ | ----------------------------------------------------------------------------------- |
| `local_hash == last_sync_hash`      | `"download"` | local is unchanged since our last sync — the server moved on, adopt it              |
| `local_hash == server_content_hash` | `"download"` | already byte-identical to the server head — adopt it, nothing to upload             |
| otherwise (incl. any `None` input)  | `"conflict"` | genuinely divergent — surface the same `SyncConflictModal` a matrix `Conflict` uses |

**The download leg needs proof, not merely the absence of a reason to worry.** `"download"` is reachable only where the
local file is _provably_ unchanged — unchanged since our own recorded baseline, or byte-identical to what the server now
holds. A missing or empty `local_hash` proves neither, so it resolves to a conflict rather than a download: with no
evidence about the local bytes we cannot claim there is no un-synced work to protect, and a download overwrites them.
That asymmetry is why every unknown-input row of the vendored ladder vectors lands on `"conflict"`.

A `"download"` result downgrades the upload to a `SaveTransfer.download_save_file` (the server save was newer and our
local had no un-synced edits); a `"conflict"` result appends a `SyncConflict` and returns without writing, so the user
resolves it via Keep Local / Use Server exactly as a matrix-row conflict. This is the upload mirror of the matrix's
download-side safety: an automatic upload never blindly overwrites a save the device isn't current on.

A `SyncConflict` names the server save by its `server_save_id`, which the resolution round-trips, so the id is required:
a re-fetched head without an `id` is a RomM contract violation, and it is reported as that file's sync error rather than
surfaced as a conflict nobody could resolve. A matrix-row conflict cannot meet one: the gavel adapter's marshalling
already fails on an id-less save before the core decides anything.

**`overwrite=true` is reserved for the explicit `keep_local` resolution.** The only caller that sets it is
`_resolve_conflict_keep_local` — when the user has chosen to overwrite the server head, the re-POST carries
`overwrite=true` to bypass the 409 gate deliberately ([#1276](https://github.com/danielcopper/romm-tender/issues/1276) /
[ADR-0017](../adr/0017-client-baseline-detection-authoritative-negotiate-is-transport.md)).

## Slot Setup Wizard

Before save sync can operate for a game, the user must choose which slot to track. This is managed by the
`slot_confirmed` flag in per-game state.

### Scenarios on first use

| Scenario | Local     | Server                    | Behavior                                       |
| -------- | --------- | ------------------------- | ---------------------------------------------- |
| A        | No saves  | Has saves                 | Wizard: choose which server slot to track      |
| B        | Has saves | No saves                  | Auto-configure with default slot (no prompt)   |
| C        | Has saves | Has saves (other slots)   | Wizard: upload to default or track server slot |
| D        | --        | --                        | Manual slot switch in game detail              |
| E        | Has saves | Has saves in default slot | Wizard: track default or use different slot    |

A game whose saves no sync carries has no slot to set up, wherever its saves are. `get_save_setup_info` takes one live
reading of the save answer and asks it through the refusal every sync uses (`refuse_unsyncable`); where that refuses, it
answers `recommended_action: "sync_unavailable"` with the refusal's `reason` and `message` — and its finding, source
kind and emulator label where it carries them, so the panel words it as it does a press on the same game — and asks RomM
nothing: no device registration, no listing. A game that syncs answers by the table above, its local probe reading that
same answer ([#2153](https://github.com/danielcopper/romm-tender/issues/2153)). `get_save_sync_availability` asks the
same refusal alone, with nothing else read and nothing asked of RomM, and answers `{available: true}` or
`{available: false}` with those fields: the question the SAVES tab asks while it holds RomM as unreachable.

### Where the check happens

- **Game detail page (SAVES tab):** shows wizard instead of save list when `slot_confirmed=false`. For a game whose
  saves no sync carries it shows the sentence the sync refuses with under the title "Save Sync", and no slot buttons;
  such an answer asked RomM nothing, so it does not feed the shared connection state either. Where the panel already
  holds RomM as unreachable, the tab skips the setup info, whose retry ladder would only wait the outage out, and asks
  `get_save_sync_availability` instead: a game that cannot sync shows its reason the same way, and a game that can keeps
  the unreachable notice and its Retry.
- **Play button:** checks before launch. If not configured and server has saves, redirects to SAVES tab. If no server
  saves, auto-configures with default. A game whose saves no sync carries starts with no setup step and no toast, also
  where RomM holds saves for it: the sync before the start passes it over, or asks first where the saves folder is
  missing. The launch watcher's cold start confirms nothing for it either.

## Save File Discovery

Where a game's save lives and what it consists of are one answer, read live per ROM from the emulator that would launch
it — the save answer (`RomInfoService.save_answer`, over `SaveLocationReader`). Tender computes no save directory of its
own: a sync, a probe, the adoption rename and the directory follow below all use the one that answer names. The
RetroDECK home migration is the exception — it walks the old home's saves root file by file.

### Where the save directory comes from

The vendored resolver reads the machine the way the emulator does: RetroDECK's `retrodeck.json` for the roots,
RetroArch's own configuration for where saves go below them, and the core's recorded behaviour for what it writes. So
RetroArch's two sort settings — `sort_savefiles_by_content_enable` (group under the content's folder, RetroDECK's
default) and `sort_savefiles_enable` (group under the core's name) — are part of the answer rather than settings Tender
reads, and so is a core that keeps its saves in a folder of its own — the resolver answers `saves/3do/opera/per_game`
for 3DO's Opera and `saves/neogeo/fbneo` for Neo Geo's FinalBurn Neo, which has not yet been observed on a device. The
answer's `directory` is the one the emulator opens; `backing_directory` is where a link resolves to and answers identity
questions only.

A sorted directory RetroArch has not created yet comes back with the `sorted-dir-missing` caveat and a
`fallback_directory`, the unsorted root RetroArch falls back to (the rule and its source are at `SORTED_DIR_MISSING` in
`domain/save_answer.py`). A probe there finds nothing, which is the truth; moving files there creates the directory
first. Any answer a sync would not carry — no emulator resolved, nothing the resolver could establish, a shared card
even where its directory is known, a save inside the content file, one written beside it, a missing saves root, a place
not read from the emulator's own settings — gives no directory to sync in (`SaveAnswer.sync_directory` is empty), and
none is supplied by a guess: the sync backstop, the status probe and the prune projection skip, and copy-to-slot,
rollback, slot switch and conflict resolution refuse — with `savefiles_in_content_dir` for an answer beside the content
(next section), otherwise with `save_shape_unsupported` and the answer's own explanation. The slot choice, with or
without a migration, refuses as a sync of the game does, a missing saves root with `saves_root_missing`
([Confirming a slot](#confirming-a-slot-confirm_slot_choice)). A place not read from the settings also carries the
resolver's finding, the source's kind and the emulator's label, which the panel words the refusal from
([The seven save states](save-sync-coverage.md#the-seven-save-states)). Two notes the resolver states change nothing
here: `redirect-outranked`, since Tender reads no redirect route and moves no save by a launch's flags, environment or
working directory, and `save-files-shared-with-core`, which stands beside a card folder the two PlayStation 2 libretro
cores share and so on an answer already refused as shared
([#2644](https://github.com/danielcopper/romm-tender/issues/2644) D2).

### Saves written next to the content (`savefiles_in_content_dir`)

An answer anchored in the directory of the ROM itself says so through its `root_kind` (`content_directory`). RetroArch's
**Write Saves to Content Directory** is the usual cause, not the only one: RetroArch reaches the same root through
`systemfiles_in_content_dir` too, and a core's own recorded behaviour can anchor it there whatever RetroArch is set to —
EasyRPG writes its save slots beside the game's files. Where the answer names a per-game file set a sync could otherwise
carry, Tender keeps save sync off for that ROM — syncing saves beside the content is a separate decision this one does
not make. Every other answer anchored there (a save **inside** the content file — PUAE on an `.adf`, Hatari on a `.st` —
writes discarded, nothing named) is not this case: `SaveAnswer.in_content_directory` reads the syncable rule itself, so
such an answer gets its own save-shape refusal and its own explanation on the status read. The single-ROM sync entry
points refuse with the benign skip (`{success: false, reason: "savefiles_in_content_dir", …}` — the game still launches,
no error); the whole-library sweep, which reads every installed ROM's answer whether its slot is confirmed or not,
passes such a ROM over inside its run and, where every ROM it read saves beside its content, refuses with the same skip,
or otherwise counts the ROMs it held back in its message ([Manual sync all](#manual-sync-all)). The secondary write
endpoints (rollback, slot switch, conflict resolve, the slot choice with or without a migration, copy to slot) refuse
with the same reason. `get_save_status` carries the additive `savefiles_in_content_dir: true` flag, so the game-detail
play section shows a banner saying the saves are written beside the game file, and naming RetroArch's setting only as
the usual cause — only while save sync is enabled, since the banner is about getting save sync to work.

**Why this is easy to confuse**: "Write Saves to **Content Directory**" controls the **destination** (next to the ROM vs
the saves directory), while "Sort Saves **Into Folders by Content Directory**" controls the **layout within** the saves
directory. They sound nearly identical and mean different things; only the first stops the sync.

### Supported systems

All paths below are relative to RetroDECK's saves root, the resolver's `saves_root()`.

**There is no fixed table any more.** The file names come from the save answer, read per ROM and per the emulator that
would launch it, so the examples below are illustrations of the SHAPE rather than a list to rely on — the same system
answers differently for a different game file, and three of the rows a table like this used to carry were wrong.

| System           | Save path example       | What the answer names                                                                   |
| ---------------- | ----------------------- | --------------------------------------------------------------------------------------- |
| Game Boy Advance | `saves/gba/game.srm`    | `<stem>.srm`                                                                            |
| Game Boy         | `saves/gb/game.srm`     | `<stem>.srm` and `<stem>.rtc`                                                           |
| Saturn           | `saves/saturn/game.bkr` | `<stem>.bkr` and `<stem>.bcr`, plus a `.smpc` that is configuration and is never synced |
| Nintendo DS      | `saves/nds/game.dsv`    | `<stem>.dsv`                                                                            |
| Neo Geo Pocket   | `saves/ngp/game.flash`  | `<stem>.flash`                                                                          |
| PlayStation 2    | —                       | a shared memory card: refused                                                           |
| Dreamcast        | —                       | a card named after the game's own id: refused                                           |

The per-state picture, and which systems land where on a stock RetroDECK, is in
[Save sync coverage](save-sync-coverage.md).

## The Local-Save Check

`check_local_drift` (`StatusService`, `services/saves/status/service.py`) reads a ROM's save files against their
recorded syncs without asking the server, so it answers while RomM is unreachable. It reports two facts apart, and one
verdict over them:

- `drifted` — a save's content differs from its last recorded sync (`last_sync_hash`). A save with no recorded sync is
  not drift, and neither is a reading that failed.
- `only_here` — save sync is on and a save a sync carries (no configuration file, no savestate, nothing under a save
  answer the sync refuses) has no recorded sync: the ROM has no save state, or its save state has no entry for that
  file. RomM is not asked, so a save RomM already holds that Tender has not recorded counts too, and "Sync now & switch"
  then only records it.
- `stranding` — `domain/save_stranding.py::stranding_verdict`: `none` while save sync is off, whatever the reading
  found; otherwise `unchecked` where the reading failed (a save file that cannot be hashed, a failed read of the save
  state, any other error), `unsynced` where either fact holds, and `none` where neither does.

Two kinds of caller read different fields, because "nothing recorded" answers their questions in opposite ways. Play
while RomM is unreachable ([Pre-launch sync](#pre-launch-sync), step 8) asks whether a later sync could overwrite a
local change, and reads `drifted` alone: a save with no recorded sync has nothing to overwrite, and an unknown counts as
no change, since a false warning there is worse than none. The version switch, the removed-game cleanup and the Saves
tab's reminder ask whether a save exists anywhere but here, and read `stranding` alone; each words `unchecked` as Tender
not having been able to check rather than as saves never uploaded. On the Saves tab a check call that rejects, or gets
no answer within `LOCAL_CALL_LIMIT_MS`, counts as `unchecked` too.

The check follows a moved save directory first ([below](#following-a-moved-save-directory)), and so holds the ROM's
`rom_lock` and waits for any other holder of it.

## Following a Moved Save Directory

RetroArch does not move existing saves when its sort settings change, so after a change the files sit where the old
answer put them and the emulator looks elsewhere. Tender follows them per game, by comparison rather than by computing
where the old directory was ([ADR-0041](../adr/0041-the-save-directory-is-the-resolvers-answer.md)).

**The record.** Each ROM's `AnsweredSaveDirectory` (`answered_save_directories`, keyed by `rom_id`) holds the directory
the resolver last answered for its save. It is compared with today's answer and read as the source of the move below; it
is never where a sync or a probe looks. Why it is a table of its own is
[ADR-0041](../adr/0041-the-save-directory-is-the-resolvers-answer.md)'s.

**When it is compared.** `SyncEngine.follow_save_directory` (`services/saves/sync_engine/engine.py`, over
`SaveDirectoryFollower.do_follow` in `services/saves/save_directory.py`) is called under the ROM's `rom_lock`, with the
live reading the caller already took, before the caller looks at any local file:

- by the four sync paths — `pre_launch_sync`, `post_exit_sync`, `sync_rom_saves`, and the whole-library sweep's per-ROM
  step for a ROM whose slot is confirmed — after the gates each checks first (save sync switched off; a pending
  RetroDECK home migration; `post_exit_sync`'s own setting; the sweep's device check and its confirmed-slot filter) and
  before the refusal that reads the same answer, so a game whose save Tender cannot sync is still followed;
- by the five write paths — `switch_slot`, `rollback_to_version`, `copy_save_to_slot`, `confirm_slot_choice` with
  migration, and `resolve_sync_conflict` — so that, for one, `switch_slot`'s pending-changes guard looks where the files
  are after a sort flip;
- by the two deletes and the two reads that count local saves — `delete_local_saves`, `delete_platform_saves`,
  `count_platform_saves` and `get_save_status` (for an installed ROM) — so a delete after a sort flip takes the old
  folder's files too, and nothing is carried back in by the next path that follows;
- by [the local-save check](#the-local-save-check), `check_local_drift` (for an installed ROM), so the version switch,
  the removed-game cleanup and the Saves tab's reminder do not find the new directory empty while the saves still lie in
  the old one.

The follow belongs to the sync, so it does nothing while save sync is off; a sorting change made meanwhile is followed
once save sync is on again, for a game whose directory was recorded. It does nothing while an update of Tender is being
installed, since the installer stops the backend without waiting for a move, and says so in the debug log. It also does
nothing while a RetroDECK home migration is pending or still running, its re-record included: `follow_save_directory`
checks that itself, so the gate holds for every caller — the status and count reads among them, which the migration rule
does not otherwise stop — and the files stay the migration's to move until it has finished. And it does nothing while
the ROM's game is running ([A game that is running](#a-game-that-is-running)), except where the caller passes
`while_running`: the sync before a start and a conflict resolved before a start run before the game starts, and the sync
after a game after it, while Steam may still list the game as starting or exiting; the sweep asked a moment before,
under the same lock. The next caller after the game follows. (Other things move save files regardless of the setting:
the RetroDECK home migration and the adoption rename.) A failure — a listing refused, the database busy — is logged and
leaves the record as it was; the operation that called it goes on. Against the record:

- an answer with no directory, one anchored in the content's own directory (beside the content file or inside it), one
  whose directory was not read from the emulator's own settings, or one whose directory still holds a hole (`<cwd>/…`,
  `…/<region>/…`, `…/<save_id>/…`; `SaveAnswer.concrete_directory`), which names no folder → nothing moves and nothing
  is recorded;
- nothing recorded → today's answer is recorded; nothing moves;
- the same directory → nothing;
- a different one → the game's files move from the recorded directory to the answered one, then the record moves on.

An answer anchored in the content's directory is left alone because, for a multi-file game, that is the game's own
folder, which an uninstall removes whole. The old record stays, so switching the setting back finds it unchanged and
moves nothing. This holds for as long as the content-directory gate does.

**The move.** The files moved are the answer's own names, configuration included, where it is syncable; where it
refuses, everything in the old directory named `<stem>.*` — never in the content file's own directory, where that
pattern is the game itself. Where there is something to carry, a missing directory is created first. A name in **both**
directories is a collision and nothing is overwritten or removed: the older copy goes through
`SaveBackup.back_up_save_file` into the `.romm-backup` folder of the directory it sits in, and a tie keeps the copy
where the emulator looks. Something other than a file under the save's name at the target stops that file and keeps the
old record. The record moves on only once every file arrived, so a move that fails part-way is tried again the next time
one of the paths above touches that game's saves. Two spellings of one directory move nothing. No Unit of Work is open
across a file operation or a resolver reading.

**Filling the record in.** A one-time background task on the first start with the record
(`SaveService.record_save_directories_once`, started by `Application.run_startup_repairs`, cancelled by
`Application.shutdown`) walks the installed ROMs one at a time under each ROM's lock and records today's answer where
none is recorded — subject to the first rule above. The `save_directories_recorded` marker in `kv_config` makes it
one-time. It is written only once the pass has finished over a detected emulator installation with no ROM failing: a
pass cut short, one that found no installation to ask, or one in which a ROM's recording failed (logged, and the pass
goes on to the rest) runs again at the next start. A RetroDECK that is installed but has not been set up yet is no
installation to ask (`AtlasSaveLocationAdapter.installation_detected`): it answers every question with its `not-set-up`
finding, which says nothing about where saves are, so neither this pass nor the re-record below acts on it. A save-sort
migration left pending by an older version cannot be followed: nothing records the directory its files are still in.
Saves that were synced can be downloaded again at the game's next sync; a save that exists only on the device stays in
the old folder.

### Relationship to `retrodeck_path_migration`

The RetroDECK **path** migration — `_migrate_retrodeck_files_io` in `migration/service.py`, triggered when the RetroDECK
home directory moves between the internal SSD and an SD card — uses a user-driven bulk strategy modal (overwrite / skip
/ cancel). That is intentional: it moves ROMs and BIOS files as well as saves, and the user decides. It walks the old
home's saves root rather than any game's answer. Once it has run, including with skipped or failed moves, it replaces
each installed ROM's record with the resolver's answer from the new home, and drops the record where that answer is one
the follow does not act on (`SaveService.rerecord_save_directories`, reached through a late binding). So the follow
finds the record equal to the answer, or finds none and records a first sight: with `skip`, the copy left in the old
home stays there and is never carried over the one the user kept. The migration rule keeps refusing and
`get_migration_status` reports the migration as still pending until that re-record has finished, although a run without
errors clears its markers before it, so no sync meets a record still naming the old home. Two limits: while no emulator
installation is detected the re-record leaves every record as it was, and a ROM whose re-record fails keeps its old
record (the failure is logged, the rest are recorded), so for those ROMs the next path that follows can still carry the
copy left in the old home. See [RetroDECK Path Migration](../user-guide/retrodeck-path-migration.md) for the user-facing
side.

### Detecting a home change

All five trigger points run the same idempotent detection, `MigrationService.detect_retrodeck_path_change()`: the three
frontend ones through the `refresh_migration_state` endpoint, the post-exit one through `MigrationService.refresh_state`
directly, and backend start by calling it as a start-up step. Running on every trigger is cheap: it has an early-return
guard that exits when the home has not changed since the last call.

| When             | Where (code location)                                                          | Why                                                                                    |
| ---------------- | ------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| Backend start    | `Application.run_startup_repairs` in `bootstrap/application.py`                | Catches changes that occurred while the backend was not running                        |
| QAM open         | `MainPage.tsx` mount `useEffect`                                               | User navigating via QAM sees current state when Settings is one tap away               |
| Game-detail open | `useMigrationRefresh` in `frontend/src/utils/migrationRefresh.ts`, per `appId` | Per-game navigation refreshes state when the user browses without launching            |
| Pre-game-launch  | `launchInterceptor.ts`                                                         | Catches a home moved since the prior session, before the launch it would affect        |
| Post-game-exit   | `SessionLifecycleService.finalize` (backend, after the post-exit sync)         | Refreshes the pending state after every session; the payload rides the finalize result |

## Slot Deletion

Users can delete save slots from the game detail SAVES tab. Deletion removes the slot from local state and bulk-deletes
all server saves in the slot.

### How it works

1. **Get delete info**: `get_slot_delete_info(rom_id, slot)` returns metadata for the confirmation modal — server save
   count, tracked file count, slot source (server/local), and whether the slot is active. A server slot whose saves
   cannot be listed is never answered as "0 saves": the RomM error answers `classify_error`'s reason and message, and
   the panel shows that message instead of opening the modal.
2. **Confirmation modal**: Always shown (both local-only and server-backed slots). Shows exact save count and whether
   saves will be deleted from the server.
3. **Perform deletion**: `delete_slot(rom_id, slot)` bulk-deletes server saves via `POST /api/saves/delete`, removes the
   slot from the `slots` dict, and cleans up `files` entries whose `tracked_save_id` matches a deleted save.

### Safety invariants

- **Active slot cannot be deleted.** The user must switch to a different slot first. This implicitly prevents deleting
  the last remaining slot — the last slot is always active (there's nothing to switch to), so it can never be deleted.
- **Server errors leave state intact.** If `delete_server_saves` fails (network error), the slot is NOT removed from
  local state, and the toast shows `classify_error`'s message. The user can retry.
- **Local-only slots** (`source: "local"`) skip server calls entirely — always deletable.

### Frontend

The delete button appears in inactive slot bodies alongside "Activate Slot". It is hidden on the active slot. Gamepad
navigation between the buttons uses `Focusable` with `flow-children="right"` for proper DPad left/right traversal.

A surface reaches steps 1 and 3 through `loadSlotDeleteInfo` and `executeSlotDelete` in
`frontend/src/utils/saveSlotFlow.ts`: the first toasts a failure, the second its answer either way, and the surface
draws the modal between them.

## Server Capabilities

The capabilities system (`get_server_capabilities` endpoint) has been removed. Every RomM version Tender accepts has
device sync, version history, slot deletion and device management, so all of them are unconditionally available. The
frontend no longer fetches or checks capability flags.

## Conflict Resolution

A `Conflict` outcome from `compute_sync_action` (matrix rows 12b, 6c, 6a, 11b, and 9b), or a conflict the
[409 backstop](#upload-time-conflicts-the-409-backstop) resolves to, opens the conflict modal. The common case fires
when the local file has diverged from the recorded baseline (`local_hash != last_sync_hash`) while the server moved to
content we never synced — either on a save we already have an entry for (`device_syncs[me].is_current=false`, row 12b)
or on a new head we have no entry for (row 6c). Rows 6a and 11b are the no-baseline siblings: we have no baseline and
our local content differs from the server head, whether we hold no entry on it (6a) or an `is_current=false` one (11b) —
the same both-sides-unreconciled hazard (#1276). A sync sends the device id, so a head this device never synced carries
RomM's placeholder: a brand-new device, which holds no baseline, lands on 11b, and a device holding a baseline from an
earlier sync beside a head it never synced lands on 12b. 6c and 6a are reached by no Tender path
([Where the never-touched rows are reached](#where-the-never-touched-rows-are-reached)). In all four the two sides have
unsynced changes that cannot be silently merged. Row 9b is the corrupt-local guard: we own the picked save and our local
diverged, but the local file is 0-byte or implausibly shrunk, so the upload is refused and the user resolves it instead
of the only good server copy being superseded (#1062).

### The modal

`SyncConflictModal` (`frontend/src/shared/SyncConflictModal.tsx`) shows the local-save row and the picked server-save
row side by side, each with size and timestamp. Three actions:

- **Keep Local** → `resolveSyncConflict(rom_id, filename, "keep_local")` → backend POSTs local content as a new server
  version with `overwrite=true` (the old server save is retained, not overwritten in place).
- **Use Server** → `resolveSyncConflict(rom_id, filename, "use_server")` → backend downloads the picked server save and
  overwrites local.
- **Cancel** → pure UI close, no endpoint call, no state mutation. The conflict re-fires on the next sync as long as the
  underlying state still produces one: matrix row 12b, 6c, 6a, 11b, or 9b, or an upload the 409 backstop resolves to a
  conflict.

On a successful resolution the modal closes and surfaces a branch-specific confirmation toast — **Keep Local** confirms
the local save was uploaded to the server, **Use Server** confirms the server save is now in use and the prior local
save was backed up to `.romm-backup`. Failure and stale branches stay inline in the modal instead (no toast).

The dialog draws and asks; what it says and what a resolution does live in `frontend/src/utils/saveConflictFlow.ts`, for
any surface that shows a conflict:

- the dialog's wording — its title, explanation, the two saves' lines and the stale-conflict sentence;
- `resolveOneConflict`, one resolution: it sends the chosen side through `resolve_sync_conflict` and answers either the
  toast to show once the dialog closes or the message to keep in it;
- `resolveConflictsSequentially`, the walk: one conflict at a time, stopping at the first cancel;
- `resolveKnownConflicts`, the conflict a surface is already showing: it reads it through `get_save_status`, never a
  re-sync, hands it to the surface's own walk and tells the other surfaces once it is resolved.

`showSyncConflictModal(conflict)` shows the dialog for one conflict and returns a Promise resolving to
`"keep_local" | "use_server" | "cancel"`; `handleConflicts(conflicts)` runs the walk with it. Four callers reach it:

- `CustomPlayButton`, through `handleConflicts` — on the launch gate's `conflict` verdict (as
  `gamepadLaunchPrompts.resolveConflicts`), and through `resolveKnownConflicts` when the user resolves the conflict the
  button is already showing.
- The launch watcher (`frontend/src/utils/launchInterceptor.ts`), through `handleConflicts` — on a `conflict` verdict
  for a start whose questions go to the gamepad modals. It does not import the modal: `index.tsx` hands it
  `gamepadLaunchPrompts` (`frontend/src/shared/launchPrompts.ts`), whose `resolveConflicts` is `handleConflicts`, as the
  fallback of `launchPromptsForThisStart` ([Pre-launch sync](#pre-launch-sync)).
- `VersionHistoryPanel.handleRestore` (in `SavesTab`), through `showSyncConflictModal` for the first conflict — when a
  version-restore pre-flight returns `conflict_blocked`.
- `useCopyToSlot` (the Saves tab's "Copy to slot…"), through `showSyncConflictModal` for the first conflict — when a
  copy returns `conflict_blocked`.

After post-exit sync, `sessionManager` only fires a toast — the conflict re-surfaces in the modal at the next
pre-launch.

### resolve_sync_conflict endpoint

`SaveService.resolve_sync_conflict(rom_id, filename, server_save_id, action)` — the async use case the `main.py`
endpoint calls. The façade delegates to `SyncEngine.resolve_sync_conflict`, whose rollback sub-module
(`services/saves/sync_engine/rollback.py`) runs the resolution:

1. Acquires the per-rom asyncio.Lock so no other sync operation for this rom can race.
2. Fetches a fresh server-saves list and re-picks the newest in the active slot.
3. **Round-trips `server_save_id`**: the caller passes the id the user was shown in the modal. If the freshly-picked
   head's id doesn't match, a third device has uploaded a newer save into the slot between the modal opening and the
   click. The backend returns `{success: False, reason: "stale_conflict", message: ...}` instead of dispatching —
   silently POSTing local content over the third device's work with `overwrite=true` would be a write-loss. The frontend
   surfaces an error and the user cancels + retries; the next sync re-evaluates the matrix with the fresh head.
4. Dispatches:
   - `keep_local` → `_resolve_conflict_keep_local` fetches a server copy and hashes it
     (`SaveTransfer.hash_server_copy`). If it matches local (rare, but possible — both devices ended up at the same
     content via different paths), the head is adopted through the sync's own adopt step
     (`SaveTransfer.adopt_server_head`, [Outcomes](#outcomes)): confirmed to RomM (`POST /api/saves/{id}/downloaded`,
     through the retry strategy), then recorded with the sync boundary and without re-uploading — after the user
     resolved the conflict this device must be current on the head. A file that no longer hashes as it did by the time
     the adopt measures it again is uploaded instead, as one that differs is; recording nothing would bring the resolved
     conflict back. Where RomM does not take that acknowledgement but the local file holds the head's bytes (by its
     `content_hash`, as for Use Server below), the resolution answers success all the same, with a warning logged and
     nothing recorded: the device already holds the head, and the next sync adopts and acknowledges it
     ([#2664](https://github.com/danielcopper/romm-tender/issues/2664) D9); a head without a `content_hash` answers the
     error. Otherwise the local file is POSTed as a new version with `overwrite=true` — the user's deliberate overwrite
     bypasses the 409 gate (#1276). The POST's own `device_save_sync` upsert leaves us `is_current=true`, so the
     redundant `confirm_download` ack is skipped (#1458). The comparison only spares an upload: a server copy this
     device cannot write (a full disk) is logged as a warning and any other failure to fetch it passed over, and either
     way the local file is uploaded.
   - `use_server` → `_resolve_conflict_use_server` downloads the picked save, writes it to the local path and
     acknowledges it once it is in place
     ([A download is acknowledged once placed, never marked at the GET](#a-download-is-acknowledged-once-placed-never-marked-at-the-get)).
     Where the file is in place but RomM did not take the acknowledgement, the resolution answers success all the same,
     with a warning logged and nothing recorded for the file: the choice is carried out on this device, and the next
     sync adopts and acknowledges the file ([#2664](https://github.com/danielcopper/romm-tender/issues/2664) D7). Kept
     open with the error, the dialog would offer Keep Local, which would claim to keep and upload a local save the
     device no longer holds. The file counts as in place when it hashes to the picked save's `content_hash`; a save
     without one answers the error.

The modal only accepts `keep_local` or `use_server`; `cancel` never reaches the backend. A wrong action string is
refused before the server is asked anything.

Before it lists, the resolution takes its device id from `SyncEngine.live_device_id` and refuses as it does
(`device_not_registered` or `server_unreachable`). A RomM error from fetching the list, from the transfer or from an
acknowledgement answers `classify_error`'s reason and message — but for Use Server's and Keep Local's acknowledgement
once the file holds the head's bytes, above. An `OSError` on the local side — a local save missing at the canonical path
among them — refuses with `resolve_failed`. Anything else is a transport error.

### Why no defer state

Earlier drafts persisted a `deferred` field in per-file state to suppress the modal on subsequent syncs until the server
state changed. This was removed before merge: the conflict is already surface-on-demand (only shown during a
user-initiated launch), and re-firing on the next launch is the desired behaviour — the user has just reopened the game
and is in a position to decide. Self-healing is automatic: if another device pushes in the meantime, the picked server
save changes and the matrix may produce Skip or Download instead of Conflict, dissolving the conflict without user
input.

### Per-rom asyncio.Lock

`SyncEngine._rom_sync_locks: dict[int, asyncio.Lock]` (`services/saves/sync_engine/engine.py`) serializes
`pre_launch_sync`, `post_exit_sync`, `sync_rom_saves`, `sync_all_saves`, and `resolve_sync_conflict` for the same
`rom_id`. `StatusService.get_save_status` also takes the lock because of its one write: the executor body records an
adopted baseline (a `Skip(adopt_baseline=True)` that needs no acknowledgement) and persists it through a
`rom_save_sync_states` read-modify-write, which would otherwise race a concurrent sync and clobber that sync's update.
It asks the server first, outside the lock, and holds the lock across the save-answer reading, the directory follow and
the local probe as well as that write. The slot list (`SlotListing.get_save_slots`) takes the lock for the same reason:
it merges the server's slots into the stored listing and writes the state back. It too asks the server first, outside
the lock, and holds the lock only for its one unit of work — the read, the merge and the write; a slot list whose server
cannot be reached reads the stored state for its last-known listing without the lock and writes nothing. The three
**slot mutations** — `SlotSwitcher.switch_slot`, `SetupWizard.confirm_slot_choice`, and `SlotDeleter.delete_slot` — take
the lock too: each loads the `RomSaveSyncState` aggregate, mutates it (active-slot flip, slot-confirm, slot/file
tracking teardown, switch downloads/deletes), and persists, so without the lock a slot op racing an in-flight sync on
the same ROM loses updates or PUTs the wrong slot's content into the tracked server save (#1057). Unlike the status and
the slot list, they hold it across their server I/O: `switch_slot` lists the ROM's server saves and downloads the new
slot's, `delete_slot` deletes the slot's server saves, and `confirm_slot_choice`'s migration lists, downloads and
uploads, all under the lock. The lock is held as well by `SaveCopyService.copy_save_to_slot`,
`VersionsService.rollback_to_version`, the removed-game cleanup (`PruneSaveSupport.lock_prune_roms`, one lock for each
ROM its save inventory names, taken in ascending id order), deleting a ROM's or a platform's local saves and counting a
platform's (`SaveService._delete_saves_for_roms`, `SaveService.count_platform_saves`), the passes that record each
installed ROM's save directory (`SyncEngine._record_each_installed_rom`), and the local-save check
(`StatusService.check_local_drift`) — the count, the record passes and the check hold it for the directory follow, not
for a write of the save state; following a moved save directory (`SyncEngine.follow_save_directory`) runs inside the
lock of whichever holder calls it. Different rom_ids have independent locks, so cross-game concurrency (e.g. Sync All
Saves running concurrently with a resolve on one specific rom) is unaffected. The lock is created lazily on first access
(`SyncEngine.rom_lock(rom_id)`).

The lock is **not reentrant** (plain `asyncio.Lock`), so a critical section must never call a peer that re-acquires the
same lock. `switch_slot` is the live instance: its tail `get_save_status` re-takes `rom_lock(rom_id)`, so the lock is
released at the end of the read-mutate-write block and the status read runs **outside** it — nesting them would
self-deadlock. The peer calls a slot mutation makes while holding the lock (`read_save_answer`, the directory follow,
`_migrate_slot_saves_io`, `_delete_server_slot_saves`, the matrix download/upload workers) are all lock-free by design,
so holding the lock across their server/file I/O is safe and is the intended serialization point.

The realistic race the lock prevents: user clicks Keep Local → executor runs the POST (`overwrite=true`) + state
mutation → in parallel, `post_exit_sync` for a game that just stopped runs and mutates the same per-file state →
last-writer-wins on the `rom_save_sync_states` aggregate, dropping one set of fields. The same lost-update window
applies to `get_save_status`'s baseline-adopt write versus a concurrent pre-launch / post-exit / manual sync. The lock
makes each read-modify-write-and-persist sequence atomic relative to the others.

## Local Save File Naming

Every download path — pre-launch / post-exit / manual sync, conflict-resolve "Use Server", rollback / version switch,
slot switch — writes content to a path of the form:

```text
<saves_dir>/<rom_basename>.<server_save.file_extension>
```

`<rom_basename>` is the ROM file's name without extension (e.g. `Example Quest - Second Journey (USA)` from
`Example Quest - Second Journey (USA).gba`); `<server_save.file_extension>` is the `file_extension` field on the chosen
RomM save (e.g. `srm`).

This is the **only** path used for local writes. The server's stored `file_name` (which may carry a timestamp tag like
`[2026-03-24_15-18-50]` or come from a different client with an unrelated naming convention) and the server's
`file_name_no_tags` are **not** consulted. RetroArch identifies SRAM purely by `<rom_basename>.<ext>` filename match —
content is opaque bytes — so writing to anything else would leave the save invisible to the emulator.

The shared helper is `_local_save_target(server_save, rom_name)` in `backend/services/saves/_helpers.py` (wrapping
`domain.save_path.compute_local_save_target`). It requires a non-None `rom_name`; there is no fallback to server-derived
names. If a ROM is not installed (`RomInfoService.get_rom_save_info` returns `None`) the saves tab shows no entry for it
and sync is a no-op for that ROM — by design, rather than guessing a path that may or may not match what RetroArch uses.

This matches the convention used by the official RomM clients [Argosy](https://github.com/rommapp/argosy-launcher) and
[Grout](https://github.com/rommapp/grout).

The version-history UI (`list_file_versions`) reflects the same principle: it returns every save in the active slot
except the currently-tracked one, with no filename filter. A user can switch to any save in the slot — even ones
uploaded by another client with a different name — and the destructive switch lands the content at the canonical local
path. A listing that could not reach RomM answers `server_unreachable` and the panel offers a retry; a definitive 404 is
`not_found`; any other RomM answer is `romm_refused` with `classify_error`'s message, which the panel shows as "Couldn't
load the versions: …" rather than blaming the connection.

## Version Switch Flow (rollback)

Users can switch the active save to a chosen older version via the Previous Versions dropdown in the SAVES tab. The flow
is more involved than a simple download because it must:

1. Capture any local changes server-side first (otherwise the destructive overwrite would lose them).
2. Make the chosen save authoritative cross-device — other devices that already have the latest save tracked must end up
   downloading the chosen version on their next sync.

### Multi-file saves: version history suppressed (interim #908 guard)

Some systems store one game state across **several files with distinct extensions** — e.g. a Sega Saturn cartridge save
is `<rom>.bkr` + `<rom>.bcr` + `<rom>.smpc` (three files = one state). RomM stores each filename as an **independent
save record with its own version stack**, so the slot's "current save" is really an N-file _set_, not a single file with
a version history. Per-file rollback would revert one component and leave the siblings on their current version — an
incoherent save.

Until grouped save-states with atomic set rollback land
([#908](https://github.com/danielcopper/romm-tender/issues/908)), Tender **detects multi-file slots and suppresses
version history + rollback** for them:

- `get_save_status` carries `multi_file: bool`, `component_files: list[str]` (the N filenames, sorted), and
  `rollback_supported: bool`. Detection counts the distinct canonical target filenames the active slot resolves to
  (across the matrix outcomes); more than one ⇒ multi-file.
- The SAVES tab replaces the Previous-Versions dropdown for a multi-file slot with a read-only **"Files in this save
  (N)"** component list plus a short note that per-version rollback isn't available yet.
- `list_file_versions` short-circuits to `{"status": "multi_file_unsupported", "versions": []}` and
  `rollback_to_version` refuses with `{"status": "unsupported"}` before any preflight or destructive I/O. Both backstops
  detect multi-file from the **local** save files on disk (a rollback target is always installed), so they add no extra
  network round-trip.

Single-file slots — including a single file with genuine prior versions — are unaffected and keep the full
version-history + rollback flow described below.

### Why a switch cannot be a download-only

A pure download to local would only update _our_ device. On the next sync from any other device, RomM's
newest-by-`updated_at` rule would still pick the original (newer) save and propagate it back to us. The switch would
silently undo itself.

To make the switch authoritative cross-device, the chosen older save's `updated_at` must become NOW so it beats every
other save in the slot.

### Matrix pre-flight

Before the destructive switch starts, `rollback_to_version` runs a full `compute_sync_action` pre-flight on the
currently-tracked save (via `do_sync_rom_saves`):

| Pre-flight outcome                           | What happens                                                                                                                                                                                                |
| -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Skip(synced)` / `Skip(adopt_baseline=True)` | No transfer; an adopt marked `acknowledge` confirms the head to RomM first (a failure is `preflight_failed`). Switch proceeds.                                                                              |
| `Upload`                                     | Local changes are silently pushed to the server first. Switch proceeds.                                                                                                                                     |
| `Download(server)`                           | The newer server save is silently adopted. Switch then proceeds (the user's chosen target is still in the slot).                                                                                            |
| `Conflict(...)`                              | Switch aborts with `{"status": "conflict_blocked", "conflicts": [...]}`. The frontend opens the standard `SyncConflictModal`; the user must resolve via Keep Local / Use Server before retrying the switch. |
| Non-conflict error                           | Switch aborts with `{"status": "preflight_failed", "errors": [...]}`.                                                                                                                                       |

The pre-flight replaces the dedicated "unsynced local changes" / "tracked save missing" warnings the previous design
used — those were workarounds for not running the matrix. With the matrix in front of every switch, local data is always
captured (or the user is forced to resolve a real conflict) before the local file is overwritten.

### The four-step destructive switch

After the pre-flight clears, `VersionsService._rollback_to_version_io` (`services/saves/versions.py`) runs the switch —
the actual file/server writes go through `SaveTransfer`:

1. **Download target**: GET the chosen older save's content and overwrite local. `download_save_file` updates
   `tracked_save_id` and `last_sync_hash` to the target version, so even if step 2 fails the local view is consistent
   with the target. The download is not acknowledged: step 2 records this device on RomM
   ([A download is acknowledged once placed, never marked at the GET](#a-download-is-acknowledged-once-placed-never-marked-at-the-get)).
2. **PUT to bump `updated_at`**: re-upload local content via `upload_save_file(server_save=target_save)`. This issues a
   PUT against the target save id with byte-identical content. RomM fires the SQLAlchemy `onupdate=utc_now` hook on
   every PUT regardless of whether the content changed, so `save.updated_at` becomes NOW; the same request also upserts
   our `device_save_sync` row (`last_synced_at = updated_at`). The target save is now newest in the slot and
   `is_current` is already `true` for us.
3. **Confirm download (redundant)**: `upload_save_file` still runs the upload's own acknowledgement. On every supported
   RomM version the PUT already upserted our sync row (step 2), so this ack is at most a redundant idempotent re-write
   of `last_synced_at = save.updated_at`, not the load-bearing step it once was (#1458).
4. **Update local state**: `upload_save_file` records `tracked_save_id`, `last_sync_hash`,
   `last_sync_server_updated_at`, and friends from the post-PUT response, leaving local state consistent with the
   now-newest server save.

After this, the next `compute_sync_action` for our device picks `target_save` (now newest), our `is_current=true`, hash
matches the baseline → `Skip(synced)`. Other devices on their next sync see `target_save` as newest with their own
`is_current=false` → matrix row 5 (`Download`) → adopt our switch. Cross-device propagation works without a dedicated
rollback API.

### Failure handling

- **Pre-flight `Conflict`**: switch never runs. Status: `conflict_blocked`. Local file untouched.
- **Pre-flight non-conflict error**: switch never runs. Status: `preflight_failed`. Local file untouched.
- **The listing after the pre-flight fails**: switch never runs. A call that could not reach RomM is
  `server_unreachable`, a definitive 404 `not_found`, and any other RomM answer `romm_refused` with `classify_error`'s
  message, which the panel words "Couldn't restore the save: …".
- **Step 1 (download) fails**: state is not mutated. Local file unchanged, except where the failure came after it was
  moved into `.romm-backup` (the move into place failing): it stays there, and the next sync downloads the slot's newest
  save (row 4). The `.tmp` is handled as [Negotiate as transport](#negotiate-as-transport-one-decision-kernel)
  describes. A RomM error is not caught here: the endpoint answers the shared failure shape (`success: false`) with
  `classify_error`'s reason and message. Where this device cannot write the download (`DeviceWriteError`, a full disk
  among them) the status is `write_failed`, its message naming the system's words for the fault, and the panel words it
  "Couldn't restore the save: …".
- **Step 1 (download) succeeds, step 2 (PUT) fails**: state mutation from the download is persisted. Status:
  `put_failed`. Local file and local state both point at the target. Cross-device propagation is incomplete — other
  devices still see the original newest save. Calling `rollback_to_version` again is safe and idempotent: step 1 is
  already done, step 2 retries the PUT.
- **Refused by the conflict rules**: while an update installs, a RetroDECK migration is pending or a removed-game
  cleanup runs, the hold in `SaveService.rollback_to_version` refuses before any of the above, with the shared failure
  shape.

The panel words both failure-shape answers "Couldn't restore the save: …", followed by the endpoint's message. After a
restore that went through (`ok` or `put_failed`) only the restored file's Previous Versions closes, and it reads its
list again when reopened; every other panel on the tab keeps its state, and an open version list re-reads itself on the
`romm_data_changed` the restore dispatches.

## Copy a save to another slot

A per-save **"Copy to slot…"** action (`SaveCopyService`, `services/saves/copies.py`; endpoint `copy_save_to_slot`)
takes one server save — from a named slot, an older version, or the read-only legacy no-slot bucket — and copies its
content into a target slot. The action is offered on every save row that carries a server save id: the active slot's
current save, its Previous-Versions rows, and the inactive/legacy slot panels.

Two things define the semantics:

- **It is a copy, never a move.** The source save is never deleted (the user removes it in the RomM web app if they want
  to). A ROM has many slots; this does not "move the ROM onto one slot".
- **The target slot becomes the ROM's active/confirmed slot,** with the copied save as its current save. This is
  mechanically forced: `SaveTransfer.upload_save_file` resolves its upload slot purely from `save_state.active_slot`
  (`RomSaveSyncState.upload_slot`) — there is no per-call slot override — so `active_slot` is set to the target
  (`confirm_slot`) _before_ the upload.

The flow is a composition of the rollback orchestration shell and the wizard-migration's make-current step (it extends
neither in place):

1. Validate the target name (empty/whitespace → `invalid_slot_name`).
2. Under `rom_lock`, refuse an unconfigured ROM (`active_slot` is `None` or the slot is unconfirmed) with
   `not_configured` — the Copy action is only reachable on a configured ROM, and this is the defensive backstop, not a
   UI-gate reliance (the #1529 lesson). `rom_not_installed` when there is no install record.
3. Multi-file (#908) saves, saves written beside the content, and answers with no save directory are refused with
   `unsupported` (the latter two carry the `savefiles_in_content_dir` and `save_shape_unsupported` reasons), exactly as
   rollback refuses them — copying one component of an N-file set would produce an incoherent save, and the other two
   have nowhere the sync writes to.
4. A **matrix pre-flight** (`do_sync_rom_saves`) runs unconditionally on the _current_ slot, protecting its dirty local
   before the copy overwrites it: `conflict_blocked` / `preflight_failed` on a bad pre-flight (same shapes as rollback).
5. `list_saves` with **no slot filter** (the chosen save may live in any slot) locates the save by id; a missing id is
   `version_deleted`. A call that could not reach RomM is `server_unreachable`, a definitive 404 `not_found`, and any
   other RomM answer `copy_failed` with `classify_error`'s message — a 429 is RomM rate-limiting, not RomM gone.
6. **Dedup pre-check**: if the chosen save's `content_hash` already matches a save in the _target_ slot, the copy is a
   no-op — `already_present{existing_id}` (the existing twin's id) is returned with **no** download / upload /
   make-current, so nothing churns (the frontend toasts "already in slot … as `#<id>`" and does not refresh). It fires
   only when `content_hash` is populated (RomM 5.0.0+); an absent hash falls through to the copy and RomM's own
   server-side dedup. This is what stops a repeat-copy of already-present content from re-pointing the tracked save.
7. **Busy-slot pre-check**: where RomM would refuse the copy's POST, the copy is `target_slot_busy` with **no**
   download, backup or make-current — the previous slot stays active, the local file and its baseline stay as they were,
   and the panel does not refresh. The check is RomM's [409-gate](#the-add_save-post-409-gate), read off the step-5
   listing by `domain/add_save_gate.py::slot_refuses_new_save`. An empty slot passes, and a slot whose newest save
   cannot be established (a time that does not parse, or a tie with a save that has no id) is left to RomM's 409. The
   toast tells the user to activate that slot — which syncs it — and copy again.
8. `SaveCopyService._copy_save_to_slot_io` then: `SaveTransfer.download_save_file` (the chosen content onto the
   canonical local file, backing the current local file up first, #965, and not acknowledged, since the POST records
   this device; a local file this device cannot write is `copy_failed`, and a RomM error is not caught here — either way
   the active slot stays as it was) → `confirm_slot(target)` (creates the slot if new, switches active + confirms) →
   `upload_save_file(server_save=None, overwrite=False)` — a POST into the now-active target slot. Any upload error is
   `upload_failed`, a **409** included — another device's upload landed in the target slot between the step-7 check and
   the POST, or the check could not establish the slot's newest save — and undoing the download and the slot change
   would need a put-back that never overwrites: as with a restore's `put_failed`, the copy is complete on this device
   and its state is persisted, and the target slot stays active. So `target_slot_busy` always means that nothing
   changed. The download recorded the source save as the file's baseline, which the copy matches, so where this device
   is current on the target slot's newest save, the next sync would find nothing changed (row 7) and never upload it.
   `upload_failed` therefore records the target slot's newest save for that file — its id, its `content_hash` and its
   size — as the baseline, and the next sync reads the copy as a local change: row 9 uploads it as a new version
   superseding that save, where this device is current on it. Row 9b still applies — a copy under half that save's size,
   or for a file the save answer states fixed-size, any copy shorter than it, is a conflict, not an upload — and so is a
   save there this device is not current on: row 12b, whether it never synced that save or another device moved it on
   since. That baseline is recorded from the save's size alone and carries no [content size](#the-content-size), so row
   9b judges the copy by raw size. A new slot has no save to record, and the next sync uploads the copy as its first
   save (row 2) — unless the upload was a 409 and the slot then holds a save of this file the step-5 listing did not:
   the file's tracking is dropped, and the next sync asks which to keep (row 11b) rather than download that save over
   the copy. Where the save RomM refused on is of another file, the copy's upload reaches the next sync's 409 backstop
   with nothing to reconcile and is reported as an error for the copied file. Where the newest save carries no
   `content_hash` or no id, there is no baseline to record from it, and the file's tracking is dropped instead: with no
   baseline beside a head it cannot prove the copy identical to, the next sync answers a conflict — row 8b where this
   device is current on that save, row 11b where it is not. Neither alternative holds: a hash made up to differ from the
   copy would claim a sync that never happened, and keeping the download's baseline, which the copy matches, would let a
   next sync on a head this device is not current on download that head over the copy.

The status union mirrors `RollbackStatus`:
`ok | not_configured | invalid_slot_name | rom_not_installed | version_deleted | unsupported{reason?, message?} |
server_unreachable{message} | not_found{message} | conflict_blocked{conflicts} | preflight_failed{errors} |
target_slot_busy{message} | already_present{existing_id} | copy_failed{message} | upload_failed{message}`.
On `ok` and `upload_failed` the frontend dispatches `romm_data_changed` so the parent re-fetches — refreshing both the
source and the target slot views; the `upload_failed` toast says the save is copied on this device but not uploaded to
RomM yet, and names the two outcomes the next sync has outside the missing-hash case and the other file's 409 that step
8 names: it uploads the copy, or asks which to keep. Outside the union the endpoint answers the shared failure shape
(`success: false`) in two cases: when the conflict rules' hold in `SaveService.copy_save_to_slot` refuses, as for a
restore, and on a RomM error at the step-8 download, with `classify_error`'s reason and message. The panel words it
"Couldn't copy the save: …", followed by the endpoint's message, and refreshes nothing. The source slot is excluded from
the target picker (a same-slot copy is just a rollback) and the legacy `""` bucket is excluded as a target (it stays a
read-only source).

## RomM Save Sync API Behaviour

Tender depends on several RomM behaviours that are not obvious from the OpenAPI schema, each read in RomM's source at
5.3.0 (`backend/endpoints/saves.py`, and `backend/handler/database/device_save_sync_handler.py` for the sync rows). They
drive design decisions throughout the sync layer.

### `is_current` is computed, not stored

RomM's `device_save_sync` table stores `last_synced_at` and `is_untracked` per device per save. The `is_current` field
surfaced on each `device_syncs[]` entry of a `GET /api/saves` response is **derived at read time** as
`sync.last_synced_at >= save.updated_at` (greater-or-**equal** — equality counts as current). There is no column to set;
you can only push the components.

### `GET /api/saves` writes no sync row

`GET /api/saves` (`get_saves`) only reads `device_save_sync` rows. What each save's `device_syncs` carries is built for
the answer by `_build_save_schema`, and depends on whether the request names a device:

- **With `device_id`** — every device's stored sync row for that save, each with `device_name`, `last_synced_at`,
  `is_untracked` and the computed `is_current`. A calling device that has no row on the save gets a placeholder of its
  own, which RomM stores nowhere: `last_synced_at` is the save's own `updated_at`, `is_untracked` and `is_current` are
  `false`. The caller's entry comes first. So this device has an entry on every save it lists, and a save it never
  synced reads as not current.
- **Without `device_id`** — `device_syncs` is empty, whatever rows are stored.

The sync decision reads the placeholder, so the RomM adapter keeps it. What the SAVES tab is shown does not: a save
row's and each version's `device_syncs` leave out the entry that names this device with the placeholder's shape —
`is_current` false while `last_synced_at` is at or after the save's `updated_at`, which RomM's own `is_current` rule
never answers for a stored row (`domain/save_status_builders.py::shown_device_syncs`). Without that, the placeholder,
listed first and stamped with the same time as the row of the device that really uploaded the save, would be the one the
"last synced by" name is taken from, and name this device on a save it never synced. A save row's `is_current` is still
read off RomM's list as it came, so a save this device never synced still reads as not current.

The rows that make a save current for a device are written only by:

- an upload, a download, and the `POST /api/saves/{id}/downloaded` ack — see the sections below;
- RomM's own background sync tasks, which act only on devices registered for file-transfer or push-pull sync — Tender
  registers its device in RomM's default API mode;
- `POST /api/saves/{id}/untrack`, which Tender does not call.

Which decision rows a `list_saves` without a device id reaches is under
[Where the never-touched rows are reached](#where-the-never-touched-rows-are-reached).

### The `add_save` POST 409-gate

`POST /api/saves` does not blindly accept a new version. When the request carries a `device_id` and a `slot` and does
**not** set `overwrite`, RomM's `add_save` handler guards against a stale device clobbering the slot:

```text
if device_id and slot and not overwrite:
    latest = first of saves_in_slot by updated_at desc, Save.id desc   # newest save in the slot (_saves_query)
    sync   = get_device_sync(device_id, latest.id)                      # our sync row for it, if any
    if sync is None or sync.last_synced_at < latest.updated_at:
        raise HTTP 409                                                  # not current (or never synced) → refuse
```

Read in RomM's source at 5.3.0 and unchanged through 5.4.1. The slot's saves are taken over every file name. Two
branches raise: a device whose `last_synced_at` is behind the slot's newest `updated_at` (stale), **and** a device that
has **never** synced the slot (`sync is None`). The second branch is easy to miss — a brand-new device POSTing into a
slot that already has saves gets a 409, not a silent second version. Tender relies on this as the server-side backstop
for its automatic-upload conflict path (see
[Upload-time conflicts (the 409 backstop)](#upload-time-conflicts-the-409-backstop)); an explicit `keep_local` sets
`overwrite=true`, which skips the gate entirely.

A listing taken with this device's id carries the same comparison: the device's `device_syncs` entry on a save has
`is_current = last_synced_at >= updated_at`, and a device with no sync row gets a placeholder entry with `is_current`
false (`_build_save_schema`). So a copy reads the gate off its listing before it writes anything
([Copy a save to another slot](#copy-a-save-to-another-slot), step 7).

### POST and PUT both upsert the calling device's sync row

Read in RomM's source at 5.3.0: both `POST /api/saves` (`add_save`) and `PUT /api/saves/{id}` (`update_save`) bump
`save.updated_at` to the server's NOW **and**, for a request that names a device — every upload Tender sends does —
upsert the calling device's `device_save_sync.last_synced_at = updated_at`. From 5.4 that row also holds the
`content_hash` the request names, which every upload Tender sends carries
([A download is acknowledged once placed, never marked at the GET](#a-download-is-acknowledged-once-placed-never-marked-at-the-get)).
Both build their response **after** that upsert, so the body's `device_syncs` shows us `is_current = true` (equality
counts) the moment it returns. The skip logic is fail-open regardless: a response that proves us current skips the ack,
anything else (including a bare body) still sends the idempotent ack.

This makes the dedicated `POST /api/saves/{id}/downloaded` ack **redundant on the normal upload path** — the upload
skips it when the upload response already shows this device `is_current` (`SaveTransfer.upload_save_file`, #1458),
saving one round-trip per uploaded file.

**`add_save`'s content-dedup early-return.** A named-slot `overwrite=false` POST whose content hash matches an existing
save in the same slot returns that pre-existing save **before** the `device_save_sync` upsert. For a request that names
a device, RomM runs its [409-gate](#the-add_save-post-409-gate) before it looks for that match, so the device is already
current on the slot's newest save: a dedup onto that save answers current, and the ack is skipped. A dedup onto an older
save answers what this device's row on that save says — current for a version it synced itself that nobody has changed
since, otherwise not current (a stale row, or RomM's placeholder).

On the automatic sync path a dedup onto an older save never reaches the ack: the dedup-to-non-head guard refuses it
first, from the listing the upload was planned against
([Upload-time conflicts](#upload-time-conflicts-the-409-backstop)). Two callers POST with `overwrite=false` and no
planned listing (`planned_group`), so the guard is inert for them: copying a save into another slot, and migrating
legacy saves into a slot. There a dedup onto an older save is acked when it answers not current, and the ack is the only
writer of this device's sync row on that save. The ack stays best-effort — failures are logged at debug and never fail
the upload.

### A download is acknowledged once placed, never marked at the GET

`GET /api/saves/{id}/content?device_id=X&optimistic=true` (`optimistic` defaults to `true`) upserts `device_save_sync`
for the calling device with `last_synced_at = save.updated_at` when it answers the GET, before the file is anywhere on
this device, recording the save's hash as the row's server hash and no device hash. `POST /api/saves/{id}/downloaded`
writes the row by the `content_hash` its body names (RomM 5.4.0 and 5.4.1, `confirm_download`; RomM 5.3 reads no hash
and always stamps `updated_at`). A row that holds a server hash and no device hash — what an optimistic GET leaves, and
what every upload an older Tender sent without a hash left — keeps its own `last_synced_at` and server hash and takes
the named hash, so the device stays exactly as current as that row was. Any other row, and a device with none, is
stamped with `updated_at`: with both hashes where the named one equals the save's own, with neither where it does not.

`download_save_content` in `adapters/romm/romm_api.py` always passes `device_id`, and every download passes
`optimistic=false` (`SaveTransfer.download_save_file`). The mark at the GET records this device current before the file
is placed, so a file that then is not placed — a backup or a rename that fails, a full disk, a stop — would leave RomM
holding this device current on a save the disk does not hold. The next sync would read the old local file as in step
with the newer head (`Skip(synced)`) and, once the game is played on it, upload over the other device's newer save
without a 409; after Use Server it would upload the local save the user chose to discard. So a download is acknowledged
with `POST /api/saves/{id}/downloaded`, through the retry strategy, only once the file is in place — one more request
per download ([#2664](https://github.com/danielcopper/romm-tender/issues/2664) D1, D2). Two downloads send no
acknowledgement: a version restore's and a copy to a slot's, whose upload right after records this device on RomM
itself. There an acknowledgement could only add a failure: their callers catch only a local `OSError` from the download,
so one that failed would end the operation before that upload, and the next sync would upload the downloaded save into
the slot still active.

Every acknowledgement names the content hash of the file this device holds — the download it placed, the local file an
adopt records, the file an upload sent — computed as RomM computes a save's `content_hash`
(`SaveFileStore.content_hash`: the MD5 of the bytes, or of a zip's entries)
([#2664](https://github.com/danielcopper/romm-tender/issues/2664) D4). Every upload and in-place update names the hash
of the file it sends as the `content_hash` query parameter, which RomM records as the row's device hash (D5), so a row
Tender leaves no longer has the shape above. A row an older Tender left still takes the first acknowledgement without
moving its sync time, so where RomM's answer to an acknowledgement still shows this device not current,
`SaveTransfer._acknowledge` sends exactly one more, which RomM then records (D6) — but only where that answer states the
save still holds the hash Tender named: a client that replaced the save between the download and the acknowledgement
would otherwise have the second one mark this device current on bytes it does not hold, so there the acknowledgement
counts as not taken; an answer that does not name the device says nothing either way and is taken as it is. Measured on
RomM 5.4.1, after another client replaced a save in place: a download and one acknowledgement with the hash leave this
device current where its upload carried the hash, and a second acknowledgement is needed where it did not.

An acknowledgement that fails — a RomM error, a first answer that shows this device not current and names other bytes,
or a second answer that still shows this device not current (`RommAcknowledgementNotTaken`, which `classify_error`
answers `server_error`) — raises with the file in place, its backup kept and nothing recorded
([#2184](https://github.com/danielcopper/romm-tender/issues/2184) D8): the sync reports the file as an error, a slot
switch counts it among the downloads that failed (`switch_incomplete`), and Use Server and Keep Local's adopt count as
done where the file holds the head's bytes ([resolve_sync_conflict endpoint](#resolve_sync_conflict-endpoint)). The next
sync finds the file identical to a head this device is not current on, and adopts and acknowledges it (rows 11a and
12a); where another client replaced the head meanwhile, it decides against that newer head as any sync does.

`download_save(save_id, dest_path)` names no device: it fetches a server copy to compare
(`SaveTransfer.fetch_server_copy`) and records nothing.

### Implication for the sync algorithm

Because `is_current` is computed and the only ways Tender makes it `true` are a PUT/POST (which upserts the uploader's
sync row directly) or the `POST /api/saves/{id}/downloaded` ack — the other writers are listed under
[`GET /api/saves` writes no sync row](#get-apisaves-writes-no-sync-row) — the algorithm can trust `is_current` as
authoritative about the server side without further hashing. It is no statement about the local file: row 8 (no baseline
yet, `is_current=true`, local exists) adopts the local file as the baseline only where it is the head's bytes (8a), and
asks the user otherwise (8b) — see
[Why row 8 splits into adopt (8a) vs conflict (8b)](#why-row-8-splits-into-adopt-8a-vs-conflict-8b).

## Sync Flows

All four sync entry points share a single decision primitive — `compute_sync_action` — and a single dispatch path —
`_dispatch_sync_action`. The flows differ only in _when_ they fire and how they surface results.

**Completion toast — per-direction counts (#250, #1481).** `_dispatch_sync_action` returns a `TransferDirection`
(`UPLOAD` / `DOWNLOAD` / `None`), so `sync_rom_saves` tallies uploads and downloads separately and each per-ROM result
dict carries additive `uploaded` / `downloaded` counts beside the `synced` total (a 409-backstop download counts as a
_download_, not the upload that lost the race). The completion toast names which way saves moved — "Saves uploaded to
RomM", "Saves downloaded from RomM", or "Saves synced with RomM (1 up, 2 down)" when a run went both ways — and a run
that transferred nothing shows no toast. **Exception (#1486):** the manual per-game "Sync Saves" click is an explicit
user action, so when it moves nothing and hits no conflicts it acknowledges with a "Saves already up to date" toast
rather than staying silent; the automatic surfaces (pre-launch, post-exit) keep the silent zero-case. The wording lives
in exactly one place: the frontend helper `saveSyncToastBody` (`frontend/src/utils/saveSyncToast.ts`), which every
surface renders through — pre-launch (`runPreLaunchSync`, for a start from the Play button), post-exit
(`sessionManager`, from the counts on the `finalize_game_session` payload), and the manual per-game sync
(`RomMPlaySection`, through `frontend/src/utils/manualSaveSync.ts`). The backend delivers the counts as data, never the
directional copy (#1481); the offline/failure body it still owns rides a separate `failure_toast` field on
`SessionFinalizeSyncResult`.

### Pre-launch sync

Runs before a RomM game starts (if `sync_before_launch` is enabled), through one of two funnels that share
`runLaunchGate` (`frontend/src/utils/launchGate.ts`). Tender's Play button runs it from `CustomPlayButton.handlePlay()`
before it starts the game itself; the launch watcher (`frontend/src/utils/launchInterceptor.ts`) gates the starts that
do not come through the Play button — Steam's own Play and a `steam://rungameid` link. Pre-launch sync is **not**
triggered via `RegisterForAppLifetimeNotifications`. A start the gate refuses before the save work — the source that
would start it switched off ([Which emulator source answers](core-emulator-selection.md#which-emulator-source-answers))
— runs no sync at all, and nor does a start refused before the gate, where the game's file does not start with its
emulator ([Launch-target validation](backend-architecture.md#launch-target-validation)).

Every launch path builds the gate's inputs with `makeLaunchGateOps` (`frontend/src/utils/launchGateOps.ts`) and acts on
its verdict with `runGateLoop` (`frontend/src/utils/launchVerdict.ts`), which runs the gate, hands the verdict to
`actOnGateVerdict` and runs the gate again for as long as the user picks Retry on the offline-drift prompt. The shared
parts are the migration check, the starting-source check, a fresh reachability probe, the drift check, the pre-launch
sync itself (`runPreLaunchSync`) and what each verdict means: a switched-off source toasts, resolved conflicts announce
the save change to the page and then launch. A path supplies only what is its own:

| Supplied by the path               | Play button                                                                                                                      | Launch watcher                                     |
| ---------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| Prompts (`LaunchPrompts`)          | `gamepadLaunchPrompts` (`frontend/src/shared/launchPrompts.ts`)                                                                  | chosen once per start (below)                      |
| Tracking setup                     | `ensureTrackingConfiguredBigPicture` — an answer it cannot settle aborts and opens the Saves tab (`applyLaunchGateSetupOutcome`) | adopts a default silently and always proceeds      |
| Toast for a sync that moved saves  | yes                                                                                                                              | no                                                 |
| A start that ends without a launch | the trigger returns to Play, or to Resolve Conflict after a cancelled conflict                                                   | nothing of its own; a pending migration is toasted |
| Acting on a verdict throws         | the trigger returns to Play                                                                                                      | fails open: the game starts (bare relaunch)        |

The watcher has no surface of its own, so `index.tsx` hands it a function rather than a set of prompts:
`() => launchPromptsForThisStart(gamepadLaunchPrompts)` (`frontend/src/utils/launchPromptRouter.ts`), which it calls
once per start, as that start's gate begins, so every question one start puts comes from the same surface. Which surface
that is:
[How the desktop surface adapts Steam's game page](desktop-dom-architecture.md#dialogs-for-a-start-the-launch-watcher-catches).

The watcher listens on `SteamClient.Apps.RegisterForGameActionStart`, which reports a start with `action` `"LaunchApp"`
before Steam creates the game's process. Its second argument is **not the appId but the 64-bit game ID** in decimal: for
a non-Steam shortcut the appId sits in the upper 32 bits and the lower 32 bits hold the shortcut mark `0x02000000` — a
shortcut with appId `3000000001` starts as `"12884901892328521728"`. `appIdFromGameId` (`frontend/src/utils/gameId.ts`)
turns it back into the appId with `BigInt` (the value is far above 2^53); a value that fits in 32 bits is taken as the
appId itself, and any other game-ID kind names nothing of ours. Every later step of the watcher reads that appId. For an
appId Tender owns (`rommAppIds`), it cancels the start synchronously (`CancelGameAction`), runs the gate, and on
approval starts the game again with `RunGame`, handing it the game ID the start was reported by — or, for a start
reported by a value that fits in 32 bits, the shortcut's game ID from Steam's app store where it has an overview.

The start is cancelled before the watcher asks the backend anything, and an endpoint call has no timeout: it waits for a
backend that is away, and one uninstalled while Steam keeps running never comes back. So the watcher's first backend
contact — the map refresh below, when one is needed, and the installed check — races `FIRST_CONTACT_DEADLINE_MS` (5 s).
Past it the watcher fails open: it starts the game again without the pre-launch sync, shows "Tender isn't responding —
started without syncing saves." and logs it. An answer that arrives after the deadline is abandoned and starts nothing
more. The later steps are not bounded by this deadline but by limits of their own, which both funnels share (below).

Neither funnel waits for the backend without end. Every backend call the gate makes has a limit: `LOCAL_CALL_LIMIT_MS`
(5 s) for the calls that stay on this machine — `check_start_source`, `is_save_tracking_configured`,
`confirm_slot_choice`, `check_core_change` and `check_local_drift` — and for `probe_reachability`, whose single
heartbeat to RomM gives up after about 3 s on its own; `SERVER_CALL_LIMIT_MS` (15 s) for the two that read or sync the
server's saves, `get_save_setup_info` and `pre_launch_sync` — though the pre-launch sync goes on past its limit, and the
start does not without the user (below). Both live in `frontend/src/utils/launchGate.ts`; the session manager bounds
with them too the backend calls it waits for while handling Steam's start and stop notifications (see
[Post-exit sync](#post-exit-sync)). A limit is a ceiling, not a delay. A step's own fallback answers a call that
**failed** — a probe that throws counts as offline — but never one that got **no answer**: that ends the check in the
"Save Sync Unavailable" dialog, reading "Couldn't check your saves in time — launch with local saves?". The limit can
expire on a slow RomM while Tender itself is fine, and `check_local_drift`'s while another holder keeps the game's
`rom_lock` (step 8 below), which is why the dialog does not blame either. "Launch Anyway" starts the game on the local
save without re-confirming the launch options, because that re-confirm asks the same backend and its own timeout stops a
start; "Cancel" leaves the game unstarted — the Play button back on Play, the watcher's start still cancelled. An answer
that arrives after the dialog appeared starts nothing more. The one exception is `check_start_source`, whose every
unanswered case — no answer, a failed call, a failed detection — asks a question of its own instead of opening that
dialog ([Which emulator source answers](core-emulator-selection.md#which-emulator-source-answers)). A wait on the user's
answer in a dialog is never bounded. One wait has no limit: once an action is picked in the conflict dialog, the dialog
waits for `resolve_sync_conflict` with its Cancel disabled
([#2176](https://github.com/danielcopper/romm-tender/issues/2176)).

**The pre-launch sync goes on past its limit, and no start goes past it without the user.** The backend gives it no time
limit of its own: `SyncEngine.pre_launch_sync` waits for the device gate and for the game's `rom_lock` until each is
free ([#2184](https://github.com/danielcopper/romm-tender/issues/2184) D1). When it has not answered within
`SERVER_CALL_LIMIT_MS`, `runPreLaunchSync` (`frontend/src/utils/launchGateOps.ts`) asks `pre_launch_sync_state` what it
is doing — within `LOCAL_CALL_LIMIT_MS`, where no answer, like a failed read, is "Tender is not answering" — and puts
the "Waiting for Save Sync" question (`LaunchPrompts.askSyncWait`, drawn by the start's prompts —
`frontend/src/shared/SyncWaitModal.tsx` on the gamepad surface). It names the reason: another save sync holds the device
(Sync All Saves Now, the sync after a game, a manual sync, or the sync of another start; `GateHolder`,
`services/saves/sync_engine/_gate.py`), something else is busy with this game's saves (its `rom_lock`), this game's own
sync is running, or Tender is not answering. Its three answers:

- **Wait** waits `SYNC_WAIT_MORE_MS` (15 s) more. A sync that answers within it is acted on as one that answered within
  the first limit — the game starts, or the conflict or fallback dialog asks. One that does not brings the question
  back, with its reason read again.
- **Play Anyway** calls `stop_pre_launch_sync`, waits for its answer within `LOCAL_CALL_LIMIT_MS`, and starts the game,
  re-confirming the launch options. After "Tender is not answering" the stop is sent without waiting for it and the
  start skips the re-confirm, as "Launch Anyway" does after a step that got no answer.
- **Cancel** starts nothing — the Play button back on Play, the watcher's start still cancelled. The sync runs to its
  end, since no game is running; its late answer tells the game's page to re-read its saves, and on the Play button a
  sync that moved saves is toasted as an answered one is.

A sync that answers while the question is open and nothing was pressed turns it into "Saves Ready", with Play and
Cancel: the game never starts by itself. One whose answer needs acting on first — a conflict, a failure — closes the
question, and the answer is acted on as above.

**The stop.** `stop_pre_launch_sync(rom_id)` stops every sync before a start of that ROM — a cancelled start leaves its
sync running, and the next start of the game calls again (`services/saves/sync_engine/_pre_launch.py`) — and a Sync All
Saves Now's or a Sync Saves' sync of the same ROM, since that game is about to start
([A game that is running](#a-game-that-is-running)); no sync of another ROM. A sync before a start still waiting for the
gate or the lock stops at once and writes nothing; a Sync Saves still waiting ends at its first check once it holds
them, before it follows a moved save directory. One that runs checks its stop (`SyncStop`,
`services/saves/_sync_stop.py`) at three points: before it follows a moved save directory, before each file, and between
a finished download and putting it in place. A download finished after the stop is discarded with its `.tmp`, and the
local file is neither backed up nor replaced; what was placed before the stop stays placed and is recorded, since
`write_save_state` still runs. A download is acknowledged only once in place, so a discarded download leaves RomM's
record of this device unchanged and the exit sync still sees the conflict
([A download is acknowledged once placed, never marked at the GET](#a-download-is-acknowledged-once-placed-never-marked-at-the-get)).
A file whose placing began before the stop is still placed, and none begins after it; a transfer already under way in
the executor runs to its end, which is why the stop is a check inside the sync rather than a cancellation message in the
socket protocol, which could not reach that transfer either. The stopped call is refused with `pre_launch_sync_stopped`,
its details carrying what it moved; the panel, which has gone on to start the game, only reads it as a late answer.

The romId comes from `sessionManager`'s appId → romId map, re-read at the moments
[App ID to ROM ID mapping](#app-id-to-rom-id-mapping) lists, while `rommAppIds` learns a shortcut as soon as a sync
writes it. So an owned appId can be missing from the map: the watcher then refreshes the map once (`refreshAppIdMap`)
and looks again, and if the romId is still missing it starts the game again without the gate.

Both funnels mark the appId in the gate's skip-set (`markLaunchSkipped`) immediately before their own `RunGame`, and the
watcher consumes that mark when the start reaches it, so a start a caller has already handled is not gated a second
time. A mark lets a direct start through only within `LAUNCH_SKIP_WINDOW_MS` (10 s) of being set; why the window exists
is stated at the skip set in `frontend/src/utils/launchGate.ts`.

The Play button's path:

1. User presses Play on the game detail page. A game that is already running is brought forward without the check.
   Otherwise the button switches at once to a disabled "Preparing..." throbber, so a second press starts no second
   check; every outcome below leaves that state. The label names no step: the check covers more than saves, and it reads
   the same whether save sync is on or off.
2. Migration: a pending RetroDECK migration blocks the start, and the button returns to Play; the page already says why.
3. Starting source: `check_start_source`. A source switched off blocks the start; where the source could not be checked,
   the user decides, and Cancel ends the press. Whether the game's file starts with its emulator is not a step of the
   check: the page has already decided whether Play can be pressed
   ([#2195](https://github.com/danielcopper/romm-tender/issues/2195) D32), and where the page's own check failed, the
   press asks again before the gate runs ([Launch-target validation](backend-architecture.md#launch-target-validation)).
4. Save tracking: `is_save_tracking_configured`. When it is not set up, `get_save_setup_info` decides between an
   automatic slot pick (`confirm_slot_choice`) and a toast with a switch to the Saves tab, which ends the press — or,
   for a game whose saves no sync carries (`sync_unavailable`), neither: the press goes on with nothing set up.
5. Core change: `check_core_change`. A changed core asks the user in the core-change dialog; Cancel ends the press.
6. Reachability: `probe_reachability` decides between steps 7 and 8.
7. Online: `pre_launch_sync` — past its limit, the "Waiting for Save Sync" question above. The button turns into
   "Syncing saves..." only on the backend's `pre_launch_sync_started` event for this ROM, which the sync sends once it
   has passed "Sync before launch", the refusal of a game whose saves cannot sync and its own reachability check
   (`heartbeat`), just before its first request that names the device. With save sync off, "Sync before launch" off or a
   game whose saves the sync refuses, no event comes, and the button never reads "Syncing saves...": it goes from
   "Preparing..." to "Launching...", or, for a missing saves folder, to the "Save Sync Unavailable" question below. No
   other sync sends it — not Sync Saves, not Sync All Saves Now, not the sync after a game. The panel listens for it,
   for its own ROM, only until its pre-launch step answers, so an event that arrives after that — Cancel or Play Anyway
   past the limit included — changes nothing. The backend fetches server saves and runs `do_sync_rom_saves`, which
   iterates files and dispatches every `compute_sync_action` outcome. If a `Conflict` was returned for any file, the
   result includes a `conflicts` list, and `actOnGateVerdict` puts each conflict through
   `LaunchPrompts.resolveConflicts` in turn — `handleConflicts` (see [The modal](#the-modal)): once all are resolved it
   notifies siblings and goes on; on the first Cancel the button switches to `conflict` and nothing launches. A sync
   that fails never launches unconditionally: the "Save Sync Unavailable" dialog asks first, and Cancel returns the
   button to Play. The benign skips (`savefiles_in_content_dir`, `save_shape_unsupported`) proceed silently; a missing
   saves folder (`saves_root_missing`) is a failure, so the dialog asks with the sentence Main's notice shows for the
   folder — when it asks, and when the exit's toast says so, is
   [The seven save states](save-sync-coverage.md#the-seven-save-states). With "Sync before launch" off the sync answers
   that before it raises the save answer's refusal, so the dialog never asks about a sync that was switched off. When
   the sync moved something, the per-direction completion toast above reports it (uploaded / downloaded / both); a
   failure's classified message appears in the dialog instead.
8. Offline: `check_local_drift`. A local save that changed since the last sync asks Start Anyway / Retry connection /
   Cancel; Retry runs the check again from the start. Only the answer's `drifted` counts here
   ([The Local-Save Check](#the-local-save-check)). The check holds the ROM's `rom_lock` for the directory follow, so it
   waits while another holder — a sync of the same game, say — keeps the lock; held past `LOCAL_CALL_LIMIT_MS`, Play
   gets the "Save Sync Unavailable" dialog ("Couldn't check your saves in time", Launch Anyway offered) instead of
   starting at once.
9. Re-confirm: `get_rom_relaunch_options`, bounded at 3 s, heals the shortcut's launch options. A timeout stops the
   launch and the button returns to Play. A start chosen in the dialog after a check that got no answer skips this step,
   and so does Play Anyway after "Tender is not answering".
10. `SteamClient.Apps.RunGame`, with the button on "Launching...".

Every backend call in steps 3–8 has the limit stated above, and one that expires ends in the "Save Sync Unavailable"
dialog — except the pre-launch sync, whose limit opens the "Waiting for Save Sync" question, and the conflict dialog's
`resolve_sync_conflict`, which has none (above).

### Post-exit sync

Triggered automatically when a game stops (if save sync and `sync_after_exit` are both on).

1. `RegisterForAppLifetimeNotifications` fires with `bRunning: false`. The notification arrives for **every** app Steam
   tracks, so `handleGameStop(unAppID)` finalizes only when the stopping app is the one that opened the active session —
   see [Session scoping](#session-scoping-one-entry-per-running-app). A stop for any other app returns before the
   `finalizeGameSession` call, so the post-exit sync never runs against a game that is still holding its save file open.
2. `sessionManager.handleGameStop` makes a single `finalizeGameSession(romId)` call; the backend
   `SessionLifecycleService.finalize` orchestrates playtime record → post-exit save sync → migration refresh and returns
   one typed payload (the old `recordSessionEnd` / `postExitSync` endpoints were collapsed into it). If Steam's JS
   context was rebuilt mid-session, `handleGameStop` still fires for the adopted session — see
   [Surviving a JS-context rebuild mid-session](#surviving-a-js-context-rebuild-mid-session) — so the post-exit sync is
   not skipped. While a removed-game cleanup runs the call is refused (`prune_active`) and none of those steps runs: the
   panel logs the refusal and says in a toast that the session's playtime and saves were not synced. Any other refusal
   is logged and shown in its own message.
3. Backend runs `do_sync_rom_saves`. For most rows the local file's hash will differ from `last_sync_hash` (the user
   just played), so the typical action is `Upload` — matrix row 9 — POSTed as a new version (`overwrite=false`,
   409-backstopped).
4. If a `Conflict` is returned, a toast notifies the user. The modal is **not** opened post-exit — the conflict re-fires
   at the next pre-launch sync, where the user resolves it via Keep Local / Use Server before launch.
5. Toast notification shown on success or conflict — the per-direction completion toast above (uploaded / downloaded /
   both) on a successful transfer, rendered frontend-side by `sessionManager` from the `uploaded` / `downloaded` counts
   on the `SessionFinalizeSyncResult` payload via `saveSyncToastBody` (#1481). Offline / failure keeps its backend-owned
   body on `SessionFinalizeSyncResult.failure_toast`. While an update is being installed or a RetroDECK migration is
   pending the sync does not start, and `failure_toast` says which held it off: the update is checked first and has a
   body of its own, the migration's body is `MIGRATION_MESSAGE`. With save sync or `sync_after_exit` switched off
   neither is checked, and nothing is toasted.

The session manager handles Steam's start and stop notifications one after another on a single chain, so a backend call
there that never answers would hold every later notification — including the stop that takes Tender's Play button off
"Launching..." and the Resume overlay off a game that has exited. Each backend call a notification waits for is bounded,
with the launch check's two values (`frontend/src/utils/launchGate.ts`). The map refresh before a start is under
[App ID to ROM ID mapping](#app-id-to-rom-id-mapping). `recordSessionStart` waits at most 5 s (`LOCAL_CALL_LIMIT_MS`);
on expiry it is logged, as a failed call is. `finalizeGameSession` holds the chain at most 15 s
(`SERVER_CALL_LIMIT_MS`). Its answer comes only after the post-exit sync, which can take longer than that with a working
backend, so an answer that arrives later is still applied — the playtime shown in Steam, the toasts, the
`romm_data_changed` refresh and the migration state — outside the chain; it touches no session state, and its expiry is
logged as a warning. Without a backend nothing arrives. A call past its limit is not cancelled. Once the chain has moved
on, a second game's post-exit sync can reach the backend's device gate while a first one still holds it (see
[Session scoping](#session-scoping-one-entry-per-running-app)). The reload adoption at start-up, which also runs on the
chain, is not bounded this way.

### Manual sync all

User-initiated from the "Sync All Saves Now" button in Save Sync settings.

1. Iterates all installed ROMs from the backend registry.
2. For each ROM **whose slot the user has confirmed** (`slot_confirmed`): runs `do_sync_rom_saves`. A never-configured
   ROM — one the user has not yet set up save sync for — is **skipped**, so its possibly-stale local save can't be
   auto-uploaded into the default slot and overwrite another device's newer progress (#1055). The single-ROM paths
   (pre-launch / post-exit / per-game manual sync) stay ungated — those are the user's explicit per-ROM actions and the
   first-sync auto-seed path, where the user decides.
3. Per-rom asyncio.Lock prevents collision with concurrent pre-launch / post-exit syncs. Under it, a confirmed ROM whose
   game is running is passed over and named ([A game that is running](#a-game-that-is-running)).
4. Every other ROM's save answer is read live, the never-configured ones' too, and asked through the refusal every sync
   uses (`refuse_unsyncable`); a ROM it refuses is passed over before anything is asked of RomM, and counted by the
   refusal's reason (`SweepTally`, `services/saves/sync_engine/_shape_refusal.py`), never by a list of save states. A
   never-configured ROM the refusal lets through counts as not set up; one it refuses counts by its reason, since it can
   no longer be set up ([#2153](https://github.com/danielcopper/romm-tender/issues/2153)). A never-configured ROM is
   neither asked about Steam nor followed into a moved save directory.
5. Reports total synced count and number of pending conflicts. Conflicts surface via the modal individually at each
   game's next pre-launch sync. The line's "across N ROM(s)" counts the ROMs whose saves were compared with RomM, those
   already in step included; a ROM passed over is not counted, nor is one whose start stopped its comparison (it is
   named as running). After it, one clause per reason that occurred, in this order, each with its count: "N game(s) not
   set up for save sync", "N game(s) skipped — their saves cannot be synced" (`save_shape_unsupported`), one "N game(s)
   skipped — the saves folder _path_ does not exist" per missing saves root (`saves_root_missing`), "N game(s) skipped —
   their saves are written beside the game file" (`savefiles_in_content_dir`), then the running games by name. A missing
   saves root fails the run (`success: false`), as it fails a single game's sync, and so would any other refusal that is
   no benign skip, counted among the games whose saves cannot be synced; every other skip is benign
   ([#2151](https://github.com/danielcopper/romm-tender/issues/2151)). `roms_checked` is the count of installed ROMs
   iterated; the panel does not read it.

### A game that is running

No save path but the sync before a start, the sync after a game and a conflict resolved before a start changes the save
of a game Steam lists as running, the RetroDECK home migration excepted (below)
([#2620](https://github.com/danielcopper/romm-tender/issues/2620)). An emulator that rewrites its save by path writes
over a save swapped under it, and the next sync then uploads that over the other device's newer save without a conflict;
one that keeps the file open writes the session into the copy moved to `.romm-backup`. Whether a ROM's game runs is read
afresh at each check, under the ROM's `rom_lock` (`RunningGameGuard`, `services/saves/sync_engine/_running_game.py`,
over `RunningRomIdsFn`; what counts as running, and how long a reading may take, is under
[Is the game running](#is-the-game-running)):

- **Sync All Saves Now** asks for each confirmed ROM when it reaches it, before it follows a moved save directory, and
  passes a running game over: nothing of it is listed, moved or written. Its follow takes no second reading
  (`while_running`): it asked a moment before, under the same lock, and a start since is the stop's (below). The rest
  sync, `success` is decided by their errors and by whether a saves folder was missing
  ([Manual sync all](#manual-sync-all)), and the answer names the games passed over — in `message`, "_\<game\>_ was
  skipped because it is running", and in `skipped_running` (`[{rom_id, name}]`). A ROM the sweep would not sync anyway,
  one whose slot is not confirmed, is not named. The partial answer of a sweep the server stops partway names them in
  its `message` only, since a partial failure's extras are what was done.
- **A press on the running game's page is refused** with `game_running`, whose message names the game ("_\<game\>_ is
  running. Quit it first."): Sync Saves (with `synced=0`), a slot switch, a copy to a slot, a version restore, a slot
  delete, Delete Local Saves, a conflict resolution, and the setup wizard's migration of legacy saves. Each asks before
  it follows a moved save directory or touches a file, so a refused press writes nothing. A slot confirmation without a
  migration writes no save and is not refused while the game runs. The launch check's conflict dialog resolves before
  the start and says so (`before_start`, from the launch prompts' `resolveConflicts`: `gamepadLaunchPrompts`', or in the
  desktop dev build `desktopLaunchPrompts`'), so it is not refused where Steam still lists a start the launch watcher
  cancelled for the check; a conflict resolved anywhere else — the Play button's Resolve, a copy or a restore — is. A
  version switch's sync before the switch is a Sync Saves and is refused the same way; the panel shows its message.
- **Deleting all local saves of a platform** leaves a running game's saves and record alone, deletes the rest, and names
  it in `message` and `skipped_running`; its `N ROM(s)` leaves the ROMs it passed over out. `count_platform_saves` still
  counts the running game's files, so the button's number is what the delete removes once the game has been quit.
- **The removed-game cleanup** leaves a running removed game out:
  [A game that is running](removed-game-cleanup.md#a-game-that-is-running).
- **The follow of a moved save directory** does nothing for a running game
  ([Following a Moved Save Directory](#following-a-moved-save-directory)) — on the game page's status read, the
  local-save check and the platform count among its callers.
- **A start stops the other syncs of its game.** Play Anyway's `stop_pre_launch_sync` reaches a sweep's sync of that ROM
  and a Sync Saves of it under way at the press, through a `SyncStop` each registers per ROM (`RomSyncStops`). Each
  checks it before it follows a moved save directory and wherever the sync before a start does (above). The sweep goes
  on with the next ROM and names the stopped one as running; a stopped Sync Saves is refused with `game_running`, its
  details carrying what it moved, and a download it discarded leaves RomM's record of this device unchanged
  ([A download is acknowledged once placed, never marked at the GET](#a-download-is-acknowledged-once-placed-never-marked-at-the-get)).
  Without it a game started with Play Anyway while a sweep was on it could have its save swapped seconds after its
  start.

The RetroDECK home migration does not wait for a running game;
[#2226](https://github.com/danielcopper/romm-tender/issues/2226) decided to remove it. A game Steam does not list yet —
between a start and its listing, a window that has not been measured — counts as not running. For a start through Play
Anyway the stop above covers a sync of that game under way at the press, not a sweep that reaches the game after the
press and before Steam lists it.

### Get save status (read-only)

Triggered by the game-detail panel and SAVES tab via `getSaveStatus(romId)`. Runs `_get_save_status_io` — a read-only
counterpart of `do_sync_rom_saves` that returns the same `compute_sync_action` decisions but performs no upload/download
I/O. The only mutation it allows is recording the baseline of a `Skip(adopt_baseline=True)` that needs no
acknowledgement, so future drift detection works; one that must be acknowledged is left to the next sync, and its row
reads "synced", which is true — the bytes are identical ([Outcomes](#outcomes)).

### Offline queue drain

If the RomM server is unreachable when a sync runs:

1. `compute_sync_action` is never reached — `list_saves` raises and the rom-level sync returns an error string.
2. The local save file is untouched. State is untouched.
3. On the next successful server contact (next sync attempt, manual sync, or pre-launch), the algorithm runs against
   current server state and produces the same outcome it would have produced earlier — typically Upload (post-play) or
   Skip.
4. No data is lost. There is no separate retry queue because the algorithm is idempotent: re-running it after a
   transient failure converges on the same end state.

### Heartbeat error classification (launch-time probe)

`pre_launch_sync` and `post_exit_sync` pre-probe the server with a single `heartbeat` call before doing any sync work. A
failure here is **classified by type**, not collapsed onto a blanket "Server offline"
([#971](https://github.com/danielcopper/romm-tender/issues/971)):

- A genuine reachability failure (`RommConnectionError` / `RommTimeoutError`) refuses with `ServerUnreachable`
  (`reason: "server_unreachable"`, `message: "Server offline"`) **plus** the `offline: true` detail, which the
  session-end toast reads; before a launch the answer is a failed sync like any other. Only this branch is caught; the
  raw exception is logged at debug there, so the probe is no silent swallow.
- Any other `RommApiError` is not caught: it propagates, and both of its readers answer it through `lib/errors.py`
  `classify_error`, so it carries its **own** `reason` + `message` — the endpoint translator for `pre_launch_sync`, the
  session lifecycle for `post_exit_sync`. A revoked token (401) surfaces `auth_failed` + "Authentication failed — check
  your username and password", an SSL misconfig surfaces the SSL message, and a 5xx surfaces one of two: an error RomM
  reports itself answers `server_error` with RomM's `detail`, any other 5xx `server_unreachable` with "Server error (N)
  — check your RomM server logs". These answers **omit** the `offline` flag — a 5xx too, whichever reason it answers —
  so the session-end toast never claims a server is offline on anything but a connection that failed. The Play button's
  fallback launch modal shows that backend `message` verbatim, so a user whose token expired sees "authentication
  failed" instead of being told the server is offline forever. Any exception that is not a RomM error is not caught
  either: the endpoint answers it as a transport error, the session lifecycle with its generic failure toast.

The same holds for device registration in `services/saves/sync_engine/devices.py`: `ensure_device_registered` and
`list_devices` catch no RomM error from the registration call or the device listing, so the endpoints answer an auth/SSL
failure with its own classified `reason` + `message` rather than a generic slug, and the device list shows
`classify_error`'s message beneath its "Could not load devices" heading. A registration the server answers without an id
refuses with `server_unreachable` / "Could not register device". Inside a save operation the registration is
`SyncEngine.live_device_id`'s, and it answers differently: a registration that could not reach RomM (an error
`classify_error` calls unreachable) refuses with `server_unreachable`, and a registration that failed any other way with
`device_not_registered`: RomM, or a proxy in front of it, answered without registering the device (the id-less answer
included), or the stored token could not be sent. A read with a failure field of its own reports it there — the status
as `server_query_failed` with the reason in `server_query_reason`, the version list as its `status`, the setup info as
its `recommended_action`; every other operation, the slot listing among them, raises it; nothing is listed either way.

### `DeviceRegistry` owns device identity

`DeviceRegistry` (`services/saves/sync_engine/devices.py`) is the **single owner** of the server device id. It reads
`kv_config["device_id"]` **once** through a narrow Unit of Work and serves the cached value thereafter via
`get_device_id()` — no per-flow transaction. The cache is refreshed when registration writes a new id and can be dropped
via `invalidate_device_id_cache()` for the rare case where `kv_config["device_id"]` is mutated outside the registry
(registration is the only in-process writer, so this is currently reached only from test backdoors). The registry is
built in the `SaveService` facade and handed to `SyncEngine` alone; no other save service holds it.

**Every save operation takes its id from one entry.** `SyncEngine.live_device_id()` registers or heals the device
(`ensure_device_registered`) and answers the id as a `LiveDeviceId` (`domain/live_device_id.py`), a type of its own.
Each save operation that reaches RomM calls it once, before its first request, and hands the id down: the four syncs
(pre-launch, post-exit, one ROM, all), the copy into another slot and the restore of an older version (both before their
pre-flight sync), the conflict resolution, the slot switch, listing and deleting a slot's saves, the setup info and the
slot-choice migration, the version list, and the save status. The slot summary (`get_save_slots`) sends no device id and
stays outside. `RommSaveApi.list_saves`, the sync logic (`do_sync_rom_saves`, `MatrixExecutor.sync_rom_saves`) and the
save transfer's steps that name the device (`SaveTransfer.download_save_file`, `upload_save_file`,
`acknowledge_server_head`, `adopt_server_head`) take a `LiveDeviceId`, so the type checker refuses a caller that passes
a stored id or `None`. The entry pays one `PUT /api/devices/{id}` per operation, reads included: a cheaper path for
reads would be the divergence the entry exists to remove. Concurrent callers wait on one lock, taken innermost — nothing
is taken under it, so a holder of `rom_lock` or the device gate may call the entry — and two of them never register
twice. What keeps the rule and what those checks miss: the invariant register in `CLAUDE.md` and
[invariants.md](invariants.md).

The cached id outside that entry is read by the play-session upload (`SaveService.get_device_id`, the
`DeviceIdProvider`), and by the device list, which marks this device's row. Registration happens whether save sync is on
or off (see [Device Registration](#device-registration)), so the play-session upload has an id with save sync off too.

**A dead id heals itself, and its queued playtime moves with it.** A cached id is trusted only as far as the best-effort
`update_device` touch every `ensure_device_registered` makes: a definitive **404** means the server no longer has this
device (its database was wiped or restored), so the dead id is forgotten and a fresh one registered in its place — every
other touch failure is a transient miss that keeps the cached id, since a server blip must never churn a
re-registration. The outbox row in `rom_playtime_sessions` stores the `device_id` its session was recorded under, so a
heal also re-addresses the pending play sessions from the dead id to the fresh one (`Playtime.reassign_pending_device`,
driven by the narrow `rom_ids_with_pending_device` lookup so only the affected aggregates are rebuilt). The id names the
**server registration**, not the machine — both ids denote this same Deck. Without the carry-over those sessions are
neither lost nor stuck; they are silently **mis-filed**. RomM accepts a POST naming an id it does not know and stores
the row with a `NULL` device (verified — see the ingest bullet under
[Native play-session ingest](#native-play-session-ingest-adr-0018)), so the queued rows would drain as `created`, land
attributed to no device, and take RomM's `(user_id, device_id, rom_id, start_time)` dedup key down with them. Only the
addressee moves: `attempts` is preserved, so a row already walking toward its quarantine ceiling keeps its position. The
re-address is bound to a _minted_ id and is best-effort — a failed re-registration, or a failure of the re-address
itself, leaves the rows queued exactly as they were.

## Playtime Tracking

### Local delta-based accumulation

Playtime is tracked per-ROM in SQLite — the `Playtime` aggregate spanning `rom_playtime` (scalars) and
`rom_playtime_sessions` (the pending-session outbox) — independent of the `saves` lifecycle. Uninstalling a ROM deletes
only its files and `rom_installs` row, leaving playtime and saves intact per
[ADR-0007](../adr/0007-rom-retention-identity-anchor.md).

Session tracking:

1. `recordSessionStart(romId)`: backend opens the session marker (`last_session_start`) on the ROM's `rom_playtime` row
   in a short write Unit of Work, then schedules a background flush of the pending-session outbox (draining any offline
   backlog on launch)
2. `recordSessionStart` also stamps a monotonic start on the row: `begin_session` stores `Clock.monotonic()` in
   `last_session_start_monotonic` alongside the wall-clock start. The monotonic clock pauses while the device is
   suspended, so its delta across the session is awake-only time — this is how suspend time is excluded (#1148). The
   frontend does no suspend accounting: the Steam suspend/resume hooks never fired on current SteamOS, so the frontend
   machinery was removed and `finalizeGameSession(romId)` carries no suspend duration
3. Session end (`finalizeGameSession(romId)` → backend `record_session_end`): in an executor worker, a short write UoW
   folds the closed session into the aggregate (`record_session` counts `monotonic_end` minus the stored monotonic start
   — the awake-only span — clamped to the wall-clock span and to 0–24h, increments `total_seconds` and `session_count`,
   records `last_session_duration_sec`) **and** enqueues the session into the outbox (when a device id is registered —
   an unregistered device folds locally and never enqueues); then, outside the transaction, the outbox flushes to RomM's
   native `/api/play-sessions` ingest (best-effort, offline-safe). When the monotonic delta is unusable (no stored start
   on a pre-migration row, a negative delta from a mid-session reboot, or a delta more than a ~2 s tolerance above the
   wall span) the counted duration falls back to the full wall span — the pre-#1148 behavior, never a regression. A
   debug line (`record_session_end: rom N wall=Xs mono=Ys awake=Zs`) is the on-device verification hook

Playtime is **additive, not a conflict** — the union of per-device session streams, so it needs none of the save-sync
newest-wins / conflict / `.romm-backup` machinery. The server dedupes on `(user_id, device_id, rom_id, start_time)`, so
a re-POST of a queued session is idempotent; the outbox dequeues on a `created` or `duplicate` result and stays queued
only on `error` (ADR-0018).

### Session scoping: one entry per running app

`sessionManager` tracks active sessions in a map keyed by Steam appId, each entry `{appId, romId, startMs}`, opened on a
game start and on adoption. Two RomM games at once hold two entries and each records its own playtime and runs its own
post-exit sync (#1624). Before that the manager held a single slot, and the second game to start displaced the first —
the displaced session was dropped outright: no playtime for the span already played, and no post-exit save sync when
that game eventually exited, because its stop found a slot that no longer held it.

The key is the appId, not the romId, for two reasons. Every write path is handed an appId — the lifetime notification's
`unAppID` on both edges, and the running-app reading at adoption. And the stop path must match against the **appId
recorded with the session**, never a fresh `appId → romId` map lookup: `RegisterForAppLifetimeNotifications` fires for
every app Steam tracks (another non-Steam shortcut, a regular Steam game, a second RomM game), and the cached map can be
stale at stop time, so a stale miss would drop a real session instead of an unrelated one. A romId key would force
exactly that reverse lookup back into the path. A stop for an app with no entry is a debug-level no-op: no playtime
write, no post-exit sync, and every open session stays open until its own app exits. Before #1621 the stop path took no
appId at all and finalized whatever was in the slot, which recorded a wrong (early) duration and — the part that
mattered — ran the post-exit save sync against a game whose emulator still held the save file open, capturing a
half-written file that the real exit then diverged from.

**A start for an app that already has an entry is ignored** (#1589) — the entry keeps its earlier `startMs` and
`record_session_start` is **not** called again. The backend RE-OPENS the marker rather than extending it, so a second
call silently discards the span already played; the re-open is deliberate (adoption depends on it), so the guard belongs
frontend-side. The duplicate start still re-dispatches `romm_session_changed` with `running: true`, so a surface that
missed the first event self-heals. The guard is checked before the rom lookup, so a map that emptied mid-session cannot
drop a live entry.

The session map is one of the signals behind `readGameRunning(appId, romId)`, the predicate every "is this game running"
question asks; see [Is the game running](#is-the-game-running).

**Concurrency of the post-exit work is deliberately not per-app.** Lifecycle events run on one serialized chain, and the
post-exit save syncs behind them serialize further on a **device-wide** gate (`services/saves/sync_engine/_gate.py`, 60s
budget) — RomM's `negotiate` is device-scoped and the engine's layout state is shared mutable state, so parallel syncs
are not safe. Two games exiting close together therefore sync one after the other — the gate keeps them apart even once
the chain has moved on past a finalize's limit (see [Post-exit sync](#post-exit-sync)); if the first overruns the budget
the second is skipped and retried at its next sync. Playtime is unaffected either way — it is recorded before the sync
runs. Per-app sync chains are a non-goal, not an oversight.

The skip says what it is (#1625). A gate timeout carries `reason: "sync_busy"` — never `server_unreachable` — and no
additive `offline` flag: nothing on the path ever contacted the server, so it may not claim it is down. The session-end
toast keys on the reason and reads "Another save sync was still running — saves will sync next time" instead of the old
"Server offline", which sent the user debugging a network that was fine. The sync before a start has no gate timeout: it
waits for the gate, and past the launch check's limit the user is told which sync it waits for and decides
([Pre-launch sync](#pre-launch-sync)). The gate's offline drift warning is not involved on that path and never was: it
is reachable only when the gate's own reachability probe says the server is down, and a sync waiting for the gate means
that probe already succeeded.

A skipped run is **not** re-queued on a delay. It is picked up by the next pre-launch or manual sync, which is why the
copy promises exactly that. Retry-on-a-timer would need scheduling and backoff machinery around a gate that must not be
parallelised, for an outcome that costs one deferred sync — deliberately deferred, not overlooked.

### Surviving a JS-context rebuild mid-session

The in-memory sessions live in the JS context, so a context rebuild while a game is running would leave the next
game-stop with nothing to finalize — the pre-rebuild playtime is lost and the post-exit sync never runs. Two signals let
the re-initialized `sessionManager` recover them:

- **Steam running-state (liveness).** A guarded reader (`utils/runningApps`) is the authority for _whether_ the games
  are still running at re-init — the durable marker (`last_session_start`) is written by `recordSessionStart` precisely
  so it survives the reload, but only Steam can attest a session has not already ended. Its one surface is
  `SteamUIStore.RunningApps`, read through a guard (an absent store, a `null` store or a throwing getter degrades to
  "nothing running", never a throw), and an entry counts only by its display status, Launching as well as Running
  ([Is the game running](#is-the-game-running)): an app Steam kept listed after its exit is not running here either, so
  an attested session for it is orphaned and none is re-stamped for it. A single read is not trusted: the store reported
  an **empty** list for several seconds with the game still running — measured when Decky Loader's `plugin_loader`
  restarted (#1054 / #1148 round 2) — so the read is **polled** (every 500ms for up to 15s), not one-shot, and a
  JS-context rebuild under a running game is guarded the same way. A timed-out round logs the `diagnostics` note that
  tells an absent store, an empty list and a throwing getter apart. The poll settles once something is running **and
  every attested app has surfaced**: the store omits apps whose overview has not loaded, so a reading listing one
  concurrent game can still be missing its sibling, and stopping at the first non-empty round would orphan it.
- **A localStorage breadcrumb (attestation).** One versioned row (`romm-tender:active-session` →
  `{v: 2, sessions: [{appId, romId, startMs}, …]}`) holds **every** open session. It lives in localStorage, which
  outlives the JS context, so it survives the reload. Every localStorage access is wrapped — a storage failure degrades
  to the no-attestation path, never throws.

  Writes are a **projection**: the map is the source of truth and the whole row is rewritten after each mutation, before
  any await, never read-modify-written. `setItem` is atomic per key, so a failed write leaves the previous row intact
  and self-heals at the next mutation. An empty map removes the row, so "no active session" stays the absent state.

  The reader branches on the stored **version first, before any field**, and each version a released build could have
  written gets its own lift into the current shape — a v1 row (one session inline) becomes a one-entry list and is
  rewritten as v2 at the next persist. Validating version and fields in one expression would fail every v1 row, and a
  failed validation is indistinguishable from no attestation, so a running session's pre-upgrade span would be silently
  discarded on the first reload after an update. Entries are read individually: one corrupt entry never voids its
  siblings. An unknown (future) version is unusable — same standing as no row at all.

At `initSessionManager` the recovery runs on the lifecycle chain (so a stop event racing the liveness poll serializes
after adoption — adopt first, then finalize). The attested set and the running set are then reconciled **per app**, so
any number of concurrent games recovers together:

- **Attested + running** → adopted as attested, durable marker untouched. Re-stamping would discard the pre-rebuild span
  the backend already holds. The **breadcrumb's own romId** is adopted, never the one the `appId → romId` map resolves
  now: the binding is 1:1 at any instant but not stable over time (a version switch moves a shortcut to a different rom
  row), and the open marker belongs to the rom the start opened — re-stamping the current one would open a second marker
  and leave the original dangling.
- **Running + ours + unattested** → adopted with the marker re-stamped to a truthful lower bound (`recordSessionStart`),
  then attested so a subsequent reload adopts it as attested instead of re-stamping again.
- **Attested but not running** once the poll settles → the session ended while no panel was loaded to see it stop; a
  truthful finalize is impossible without an observed end, so the entry is dropped and logged as orphaned — never a
  fabricated end time. A stale breadcrumb left by a reboot resolves the same way at the next init; no expiry timers.
- **Running but not ours** → ignored entirely. A foreign app is never adopted, and never a reason to orphan anything
  else. Before #1624 adoption read only the head of the running list, so a foreign app in the foreground orphaned a RomM
  game still running behind it.

An app whose session a start notification already opened is skipped by both passes, so adoption never re-opens a live
session (#1589).

**Attestation invariant:** every finalize fold uses a marker stamped by a start we actually observed (either the
original `recordSessionStart` or an adoption re-stamp) — the client never invents an end time for a session whose stop
it did not see.

### Steam display

Steam natively tracks playtime for non-Steam shortcuts. No additional work is needed — Steam's built-in tracking handles
the display in the library.

### RomM last_played and cross-device reconcile

The native ingest updates the ROM's `last_played` timestamp (and `device.last_seen`) on the RomM server, keeping the
library sorted by recent activity. Opening a game's detail page runs `reconcile_playtime(romId)`: it flushes the outbox,
then `GET /api/play-sessions` for the ROM and sets the displayed total to
`max(local_total, Σ server duration_ms / 1000)`. Playtime is monotonic, so `max()` is always safe — with the outbox
drained it adopts the cross-device server union; offline / partial-flush / not-yet-backfilled it keeps the display from
regressing below local truth. The GET needs the `roms.user.read` scope (added to the minted token in #1280); without it
the reconcile degrades to local-only — never an error.

The `reconcile_playtime` result also carries the restored `last_played` (ISO-8601; one without an offset is RomM's, and
UTC: `resolveLastPlayed`). The game-detail Play section (`RomMPlaySection`) renders it as the **LAST PLAYED** value in
preference to Steam's device-local `rt_last_time_played`: Steam synthesizes the latter to "now" after a device cutover /
fresh device, so the restored cross-device timestamp is the truthful one. When `last_played` is `null` (the server has
no session for the ROM yet, or the reconcile ran local-only) the display falls back to Steam's value, so there is no
regression before any server data exists. This is display-only — Tender does not write the restored value back into
Steam's `rt_last_time_played` (#1294).

A session's end on an open page is the one time Steam's value wins over a restored one. Every playtime write goes
through `updatePlaytimeDisplay`, and the `romm_playtime_changed` signal it fires says whether the write also set
`rt_last_time_played` to now (`lastPlayedWritten`), which only the session's end does. On such a signal the page drops
the restored value it holds and shows Steam's, so LAST PLAYED names the session just played; on any other signal — the
reconcile's own write, a bulk pass at load or after a sync — the restored value still wins. A session's end sets Steam's
time and sends the signal even when the game's whole recorded total is under a minute, and then leaves the playtime as
Steam shows it; any other write under a minute writes nothing and sends no signal.

## Save-Sync State — the `RomSaveSyncState` aggregate

Per-ROM save-sync state lives in SQLite — there is no JSON file. The per-ROM scalars are the `RomSaveSyncState`
aggregate (`domain/rom_save_sync_state.py`), backed by the `rom_save_sync_states` table; the per-file baselines are
`FileSyncState` value objects (one per filename), backed by the `rom_save_files` table. Both are reached through the
Unit of Work as `uow.rom_save_sync_states`, which spans the two tables (sync sqlite3 run via `run_in_executor`, per
[ADR-0004](../adr/0004-sync-sqlite-unit-of-work.md)).

The canonical source for the table DDL, columns, and aggregate invariants is [database-design.md](database-design.md).
This page describes the state conceptually; the field reference below maps each logical field to its column.

The save-sync **feature toggles** (`save_sync_enabled`, `sync_before_launch`, `sync_after_exit`, `default_slot`,
`autocleanup_limit`) and the **device label** (`device_name`) live in `settings.json`, not in this aggregate — they are
user-intent config, not synced relational state (ADR-0003). Device identity is `kv_config['device_id']` (see the
[Device Registration](#device-registration) section above), not a field on the per-ROM aggregate.

The logical shape of a single ROM's save state — the scalars as a `rom_save_sync_states` row plus its child
`rom_save_files` rows — looks like this:

```json
{
  "42": {
    "system": "gba",
    "active_slot": "default",
    "slot_confirmed": true,
    "last_synced_core": "mgba_libretro",
    "own_upload_ids": [18],
    "last_sync_check_at": "2026-02-17T10:31:00+00:00",
    "files": {
      "game.srm": {
        "tracked_save_id": 18,
        "last_sync_hash": "d41d8cd98f00b204e9800998ecf8427e",
        "last_sync_at": "2026-02-17T10:30:00+00:00",
        "last_sync_server_updated_at": "2026-02-17T10:30:00+00:00",
        "last_sync_server_save_id": 18,
        "last_sync_server_size": 32768,
        "last_sync_local_mtime": 1739789395.0,
        "last_sync_local_size": 32768,
        "last_sync_content_size": 32768
      }
    }
  }
}
```

Per-ROM playtime is a separate aggregate (`Playtime`, `rom_playtime` table) — see
[Playtime Tracking](#playtime-tracking) above.

### Field reference

The `saves.<id>.*` fields are columns on the `rom_save_sync_states` table (one row per ROM); the
`saves.<id>.files.<fn>.*` fields are columns on the `rom_save_files` table (one row per `(rom_id, filename)`). The
`saves.<id>` / `files.<fn>` notation here mirrors the logical shape above — see [database-design.md](database-design.md)
for the physical column names and constraints.

| Field                                               | Type                    | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| --------------------------------------------------- | ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `saves`                                             | object                  | Per-ROM sync metadata, keyed by `rom_id` (string)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `saves.<id>.system`                                 | string                  | RetroDECK system slug (e.g. `"gba"`, `"snes"`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `saves.<id>.emulator`                               | string                  | Emulator tag (default `"retroarch"`); forms the RomM save-folder path `saves/{system}/{rom_id}/{emulator}/`.                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `saves.<id>.active_slot`                            | string                  | Which RomM slot this game syncs to (e.g. `"default"`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `saves.<id>.slot_confirmed`                         | boolean                 | Whether user has explicitly chosen their slot (see "Slot Setup Wizard")                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `saves.<id>.last_synced_core`                       | string / null           | RetroArch core used at last sync (for core change detection, e.g. `"mgba_libretro"`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `saves.<id>.own_upload_ids`                         | array of integer        | Save ids this device originally POSTed. Drives the `uploaded_by_us` indicator on the SAVES tab.                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `saves.<id>.slots`                                  | object                  | Merged slot listing (read-model cache): per slot, its `source` / `count` / latest `updated_at`. A `get_save_slots` that could not reach the server hands this back in its own `last_known` field (`slots` / `active_slot`), but only once `slot_confirmed` is set — the QAM renders it as the state at the last contact. It carries no timestamp: nothing records when the listing was last refreshed, and `last_sync_check_at` moves independently of it (#1755).                                                                                       |
| `saves.<id>.last_sync_check_at`                     | ISO-8601 string / null  | Timestamp of the most recent `do_sync_rom_saves` run for this rom (regardless of whether files transferred).                                                                                                                                                                                                                                                                                                                                                                                                                                             |
| `saves.<id>.files`                                  | object                  | Per-file sync state, keyed by filename (e.g. `"game.srm"`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| `saves.<id>.files.<fn>.tracked_save_id`             | integer / null          | Most recent RomM save id this device tracked. Used to exclude the active save from the Previous Versions dropdown and as an uploader-attribution hint; **not** consulted by `compute_sync_action` (the algorithm picks newest by `updated_at`).                                                                                                                                                                                                                                                                                                          |
| `saves.<id>.files.<fn>.last_sync_hash`              | content-hash hex string | RomM-parity `content_hash` of the save file at last sync (zip-aware: MD5 for a single file, per-entry combined for a zip — `SaveFileStore.content_hash`, #1457). Drift baseline used by matrix rows 6a–6e/7/8a/8b/9/9b/10a/10b/11a/11b/12a/12b.                                                                                                                                                                                                                                                                                                          |
| `saves.<id>.files.<fn>.last_sync_at`                | ISO-8601 string         | Timestamp of last successful sync.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `saves.<id>.files.<fn>.last_sync_server_updated_at` | ISO-8601 string         | Server's `updated_at` at last sync.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `saves.<id>.files.<fn>.last_sync_server_save_id`    | integer                 | RomM save id for the most recently synced server save.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `saves.<id>.files.<fn>.last_sync_server_size`       | integer                 | Server file size at last sync.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `saves.<id>.files.<fn>.last_sync_local_mtime`       | float                   | Local file mtime (epoch seconds) at last sync.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| `saves.<id>.files.<fn>.last_sync_local_size`        | integer                 | Local file size (bytes) at last sync.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `saves.<id>.files.<fn>.last_sync_content_size`      | integer / null          | Local file [content size](#the-content-size) at last sync. Null for a baseline recorded before Tender took it, until a sync records it from the file still holding that baseline, and for one recorded from the server's size alone (the copy flow).                                                                                                                                                                                                                                                                                                     |
| `playtime`                                          | object                  | Per-ROM playtime lives in the `rom_playtime` table (the `Playtime` aggregate, with its `rom_playtime_sessions` outbox child), a separate aggregate from saves — `RomRemovalService` keeps playtime on uninstall per [ADR-0007](../adr/0007-rom-retention-identity-anchor.md). The scalar columns below (`total_seconds`, `session_count`, `last_session_start`, `last_session_start_monotonic`, `last_session_duration_sec`) are keyed by `rom_id`; the outbox rows are keyed `(rom_id, start_time)`. See [Playtime Tracking](#playtime-tracking) above. |
| `playtime.<id>.total_seconds`                       | integer                 | Accumulated playtime in seconds.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| `playtime.<id>.session_count`                       | integer                 | Number of completed play sessions.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `playtime.<id>.last_session_start`                  | ISO-8601 / null         | Start time of current session (null when not playing).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| `playtime.<id>.last_session_start_monotonic`        | float / null            | `Clock.monotonic()` reading captured when the session opened; its delta to session end is the awake-only span (suspend excluded, #1148). Null when idle or on a pre-migration row (→ wall-span fallback).                                                                                                                                                                                                                                                                                                                                                |
| `playtime.<id>.last_session_duration_sec`           | integer / null          | Duration of last completed session.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |

The save-sync feature toggles (`save_sync_enabled`, `sync_before_launch`, `sync_after_exit`, `default_slot`,
`autocleanup_limit`) and the device label (`device_name`) live in `settings.json` (ADR-0003), not in this aggregate.
`save_sync_enabled` is the master feature toggle. When it is off, `pre_launch_sync` returns before it syncs anything, so
the launch gate has no conflict to map. `get_save_status` returns an empty `conflicts` array as well, so no reader of
that array — the Play button, the game page (`panelEvents.ts`, `gameDetailStore.ts`) and the `save_status_updated` push
that `index.tsx` forwards — shows a conflict the user cannot resolve: the SAVES tab where a conflict is resolved is
hidden while sync is disabled. A stale server-side conflict (e.g. another device moved the save) therefore can't render
a game unplayable. `sync_before_launch` / `sync_after_exit` gate the automatic pre-launch / post-exit syncs;
`default_slot` is the slot new games adopt (`"autosave"`, matching the official RomM clients Argosy and Grout);
`autocleanup_limit` caps retained save versions per slot on the server (10).

Conflicts are no longer persisted. They are returned ephemerally from `do_sync_rom_saves` and `_get_save_status_io` and
surfaced via the modal at the moment of the sync. If the user dismisses the modal (Cancel), the conflict re-fires on the
next sync as long as the underlying state still produces one: a conflict row (12b, 6c, 6a, 11b, or 9b), or an upload the
409 backstop resolves to a conflict.

### Legacy field migration

The per-file schema migrations that the old JSON aggregate ran at load time are moot: SQLite starts empty and no JSON
state is imported into it (Tender was in beta at the cutover — the library re-syncs from RomM). There is no on-disk
aggregate to rebuild, so the old `active_core` → `last_synced_core` rename and the `dismissed_newer_save_id` strip no
longer happen.

Nothing reads or writes `save_sync_state.json` any more. The save-sync feature toggles and `device_name` it once held
live in `settings.json`, and a settings file from before they moved there is older than any version this release reads
(see [Backend Architecture — PersistenceAdapter notes](backend-architecture.md#persistenceadapter-notes)).

## Session Detection

Game start and stop events are detected using Steam's frontend APIs, not by polling emulator processes.

### RegisterForAppLifetimeNotifications

The primary mechanism. `SteamClient.GameSessions.RegisterForAppLifetimeNotifications` fires a callback whenever any app
(including non-Steam shortcuts) starts or stops.

The callback receives:

- `bRunning: boolean` — whether the app just started (`true`) or stopped (`false`)
- `unAppID: number` — the app ID

### Running-app detection (`utils/runningApps`)

The reader has one surface, `SteamUIStore.RunningApps` — the only running-app page global Steam actually exposes, and
there is no second one to add: `Router` is not a page global on any SteamUI build, and `@decky/ui`'s `Router` export
resolves to the same `SteamUIStore` singleton (#1588). It is read through a guard (an absent, `null`, or throwing-getter
store degrades to "reported nothing", never a throw) and returns the listed apps that count as running — by the display
status rule under [Is the game running](#is-the-game-running) — with a `diagnostics` string distinguishing absent /
empty / threw / every listed entry with its display status (`[<appid>:<status>,…]`, `?` for a status it cannot read).

**It answers membership, never identity.** The list is the store's private running-appid array mapped through the app
store with unloaded overviews dropped, so its head is not even reliably Steam's own `MainRunningApp` — the two diverge
during exactly the post-launch window Tender cares about. The order it carries is not a launch order: `SetRunningApp`
removes and unshifts — Steam's own Play calls it for the game it starts, where another is listed (below) — while the
reconciler that notices a newly-launched process appends it at the tail. So the head may or may not be the app that just
started, and nothing reads it to identify one: the session manager takes the starting app's id from the lifetime
notification's own `unAppID` (#1624 — it previously waited 500ms and read the head, which both mis-attributed a start
while another game was running and stalled the serialized lifecycle chain).

The store is also not reliable across timing — why, and what was measured, is under
[Surviving a JS-context rebuild mid-session](#surviving-a-js-context-rebuild-mid-session) — so the adoption path
**polls** the reader instead of reading once, and a failed round logs what the store reported. The same reader is the
fallback signal behind the already-running skip on both launch surfaces — the launch watcher and the Play button
([ADR-0015](../adr/0015-single-launch-gate-cancel-then-relaunch.md)) — and an observed stop overrules it there, because
it can also keep an app listed that has already exited: [Is the game running](#is-the-game-running).

### Is the game running

Six places in the panel ask whether a game is running; the backend's save paths and the removed-game cleanup ask its own
reading, below. Five ask one predicate in the session manager, `readGameRunning(appId, romId)`: the launch watcher's
already-running guard, and the Play button's launch guard, its Resume overlay at mount, Resume and Stop. The sixth asks
differently: the stranded panel's card, in Quick Access and on a game page (`StrandedPanelCard.tsx`), takes every app
the store lists as running or starting whose status was read, leaves out one whose stop was observed since its last
start (`isStopObserved`, the session manager's stop record), with no ROM and so without rule 1, and lets its own last
lifetime notification per app answer first, to tell whether ANY game runs. At the two launch guards a wrong "running"
skips the whole launch gate — the migration block, the starting source, tracking setup, the core-change confirmation,
the offline drift check, the pre-launch sync and conflict resolution.

1. **An active session answers "running"**, whatever the store says: the store has been measured reporting nothing with
   the game still up ([Surviving a JS-context rebuild mid-session](#surviving-a-js-context-rebuild-mid-session)).
2. **Otherwise `SteamUIStore.RunningApps` answers — an entry counts only while its display status reads Running — unless
   a lifetime stop for the app has been observed since its last observed start.** Why being listed is not enough is
   below. An observed stop overrules the store, because the store can keep an exited app listed for a while. Measured in
   windowed Big Picture: after a game started through Tender's Play button exited, the store still listed it about 6 and
   12 s later but no longer about 18 s later; after a game started through a `steam://rungameid` link it was empty about
   3 s after the exit. Before the stop rule, a press of the Play button 12 s after an exit skipped the gate, and Steam
   itself started the game normally. In the desktop client the store was empty about 5 s after an exit. Which of the
   store's inputs lags has not been established, and Game Mode has not been measured; the rule does not depend on how
   long the lag is. A second start of a game that really is running is refused by Steam itself ("already running").
3. **The store stays the fallback for a start that has no session behind it** — one that happened before this JS context
   existed (until reload adoption has finished), one whose session is not open yet (between a button's render and its
   press), and one for an app the session manager cannot map to a rom.

The stop record is per app and in memory only. A stop is recorded the moment Steam reports it, even while the lifecycle
chain is busy with another game's post-exit sync, and every lifetime start clears it — including a start for an app the
session manager then cannot map to a rom. A session that is still open because its own stop is queued on that chain
still answers "running" under rule 1 — only when two games run at once, because when games run one after the other the
second game's start waits in the same chain, so its session is not open yet. After a JS-context rebuild the record is
empty, so the store answers unopposed until the next stop is observed.

**An entry of `RunningApps` counts only by its overview's display status: Running alone for `readGameRunning`, Launching
or Running for reload adoption and the stranded panel's card.** The status is `local_per_client_data.display_status` on
the entry itself, which is the app store's overview object (the list maps its appids through
`appStore.GetAppOverviewByAppID`), and its values are Steam's `EDisplayStatus`: Launching 1, Running 4, ReadyToLaunch
11, Terminating 36. An entry whose status cannot be read — no `local_per_client_data`, a status that is not a number, a
getter that throws — counts as running, so a Steam build that moves the field falls back to counting every listed entry
rather than to "nothing runs" under every reader at once. Being listed is not enough, because of two things Steam's own
interface code does (read in the Steam client's webpack modules):

- **It lists a game it is only starting.** Steam's own Play calls `SteamUIStore.SetRunningApp(appId)` right after
  `RunGame`, and where another app is already listed that puts the new one at the head of the list — before
  `RegisterForGameActionStart` reports the start. With nothing else listed it adds nothing. Read without the status, the
  launch watcher took such a start, from Steam's desktop client with another game running, for a press on a game already
  running, and let it through without the pre-launch sync.
- **It keeps a game listed after it exited while it still knows a focusable window for it.** Its reconciler
  (`ScopeRunningApps`) keeps an app that reads neither Running nor Launching when, on SteamOS,
  `WindowStore.BHasAppWindow(appId)` is true. Only the composition store that recorded a window removes it, and a window
  Big Picture's store recorded was still there after Big Picture's window had gone — so a game started in Big Picture
  and quit with the desktop client in front stayed listed, reading ReadyToLaunch, until Steam was restarted.

Measured on the Deck on 2026-10-10, in Desktop Mode with windowed Big Picture and the desktop client both in use: a
start from Play in the desktop client while another game ran read ReadyToLaunch (11), at the head of the list, when
`RegisterForGameActionStart` reported it, with the running game reading Running (4), and the watcher gated it. In each
of two starts, the started game read Launching (1) when the lifetime notification reported its start, while the game
already running read Running (4); the first read Running at most about 22 s after its start was reported. A start the
watcher cancelled left no entry in the list. A game started in Big Picture and quit with the desktop client in front no
longer held the stranded panel's reload, which came about 10 s after both games had exited.

So a game Steam has reported started can still read Launching, and reload adoption and the stranded panel's card count
an entry that reads Launching as well as Running (`readRunningOrStartingApps`) — including one Steam has not yet
reported started, a state that was not measured. The launch guards count Running alone: the start they decide on read
ReadyToLaunch, not Launching, but only Play in the desktop client was measured, and a start that read Launching at that
moment would be let through without the pre-launch sync. The guard's log line names every entry's status, and so does
the line the session manager logs the moment Steam reports a start (`App start reported: …`, at `info`); those are where
a device shows it.

`readGameRunning` applies it with Running alone; the stranded panel's card and reload adoption count Launching as well.
The session-budget banner's **Restart Steam now** asks the list another question, with no stop rule, twice — the banner
to disable the button (`SessionBudgetBanner.tsx`) and `restartSteam` at the press (`utils/steamRestart.ts`): does any
listed game read Launching, Running or Terminating, or a status that cannot be read (`isAnyAppHolding`)? A restart
closes Steam and any game with it, and a game on its way in or out is one it would close; an entry left behind after its
exit reads ReadyToLaunch and does not refuse the restart that would clear it. The backend reads the list the same way —
Launching and Terminating hold as well as Running, written out under
[the stranded panel's reload](loading-the-panel.md#a-panel-an-earlier-backend-left-behind) — and two waits share that
reading, the reload and an update's install
([UpdateInstallService notes](backend-architecture.md#updateinstallservice-notes)), as do the save paths and the
removed-game cleanup, below. Each side names the values once (`DISPLAY_STATUSES_THAT_HOLD`, in `utils/runningApps.ts`
and `backend/host/inject/recovery.py`), and `tests/host/inject/test_recovery.py` holds the two equal.

**The save sync reads the backend's list too, by app id.** Each entry of `RUNNING_APPS_EXPRESSION` names its `appid`
beside its name, `null` where the entry carries no number for it. `RunningGames.running_rom_ids`
(`backend/services/running_games.py`) takes one reading on every call — never one kept from an earlier call — and
answers the ROMs whose shortcut has a listed app id (`roms.shortcut_app_id`, the id the panel matches a session by). So
a game counts as running for the save sync as it holds the backend's waits: Launching, Running or Terminating, or a
status that cannot be read; an entry left behind after its exit, reading ReadyToLaunch, does not. A game on its way in
or out is one whose save the emulator may be writing. Where no reading can be taken — no debugger attached, the panel
not loaded, a reader that raised, or one that did not answer within `READING_TIMEOUT_SECONDS` (2 s) — nothing counts as
running, so the save sync works as before rather than refusing every game; a failed read of the ROMs answers none too,
and an entry without an app id matches no ROM. The limit is shorter than the host's 10 s per debugger command
(`COMMAND_TIMEOUT_SECONDS`, `host/inject/injector.py`) because a renderer that is attached but does not answer would
otherwise hold every save path that asks — under the game's `rom_lock`, and the sweep once per ROM while it holds the
device gate — for that long. What the save sync does with a running game is
[A game that is running](#a-game-that-is-running).

An app that counts only because its status could not be read holds both waits, and only the user can tell whether it has
closed, so the panel says what to do about it in one sentence (`utils/runningAppsWording.ts`): quit it if it is open,
restart Steam if it has already closed. The update page shows it under **Waiting for:** whenever `app_running` carries
`apps_status_unread`, beside "A game to close (…)" for the apps whose status was read. The stranded panel's card shows
it in place of its quit line whenever the store lists such an app and no game is seen to run — even where Tender has
seen that app stop, because the backend's reload has no stop rule and waits on it all the same; a game seen to run keeps
the quit line.

Each launch guard logs one line per decision, whether it skips the gate or runs it. The launch watcher logs at `info`
and the Play button at `debug`, so neither line is written at the default `warn` level. The line names the signal that
decided (`session`, `store`, `stop` or `none`) beside the session state, whether a stop was observed, and the store's
`diagnostics` — every listed entry with its display status. The launch watcher's line for a start it gates also names
Steam's `launchSource` for it, which nothing acts on.

### State-aware Resume button (#1313)

When the game is already running, the RomM Play button (`CustomPlayButton`) renders **Resume** — top precedence over its
install / conflict / download states — and a press brings the running game to the foreground via **Steam's own gamescope
"Resume Game" path**: `SteamUIStore.SetRunningApp(appId)` followed by `SteamUIStore.NavigateToRunningApp()`. This is
pure UI focus navigation, not a launch: it fires no `GameActionStart` (so the launch interceptor never re-enters) and
shows no Steam "already running" dialog, which dissolves the mid-session-sync problem at the UX level rather than
defending against it. (`SteamClient.Apps.RaiseWindowForGame` — the earlier approach — is a **desktop-overlay** call:
native only acts on it when `SteamUIStore.GetOverlayInstances(appId)` is non-empty, which it never is in gamescope Game
Mode, so it reports `Success` but silently does nothing. Hence the switch to the store-navigation path.) A click first
runs a **liveness gate**: if nothing is actually running (`readGameRunning(appId, romId)` says no — a stale overlay from
a session that ended without a stop event reaching the button), it clears the overlay and falls through to the normal
launch funnel (self-heal). If `NavigateToRunningApp` is absent on an older SteamUI build, the foreground falls back to
`Navigation.Navigate("/apprunning")` after the `SetRunningApp` selection (same visible effect).

Detection is **reactive, not polled**: the overlay is seeded synchronously at mount from `readGameRunning(appId, romId)`
(so a page opened mid-session — or after a reload-adoption — shows Resume immediately) and flipped live by the
`romm_session_changed` DOM event, which `sessionManager` dispatches on game start (`running: true`), game stop
(`running: false`), and both reload-adoption branches (`running: true`). The already-running guards from #1148 / \#1308
(the interceptor and the `handlePlay` guard) stay in place as **backstops** for the render→click race, where the session
begins between the button rendering Play and the user pressing it; they ask the same predicate.

#### Stop Game

Beside Resume sits a chevron whose menu holds one destructive action: **Stop Game**. It confirms first (one line: any
progress since the last in-game save may be lost — Tender promises nothing about the save, because it cannot), then
calls the `stop_running_game(rom_id)` backend endpoint.

**Why Tender ends the game itself.** In Gaming Mode, Exit Game in Steam's overlay ends these games. In Desktop Mode the
emulator runs inside Steam's process tree as well — `reaper`, then `tender-rom-launcher`, then `bwrap`, then the
emulator — but Steam's overlay does not open over a Flatpak game there, so Stop Game is the way out; and RetroDECK can
run more than one session at a time, of which only the one running this ROM may be ended. The kill happens backend-side,
against the host process table.

The backend finds those processes through the **flatpak instance registry**: `/run/user/<uid>/.flatpak/<instance>/info`
names the app (`name=<app id>`), the sibling `bwrapinfo.json` carries the instance's inner-`bwrap` host pid in
`child-pid`, and `/proc/<pid>/task/<pid>/children` walks down to the emulator. The walk returns pids **deepest-first**
so the emulator is reached before the shell wrappers whose exit would tear it down mid-write; `bwrap` processes are
descended through but never signalled (killing the sandbox scaffolding collapses it under the emulator instead of
letting it flush). Every read is fail-soft — a pid that vanishes mid-scan is a normal race and is skipped, never fatal.
Nothing shells out: direct `/proc` reads and `os.kill` only, all as Tender's own uid, so no `flatpak kill` subprocess
and no elevation.

**One app id, several live instances — and only one of them is the game.** RetroDECK is a single flatpak app, so a
second game launched from another shortcut, or ES-DE opened on its own, is another live instance of the _same_ app id.
The registry scan therefore reports the instances **separately** (`GameInstance`: the tree's signal-target pids plus the
`/proc/<pid>/cmdline` argv tokens read across them) rather than pooling every pid, and the service signals exactly one
of them. Before #1619 the endpoint took no arguments and every instance was signalled, so stopping one game ended every
RetroDECK session on the device; the `rom_id` argument is what makes the target knowable at all.

The **matching rule**: the expected launch target comes from `RelaunchOptionsResolver.launch_path_for_rom` — the same
derivation the shortcut's `launch_options` are baked from, so the comparison runs against that derivation rather than a
second opinion of it. (It re-resolves from the current `rom_installs` row and disc pick, so a disc switch or reinstall
since the launch yields a path that matches nothing; the failure direction is a refusal, never a wrong instance.) An
instance matches when that exact absolute path is one of its argv tokens; failing that, when one of its tokens has the
same **path tail** — `<parent>/<file>`, e.g. `snes/Aladdin.zip`.

The tail, not the bare filename, because only the mount ROOT can differ between the host path and the sandboxed command
line, and it is not yet verified on device whether they differ at all. Comparing the tail survives a re-rooting while
still telling `roms/snes/Aladdin.zip` from `roms/genesis/Aladdin.zip` — one game, two platform directories, one
filename, ordinary in a multi-platform library and fatal to match on, because in the sandbox regime **every** match is a
fallback match. Comparison is whole-component equality, never a substring test, so `a.bin` cannot match `aaa.bin`. Two
refusals guard the fallback since it is the weaker signal: the exact pass always wins outright, and a tail that matches
**more than one** instance refuses rather than picking by scan order. Which discriminator matched (`path` vs
`path-tail`) is logged at INFO precisely so an on-device run settles the open question; a log that only ever says
`path-tail` means the sandbox path differs.

The pure decision lives in `domain/game_instance.py` (`match_instance_for_launch_path`), so the "which tree" question is
testable without a process table. **No attributable instance means signal nothing** — whether nothing matched or too
much did, killing the wrong emulator mid-save is worse than refusing — reported as the distinct `game_not_running`
failure below.

**The ladder never re-sends the polite signal.** It is strictly _one SIGTERM per process → a bounded grace window
(polled, 6 s) → SIGKILL for whatever is left_. A "retry SIGTERM a few times" loop looks like robustness and is in fact
save destruction: RetroArch's handler runs `if (unix_sighandler_quit == 2) exit(1);` and registers no `atexit`, so the
second signal skips the save flush the first one started; DuckStation and PCSX2 route the repeat through `quick_exit`,
and Dolphin installs its handler with `SA_RESETHAND` so the second signal takes the default terminate action. In every
case the repeat destroys the file being written. SIGKILL is the only permitted escalation, and only once the window is
fully spent. The rule is documented at the ladder in `services/game_process.py` so it survives future edits.

**"Exactly once" spans calls, not just the loop.** The grace window yields the event loop for seconds while the emulator
flushes, and the UI shows nothing changing in the meantime — the precise conditions under which a user presses Stop
again. A second concurrent call would rediscover the same still-alive pids and send them the same fatal repeat, so
`GameProcessService` holds a **single-flight claim** taken before its first `await` and released in a `finally`; the
loser is refused with `{success: false, reason: "already_stopping", message}`. It is a plain flag rather than an
`asyncio.Lock` deliberately: a lock _queues_ the second caller and fires the repeat a few seconds late instead of never,
whereas the needed semantic is refuse. (This mirrors the compare-and-swap shape of `LibrarySyncStateBox.try_begin_run`.)
The claim stays **global** rather than per-ROM even though the ladder now targets one instance: two per-ROM ladders
whose matches landed on the same tree — not excluded, since the path-tail fallback is a weaker signal than an exact hit
— would fire exactly the repeat the claim exists to prevent. Global costs at most the grace window of a stop the user is
already watching, and the poll returns as soon as the tree is gone, so the usual cost is one 0.25 s interval rather than
the full 6 s; that is the cheaper side of the trade. The frontend adds its own in-flight flag on top — the Stop Game
menu item is disabled and reads "Stopping…" while the call is outstanding ([qam-panel.md](qam-panel.md#waiting) has what
the throbber does meanwhile) — but that is a convenience, not the guarantee: a remount, a second game-detail page, or
the retry the error toast invites all bypass component state, so the backend claim is the load-bearing half.

Layering follows the usual split: `GameProcessControl` (`services/protocols/infra.py`) is the semantic seam — POSIX
signal numbers and `/proc` never reach `services/` — `adapters/game_process.py` implements it, `RomLaunchPathReader`
(`services/protocols/cross_service.py`) is the launch-target seam the match reads through, and `GameProcessService` owns
the policy with an injected `Sleeper` for the grace window.

The endpoint answers `{success: true, stopped, force_killed}`, or a canonical failure: `not_running` when nothing of
RetroDECK's is alive, `game_not_running` when it is but no instance could be attributed to this ROM (nothing was
signalled), `already_stopping` when a ladder is in flight. The frontend treats `not_running` exactly like its own
stale-overlay self-heal: clear the overlay (and reset a `launching` state stuck underneath it) rather than surface an
error. `game_not_running` is deliberately **not** treated that way — the game may well still be running, only
unidentified — so the overlay stays up, Resume stays reachable, and the backend's message is toasted.

### App ID to ROM ID mapping

The session manager maintains a cached `appId -> romId` map loaded from the backend's synced-ROM registry (the `roms`
SQLite table, via `get_app_id_rom_id_map`). This map is refreshed:

- On session manager initialization (panel load)
- Before each game start event (in case a sync added new shortcuts)
- When a library sync run ends — on every `sync_progress` frame that stops the run with a terminal stage, so a run that
  completed, was cancelled or paused, or stopped with an error alike — and when a version switch has committed, since
  each can move a shortcut's binding onto another `romId` in the running session — without it, Steam's own Play on a
  shortcut whose binding moved would be gated, and its launch command re-confirmed, for the version it is no longer
  bound to. A unit acknowledged only after its run ended is left to the refresh before the next start

The refresh before a start waits at most 5 s (`LOCAL_CALL_LIMIT_MS`), because the start and stop notifications are
handled one after another and a refresh without an answer would hold them all (see [Post-exit sync](#post-exit-sync)).
On expiry the map already held stays and the expiry is logged, as after a failed read.

The map can lack a game Tender owns: one synced after the map was last read, or every game while no read has succeeded
since panel load. So Tender's Play button names the ROM right before it starts a game (`noteAppRom`). A start's ROM then
comes from the map when this start's refresh of it answered — the backend's current word, so no name overrides it —
otherwise from the button's name, otherwise from the map already held. The name outranks a held map because a held map
can be older than it, for example after a version switch moved the shortcut to another ROM. A name is used once, and
only by a start Steam reports within 60 s of it being set, so a start that never happened cannot name a later one. That
window only has to outlast the time between the button's `RunGame` and Steam's start notification, which measured about
1.2 s in windowed Big Picture on a Steam Deck. A launched app this ranking gives no ROM is not treated as a RomM
shortcut, and the session manager ignores it.

### Suspend exclusion via the monotonic clock (#1148)

Sleep time is excluded from playtime **backend-side**, using the monotonic clock — not the Steam suspend/resume events.
`CLOCK_MONOTONIC` pauses while the device is suspended (hardware-verified on the Steam Deck), so the monotonic delta
across a session counts only awake time. `begin_session` stores a `Clock.monotonic()` start on the `rom_playtime` row;
at session end `record_session` counts `monotonic_end` minus that start, clamped to the wall-clock span (awake can never
exceed the real elapsed span) and then to 0–24 h.

When the monotonic delta is unusable the counted duration falls back to the full wall span — exactly the pre-#1148
behavior, so a missing/invalid reading is never a regression, only the loss of the suspend exclusion. The fallback
covers three cases: no stored monotonic start (a pre-migration row, or a session opened before the column existed), a
negative delta (the monotonic counter reset across a reboot mid-session), and a delta more than a ~2 s tolerance above
the wall span (the two readings cannot belong to the same session).

There is **no frontend suspend machinery**. The earlier design registered Steam suspend/resume hooks and accumulated a
`suspended_seconds` value passed to `finalizeGameSession`, but those hooks (`System.RegisterForOnSuspendRequest` /
`RegisterForOnResumeFromSuspend` and their renamed `User.*` progress successors) never fired on current SteamOS, so the
accumulator stayed at zero and the whole mechanism was removed. `finalizeGameSession(romId)` now carries no suspend
argument.

## Native play-session ingest (ADR-0018)

Playtime uses RomM's first-party play-session store (since 4.9.0), not a storage hack. Two round-trips, both best-effort
and off the hot path:

- **Ingest (`POST /api/play-sessions`).** On game-exit the closed session `(rom_id, start_time, end_time, duration_ms)`
  is enqueued into the `rom_playtime_sessions` outbox and POSTed under this device's `device_id` (batch, max 100). The
  server accumulates the additive union across devices and dedupes on `(user_id, device_id, rom_id, start_time)`, so a
  re-POST is idempotent. A `created` or `duplicate` result dequeues the outbox row; only an `error` keeps it queued.
  Offline, the session stays queued and flushes on the next launch/session-end/reconcile. A **404** on the POST is
  handled as a routing fault, not a verdict on the sessions. Verified against a live server: a POST naming an unknown
  `device_id` **and** an unknown `rom_id` still returns `201` with `status: "created"` — RomM accepts the row and
  resolves both unknown ids to `NULL` rather than rejecting. So this endpoint does not 404 over the _contents_ of a
  batch; a 404 means the request never reached it (a misrouted tunnel, a server not exposing the route), which is
  recoverable server-side. The batch is therefore retained untouched and logged with the 404 named as the cause;
  advancing the retry counter would quarantine the whole outbox over an infrastructure fault, discarding playtime that
  exists nowhere else.
- **Reconcile (`GET /api/play-sessions?rom_id={id}`).** Opening a game's detail page runs `reconcile_playtime(rom_id)`:
  flush the outbox, sum the returned sessions' `duration_ms`, and fold `Σ / 1000` into the local total via
  `reconcile_total` (monotonic `max()`, never regresses). The GET needs the `roms.user.read` scope (minted in #1280);
  without it the reconcile degrades to local-only, never an error.

The local `Playtime` total stays the always-correct cumulative read-model (folded regardless of network); the server
holds the interoperable per-session record. `duration_ms` is screen-on time — our monotonic-derived awake seconds × 1000
(suspend excluded, #1148) — mapping 1:1 onto RomM's model. There is no `content_hash`, no newest-wins, no conflict
modal, no `.romm-backup`: playtime is additive, so none of the save-sync machinery applies.

### Migration off the note

Before ADR-0018 Tender stored playtime in a RomM user note (`romm-sync:playtime` = `{"seconds", "updated", "device"}`).
That note is **retired**: Tender no longer reads or writes it, and migration `006` drops the now-readerless
`rom_playtime.note_id` column. Native accumulation starts fresh at the cutover (option B1 — no backfill of the
historical total); the local total keeps showing the true historical value via `max()` until server-side accumulation
overtakes it. Existing `romm-sync:playtime` notes are left in place on the server — orphaned but harmless (no reader
remains) — rather than mass-deleted; a release note tells users they may delete them, and an optional Settings cleanup
action can be added later. A backfill of the historical total is deferred to #868.

## Known Limitations

### Shared memory cards are refused, not deferred

An emulator that writes a card many games share cannot be synced per game — a download would carry another game's
progress onto this one's record and back out to every device. The machine now decides this per ROM: the save answer
reports the `shared` state and the sync refuses with the benign-skip shape, so the user is told rather than shown "no
saves". Offering to switch a core off its shared card is separate work, tracked on the
[GitHub Projects board](https://github.com/users/danielcopper/projects/2).

### No aggregate playtime field in RomM (yet)

RomM (read at 5.3.0) stores raw play-session rows and renders `last_played`, but exposes **no aggregate playtime
number** and has no frontend playtime surface — it sums sessions internally only to rank recommendations. Tender's local
`Playtime` total is therefore still the display read-model; the native store is forward-compatible with a future RomM
playtime UI (#903). Under the no-backfill cutover (option B1) the server under-reports the true historical total until
it re-accumulates — `max()` protects the local display; a synthetic-session backfill is deferred to #868. See
[Native play-session ingest (ADR-0018)](#native-play-session-ingest-adr-0018) above.

### Emulator save states not synced

Save states are not synced — only the save files the save answer names are. Save states are large,
emulator-version-specific, and not portable between different core versions.

### Copying a save into another slot

Copying a single save from one slot into another is supported via the per-save **"Copy to slot…"** action (see
[Copy a save to another slot](#copy-a-save-to-another-slot)) — the target slot becomes the ROM's active slot and the
source save is preserved. What is _not_ supported is a bulk **move** of a whole slot's contents (delete-from-source
semantics): users copy individual saves, then delete the source slot from the server if they want it gone. The legacy
(slot-less) bucket is a read-only copy _source_ — it cannot be a copy target, and cannot be deleted from Tender (#1478).

### Cross-device save browsing limited

While `device_syncs` per save shows which devices have synced, Tender cannot filter or browse saves by a specific other
device. This is an API limitation — `GET /api/saves` takes no parameter that selects saves by device: its `device_id`
names the caller, whose entry it puts first ([`GET /api/saves` writes no sync row](#get-apisaves-writes-no-sync-row)).
