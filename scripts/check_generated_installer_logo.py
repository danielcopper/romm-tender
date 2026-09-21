#!/usr/bin/env python3
"""The installer's terminal mark is what its generator emits today.

The block between the markers in `install.sh` is written by
`scripts/logo/build.py --terminal` from `scripts/logo/terminal.py`, which reads
the mark's own shipped PNG. It says "do not edit" — and the whole reason it is
generated is that a hand-kept copy is the one that drifts away from the mark, so
the sentence saying it must not be edited is worth no more than a check that
notices when it was.

Two ways to fail, and both matter:

* someone edited the block, and the installer now draws something the mark is
  not a picture of;
* someone changed the mark or the palette and did not re-run the build, so the
  committed art is a render of colours that no longer ship.

Unlike its sibling `check_generated_tab_icon.py` this needs no formatter and no
`node_modules` — the block is bash, emitted in its final form — so it runs in
CI's `lint` job with the rest of the `scripts/check_*` family rather than in
`build`.
"""

from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
GENERATED = REPO / "install.sh"

sys.path.insert(0, str(REPO / "scripts" / "logo"))


def main() -> int:
    if not GENERATED.exists():
        print(f"FAIL: {GENERATED.relative_to(REPO)} does not exist")
        return 1

    import terminal  # noqa: PLC0415 — the generator only resolves once sys.path is set above

    current = GENERATED.read_text(encoding="utf-8")
    if terminal.replace_in(current) == current:
        print("OK: the installer's terminal mark matches what its generator emits.")
        return 0

    print(
        f"FAIL: the generated block in {GENERATED.relative_to(REPO)} is not what "
        "`scripts/logo/terminal.py` emits today.\n"
        "      Either it was edited by hand, or the mark changed and the build was not re-run.\n"
        "      Re-run `python3 scripts/logo/build.py --install --terminal` and commit the result."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
