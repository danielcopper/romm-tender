"""Which bound rows a platform unit's convergence takes the shortcut off, the running games left out.

Shared by :class:`~services.library.unit_apply.UnitApplier` and
:class:`~services.library.sync_preview.SyncPreview`, so the removals a preview
counts are the ones its apply makes, each with Steam's running apps read at its
own time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.sync_diff import converge_bound_groups

if TYPE_CHECKING:
    import logging
    from collections.abc import Sequence
    from collections.abc import Set as AbstractSet

    from domain.sync_diff import ConvergedBinding
    from services.protocols import RunningRomIdsFn


async def converge_unless_running(
    listed: Sequence[dict[str, Any]],
    registry: dict[str, dict[str, Any]],
    installed_rom_ids: AbstractSet[int],
    *,
    platform_slugs: AbstractSet[str],
    preferred_region: str,
    running_rom_ids: RunningRomIdsFn,
    logger: logging.Logger,
) -> list[ConvergedBinding]:
    """:func:`~domain.sync_diff.converge_bound_groups`, leaving out a group whose shortcut Steam lists as running.

    Steam's running apps are read only when a group would converge, so a sync
    with nothing to converge asks Steam nothing. A group left out stays as it
    is and converges on a later run (#2645 D5).
    """
    converged = converge_bound_groups(
        listed, registry, installed_rom_ids, platform_slugs=platform_slugs, preferred_region=preferred_region
    )
    if not converged:
        return converged
    running = await running_rom_ids()
    if not running:
        return converged
    left = converge_bound_groups(
        listed,
        registry,
        installed_rom_ids,
        platform_slugs=platform_slugs,
        preferred_region=preferred_region,
        running_rom_ids=running,
    )
    if len(left) < len(converged):
        logger.info(f"Convergence: {len(converged) - len(left)} shortcut(s) of a running game stay until a later sync")
    return left
