# Third-party notices

Tender's own code is GPL-3.0-only (see [LICENSE](LICENSE)). The bundles it ships also contain code from the projects
below, each under its own licence.

## What this file covers, and where the rest is

**This file covers the JavaScript bundles, and nothing else.** A bundled npm dependency disappears into `dist/`: whoever
receives the artefact never sees `node_modules`, so there is nowhere for its licence to sit except a file like this one.
Python and native code ship differently here, and each already carries its licence where a reader of that code will find
it — so listing them again would be a second copy to keep in step, not a second protection.

Tender also ships, and this file does not describe:

| What                                         | Licence                                          | Where its text is                                                                    |
| -------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------ |
| `backend/_vendor/atlas` (emu-atlas)          | MIT                                              | `backend/_vendor/atlas.LICENSE`, beside the tree                                     |
| `backend/_vendor/vdf` (ValvePython/vdf)      | MIT                                              | `backend/_vendor/vdf/LICENSE`, inside the tree                                       |
| `backend/_vendor/backports` (backports.zstd) | PSF-2.0, and BSD for the zstd library it carries | `backend/_vendor/backports/zstd/LICENSE.txt` and `LICENSE_zstd.txt`, inside the tree |
| `backend/native/libgavel-x86_64-linux.so`    | MIT                                              | `backend/native/libgavel-x86_64-linux.so.LICENSE`, beside the binary                 |

The vendored trees are not all held the same way, and the difference is deliberate rather than an inconsistency:
`scripts/check_vendored_trees.py` asserts the sibling `atlas.LICENSE` because upstream's own wheel manifest names a
licence file to hold it against, while the manifests of `vdf` and `backports` are generated here (each copy carries a
local patch, so no upstream manifest can ever match it) and their licence texts are covered instead by the tree digests,
which the same gate requires to match the vendored file set exactly. Provenance for all three is in
[`backend/_vendor/README.md`](backend/_vendor/README.md).

**`backend/native/` is pinned differently from the vendored trees.** The `.so` is a compiled build of
[romm-gavel](https://github.com/danielcopper/romm-gavel), MIT and by this project's own author, shipped as a binary
rather than as source — so its licence is a sibling file, copied verbatim from upstream at the release the checksum
pins, and the checksum itself is upstream's own artifact and pins only the `.so`. What that means for the licence file,
and how to re-copy it on a bump, is at [`backend/native/README.md`](backend/native/README.md).

## @decky/ui

- **Version:** the one `frontend/pnpm-lock.yaml` resolves at the release's tag, which is the version actually bundled;
  the source package below carries it in its name. [`frontend/package.json`](frontend/package.json) declares a range, so
  it is the lock and not the manifest that fixes which bytes ship
- **Licence:** LGPL-2.1
- **Source:** `decky-ui-<version>.tgz`, attached to every release from 1.0.0 on, on the
  [releases page](https://github.com/danielcopper/romm-tender/releases) beside Tender's own tarball, and checked against
  the lock's integrity hash before it is attached. It is the npm package the build used: the compiled `dist/` the
  bundles are built from, and the TypeScript it is compiled from under `src/`. Upstream's build configuration is in its
  repository: <https://github.com/SteamDeckHomebrew/decky-frontend-lib>
- **Where the text is:** `LICENSE-@decky-ui.txt`, which the build emits into `dist/` beside the bundles that carry it

The package is how Tender reaches Steam's own interface components. It is not a component library of its own: almost
every member is a search predicate that finds one of Steam's minified modules and hands it back, which is why Tender
uses it rather than keeping such predicates itself.

`dist/index.js` — the bundle for a machine with no Decky Loader — contains a copy of the package. `dist/globals.js`
contains its module-cache half. `dist/index-coexistence.js` contains none of it: that bundle takes the package from the
copy Decky Loader has already loaded, so it distributes nothing.

As LGPL-2.1 requires, you may replace the bundled copy with your own. Tender is open source and the version is fixed by
`frontend/pnpm-lock.yaml`, so doing so needs nothing from us: change the range in `frontend/package.json` (or point it
at your own build), run `pnpm -C frontend install && pnpm -C frontend build`, and the bundles that come out carry your
copy instead.

## react-icons

- **Version:** the one `frontend/pnpm-lock.yaml` resolves at the release's tag
- **Licence:** MIT
- **Source:** <https://github.com/react-icons/react-icons>
- **Where the text is:** below. A bundle carries a package's code but not its LICENSE file, so the notice travels in
  this file

`dist/index.js` and `dist/index-coexistence.js` each carry react-icons' icon runtime and the icons the panel imports
from it. `dist/globals.js` does not.

```text
Copyright 2018 kamijin_fanta <kamijin@live.jp>

Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files (the "Software"), to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
```

### Font Awesome Free icons

The one icon set Tender imports from react-icons is `react-icons/fa`, react-icons' conversion of the Font Awesome Free 5
SVG icons — built from upstream just past the 5.15.4 tag, as react-icons' README names it (`5.15.4-3-gafecf2a`) — into
JavaScript components. Those icons are not react-icons' own and carry a licence of their own:

- **Icons:** Font Awesome Free, by Fonticons, Inc. — <https://fontawesome.com>
- **Licence:** Creative Commons Attribution 4.0 International (CC BY 4.0) —
  <https://creativecommons.org/licenses/by/4.0/>; Font Awesome's own statement of it:
  <https://fontawesome.com/license/free>
- **Source:** <https://github.com/FortAwesome/Font-Awesome>
- **Changes:** converted from SVG into React components by react-icons; Tender does not change them further
