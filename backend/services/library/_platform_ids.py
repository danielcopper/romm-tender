"""Keeping the listed platforms' ids, which ``PlatformSystemService`` reads offline."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.platform_system import PLATFORM_IDS_KEY, encode_platform_ids, platform_ids_by_slug

if TYPE_CHECKING:
    import logging

    from services.protocols import UnitOfWorkFactory


def keep_platform_ids(uow_factory: UnitOfWorkFactory, logger: logging.Logger, platforms: list[dict[str, Any]]) -> None:
    """Replace the kept ids with every listed platform's. Blocking.

    A failed write is logged and leaves the run alone: the ids kept before
    still answer, and a platform with none is read from RomM when asked.
    """
    try:
        with uow_factory() as uow:
            uow.kv_config.set(PLATFORM_IDS_KEY, encode_platform_ids(platform_ids_by_slug(platforms)))
    except Exception as exc:
        logger.warning(f"Could not keep the platforms' ids: {exc}")
