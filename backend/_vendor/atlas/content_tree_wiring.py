"""Content-tree wiring — the symlink pairs an arrangement's preparation promises.

RetroDECK files texture packs and mods in two hub directories and reaches
them from each emulator by replacing the emulator-side directory with a
symlink (``dir_prep``). That wiring is the *arrangement's* work, which is why
it cannot live on the texture or mods cards: a card states where the emulator
reads, and the installer's link target is provably not always that path
(Citra's card derives ``citra-emu/load/textures`` while the installer links
``saves/Citra/load/textures`` — issue #98's gap). The table here records the
pairs each arrangement version it was read at promises, with citations into
that version's shipped scripts, so :meth:`atlas.installations.RetroDeck.health`
can state when a hub tree exists that no emulator-side link reaches — the
upgraded-without-reset state, where content filed in the hub never reaches an
emulator.

The knowledge is version-pinned — one row set per version it was read at,
each citing that version's scripts — and the check fails closed: a machine
whose marker names any other version is measured against nothing, because the
promise of that version was never read.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ._data import packaged_text

WIRING_SCHEMA = 2

# The two content-tree families retrodeck.json declares hub roots for. A row
# outside them would name a hub the health check never resolves.
WIRING_FAMILIES = ("texture_packs", "mods")

# The roots an emulator-side path may hang off: two marker paths and the two
# XDG homes the flatpak pins. A closed set — the resolver beside this loader
# maps each word to a directory, and a word outside the set is a load error,
# never a guess.
WIRING_BASES = ("bios", "storage", "xdg-data", "xdg-config")


@dataclass(frozen=True, slots=True)
class WiringRow:
    """One ``dir_prep`` pair: the hub tree created, the emulator-side path linked."""

    family: str
    hub: str
    base: str
    path: str
    source: str


@dataclass(frozen=True, slots=True)
class ArrangementWiring:
    """One arrangement's promised pairs, pinned to the version they were read at."""

    version: str
    rows: tuple[WiringRow, ...]


def _expect_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def _expect_relpath(value: Any, where: str) -> str:
    text = _expect_str(value, where)
    if text.startswith("/") or text.endswith("/") or ".." in text.split("/"):
        raise ValueError(f"{where}: expected a clean relative path, got {text!r}")
    return text


def _row(entry: Any, where: str) -> WiringRow:
    if not isinstance(entry, dict) or set(entry) != {"family", "hub", "base", "path", "source"}:
        raise ValueError(
            f"{where}: a row names exactly family, hub, base, path and source, got {entry!r}"
        )
    family = _expect_str(entry["family"], f"{where}: family")
    if family not in WIRING_FAMILIES:
        raise ValueError(f"{where}: family must be one of {WIRING_FAMILIES}, got {family!r}")
    base = _expect_str(entry["base"], f"{where}: base")
    if base not in WIRING_BASES:
        raise ValueError(f"{where}: base must be one of {WIRING_BASES}, got {base!r}")
    return WiringRow(
        family=family,
        hub=_expect_relpath(entry["hub"], f"{where}: hub"),
        base=base,
        path=_expect_relpath(entry["path"], f"{where}: path"),
        source=_expect_str(entry["source"], f"{where}: source"),
    )


def _row_set(where: str, entry: Any) -> ArrangementWiring:
    if not isinstance(entry, dict) or set(entry) != {"version", "rows"}:
        raise ValueError(f"{where}: expected exactly 'version' and 'rows', got {entry!r}")
    rows_raw = entry["rows"]
    if not isinstance(rows_raw, list) or not rows_raw:
        raise ValueError(f"{where}: rows must be a non-empty list, got {rows_raw!r}")
    rows = tuple(_row(row, f"{where}: rows[{i}]") for i, row in enumerate(rows_raw))
    pairs = [(row.family, row.hub, row.base, row.path) for row in rows]
    if len(set(pairs)) != len(pairs):
        raise ValueError(f"{where}: rows repeat a (family, hub, base, path) pair")
    return ArrangementWiring(version=_expect_str(entry["version"], f"{where}: version"), rows=rows)


def _arrangement(kind: str, entry: Any) -> dict[str, ArrangementWiring]:
    where = f"content-tree wiring {kind!r}"
    if not isinstance(entry, list) or not entry:
        raise ValueError(f"{where}: expected a non-empty list of row sets, got {entry!r}")
    versions: dict[str, ArrangementWiring] = {}
    for i, raw in enumerate(entry):
        wiring = _row_set(f"{where}[{i}]", raw)
        if wiring.version in versions:
            raise ValueError(f"{where}: version {wiring.version!r} has two row sets")
        versions[wiring.version] = wiring
    return versions


def load_content_tree_wiring(text: str | None = None) -> dict[str, dict[str, ArrangementWiring]]:
    """Load the packaged wiring table (or *text* when supplied, for tests).

    Arrangement kind → version read at → that version's wiring.
    """
    if text is None:
        text = packaged_text("content_tree_wiring.json")
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != WIRING_SCHEMA:
        raise ValueError(
            f"content_tree_wiring: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {WIRING_SCHEMA})"
        )
    arrangements = raw.get("arrangements", {})
    if not isinstance(arrangements, dict):
        raise ValueError(f"content_tree_wiring: arrangements must be an object, got {arrangements!r}")
    return {kind: _arrangement(kind, entry) for kind, entry in arrangements.items()}


_PACKAGED: dict[str, dict[str, ArrangementWiring]] | None = None


def lookup_content_tree_wiring(kind: str, version: str) -> ArrangementWiring | None:
    """The packaged wiring one arrangement kind promised at *version*, or ``None``.

    Exact on both: a version the table was never read at has no wiring, however
    close it is to one that was.
    """
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_content_tree_wiring()
    return _PACKAGED.get(kind, {}).get(version)
