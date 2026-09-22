# The release ships the packager's tarball, and every PR proves it

## Status

Accepted. **Closes the gap [ADR-0035](0035-the-release-builds-no-decky-artifact.md) left open deliberately** — its first
consequence lapses with this decision, and its fourth in part.

## Context

`install.sh` downloads exactly two files from a release: `romm-tender-<version>.tar.gz` and its `.sha256`. It verifies
the pair with `sha256sum -c` from the directory it downloaded both into, unpacks the archive with
`--strip-components=1`, refuses one that does not carry the files it names, and starts what comes out as a systemd user
unit.

Nothing published those two files. ADR-0035 removed the Decky build path and left `release.yml` at release-please alone,
saying so in its own header comment: a tag from that workflow carries no downloadable asset. That was the intended state
at the time, not an oversight — there was no installer yet to want one.

There is now. `scripts/package.sh` assembles the archive from a directory and `mise run package` builds the frontend and
runs it, which is what a developer installs from with `install.sh --from`. What was missing is a release that does the
same thing, and anything at all holding the result to what the installer needs: the packager says what goes into the
archive, the installer says what has to come out of it, and neither half can see the other. A shipped path dropped, a
prune rule widened, a file the backend newly starts from — each leaves both sides green and the download broken on a
user's machine.

## Decision

**1. One producer.** `scripts/package.sh` assembles the tarball for a local build and for a release alike. The release
workflow runs the same two steps `mise run package` runs — build the frontend, then pack — rather than a packaging step
of its own. There is one layout because there is one script that writes it.

**2. The check runs on every pull request, over a tarball packed from that PR's own build.** CI's `build` job is the
only one holding a built `dist/`, which is what the packager requires, so the pack and the check live there.
`scripts/check_release_tarball.py` opens the archive and asserts the shape the installer relies on: one top-level
`romm-tender/` directory and nothing beside it, the files an install starts from together with the version file and the
licence texts a distributed copy carries, an executable launcher, nothing the packager prunes, no link and no path that
escapes the root, a `version.txt` agreeing with the archive's name and — where a tag is given, which on a pull request
it is not — with the tag, and a sidecar carrying one `sha256sum` line that names the archive by its bare name. The
reason it is not left to the tag is that a published tag cannot be withdrawn: at that point the only way out of a bad
tarball is another release.

**3. The release job builds from the tag.** It checks out `tag_name`, installs, builds, packs, runs the same check with
`--tag`, and uploads the archive and its `.sha256` with `gh release upload`. It carries no artifact over from CI. A tag
is the one thing about a release that is fixed, so a release that is reproducible from it alone can be rebuilt by
anyone, and a CI artifact handed between two workflows would make the published bytes depend on a run rather than on a
commit.

**4. The packager is given no `--version` there**, for the reason stated at that step in
`.github/workflows/release.yml`.

## Consequences

- **ADR-0035's first consequence lapses.** A tag from this workflow carries a downloadable asset again — the tarball and
  its checksum, not a Decky zip. Nothing about that decision's removal of the Decky build path is reopened.
- **ADR-0035's fourth consequence lapses in part.** `release.yml` is still read by no gate, so a step reappearing there
  fails nothing. What is no longer unguarded is the artifact: the check runs on every PR, so the shape the release
  uploads is asserted continuously rather than reviewed.
- **Every pull request costs one pack step.** A `cp -a` of the shipped tree, a prune, a `tar` and a `gzip`, in a job
  that has already built the frontend.
- **The check starts nothing, and that is a wide blind spot.** What it does read, and what that leaves unseen, is stated
  in `scripts/check_release_tarball.py`'s own docstring. What would close it is a run, which needs a Steam and a machine
  this repository's CI does not have.
- **`REQUIRED_FILES` is a second list beside the packager's `SHIPPED`, and they can disagree.** That is deliberate, and
  `scripts/check_release_tarball.py` states why at that list.

## Alternatives considered

**Check only at the tag.** The cheapest reading of the issue, and the one the owner rejected. A release is the worst
possible place for a check's first run: the tag is already published and immutable when it fails, the fix is another
version number, and a failure there is discovered by whoever is releasing rather than by whoever broke it.

**Hand CI's build job artifact to the release job.** Rejected. It spans two workflows, so the published bytes would
depend on which run produced them rather than on the commit, and the wiring — upload, retention, a download in another
workflow — is more machinery than a checkout and a build. A tag that builds reproducibly from itself needs none of it.

**Let `release.yml` do its own packaging.** Rejected for the reason decision 1 exists: a second place that decides the
layout is a second layout — the thing ADR-0033 pinned to one place, and a step of exactly the kind ADR-0035 removed from
this workflow outright.

## Related

- [ADR-0035](0035-the-release-builds-no-decky-artifact.md) — the decision that emptied this workflow and named the gap.
- [ADR-0036](0036-the-backend-hosts-itself.md) — why there is an installer to ship a tarball to at all.
