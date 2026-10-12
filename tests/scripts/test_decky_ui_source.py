"""Tests for ``scripts/decky_ui_source.py``.

The script is loaded via ``importlib`` because ``scripts/`` is not on
``sys.path``. No case touches the network. The first and the last case call
``locked_package`` directly, the last on the repository's own lock. The cases
that run ``main`` read a lock written under ``tmp_path`` and replace the
module's ``fetch`` with one answering bytes the case chose — except the one
about a download that breaks off, which keeps ``fetch`` and replaces the
``urlopen`` beneath it.
"""

from __future__ import annotations

import base64
import hashlib
import http.client
import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from types import ModuleType

_REPO = Path(__file__).resolve().parents[2]
_SCRIPT_PATH = _REPO / "scripts" / "decky_ui_source.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("decky_ui_source", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


source = _load_module()

_PACKAGE_BYTES = b"the package as the registry serves it"


def _integrity(data: bytes) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode("ascii")


def _lock(*versions: str, integrity: str | None = None) -> str:
    """A pnpm lock in the shape ``frontend/pnpm-lock.yaml`` has, resolving ``@decky/ui`` at ``versions``."""
    resolved = integrity or _integrity(_PACKAGE_BYTES)
    importers = "".join(f"      '@decky/ui':\n        specifier: ^4.11.6\n        version: {v}\n" for v in versions[:1])
    packages = "".join(f"  '@decky/ui@{v}':\n    resolution: {{integrity: {resolved}}}\n\n" for v in versions)
    snapshots = "".join(f"  '@decky/ui@{v}': {{}}\n\n" for v in versions)
    return (
        "lockfileVersion: '9.0'\n\n"
        "importers:\n\n  .:\n    dependencies:\n"
        f"{importers}\n"
        "packages:\n\n"
        f"{packages}"
        "  react@19.2.8:\n"
        f"    resolution: {{integrity: {_integrity(b'react')}}}\n\n"
        "snapshots:\n\n"
        f"{snapshots}"
    )


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lock: str, served: bytes) -> tuple[int, Path]:
    lock_path = tmp_path / "pnpm-lock.yaml"
    lock_path.write_text(lock, encoding="utf-8")
    out = tmp_path / "build"
    monkeypatch.setattr(source, "fetch", lambda _url: served)
    return source.main(["--lock", str(lock_path), "--out", str(out)]), out


def test_it_reads_the_version_and_integrity_from_the_lock() -> None:
    assert source.locked_package(_lock("4.12.1")) == ("4.12.1", _integrity(_PACKAGE_BYTES))


def test_a_package_matching_the_integrity_is_written_as_the_asset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return _PACKAGE_BYTES

    monkeypatch.setattr(source, "fetch", fetch)
    lock_path = tmp_path / "pnpm-lock.yaml"
    lock_path.write_text(_lock("4.12.1"), encoding="utf-8")

    assert source.main(["--lock", str(lock_path), "--out", str(tmp_path / "build")]) == 0
    assert fetched == ["https://registry.npmjs.org/@decky/ui/-/ui-4.12.1.tgz"]
    assert [path.name for path in (tmp_path / "build").iterdir()] == ["decky-ui-4.12.1.tgz"]
    assert (tmp_path / "build" / "decky-ui-4.12.1.tgz").read_bytes() == _PACKAGE_BYTES


def test_a_package_with_other_bytes_is_refused_and_leaves_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(monkeypatch, tmp_path, _lock("4.12.1"), _PACKAGE_BYTES + b"!")

    assert code == 1
    assert "the lock says" in capsys.readouterr().err
    assert not out.exists() or not any(out.iterdir())


def test_a_download_that_breaks_off_is_refused_and_leaves_no_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    class _BrokenOff:
        def __enter__(self) -> _BrokenOff:
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def read(self) -> bytes:
            raise http.client.IncompleteRead(_PACKAGE_BYTES[:5], len(_PACKAGE_BYTES) - 5)

    monkeypatch.setattr(source.urllib.request, "urlopen", lambda *_args, **_kwargs: _BrokenOff())
    lock_path = tmp_path / "pnpm-lock.yaml"
    lock_path.write_text(_lock("4.12.1"), encoding="utf-8")
    out = tmp_path / "build"

    assert source.main(["--lock", str(lock_path), "--out", str(out)]) == 1
    assert "ERROR: no @decky/ui source asset" in capsys.readouterr().err
    assert not out.exists()


@pytest.mark.parametrize(
    ("versions", "reason"),
    [((), "resolves no @decky/ui"), (("4.12.1", "4.12.0"), "more than one version: 4.12.1, 4.12.0")],
    ids=["no-entry", "two-versions"],
)
def test_a_lock_without_exactly_one_version_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    versions: tuple[str, ...],
    reason: str,
) -> None:
    code, out = _run(monkeypatch, tmp_path, _lock(*versions), _PACKAGE_BYTES)

    assert code == 1
    assert reason in capsys.readouterr().err
    assert not out.exists()


def test_the_repositorys_own_lock_resolves_one_version_with_a_sha512_integrity() -> None:
    version, integrity = source.locked_package((_REPO / "frontend" / "pnpm-lock.yaml").read_text(encoding="utf-8"))

    assert version
    assert integrity.startswith("sha512-")
    assert len(base64.b64decode(integrity.removeprefix("sha512-"), validate=True)) == 64
