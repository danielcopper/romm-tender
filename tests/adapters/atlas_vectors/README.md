# emu-atlas recorded machines — vendored

`vectors/` is [emu-atlas](https://github.com/danielcopper/emu-atlas)'s `vectors/` folder, copied verbatim from the
release asset `emu-atlas-vectors-v0.34.0.tar.gz`
(`sha256:a35fa29d6bfa20a9cde0928d4bd84ad1b1cf1e75b50fa288ca7b246e8bb6fa4b`). It is the same release as the resolver
vendored at `backend/_vendor/atlas/`, and upstream's release manifest, vendored verbatim as
`backend/_vendor/atlas.SHA256SUMS`, lists that digest for that asset.

Each vector is a whole machine the resolver is run against — files, folders, links and what each core answers — and the
answer it must give there. `tests/services/test_firmware_vectors.py` runs the firmware questions among them
(`machines/firmware.json`, `machines/disc-region.json` and `machines/named-cases.json`) through Tender's BIOS reading
and compares the level with the resolver's verdict, and `tests/adapters/test_launch_vectors.py` runs every launch
question among them through Tender's launch reading; each docstring says what it compares and what it leaves out.
`tests/adapters/test_atlas_launchability.py` asks a few of the launch machines (`machines/disc-opens.json`) one by one.
The other files come along because the folder is copied whole.

The copy lives under `tests/` rather than `backend/_vendor/`, because everything under `backend/` ships to users
([#2585](https://github.com/danielcopper/romm-tender/issues/2585) D1). It is licensed as the resolver is, MIT
(`backend/_vendor/atlas.LICENSE`).

## What pins it

- `vectors.SHA256SUMS`, generated here from the copy: upstream publishes a digest of the tarball, not of each file.
  `scripts/check_vendored_trees.py` checks it like a manifest under `backend/_vendor/`: every digest matches, and the
  copy holds exactly the files it names.
- The release line above. `test_firmware_vectors.py` reads the asset's name and digest from it and fails when the
  release is not the vendored resolver's version, or when `backend/_vendor/atlas.SHA256SUMS` does not list that digest
  for that asset.

What neither sees is whether the files are the asset's contents; steps 1 and 2 of the update procedure are the only
guard there.

The copy is excluded from markdownlint and `deno fmt` (`mise.toml`, `.github/workflows/ci.yml`, `deno.json`), so that
upstream's README in it stays byte for byte as upstream wrote it.

## Updating

Together with the resolver, never on its own: the vectors are the answers of one release.

1. Download the vectors asset and the release manifest of the release `backend/_vendor/atlas/` now holds, and check one
   against the other:

   ```sh
   gh release download <tag> -R danielcopper/emu-atlas -p 'emu-atlas-vectors-*.tar.gz' -p SHA256SUMS -D /tmp/vectors
   cd /tmp/vectors && sha256sum -c --ignore-missing SHA256SUMS
   ```

2. Delete `tests/adapters/atlas_vectors/vectors/` and unpack the asset in its place:

   ```sh
   tar xzf /tmp/vectors/emu-atlas-vectors-<tag>.tar.gz -C tests/adapters/atlas_vectors
   ```

3. Regenerate the manifest from the copy:

   ```sh
   cd tests/adapters/atlas_vectors && find vectors -type f -not -path '*/__pycache__/*' \
       | LC_ALL=C sort | xargs sha256sum > vectors.SHA256SUMS
   ```

4. Put the new asset name and its digest in the release line above.
5. Compare `fixture_machine` and `select` in `tests/_atlas_machines.py` with `fixture_machine` and `_select` in the new
   release's `tests/test_machine_vectors.py`, and bring them and the line numbers they cite up to date: a machine built
   differently from upstream's is not the machine the vector recorded.
6. Run `python scripts/check_vendored_trees.py`, `tests/services/test_firmware_vectors.py` and
   `tests/adapters/test_launch_vectors.py`. A new release may answer differently; what the firmware test's list of known
   mismatches may then do is its module docstring's, and a launch answer Tender reads otherwise than the resolver states
   it is a finding.
