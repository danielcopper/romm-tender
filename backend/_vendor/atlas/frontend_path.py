"""The PATH a frontend on the host searches, and the directory it runs in — the lookup's world knowledge (#84 part 3).

ES-DE searches a ``systempath`` find rule, and the first word of a command
without an emulator token, over the directories of its own process's
``PATH``, and tests a relative first word against its working directory. A
frontend on the host gets both from whatever started it, which no file on the
machine states. So they are packaged here, keyed by the frontend's
installation kind, each directory with the citation of the line that puts it
there (``atlas/data/frontend_path.json``). An answer that searched the
``PATH``, or tried a word in the working directory, says so: the lookup
carries ``launch-path-assumed`` beside its verdict. A frontend with no
working directory here has none atlas can state.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ._data import packaged_text

FRONTEND_PATH_SCHEMA = 1

# A directory under the user's home is spelled with this prefix in the table,
# because the home is the machine's and the table is not.
_HOME_PREFIX = "~/"


@dataclass(frozen=True, slots=True)
class PathDirectory:
    """One directory atlas assumes for a frontend — of its ``PATH``, or the one it runs in — and the line that puts
    it there.
    """

    directory: str
    citation: str


def load_frontend_path(text: str | None = None) -> Mapping[str, tuple[PathDirectory, ...]]:
    """Load the packaged search paths by frontend (or *text* when supplied, for tests)."""
    return {name: _search_path(name, entry) for name, entry in _frontends(text).items()}


def load_frontend_working_directories(text: str | None = None) -> Mapping[str, PathDirectory]:
    """Load the packaged working directories by frontend — only the frontends that carry one (or *text*, for tests)."""
    loaded: dict[str, PathDirectory] = {}
    for name, entry in _frontends(text).items():
        _search_path(name, entry)
        if _WORKING_DIRECTORY in entry:
            loaded[name] = _directory(f"frontend_path {name!r} {_WORKING_DIRECTORY}", entry[_WORKING_DIRECTORY])
    return loaded


def _frontends(text: str | None) -> Mapping[str, Any]:
    if text is None:
        text = packaged_text("frontend_path.json")
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != FRONTEND_PATH_SCHEMA:
        raise ValueError(
            f"frontend_path: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {FRONTEND_PATH_SCHEMA})"
        )
    frontends = raw.get("frontends")
    if not isinstance(frontends, dict) or not frontends:
        raise ValueError(f"frontend_path: expected a non-empty 'frontends' object, got {frontends!r}")
    return frontends


_SEARCH_PATH = "search_path"
_WORKING_DIRECTORY = "working_directory"


def _search_path(name: str, entry: Any) -> tuple[PathDirectory, ...]:
    where = f"frontend_path {name!r}"
    if not isinstance(entry, dict) or _SEARCH_PATH not in entry or not set(entry) <= {_SEARCH_PATH, _WORKING_DIRECTORY}:
        raise ValueError(
            f"{where}: an entry names exactly 'search_path', and 'working_directory' where it states one, got {entry!r}"
        )
    directories = entry["search_path"]
    if not isinstance(directories, list) or not directories:
        raise ValueError(f"{where}: search_path must be a non-empty list")
    return tuple(_directory(f"{where} search_path[{index}]", item) for index, item in enumerate(directories))


def _directory(where: str, item: Any) -> PathDirectory:
    """One directory — validated, never coerced: a spelling the lookup can join a rule entry to, and its citation."""
    if not isinstance(item, dict) or set(item) != {"directory", "citation"}:
        raise ValueError(f"{where}: a directory names exactly 'directory' and 'citation', got {item!r}")
    directory = item["directory"]
    citation = item["citation"]
    if not isinstance(directory, str) or not (directory.startswith("/") or directory.startswith(_HOME_PREFIX)):
        raise ValueError(f"{where}: a directory is absolute or under ~/, got {directory!r}")
    if not isinstance(citation, str) or not citation.strip():
        raise ValueError(f"{where}: every directory carries a citation")
    return PathDirectory(directory, citation)


_PACKAGED: Mapping[str, tuple[PathDirectory, ...]] | None = None
_PACKAGED_WORKING: Mapping[str, PathDirectory] | None = None


def frontend_search_path(frontend: str, home: str) -> tuple[str, ...]:
    """The packaged ``PATH`` of *frontend*'s process, in order, with ``~/`` expanded against *home*.

    Raises ``KeyError`` for a frontend the table does not carry: only a
    handle whose frontend has an entry asks.
    """
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_frontend_path()
    return tuple(_expanded(entry.directory, home) for entry in _PACKAGED[frontend])


def _expanded(directory: str, home: str) -> str:
    if directory.startswith(_HOME_PREFIX):
        return os.path.join(home, directory[len(_HOME_PREFIX) :])
    return directory


def frontend_working_directory(frontend: str, home: str) -> str | None:
    """The packaged working directory of *frontend*'s process, ``~/`` expanded against *home* — ``None`` where the
    table states none.
    """
    global _PACKAGED_WORKING
    if _PACKAGED_WORKING is None:
        _PACKAGED_WORKING = load_frontend_working_directories()
    entry = _PACKAGED_WORKING.get(frontend)
    return None if entry is None else _expanded(entry.directory, home)
