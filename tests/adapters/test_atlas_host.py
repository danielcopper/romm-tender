"""Tests for the atlas host grants — what this runtime hands the vendored resolver.

The resolver's own decisions are upstream's and are not re-tested here. What is
under test is the host's half: which paths this plugin hands over, which it
refuses, and that the answer says where a probe's interpreter came from — the
one place the grant's absence is ever visible.

Every case reads the answer back through the resolver's own
:func:`_vendor.atlas.core_probe_interpreter` rather than a stand-in, because the
registration is process-global state inside the vendored package and a stand-in
would prove nothing about what a probe would actually start.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest
from _vendor.atlas import core_probe_interpreter, register_core_probe_interpreter

from adapters import atlas_host

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture(autouse=True)
def _forget_the_grant() -> Iterator[None]:
    """Clear the registration after every test in this file.

    The slot lives in the vendored package for the life of the process, so a
    grant left standing here would silently change what every later test in the
    session probes with — including the suites that drive the real resolver over
    whatever RetroDECK the machine has.
    """
    yield
    register_core_probe_interpreter(None)


@pytest.fixture
def frozen_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The runtime the grant exists for: one atlas will derive no interpreter from.

    Decky Loader is a PyInstaller build, and atlas spawns ``sys.executable``
    only where the running program is plainly an interpreter. Without this the
    ambient CPython running the suite answers, and a refused grant would be
    indistinguishable from a granted one.
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)


class TestGrantCoreProbeInterpreter:
    def test_an_existing_executable_is_handed_to_the_resolver(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_host: None
    ) -> None:
        interpreter = tmp_path / "python3"
        interpreter.write_text("#!/bin/sh\n", encoding="utf-8")
        interpreter.chmod(0o755)
        monkeypatch.setattr(atlas_host, "_HOST_INTERPRETER", str(interpreter))

        report = atlas_host.grant_core_probe_interpreter()

        granted = core_probe_interpreter()
        assert granted is not None
        assert granted.path == str(interpreter)
        assert granted.registered is True
        assert str(interpreter) in report
        assert "granted by the plugin" in report

    def test_a_path_that_is_not_there_is_granted_to_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_host: None
    ) -> None:
        missing = tmp_path / "python3"
        monkeypatch.setattr(atlas_host, "_HOST_INTERPRETER", str(missing))

        report = atlas_host.grant_core_probe_interpreter()

        # A probe would start nothing at all: the grant was refused here, and a
        # frozen host leaves atlas nothing to derive. Registering the path
        # regardless answers with it, so this is the case that fails when the
        # existence check goes.
        assert core_probe_interpreter() is None
        assert str(missing) not in report
        assert "no interpreter" in report

    def test_a_file_without_the_execute_bit_is_granted_to_nothing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frozen_host: None
    ) -> None:
        unrunnable = tmp_path / "python3"
        unrunnable.write_text("#!/bin/sh\n", encoding="utf-8")
        unrunnable.chmod(0o644)
        monkeypatch.setattr(atlas_host, "_HOST_INTERPRETER", str(unrunnable))

        report = atlas_host.grant_core_probe_interpreter()

        # It is there, so the execute bit is the only thing between this and the
        # granted case: the spawn would fail, and atlas takes a registered path
        # at its word rather than checking it.
        assert core_probe_interpreter() is None
        assert str(unrunnable) not in report

    def test_the_answer_names_atlas_own_reading_where_nothing_was_granted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # An unfrozen host that IS an interpreter: atlas derives one itself, and
        # the answer has to say so rather than claim this grant made it.
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        monkeypatch.setattr(sys, "executable", "/opt/cpython/bin/python3")
        monkeypatch.setattr(atlas_host, "_HOST_INTERPRETER", str(tmp_path / "python3"))

        report = atlas_host.grant_core_probe_interpreter()

        granted = core_probe_interpreter()
        assert granted is not None
        assert granted.registered is False
        assert "/opt/cpython/bin/python3" in report
        assert "atlas's own" in report
