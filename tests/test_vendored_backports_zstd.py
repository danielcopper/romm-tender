"""The vendored ``backports.zstd`` copy resolves under ``_vendor`` and carries the patch it is pinned with.

Upstream imports its own package by its absolute name (``import
backports.zstd._zstd``), and no top-level ``backports`` exists in this process,
so a verbatim copy does not import as ``_vendor.backports.zstd``. The local
patch makes every such import relative (``backend/_vendor/README.md``,
``backports``), and a re-copy that forgets it fails here rather than as a
codec that silently never registers on a device.

The import half runs only where the backport can load at all: it is a
``cp313`` build, and upstream refuses every interpreter from 3.14 on, where the
standard library's ``compression.zstd`` is the codec instead.
"""

from __future__ import annotations

import ast
import importlib
import pathlib
import sys

import pytest

VENDOR_DIR = pathlib.Path(__file__).resolve().parent.parent / "backend" / "_vendor"
TREE = VENDOR_DIR / "backports"

LOADS_HERE = sys.version_info[:2] == (3, 13)


def _absolute_self_imports(path: pathlib.Path) -> list[str]:
    """Every import in ``path`` that names ``backports`` as its first component, as ``<file>:<line>``."""
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            names = [node.module]
        else:
            continue
        if any(name == "backports" or name.startswith("backports.") for name in names):
            found.append(f"{path.relative_to(VENDOR_DIR).as_posix()}:{node.lineno}")
    return found


def test_the_manifest_pins_the_extension_and_both_licences_and_the_provenance_names_the_wheel() -> None:
    # The gate (scripts/check_vendored_trees.py) holds the tree equal to the
    # manifest; this holds the manifest to what has to be in it, and the
    # provenance entry to the wheel those bytes came from.
    pinned = {line.split("  ", 1)[1] for line in (VENDOR_DIR / "backports.SHA256SUMS").read_text().splitlines()}
    provenance = (
        (VENDOR_DIR / "README.md").read_text(encoding="utf-8").split("\n## backports\n", 1)[1].split("\n## ", 1)[0]
    )

    assert {
        "backports/zstd/__init__.py",
        "backports/zstd/_zstd.cpython-313-x86_64-linux-gnu.so",
        "backports/zstd/LICENSE.txt",
        "backports/zstd/LICENSE_zstd.txt",
    } <= pinned
    assert "**Version:** 1.7.0" in provenance
    assert "sha256:f3f4887a8a1fd1290017fe5a1d29a7d1dc5c57f9477fbd64f119316a7e3ae769" in provenance


def test_no_absolute_self_import_is_left_in_the_vendored_tree() -> None:
    sources = sorted(path for suffix in ("*.py", "*.pyi") for path in TREE.rglob(suffix))
    assert sources, "the vendored tree holds no Python source to check"

    left = [hit for path in sources for hit in _absolute_self_imports(path)]

    assert left == []


@pytest.mark.skipif(not LOADS_HERE, reason="the vendored build is cp313; upstream refuses every other interpreter")
def test_the_copy_imports_under_vendor_with_its_compiled_extension() -> None:
    codec = importlib.import_module("_vendor.backports.zstd")

    assert codec._zstd.__name__ == "_vendor.backports.zstd._zstd"
    assert codec._zstd.__file__ is not None
    assert codec._zstd.__file__.endswith(".so")
    assert codec.decompress(codec.compress(b"sealed catalogue" * 4)) == b"sealed catalogue" * 4
    assert "backports" not in sys.modules
