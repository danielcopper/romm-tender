# Third-party notices

Tender's own code is GPL-3.0 (see [LICENSE](LICENSE)). The panel bundle it ships also contains code from the project
below, under that project's own licence.

## What this file covers, and where the rest is

**This file covers the JavaScript bundle, and nothing else.** A bundled npm dependency disappears into `dist/index.js`:
whoever receives the artefact never sees `node_modules`, so there is nowhere for its licence to sit except a file like
this one. Python and native code ship differently here, and each already carries its licence where a reader of that code
will find it — so listing them again would be a second copy to keep in step, not a second protection.

Tender also ships, and this file does not describe:

| What                                      | Licence | Where its text is                                                    |
| ----------------------------------------- | ------- | -------------------------------------------------------------------- |
| `backend/_vendor/atlas` (emu-atlas)       | MIT     | `backend/_vendor/atlas.LICENSE`, beside the tree                     |
| `backend/_vendor/vdf` (ValvePython/vdf)   | MIT     | `backend/_vendor/vdf/LICENSE`, inside the tree                       |
| `backend/native/libgavel-x86_64-linux.so` | MIT     | `backend/native/libgavel-x86_64-linux.so.LICENSE`, beside the binary |

The two vendored trees are not held the same way, and the difference is deliberate rather than an inconsistency:
`scripts/check_vendored_trees.py` asserts the sibling `atlas.LICENSE` because upstream's own wheel manifest names a
licence file to hold it against, while `vdf`'s manifest is generated here (the copy carries a local patch, so no
upstream manifest can ever match it) and its `LICENSE` is covered instead by the tree digests, which the same gate
requires to match the vendored file set exactly. Provenance for both is in
[`backend/_vendor/README.md`](backend/_vendor/README.md).

**`backend/native/` is pinned differently from the two vendored trees.** The `.so` is a compiled build of
[romm-gavel](https://github.com/danielcopper/romm-gavel), MIT and by this project's own author, shipped as a binary
rather than as source — so its licence is a sibling file, copied verbatim from upstream at the release the checksum
pins, and the checksum itself is upstream's own artifact and pins only the `.so`. What that means for the licence file,
and how to re-copy it on a bump, is at [`backend/native/README.md`](backend/native/README.md).

## @decky/ui

- **Version:** 4.12.0 — what `frontend/pnpm-lock.yaml` resolves, which is the version actually bundled.
  [`frontend/package.json`](frontend/package.json) declares the range `^4.11.6`, so it is the lock and not the manifest
  that fixes which bytes ship
- **Licence:** LGPL-2.1
- **Source:** <https://github.com/SteamDeckHomebrew/decky-frontend-lib>
- **Where the text is:** `LICENSE-@decky-ui.txt`, emitted next to the bundle by the build

The package is how Tender reaches Steam's own interface components. It is not a component library of its own: almost
every member is a search predicate that finds one of Steam's minified modules and hands it back, which is why Tender
uses it rather than keeping such predicates itself.

`dist/index.js` — the bundle for a machine with no Decky Loader — contains a copy of the package. `dist/globals.js`
contains its module-cache half. `dist/index-coexistence.js` contains none of it: that bundle takes the package from the
copy Decky Loader has already loaded, so it distributes nothing.

As LGPL-2.1 requires, you may replace the bundled copy with your own. Tender is open source and the version is pinned,
so doing so needs nothing from us: change the pin in `frontend/package.json` (or point it at your own build), run
`pnpm -C frontend install && pnpm -C frontend build`, and the bundle that comes out carries your copy instead.
