# Vendored copies

What this directory holds is **upstream code we do not own** — verbatim, or verbatim plus a documented local patch,
pinned by the provenance below, excluded from our own linters and gates, and redistributed in the release tarball, so
each copy keeps its upstream licence (inside the package directory, or beside it as `<package>.LICENSE` where the copy
is pinned against upstream's own file manifest). That, and not any one import mechanism, is what puts something here;
keeping the copies under one root is what lets a single set of exclusions cover them all. See the `_vendor/` rules in
[`CLAUDE.md`](../../CLAUDE.md).

Everything here today is a third-party runtime dependency, imported as `from _vendor import <package>` — and only
adapters import `_vendor.*`. They are vendored because Tender runs on the system Python, with no pip and no venv. A venv
is tied to the Python minor version it was built with, so an OS update that moves the system Python to a new minor
version would strand one; a pure-Python copy under the program's own code carries no such tie.
[The runtime a vendored copy has to load in](#the-runtime-a-vendored-copy-has-to-load-in) states the limit that follows.
The provenance entries below make updating any of them a deliberate diff rather than "diff and pray".

## Manifests

Every package directory here is pinned by a `<package>.SHA256SUMS` beside it, in the format `sha256sum` writes
(`<digest>  <path>`, paths relative to this directory). `scripts/check_vendored_trees.py` discovers those manifests
rather than being told about them: per tree it asserts that every `<package>/` digest matches and that the vendored file
set **equals** the manifest's entries under that prefix, and it **fails on a package directory that has no manifest at
all** — so vendoring a package and forgetting its manifest breaks the build instead of quietly leaving one tree
unguarded beside a guarded one.

A manifest comes in two kinds, and each package's entry below says which kind it has. Both are asserted identically;
what differs is what the digests prove.

- **Upstream's own release manifest, vendored verbatim** — `atlas`. The digests prove both that nobody has edited the
  copy and that it IS the tagged release, because upstream published them. Such a manifest also lists files we never
  vendor (release artifacts, the dist-info), which is why the gate compares only the `<package>/` entries — and why it
  checks the manifest's dist-info licence entry against the sibling `<package>.LICENSE`.
- **Generated here from the tree as we ship it** — `vdf`. A copy carrying a deliberate local patch can never match an
  upstream release manifest, so its manifest is our own digest of the patched copy: it proves that nobody has reached
  into the tree since, and nothing about upstream identity. Only review catches a manifest regenerated to bless an edit,
  so regenerating one is the last step of a deliberate re-copy, version bump or patch — never the answer to a failing
  gate:

  ```sh
  cd backend/_vendor && find <package> -type f -not -path '*/__pycache__/*' \
      | LC_ALL=C sort | xargs sha256sum > <package>.SHA256SUMS
  ```

  `LC_ALL=C` is not decoration. The gate reads the manifest into a mapping and does not care about line order, but a
  locale-collated `sort` orders `vdf/__init__.py` against `vdf/LICENSE` differently on a German machine than under C, so
  without it every regeneration is a whole-file diff.

## atlas

The [emu-atlas](https://github.com/danielcopper/emu-atlas) resolver — the config-aware emulator-knowledge library
extracted from this plugin.

- **Upstream:** <https://github.com/danielcopper/emu-atlas>
- **Version:** 0.20.0 — tag `v0.20.0`, from the release's `emu_atlas-0.20.0-py3-none-any.whl`
  (`sha256:d79dffa61a80f0718fcf983d8bfc078fbf31efde021a0939d3ea1854f5c1b366`)
- **License:** MIT — see [`atlas.LICENSE`](atlas.LICENSE)
- **Local patches:** none. Upstream made the package relocatable in
  [emu-atlas#327](https://github.com/danielcopper/emu-atlas/issues/327) — no absolute self-imports, no `files("atlas")`
  — so a verbatim copy resolves under `_vendor.atlas` with nothing to change. That is the whole premise of the checksum
  pin: unlike `vdf` below, there is no patch to reapply, so an edit here is always wrong.

The licence sits **beside** the tree as `atlas.LICENSE`, not inside it, and upstream's own release manifest is vendored
verbatim as `atlas.SHA256SUMS`. That keeps `atlas/` exactly equal to the manifest's file set, which is what lets
`scripts/check_vendored_trees.py` assert set **equality** — nothing missing, nothing added — with no per-file
exceptions. The equality half is not optional: `sha256sum -c --ignore-missing` exits 0 after a vendored file is deleted,
so a plain checksum sweep would pass a half-copied tree.

`_vendor.atlas` is consumed by three adapters: `adapters/atlas_firmware.py` (the firmware seams behind
`services.protocols.FirmwareResolver` and `FirmwareFolderVerdictFn`), `adapters/atlas_catalogue.py` (the emulator
catalogue behind `CoreInfoProvider`, `SystemSupportedExtensionsFn`, `SystemM3uSupportFn` and `SystemKnownFn`), and
`adapters/atlas_saves.py` (where one ROM's save lives and what it consists of, behind `SaveLocationReader`).
`tests/test_vendored_atlas.py` additionally imports it and asserts the pinned version, so the copy is proven to resolve
and not merely to hash correctly even if all three adapters ever stop importing it.

### How to update atlas

1. Download the wheel and the manifest from the newer tagged release:

   ```sh
   gh release download <tag> -R danielcopper/emu-atlas -p 'emu_atlas-*-py3-none-any.whl' -p SHA256SUMS -D /tmp/atlas
   ```

2. Verify the wheel against the manifest, then unpack it:

   ```sh
   cd /tmp/atlas && sha256sum -c --ignore-missing SHA256SUMS && unzip -d u emu_atlas-*-py3-none-any.whl
   ```

3. Replace `backend/_vendor/atlas/` with `/tmp/atlas/u/atlas/`, leaving out any `__pycache__`. Copy
   `/tmp/atlas/u/emu_atlas-<version>.dist-info/licenses/LICENSE` to `backend/_vendor/atlas.LICENSE` and
   `/tmp/atlas/SHA256SUMS` to `backend/_vendor/atlas.SHA256SUMS`.
4. Bump the **Version** bullet above and the pinned version in `tests/test_vendored_atlas.py`.
5. Re-run the gate: `python scripts/check_vendored_trees.py`.

The gate runs in `mise run lint` and in CI (`.github/workflows/ci.yml`), so both a tampered copy and a dropped one fail
the pipeline.

## vdf

- **Upstream:** <https://github.com/ValvePython/vdf>
- **Version:** 3.4 — tag `v3.4`, commit `8104cb27c0b222bd802b69df58204ab389fc714c`
- **License:** MIT — see [`vdf/LICENSE`](vdf/LICENSE)
- **Local patches:** `vdf/__init__.py` — `from vdf.vdict import VDFDict` changed to `from .vdict import VDFDict`
  (relative self-import so the package resolves under `_vendor.vdf`, not a top-level `vdf`).
- **Manifest:** `vdf.SHA256SUMS`, **generated here** from the tree as we ship it — the patch above means no upstream
  manifest can ever match it. It pins the copy against later edits and says nothing about upstream identity; the
  provenance above is what ties it to `v3.4`.

The licence is vendored **inside** the tree as `vdf/LICENSE`, so the manifest covers it like any other file and there is
no sibling `vdf.LICENSE`. A generated manifest carries no dist-info licence entry, and the gate wants the two to agree
about each other: no entry and no sibling is the clean pair it expects here, and putting a `vdf.LICENSE` beside this
tree would be reported as pinned by nothing rather than quietly ignored.

That file is **not** upstream's own `vdf/LICENSE` — upstream's package directory holds `__init__.py` and `vdict.py` and
nothing else. It is the repository's root `LICENSE`, copied inside the package so the redistributed tree carries its own
licence, and the update procedure below has to put it back by hand for exactly that reason.

### How to update vdf

1. Replace `backend/_vendor/vdf/` with the new upstream `vdf/`, leaving out any `__pycache__` — delete the old directory
   first rather than copying over it, since a file upstream has dropped would otherwise survive and step 4 would
   regenerate the manifest from a tree holding it, pinning the leftover as if it belonged. This is the one procedure
   here with no upstream manifest to catch that. Then reapply the self-import patch above — dropping it makes the
   package resolve to a top-level `vdf` that is not installed.
2. Copy the upstream repository's **root** `LICENSE` back in as `backend/_vendor/vdf/LICENSE`. The previous step deletes
   it and upstream's `vdf/` does not carry one, so skipping this ships the package with no licence at all — and nothing
   would say so: step 4 regenerates the manifest from whatever tree is there, a generated manifest has no dist-info
   licence entry for the gate's licence assertion to run off, and there is no sibling `vdf.LICENSE` either, so the check
   stays green over a licence-less redistribution.
3. Bump the **Version** and **Local patches** bullets above.
4. Regenerate the manifest from the patched tree with the command under [Manifests](#manifests), then re-run the gate:
   `python scripts/check_vendored_trees.py`.

## The runtime a vendored copy has to load in

A vendored copy loads under the interpreter the service unit starts — `/usr/bin/python3`, or whatever `TENDER_PYTHON`
named when `install.sh` wrote the unit — and not under the Python the venv, `mise run test`, basedpyright and the
linters run, which is the version the toolchain pins (`mise.toml`, and the workflows' `setup-python` beside it).
`install.sh` refuses an interpreter older than that one, but nothing keeps the two equal: an OS update moves the system
Python without a commit here. A vendored package's assumptions about the standard library are therefore proven only
against CI's Python, and surface on a device — when the backend starts, or the first time a question reaches the
assumption.

A compiled extension module built for one CPython minor version's ABI does not fit this model — a `cp313-cp313` wheel
does not load under 3.14, so a copy vendored for today's system Python stops loading when an OS update moves it, the
very event vendoring exists to survive. A stable-ABI (`abi3`) build is the exception. The one expected so far is
`backports.zstd`, which ships per-version builds only (1.7.0: cp310–cp313, no abi3, no cp314); how it ships is #1735's
decision.

Neither artifact can see any of this, and each says less than it looks like it does. The checksum gate says the copy is
the bytes we pinned; it never imports anything. What says the copy imports is the test suite — most directly
[`tests/test_vendored_atlas.py`](../../tests/test_vendored_atlas.py), whose whole job that is, and alongside it every
test that reaches the firmware adapter — and all of it only under CI's Python. What that makes of vendoring or bumping a
package is [`.claude/rules/vendored-assets.md`](../../.claude/rules/vendored-assets.md)'s.

## Formatters and vendored copies

`.githooks/pre-commit` formats **explicitly staged paths** — and an exclude written for a directory walk does not always
apply to a path handed over explicitly. That is how a formatter reaches a vendored copy:

- **Python — already handled, do not undo it.** `pyproject.toml` sets `force-exclude = true` under `[tool.ruff]`. That
  is what makes ruff honour `extend-exclude` for the explicit paths the hook passes; without it, `ruff format` on a
  staged vendored tree rewrites most of it and breaks byte-identity against the manifest. Changing `line-length` does
  not rescue it — upstream runs no Python formatter at all, so the line lengths measured all rewrite a large share of
  both trees; the measurement lives beside the setting it argues about, in the `[tool.ruff]` comment. Excluding the
  trees is the only answer. The checksum gate would catch it, but only after the commit — and removing that one line
  puts every future vendored package back in the same position, silently. It also stops the hook formatting and linting
  `./scripts`, which the same `extend-exclude` already asked for and CI already did; the hook was the last thing
  checking those files.
- **Markdown — still open, and nothing needs it yet.** `deno fmt` (`deno.json` includes `**/*.md`), `markdownlint-cli2`
  (the `lint:md` mise task) and `scripts/check_markdown_links.py` (which walks `git ls-files '*.md'`) all reach tracked
  markdown under `_vendor/`, and the hook reformats staged `.md` too. The emu-atlas wheel ships no markdown, so there is
  nothing to exclude today — but the next vendored package may, and reformatted prose breaks byte-identity exactly like
  reformatted code.

**The trap is not limited to vendored trees.** Any directory a _different_ formatter owns is exposed the same way, and
there it costs a whole tree rather than one file: the markdown formatter is configured for `**/*.md`, but handed `src/`
as an explicit path it reformats the TypeScript that prettier owns — 186 files in one command, every one a real diff,
and `pnpm format:check` is the only thing that notices. Hand a formatter the paths it owns, never a directory that
merely contains them.
