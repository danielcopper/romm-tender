"""Tests for ``services/protocols/__init__.py`` — the package's re-exports."""

from __future__ import annotations

import ast
from pathlib import Path

import services.protocols as protocols

_REPOSITORIES_PATH = Path(protocols.__file__).parent / "repositories.py"


def _protocols_defined_in_repositories() -> set[str]:
    """Every class ``repositories.py`` defines with ``Protocol`` among its bases."""
    tree = ast.parse(_REPOSITORIES_PATH.read_text(encoding="utf-8"))
    return {
        node.name
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases)
    }


class TestRepositoryProtocolsAreReExported:
    def test_the_scan_finds_the_repository_protocols(self):
        # Guards the check below from passing over an empty set.
        assert {"RomRepository", "KvConfigRepository"} <= _protocols_defined_in_repositories()

    def test_every_repository_protocol_is_importable_from_the_package(self):
        missing = {name for name in _protocols_defined_in_repositories() if not hasattr(protocols, name)}
        assert not missing, f"not re-exported from services.protocols: {sorted(missing)}"

    def test_every_repository_protocol_is_in_all(self):
        missing = _protocols_defined_in_repositories() - set(protocols.__all__)
        assert not missing, f"missing from services.protocols.__all__: {sorted(missing)}"
