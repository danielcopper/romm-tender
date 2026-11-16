"""Stand-in ``PlatformSwitchesWriter`` for wiring tests that never write the per-platform sync toggles."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class FakePlatformSwitches:
    """Writes nothing: a test that writes the toggles uses the real ``PlatformSwitches``."""

    def replace(self, build: Callable[[Mapping[str, Any]], dict[str, Any]]) -> None:
        raise AssertionError("this test does not write the per-platform sync toggles")

    def carry_over(self, build: Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any] | None]) -> bool:
        raise AssertionError("this test does not move the per-platform sync toggles")
