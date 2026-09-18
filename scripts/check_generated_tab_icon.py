#!/usr/bin/env python3
"""The panel's tab-glyph module is what its generator emits today.

`frontend/src/qam/tabIconArt.ts` is written by `scripts/logo/build.py --tab-icon`
from `scripts/logo/tabicon.py`, which draws the glyph with the mark's own
routines. Its header says "GENERATED, do not edit" and the whole reason it is
generated is that a hand-written copy is the one that drifts away from the mark —
so the sentence saying it must not be edited is worth no more than a check that
notices when it was.

Two ways to fail, and both matter:

* someone edited the generated file, and the mark now draws one glyph while the
  panel renders another;
* someone changed the mark or `tabicon.STRIP_GEOMETRY` and did not re-run the
  build, so the committed glyph is a render of a geometry that no longer exists.

The comparison goes through prettier, because the build does: the repository's
commit hook formats TypeScript, so the committed file is the formatted one and a
raw `ts_module()` would differ from it by whitespace alone.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
GENERATED = REPO / "frontend" / "src" / "qam" / "tabIconArt.ts"
PRETTIER = REPO / "frontend" / "node_modules" / ".bin" / "prettier"

sys.path.insert(0, str(REPO / "scripts" / "logo"))


def main() -> int:
    if not GENERATED.exists():
        print(f"FAIL: {GENERATED.relative_to(REPO)} does not exist")
        return 1
    if not PRETTIER.exists():
        print(f"SKIP: {PRETTIER.relative_to(REPO)} not found — run `mise run setup` to check this")
        return 0

    import tabicon  # noqa: PLC0415 — the generator only resolves once sys.path is set above

    formatted = subprocess.run(
        [str(PRETTIER), "--parser", "typescript"],
        input=tabicon.ts_module(),
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    if formatted == GENERATED.read_text():
        print("OK: the tab glyph matches what its generator emits.")
        return 0

    print(
        f"FAIL: {GENERATED.relative_to(REPO)} is not what `scripts/logo/tabicon.py` emits today.\n"
        "      Either it was edited by hand, or the mark changed and the build was not re-run.\n"
        "      Re-run `python3 scripts/logo/build.py --install --tab-icon` and commit the result."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
