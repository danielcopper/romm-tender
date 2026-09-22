# The release builds no Decky artifact

## Status

**Consequence 1 lapsed with [ADR-0039](0039-the-release-ships-the-packagers-tarball.md)**, which attaches the packager's
tarball and its checksum to every tag and checks the result on every pull request — and consequence 4 in part:
`release.yml` is still read by no gate, so what is now guarded mechanically is the artifact, not the workflow.

Accepted. **Supersedes [ADR-0033](0033-the-shipped-folder-name-is-chosen-here.md)** — not its reasoning, which was
sound, but its mechanism: the build directory that fed the Decky CLI's `FilenameSource::Directory` no longer exists,
because the workflow no longer builds a Decky-shaped artifact at all.

## Context

ADR-0033 pinned the delivered folder name because Decky derives all four of its per-plugin directories — `plugins/`,
`data/`, `settings/` and `logs/` — from it, and because the name had been following the GitHub repository name silently.
It moved every user's directories once, at 0.31.0. The pin lived in one place: the release workflow's packaging step,
which copied the checkout to a directory called `romm-tender` and built from there.

The plugin now hosts itself. Nothing is delivered through Decky, and no further Decky-form artifact will be built — the
one exception considered, a transitional zip carrying a migration card, was weighed and dropped: 0.33 users are told
about the move directly, and anyone who needs a Decky-form build to carry their data across downloads the
already-published `tender-v0.33.0`.

With the Decky build path gone from `.github/workflows/release.yml`, there is no build directory, no CLI invocation and
no zip whose top-level folder could be wrong. ADR-0033's first and third decisions describe a workflow step a reader
will look for and not find.

## Decision

**1. The release workflow builds no Decky-shaped artifact.** The CLI download and its checksum pin, the packaging step
that ran it under `sudo`, the Decky-shaped smoke test and the `Tender.zip` upload are removed. What remains is
`release-please`: version, tag and changelog.

**2. ADR-0033's decisions 1 and 3 are void.** They pinned a name in a step that no longer exists, and asserted it in a
smoke test over an artifact that is no longer produced.

**3. ADR-0033's decision 4 is retired, because the question it answered no longer has an asker.** That decision kept the
folder a release unpacks into free to disagree with the other homes of the identifier, and it had to: Decky _derived_
four per-plugin directories from that one name. Nothing derives anything from a folder name now — the installer resolves
the roots once and writes them into the unit as absolute paths, and the backend reads only those. So the identifier has
four homes rather than five, and this is a question retired rather than one passed on; a future packaging step that
wanted to derive a user directory from its own folder name would be reintroducing the coupling, not inheriting it.

**4. Nothing is renamed on anyone's device.** Installs already in the field keep `romm-tender` in `~/homebrew/plugins/`,
with their settings, data and logs where they are. This decision removes a build path; it moves no files.

## Consequences

- **A tag from this workflow carries no downloadable asset** until the tarball lands
  ([#1904](https://github.com/danielcopper/romm-tender/issues/1904)). That is the intended state between here and 1.0.0,
  not an oversight — and it is the reason the install instructions must name a fixed tag rather than `latest`.
- **The development deploy is unaffected.** `mise run deploy` still stages Decky's layout — the backend tree as
  `py_modules/`, the entry point beside it — and the plugin still loads: measured on the device on 2026-09-15, with the
  backend up, the server reached and the library registered. Decky Loader puts `py_modules/` on the import path itself,
  which is why the entry point no longer needs to.
- **ADR-0033's third consequence lapses.** `mise.toml`'s hand-kept copy of the folder name had a CI counterpart it could
  disagree with; it no longer has one. The copy stands alone until the installer replaces it.
- **Nothing mechanical guards this removal.** No gate reads `release.yml`, so a build step reappearing there fails
  nothing. Review is the whole of the enforcement.

## Alternatives considered

**Keep ADR-0033 standing and let it age.** Cheapest, and wrong: it reads as current, describes a pin a maintainer would
go looking for, and its smoke-test assertion is the sort of thing a reader trusts precisely because it is mechanical.

**Build one more Decky zip for the transition, with a migration card.** The plan of record until this cut. Dropped by
the owner: Decky's updater does not reach this plugin, which is distributed outside the store, so the card would reach
nobody who was not already being told directly — and the release that carries a user's data across already exists.

**Keep the packaging step but let it fail.** Rejected for the reason the step was removed rather than emptied: a build
step that claims to produce a plugin the repository can no longer shape is a false statement about the tree, and a
failing one is a false statement that also blocks the release.

## Related

- [ADR-0033](0033-the-shipped-folder-name-is-chosen-here.md) — the decision this supersedes.
- [ADR-0031](0031-user-data-lives-outside-the-plugin-directory.md) — the user's data leaves the plugin's directories,
  which is what kept this removal from touching anyone's library.
- [ADR-0032](0032-shortcuts-are-rewritten-in-place.md) — the launcher leaves the plugin folder and shortcuts are
  repointed.
