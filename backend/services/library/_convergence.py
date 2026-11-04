"""Which bound rows a convergence takes the shortcut off, the running games left out.

A platform unit converges over its listing (:func:`converge_unless_running`),
the run's collection deliveries over the bound rows they bring in
(:func:`converge_collection_deliveries`, #2661).
:class:`~services.library.unit_apply.UnitApplier` and
:class:`~services.library.sync_preview.SyncPreview` both call them, so the removals a preview
counts are the ones its apply makes, each with Steam's running apps read at its
own time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.sync_diff import brought_in_by_delivery, converge_bound_groups, stored_version

if TYPE_CHECKING:
    import asyncio
    import logging
    from collections.abc import Collection, Mapping, Sequence
    from collections.abc import Set as AbstractSet

    from domain.sync_diff import ConvergedBinding
    from services.library.local_library_reader import LocalLibraryReader
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
    hidden_rom_ids: Collection[int] | None = None,
) -> list[ConvergedBinding]:
    """:func:`~domain.sync_diff.converge_bound_groups`, leaving out a group whose shortcut Steam lists as running.

    Steam's running apps are read only when a group would converge, so a sync
    with nothing to converge asks Steam nothing. A group left out stays as it
    is and converges on a later run (#2645 D5). *hidden_rom_ids* is the run's
    hidden set, ``None`` where RomM's answer could not be read, which takes
    nothing for hidden.
    """
    hidden = hidden_rom_ids or frozenset()
    converged = converge_bound_groups(
        listed,
        registry,
        installed_rom_ids,
        platform_slugs=platform_slugs,
        preferred_region=preferred_region,
        hidden_rom_ids=hidden,
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
        hidden_rom_ids=hidden,
    )
    if len(left) < len(converged):
        logger.info(f"Convergence: {len(converged) - len(left)} shortcut(s) of a running game stay until a later sync")
    return left


async def converge_collection_deliveries(
    delivered: Sequence[dict[str, Any]],
    sibling_stubs: Mapping[int, Sequence[Mapping[str, Any]]],
    registry: dict[str, dict[str, Any]],
    *,
    loop: asyncio.AbstractEventLoop,
    local_library_reader: LocalLibraryReader,
    platform_slugs: AbstractSet[str],
    preferred_region: str,
    running_rom_ids: RunningRomIdsFn,
    logger: logging.Logger,
    hidden_rom_ids: Collection[int] | None = None,
) -> list[ConvergedBinding]:
    """:func:`converge_unless_running` over the bound rows the run's collection deliveries bring in (#2661 D1, D4).

    *delivered* is the built entry of every ROM the run's collection units
    fetched, *sibling_stubs* each one's ``sibling_roms``, *registry* every bound
    row, and *platform_slugs* the platforms the sync leaves off. A brought-in
    row no collection delivered is ranked by its stored version fields
    (:func:`~domain.sync_diff.stored_version`), unless the user hid it
    (*hidden_rom_ids*, the run's hidden set or ``None``): RomM still lists
    such a version among its siblings, and it ranks as one RomM no longer
    lists, as on a platform (#2502 D8). Every brought-in row's install is read
    from its install record, as the preview and the apply both read it here.
    """
    hidden = hidden_rom_ids or frozenset()
    brought_in = brought_in_by_delivery(delivered, sibling_stubs, registry)
    if not brought_in.rows:
        return []
    stored = await loop.run_in_executor(None, local_library_reader.do_read_stored_rows, set(brought_in.undelivered))
    installed = await loop.run_in_executor(
        None, local_library_reader.do_read_installed_rom_ids, {int(rid) for rid in brought_in.rows}
    )
    versions = [
        stored_version(stored[rom_id], is_main_sibling=is_main)
        for rom_id, is_main in brought_in.undelivered.items()
        if rom_id in stored and rom_id not in hidden
    ]
    return await converge_unless_running(
        [*delivered, *versions],
        brought_in.rows,
        installed,
        platform_slugs=platform_slugs,
        preferred_region=preferred_region,
        running_rom_ids=running_rom_ids,
        logger=logger,
        hidden_rom_ids=hidden,
    )
