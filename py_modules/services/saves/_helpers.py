"""Logging wrappers around pure domain helpers consumed by the saves package."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from domain.iso_time import parse_iso_to_epoch
from domain.save_answer import pick_download_name
from domain.save_path import compute_local_save_target

if TYPE_CHECKING:
    from collections.abc import Sequence

_logger = logging.getLogger(__name__)


def local_save_target(server_save: dict[str, Any], rom_name: str, *, known_names: Sequence[str]) -> str:
    """Resolve the local filename for *server_save*, logging any sanitization.

    *known_names* is what the save resolver says this ROM's emulator writes, and
    it decides the name wherever it can: the path math builds
    ``<rom_name>.<server file_extension>``, which is not the name every core
    opens. Pass an empty sequence only where no answer is available — the caller
    then gets the path math alone, which is what this did for every save before
    the resolver could be asked.
    """
    result = compute_local_save_target(server_save, rom_name)
    if result.fallback_extension is not None:
        _logger.warning(
            "Sanitized server-supplied save target — invalid file_extension=%r; falling back to 'srm'",
            result.fallback_extension,
        )
    elif result.sanitized_from is not None:
        _logger.warning(
            "Sanitized server-supplied save target from %r to %r (file_extension=%r)",
            result.sanitized_from,
            result.filename,
            server_save.get("file_extension", "srm"),
        )
    return pick_download_name(known_names, result.filename)


def newest_server_saves_by_target(
    server_saves: list[dict[str, Any]], rom_name: str, *, known_names: Sequence[str]
) -> dict[str, dict[str, Any]]:
    """Pick the newest server save per canonical local target.

    Groups *server_saves* by the canonical on-disk filename each would download
    into (:func:`local_save_target` — ``<rom_name>.<ext>``, independent of the
    server row's own filename) and keeps only the newest by ``updated_at`` per
    target, so a target's carried save is deterministic rather than
    server-list-order dependent (#1058). Keyed by the canonical target filename.
    Shared by the slot-switch state sync and the setup wizard's legacy migration.
    """
    newest: dict[str, dict[str, Any]] = {}
    for ss in server_saves:
        target = local_save_target(ss, rom_name, known_names=known_names)
        current = newest.get(target)
        if current is None or (parse_iso_to_epoch(ss.get("updated_at")) or 0.0) > (
            parse_iso_to_epoch(current.get("updated_at")) or 0.0
        ):
            newest[target] = ss
    return newest
