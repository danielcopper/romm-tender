# Dependency management

One page for the question "**why is _that_ on _this_ version, and who bumps it?**" Every version in the repo lives in
one of the files below. Most update automatically (Renovate); a few are pinned by hand on purpose.

## Where every version lives

| File                                               | Holds                                                                                                                       | Updated by                                                         |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `mise.toml`                                        | the dev **toolchain**: `node`, `pnpm`, `python`, `uv`, `deno`                                                               | **by hand** (see [Bumping by hand](#bumping-by-hand))              |
| `frontend/package.json`                            | npm deps (`^` ranges), `packageManager` (pnpm), `pnpm.peerDependencyRules`                                                  | Renovate (npm)                                                     |
| `frontend/pnpm-lock.yaml`                          | resolved npm versions                                                                                                       | generated from `frontend/package.json` (Renovate keeps it in sync) |
| `requirements-dev.txt` / `docs/requirements.txt`   | Python dep **ranges** — the **source of truth**, incl. deliberate ceilings                                                  | by hand for ceilings; Renovate refreshes within them               |
| `requirements-dev.lock` / `docs/requirements.lock` | resolved Python versions (uv-compiled)                                                                                      | `mise run lock-update` locally; Renovate recompiles on its PRs     |
| `.github/workflows/*.yml`                          | action and reusable-workflow SHA pins (`@<sha> # vX`; `@<sha> # main` for `danielcopper/.github`), `setup-*` version inputs | Renovate (github-actions) for the SHAs; toolchain inputs by hand   |
| `renovate.json`                                    | **policy only** — who may bump what. Not a version source.                                                                  | by hand                                                            |

## What's genuinely duplicated (and why it's safe)

A handful of **toolchain** versions appear in more than one file, because each tool reads its own config location and
all copies must agree:

| Tool           | Appears in                                                                                           | Must match because                                                                                                                                                                                                                                                                                                                                                                                                                                                           |
| -------------- | ---------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `pnpm`         | `mise.toml` + `frontend/package.json` `packageManager`                                               | the package manager you run must be one version                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `python`       | `mise.toml` + workflow `setup-python` + `pyproject.toml` + `sonar-project.properties` + `install.sh` | Tender runs on the system interpreter, and SteamOS ships Python 3.13: 3.13.1 in SteamOS 3.7 and 3.13.5 in 3.8, the current stable (Valve's package mirror, `core-3.7` / `core-3.8`). SteamOS 3.9, not yet stable, ships 3.14.6 (`core-3.9`), so CI also runs the backend suite on 3.14 (`test-3.14`). That version is tested against, not targeted: ruff, basedpyright and the installer's floor stay at 3.13. `test-3.14`'s `setup-python` is not one of this row's copies. |
| `uv`           | `mise.toml` + workflow `setup-uv`                                                                    | the local resolver must equal the lock author (reproducible locks)                                                                                                                                                                                                                                                                                                                                                                                                           |
| `node`, `deno` | `mise.toml` + workflows                                                                              | local == CI                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |

These are the only real duplicates, and **Renovate is configured to never touch them** (excluded by dependency name in
`renovate.json`). So a bot can't bump one copy and desync the rest — they only move when **you** move them, together.

## Who bumps what — the auto-merge policy

Update PRs are opened by [Renovate](https://docs.renovatebot.com/) (`renovate.json`, the Renovate GitHub App).
Auto-merge uses GitHub's native auto-merge, so **the required CI checks are the gate** — a PR only merges itself when
everything is green. The checks say nothing about whether a release is authentic, so Renovate proposes an npm or PyPI
release only once it is three days old (the `security:minimumReleaseAgeNpm` and `security:minimumReleaseAgePypi`
presets), which gives a compromised release time to be flagged and pulled before it can merge itself — lock-file
maintenance, pins and replacements are exempt, and GitHub Actions updates carry no such wait. Renovate opens a new PR
only before 6am on Mondays (UTC), so a week's updates arrive together; a PR already open is kept up to date outside that
window too.

| Category                                           | Auto-merges?                    |
| -------------------------------------------------- | ------------------------------- |
| Python (pip) in-range minor/patch + lock refreshes | ✅                              |
| npm / GitHub-Actions minor / patch / digest        | ✅                              |
| `@decky/ui` minor / patch — any day, as a fix      | ✅ (see below)                  |
| Monthly lock-file maintenance (transitive refresh) | ✅                              |
| **Any major** (any ecosystem)                      | ❌ → human review               |
| **Shared workflows** (`danielcopper/.github`)      | ❌ → human review               |
| **Toolchain** (`node`/`pnpm`/`python`/`uv`/`deno`) | ❌ not even proposed (excluded) |
| **Raising a deliberate ceiling** (see below)       | ❌ not even proposed            |

### `@decky/ui` ships as a fix

`@decky/ui` is the one dependency whose update can repair Tender rather than maintain it. The standalone panel bundle
carries its own copy ([how the panel is built](../architecture/frontend-bundles.md)), and when a Steam update moves a
module one of its lookups searches for, upstream repairs the lookup in a new release — which reaches a standalone user
only inside a new Tender release. So one rule in `renovate.json` treats its minor and patch updates apart:

- **No schedule.** Its PR opens on whichever day the release becomes old enough, not in the next Monday window.
- **`fix` as the commit type.** An in-range bump touches only the lock, and the `:semanticPrefixFixDepsChoreOthers`
  preset that `config:recommended` brings commits a lock-only update as `chore(deps)`. release-please proposes no
  release for a `chore`; as `fix(deps)` the bump is a bug fix in the next release PR, and on its own is enough for
  release-please to propose one.

The three-day release age stays: it is the supply-chain lever where the schedule is only the noise one, and the Steam
breaks so far reached Steam's beta first, which leaves that time. Its package budget is also wider than the others', so
a repair that grows the package does not stop the auto-merge
([Third-party package budgets](../architecture/frontend-bundles.md#third-party-package-budgets)). A major `@decky/ui`
update keeps the default handling — the Monday window and human review — and its commit type is chosen when it is merged
by hand.

## Ceilings — versions deliberately held back

One Python dev tool is capped tighter than "next major", on a standing policy rather than on an observed break:

| Dep    | Ceiling | Why                                        |
| ------ | ------- | ------------------------------------------ |
| `ruff` | `<0.16` | policy: 0.x minors may ship new lint rules |

The ceiling lives in `requirements-dev.txt`. Renovate's `rangeStrategy: update-lockfile` refreshes the lock _within_ the
range, but it does **not** stop a range-widening PR when a newer version is _above_ the ceiling — so `renovate.json`
also carries an `allowedVersions` cap for that dep to keep Renovate from proposing the raise at all.

**Heads up — this ceiling is written in three places:** `requirements-dev.txt`, `renovate.json` (`allowedVersions`, the
rule commented `KEEP IN SYNC`) and the table above. If you deliberately raise it, raise it in all three. This is the
only version duplication Renovate forces on us.

**A ceiling that is no longer needed is removed from all three, too.** `pytest-asyncio` was held `<1.4` until
[#1886](https://github.com/danielcopper/romm-tender/pull/1886) rewrote the suite to stop relying on the implicit
thread-default event loop that 1.4 removed; the constraint in `requirements-dev.txt` went back to a plain next-major
`<2.0`, but the `allowedVersions` cap and this table's row were left behind. Nothing visibly went wrong while they
stood: the same cut moved the lock to 1.4.0 and no higher release appeared after it, so Renovate never had a raise to
decline. The cost was latent, which is exactly why the shape is worth naming — the locked version already sat above a
cap Renovate was still enforcing, so the next release would have been withheld with nothing anywhere saying so.

## Bumping by hand

- **Toolchain** (`mise.toml`): edit the pin, then update every coupled copy in the same commit — `frontend/package.json`
  `packageManager` for pnpm, the workflow `setup-*` inputs for python/uv/node/deno. Run `mise install` to pick it up.
- **A Python ceiling**: three places, then the lock — raise the `<X` in the `.txt` source, raise the matching
  `allowedVersions` in `renovate.json`, raise the `Ceiling` column in the table above, run `mise run lock-update`,
  commit together. Dropping one is the same three in reverse — widen the `.txt` constraint **and** delete the
  `renovate.json` rule **and** delete its row, or Renovate keeps enforcing a cap the source no longer states.
- **Python locks** after any `.txt` source edit: `mise run lock-update` (the `check_lock_sync` CI gate enforces this).
