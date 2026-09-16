# Native binaries

Compiled artifacts that ship inside `backend/`. Unlike the Python packages under `backend/_vendor/`, these have no
source in this repo — they are downloaded verbatim from an upstream release, so a pinned checksum (verified in CI) is
what makes an update a deliberate, reviewable diff rather than a silent binary swap.

The `.so` is loaded by [`adapters/gavel_native.py`](../adapters/gavel_native.py) via `ctypes`; there is no Python
fallback, so a missing or mismatched artifact is a fatal, loud failure at bootstrap.

## libgavel-x86_64-linux.so

The compiled [romm-gavel](https://github.com/danielcopper/romm-gavel) core — both save-sync decision kernels: the full
per-`(rom, filename, slot)` sync action (`gavel_compute_sync_action`) and the upload-409 resolution fallback
(`gavel_resolve_upload_conflict`).

- **Upstream:** <https://github.com/danielcopper/romm-gavel>
- **Release:** `v1.0.1` — the C ABI has been part of upstream's promise since `v1.0.0`. Struct layouts, signatures and
  enumerator values cannot change now without a major bump, which is what makes pinning a compiled artifact meaningful
  rather than hopeful. The binary is byte-identical to `v0.4.0`; only the guarantee attached to it changed.
- **Architecture:** `x86_64` Linux — freestanding (zero library dependencies: no NEEDED entries, no global undefined
  symbols; upstream release CI enforces this), so it loads on any x86_64 Linux regardless of libc flavor or version
- **Checksum:** pinned in `libgavel-x86_64-linux.so.sha256` (SHA-256)
- **License:** MIT — `libgavel-x86_64-linux.so.LICENSE`, upstream's own `LICENSE` at `v1.0.1`, copied verbatim (git blob
  `f29abcb0ab339eab33753b0eb3266fb5af3dfa5a`; `git hash-object` on the copy answers the same). Nothing is owed to a
  third party — gavel is this project's own — but the two shipped trees under `backend/_vendor/` carry their text, and a
  reader should not have to go and find out why this one did not.

  **It is pinned by nothing**, and that is a limit rather than an oversight. `libgavel-x86_64-linux.so.sha256` is
  upstream's own release artifact, downloaded verbatim by step 1 below and overwritten by the next download — adding a
  line for the licence would be edited away at the following bump, silently. So the blob hash above is the re-verifiable
  record, and re-copying the licence is step 3.

### How to update

1. Download the artifact and its checksum from a newer release:

   ```sh
   gh release download <tag> -R danielcopper/romm-gavel -p 'libgavel-x86_64-linux.so*' -D backend/native/
   ```

2. Verify the downloaded artifact against its checksum:

   ```sh
   cd backend/native && sha256sum -c libgavel-x86_64-linux.so.sha256
   ```

3. Re-copy upstream's `LICENSE` at the new tag over `libgavel-x86_64-linux.so.LICENSE`, and record its blob hash above:

   ```sh
   gh api repos/danielcopper/romm-gavel/contents/LICENSE?ref=<tag> --jq .content | base64 -d \
     > backend/native/libgavel-x86_64-linux.so.LICENSE
   git hash-object backend/native/libgavel-x86_64-linux.so.LICENSE
   ```

4. Bump the **Release** tag above.
5. Re-run the save-sync conformance tests (`tests/adapters/test_gavel_native.py`,
   `tests/adapters/test_gavel_native_table_vectors.py`) — the shipped binary must still match the vendored gavel
   vectors. A gavel major bump means at least one expected outcome changed, so re-copy the vectors
   (`tests/adapters/gavel_vectors/`) in the same change.

The checksum is re-verified by CI (`.github/workflows/ci.yml`), so a swapped binary fails the pipeline. It names one
path and verifies that file alone, so nothing else in this directory affects it — and nothing else in this directory is
checked by anything: `scripts/check_vendored_trees.py` roots at `backend/_vendor/` and does not mention `native` at all,
so there is no set-equality comparison here for a sibling file to fall into or out of.
