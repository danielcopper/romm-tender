"""What a save sync answers when a conflict rule its use case holds refuses it, over the real wiring.

The three save-sync endpoints hand the conflict rules their own refusal details,
the keys ``.claude/rules/endpoints.md`` gives a save-sync refusal, and each
refusal the rules raise for them carries those keys. A slot switch holds the
same three rules and hands them none, and its refusal carries neither key. The
machine is put in each state the way ``test_conflict_refusals.py`` puts it, and
the endpoints are called as the panel calls them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from ._harness import hold_migration_pending, hold_prune_active, hold_update_in_progress

if TYPE_CHECKING:
    from collections.abc import Callable

_ROM = 41

# Each rule the three sync endpoints hold: how the machine is put in its state, and the reason it refuses with.
_RULES = [
    pytest.param(hold_update_in_progress, "blocked_by_update", id="update"),
    pytest.param(hold_migration_pending, "blocked_by_migration", id="migration"),
    pytest.param(hold_prune_active, "prune_active", id="cleanup"),
]

_SYNCS: list[Any] = [
    pytest.param(lambda endpoints: endpoints.pre_launch_sync(_ROM), {"synced": 0}, id="pre_launch_sync"),
    pytest.param(lambda endpoints: endpoints.sync_rom_saves(_ROM), {"synced": 0}, id="sync_rom_saves"),
    pytest.param(lambda endpoints: endpoints.sync_all_saves(), {"synced": 0, "conflicts": 0}, id="sync_all_saves"),
]


def _without_message(result: dict[str, Any]) -> dict[str, Any]:
    assert isinstance(result["message"], str)
    assert result["message"]
    return {key: value for key, value in result.items() if key != "message"}


@pytest.mark.parametrize(("hold_rule", "reason"), _RULES)
@pytest.mark.parametrize(("call", "keys"), _SYNCS)
async def test_a_sync_refused_by_a_rule_it_holds_answers_with_the_sync_keys(
    harness, call: Callable[[Any], Any], keys: dict[str, int], hold_rule: Callable[[Any], None], reason: str
):
    hold_rule(harness)

    result = await call(harness.endpoints)

    assert _without_message(result) == {"success": False, "reason": reason, **keys}


@pytest.mark.parametrize(("hold_rule", "reason"), _RULES)
async def test_a_slot_switch_refused_by_the_same_rule_answers_without_them(
    harness, hold_rule: Callable[[Any], None], reason: str
):
    hold_rule(harness)

    result = await harness.endpoints.switch_slot(_ROM, "default")

    assert _without_message(result) == {"success": False, "reason": reason}
