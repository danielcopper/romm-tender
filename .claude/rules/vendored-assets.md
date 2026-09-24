---
paths:
  - "backend/_vendor/**"
  - "backend/native/**"
  - "defaults/**"
---

# Vendored code, binaries, and data `[ours]`

**Vendored deps (`_vendor/`)**: Third-party runtime deps are vendored under `backend/_vendor/<package>/` (Tender runs on
the system Python with no pip and no venv; why that rules a venv out, and the limit it sets for compiled extension
modules, is [`_vendor/README.md`](../../backend/_vendor/README.md)'s) and imported as `from _vendor import <package>`.
Only adapters import `_vendor.*`; services/domain/lib stay third-party-free (`domain-stdlib-only` contract in
`.importlinter`). `_vendor/` is excluded from ruff, basedpyright, and Sonar. Every vendored package ships its upstream
`LICENSE` and a provenance entry in [`_vendor/README.md`](../../backend/_vendor/README.md).

**One manifest per tree.** `backend/_vendor/<package>/` is pinned by the `<package>.SHA256SUMS` beside it, and
`scripts/check_vendored_trees.py` fails on a package directory that has none — the manifest is discovered, never named
in the script, so vendoring a package without dropping one next to it breaks the build rather than leaving an unguarded
tree beside a guarded one. The manifest is upstream's own release manifest where the copy is verbatim (`atlas`), or one
generated from the tree where the copy carries a documented local patch (`vdf`); `_vendor/README.md` records which kind
each one is, and both are asserted the same way. Where it is a wheel's manifest the licence sits **beside** the tree as
`<package>.LICENSE`: the checked file set is an exact equality, so a licence inside the tree would be an extra file the
gate has to except.

**Vendoring or bumping a package is a device-test trigger**, because the Python CI runs is not the Python a device runs;
[`_vendor/README.md`](../../backend/_vendor/README.md#the-runtime-a-vendored-copy-has-to-load-in) holds why. A green
`mise run gate` says the copy hashes correctly and imports under CI's Python; it says nothing about the system Python on
the Deck. How far a load-time failure spreads is a property of the wiring, not of vendoring: today `main.py` →
`bootstrap/adapters.py` → `adapters/atlas_firmware.py` → `from _vendor.atlas import …` are all module-level imports, so
a raise inside the vendored tree takes the whole backend down rather than one feature. A package reached only behind a
lazy import would cost just the path that reaches it.

**Compiled binaries** (no source in this repo) are vendored under `backend/native/` instead — downloaded verbatim from
an upstream release with a pinned SHA-256 (CI re-verifies it), loaded by an adapter via `ctypes` with no Python
fallback; provenance and the update procedure live in [`native/README.md`](../../backend/native/README.md).

**Vendored data** used to be a third category — `defaults/bios_registry.json`, a firmware snapshot copied from an
emu-atlas release under its own checksum. It is gone with the swap to the live resolver, and nothing in `defaults/` is
vendored today; `config.json` is maintained in this repo.

The shared rule across the categories that remain: **the artifact is an upstream copy pinned by checksum** — verbatim,
or verbatim plus the local patch its provenance entry documents. Editing one in place to fix a problem is always wrong —
the fix belongs upstream, followed by a deliberate re-copy and a checksum bump.
