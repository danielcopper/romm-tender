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

The comparison goes through prettier because the build does — `build_tab_icon`
formats the file before installing it, so the committed copy is the formatted
one. That is more than whitespace: prettier also rewrites the number literals
the generator emits, `at: 0.0000` to `at: 0.0`, so a raw `ts_module()` differs
from the committed file in the values themselves.

**It runs in CI's `build` job, not in `lint` with the rest of the
`scripts/check_*` family**, because prettier lives in the frontend's
`node_modules` and `lint` installs Python alone. Without prettier it reports
that it checked nothing rather than passing quietly, but it does not fail: a
contributor who has not run `mise run setup` is not the person this is aimed at.
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
        print(
            f"NOT CHECKED: {PRETTIER.relative_to(REPO)} is missing, so the tab glyph was not compared\n"
            "             against its generator. Run `mise run setup` to check it here."
        )
        return 0

    import tabicon  # noqa: PLC0415 — the generator only resolves once sys.path is set above

    # `--stdin-filepath` and not `--parser`: prettier resolves its configuration
    # from the path of the file it is formatting, and text arriving on stdin has
    # none — so `--parser typescript` alone formats to prettier's defaults while
    # the build, which writes the real file, formats to the repository's. The two
    # then differ in line breaks and the check fails on a correct file.
    formatted = subprocess.run(
        [str(PRETTIER), "--stdin-filepath", str(GENERATED)],
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
