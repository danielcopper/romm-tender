# 1.0 raises the RomM floor to 5.3.0

## Status

Accepted. **Supersedes the RomM floor that [ADR-0016](0016-save-sync-hands-detection-to-romm-negotiate.md) set** and
[ADR-0017](0017-client-baseline-detection-authoritative-negotiate-is-transport.md) carried forward — 4.9.0 there, 5.3.0
here. Nothing else either record decides is touched.

## Context

The program refuses a server below a fixed minimum: `test_connection()` compares the version the heartbeat reports
against `_MIN_REQUIRED_VERSION` and answers `version_error` below it, and the plugin is inert until the server is
updated. ADR-0016 set that floor to 4.9.0, the release that shipped RomM's Device Sync.

No code path in the program needs anything newer than 4.9.0 today. Saves are still queried with `rom_id`, and RomM 5.3.0
kept that parameter on purpose: the change that made `rom_ids` the only ROM scope inside its save and state handlers
left the public `rom_id` on both routes, because dropping it "would break every existing client"
([rommapp/romm#4305](https://github.com/rommapp/romm/pull/4305)).

What changes the picture is the work planned after 1.0, which builds on what RomM 5.3.0 added. Neither addition is in
5.2.0:

- a `rom_ids` scope on `POST /api/sync/negotiate` (and on `GET /api/saves`), so a device holding part of a library can
  scope a sync session to the ROMs it holds;
- per-ROM `title_id`, `save_target` and `save_target_layout` fields on the ROM.

A higher floor is a breaking change for everyone on an older server, whenever it lands. 1.0 is a breaking release
already. Raising the floor inside 1.x would break users in a minor release.

## Decision

**1.0 requires RomM 5.3.0.** `_MIN_REQUIRED_VERSION` moves from `(4, 9, 0)` to `(5, 3, 0)`. The gate itself does not
change: a server below the floor is refused, a pre-release ranks below its own release (so `5.3.0-beta.1` is refused and
`5.3.1-beta` accepted), and a `development` build or a missing version bypasses the check.

The floor moves ahead of the first feature that needs it, so that the work built on what 5.3.0 added does not raise it
inside 1.x.

## Consequences

- **Users on RomM 4.9 through 5.2 are refused at connect from 1.0 on**, with the version error page. There is no reduced
  mode for them: the plugin stays inert until the server is updated.
- **The floor is ahead of what the code uses.** Until the first feature built on 5.3.0 lands, a 5.2 server would serve
  every request the program makes. The floor states what the program supports, not the lowest server that happens to
  work today.
- **Enforcement is unchanged in kind.** The gate in `test_connection()` is pinned by tests in both directions at the new
  floor, and the reader-facing statements of the floor that `scripts/check_romm_min_version.py` lists are held to the
  constant; it reads no ADR, so the numbers in this record and the two it supersedes stay as written.

## Alternatives considered

- **Raise the floor with the first feature that needs 5.3.0, and ship that as 2.0.** Rejected: it spends a major version
  on a floor raise that 1.0, a breaking release already, can carry.

## Related

- [ADR-0016](0016-save-sync-hands-detection-to-romm-negotiate.md) — the previous floor, and why a floor gates the whole
  program rather than a feature.
- [ADR-0017](0017-client-baseline-detection-authoritative-negotiate-is-transport.md) — negotiate kept as the session
  transport, the protocol the `rom_ids` scope extends.
