#!/usr/bin/env python3
"""The installer's terminal mark is what its generator emits today.

The block between the markers in `install.sh` is written by
`scripts/logo/build.py --terminal` from `scripts/logo/terminal.py`, which draws
the mark and rasterises it. It says "do not edit" — and the whole reason it is
generated is that a hand-kept copy is the one that drifts away from the mark, so
the sentence saying it must not be edited is worth no more than a check that
notices when it was.

Two ways to fail, and both matter:

* someone edited the block, and the installer now draws something the mark is
  not a picture of;
* someone changed the mark or the palette and did not re-run the build, so the
  committed art is a render of colours that no longer ship.

It needs `rsvg-convert`, because the mark is drawn in the terminal's own pose
rather than read from a shipped raster. It runs in CI's `lint` job, which
installs it, rather than in `build` where its sibling
`check_generated_tab_icon.py` lives for prettier's sake. A contributor who has
not got it is told the block was not compared rather than that it is wrong —
the same answer that sibling gives without prettier, and for the same reason.
"""

from __future__ import annotations

import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
GENERATED = REPO / "install.sh"

sys.path.insert(0, str(REPO / "scripts" / "logo"))


def main() -> int:
    if not GENERATED.exists():
        print(f"FAIL: {GENERATED.relative_to(REPO)} does not exist")
        return 1
    if shutil.which("rsvg-convert") is None:
        print(
            "NOT CHECKED: rsvg-convert is missing, so the installer's mark was not compared\n"
            "             against its generator. Install librsvg to check it here."
        )
        return 0

    import terminal  # noqa: PLC0415 — the generator only resolves once sys.path is set above

    stale = []
    current = GENERATED.read_text(encoding="utf-8")
    try:
        rewritten = terminal.replace_in(current)
    except terminal.MarkersMissing as missing:
        print(
            f"FAIL: {GENERATED.relative_to(REPO)} carries no block for the mark to go in.\n"
            f"      Put the two markers back — the first is `{missing}` — and re-run\n"
            "      `python3 scripts/logo/build.py --install --terminal`."
        )
        return 1
    if rewritten != current:
        stale.append(f"the generated block in {GENERATED.relative_to(REPO)}")
    for destination, render in terminal.WORDMARK_FILES:
        if not destination.exists() or destination.read_text(encoding="utf-8") != render():
            stale.append(str(destination.relative_to(REPO)))

    if not stale:
        print("OK: the installer's terminal mark and the wordmark match what their generator emits.")
        return 0

    print(
        "FAIL: not what `scripts/logo/terminal.py` emits today:\n"
        + "".join(f"      - {name}\n" for name in stale)
        + "      Either it was edited by hand, or the mark changed and the build was not re-run.\n"
        "      Re-run `python3 scripts/logo/build.py --install --terminal` and commit the result."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
