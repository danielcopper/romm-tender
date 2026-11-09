"""Keeping each listed platform's record, which ``PlatformSystemService`` reads offline."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.platform_system import PLATFORM_IDS_KEY, encode_platform_ids, kept_platforms

if TYPE_CHECKING:
    import logging

    from services.protocols import UnitOfWorkFactory


def keep_platform_ids(
    uow_factory: UnitOfWorkFactory,
    logger: logging.Logger,
    platforms: list[dict[str, Any]],
    romm_version: str | None,
) -> None:
    """Replace the kept platforms with every listed one, read with *romm_version*. Blocking.

    A failed write is logged and leaves the run alone: the platforms kept
    before still answer, and one with nothing kept is read from RomM when asked.
    """
    try:
        with uow_factory() as uow:
            uow.kv_config.set(PLATFORM_IDS_KEY, encode_platform_ids(kept_platforms(platforms, romm_version)))
    except Exception as exc:
        logger.warning(f"Could not keep the platforms' ids: {exc}")
