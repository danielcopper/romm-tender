"""In-memory ``PlatformSwitchesWriter`` for service tests: keeps the toggles in its own attributes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class FakePlatformSwitches:
    """Keeps the toggles in ``switches`` and those kept by RomM id in ``by_id``; ``None`` there is nothing kept."""

    def __init__(self) -> None:
        self.switches: dict[str, bool] = {}
        self.by_id: dict[str, Any] | None = None

    def replace(self, build: Callable[[Mapping[str, bool]], dict[str, bool]]) -> None:
        self.switches = build(self.switches)

    def carry_over(self, build: Callable[[Mapping[str, Any], Mapping[str, bool]], dict[str, bool] | None]) -> bool:
        if self.by_id is None:
            return False
        moved = build(self.by_id, self.switches)
        if moved is None:
            return False
        self.switches = moved
        self.by_id = None
        return True
