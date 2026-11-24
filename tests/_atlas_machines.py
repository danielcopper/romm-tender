"""The resolver's recorded machines, built and asked as upstream's runner does.

Shared by the tests that run the vendored copy's vectors
(``tests/adapters/atlas_vectors/``) through Tender's reading of an answer. A
machine built differently from upstream's is not the machine the vector
recorded, so the machine and the installation asked follow emu-atlas ``tests/test_machine_vectors.py``
at the release the copy's README names, and an answer is compared in the
projection its ``tests/platform_projection.py`` computes, which the copy's
``vectors/README.md`` defines.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from _vendor import atlas
from _vendor.atlas.machine import FixtureMachine

from domain.platform_system import SystemArgument, SystemPair

MACHINES = Path(__file__).resolve().parent / "adapters" / "atlas_vectors" / "vectors" / "machines"


def fixture_machine(inp: dict[str, Any]) -> FixtureMachine:
    """The vector's machine, as upstream's ``fixture_machine`` builds it (lines 70-86)."""
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
    """The installation a question names, else the first detected — upstream's ``_select`` (lines 89-96)."""
    if selector is None:
        assert installs, "no installation detected to answer the query"
        return installs[0]
    return next(install for install in installs if install.kind == selector)


def asked_system(system: Any) -> SystemArgument:
    """A vector's system as Tender hands one to a question: a name as it stands, a pair, or several pairs, as tuples.

    The vectors spell a pair as a JSON list, which no answer is kept under.
    """
    if isinstance(system, str):
        return system
    if isinstance(system[0], str):
        return SystemPair(*system)
    return tuple(SystemPair(*pair) for pair in system)


def vectors_asking(question: str) -> list[tuple[str, dict[str, Any]]]:
    """Every recorded machine whose expectations answer *question* (``launchable``, …), with its file's stem."""
    return [
        (path.stem, vector)
        for path in sorted(MACHINES.glob("*.json"))
        for vector in json.loads(path.read_text(encoding="utf-8"))["vectors"]
        if question in vector["expected"]
    ]


def vector_named(question: str, stem: str, name: str) -> dict[str, Any]:
    """The recorded machine *name* of ``machines/<stem>.json`` whose expectations answer *question*."""
    return next(vector for each, vector in vectors_asking(question) if each == stem and vector["name"] == name)


def asked_launch_command(vector: dict[str, Any]) -> Any:
    """The resolver's live answer to the vector's launch-command question, asked as the vector records it."""
    inp = vector["input"]
    query = inp["launch_command_query"]
    installation = select(atlas.detect(inp["home"], fixture_machine(inp)), query.get("installation"))
    return installation.launch_command(query["system"], query["content_path"], label=query.get("label"))


def _projected_sources(answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "source": answer["source"],
            "status": answer["status"],
            "keys": [{"key": key["key"], "relation": key["relation"]} for key in answer["keys"]],
        }
        for answer in answers
    ]


def translation_projection(contract: dict[str, Any]) -> dict[str, Any]:
    """A serialized translation without its citations and source versions — upstream's ``translation_projection``."""
    return {
        "table_version": contract["table_version"],
        "pairs": [{**pair, "sources": _projected_sources(pair["sources"])} for pair in contract["pairs"]],
        "intersection_status": contract["intersection_status"],
        "intersection": _projected_sources(contract["intersection"]),
        "conflict": contract["conflict"],
        "caveats": contract["caveats"],
    }


def with_projected_resolutions(node: Any) -> Any:
    """A serialized answer with every resolution's translation projected — upstream's ``with_projected_resolutions``."""
    if isinstance(node, list):
        return [with_projected_resolutions(item) for item in node]
    if not isinstance(node, dict):
        return node
    projected = {key: with_projected_resolutions(value) for key, value in node.items()}
    resolution = node.get("resolution")
    if isinstance(resolution, dict) and resolution.get("translation") is not None:
        projected["resolution"] = {**resolution, "translation": translation_projection(resolution["translation"])}
    return projected
