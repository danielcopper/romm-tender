"""The resolver's recorded machines, built and asked as upstream's runner does.

Shared by the tests that run the vendored copy's vectors
(``tests/adapters/atlas_vectors/``) through Tender's reading of an answer. A
machine built differently from upstream's is not the machine the vector
recorded, so both functions follow emu-atlas ``tests/test_machine_vectors.py``
at the release the copy's README names.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from _vendor.atlas.machine import FixtureMachine

MACHINES = Path(__file__).resolve().parent / "adapters" / "atlas_vectors" / "vectors" / "machines"


def fixture_machine(inp: dict[str, Any]) -> FixtureMachine:
    """The vector's machine, as upstream's ``fixture_machine`` builds it (lines 68-84)."""
    return FixtureMachine(
        inp["files"],
        symlinks=inp.get("symlinks"),
        cores=inp.get("cores"),
        dirs=inp.get("dirs"),
        inaccessible=inp.get("inaccessible"),
        unlistable=inp.get("unlistable"),
        appimages=inp.get("appimages"),
        appimage_stamps=inp.get("appimage_stamps"),
        ps2_bios_headers=inp.get("ps2_bios_headers"),
        ps3_files=inp.get("ps3_files"),
        disc_identities=inp.get("disc_identities"),
        archive_texts=inp.get("archive_texts"),
        archives=inp.get("archives"),
        whdload_slaves=inp.get("whdload_slaves"),
    )


def select(installs: list[Any], selector: str | None) -> Any:
    """The installation a question names, else the first detected — upstream's ``_select`` (lines 87-94)."""
    if selector is None:
        assert installs, "no installation detected to answer the query"
        return installs[0]
    return next(install for install in installs if install.kind == selector)


def vectors_asking(question: str) -> list[tuple[str, dict[str, Any]]]:
    """Every recorded machine whose expectations answer *question* (``launchable``, …), with its file's stem."""
    return [
        (path.stem, vector)
        for path in sorted(MACHINES.glob("*.json"))
        for vector in json.loads(path.read_text(encoding="utf-8"))["vectors"]
        if question in vector["expected"]
    ]
