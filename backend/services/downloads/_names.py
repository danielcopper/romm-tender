"""Where a download lands on disk: its safe file and folder names, and the partial file it streams into.

Shared by the start of a download, which weighs and gates the paths, and by the
transfer, which writes them, so both name a ROM's file and folder the same way.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from domain.rom_files import (
    TMP_EXT,
    ZIP_TMP_EXT,
    is_multi_file_download,
    resolve_extract_dir_name,
    resolve_local_file_name,
    synthetic_rom_name,
)
from domain.served_shape import is_zip_name
from lib.path_safety import coerce_safe_component

if TYPE_CHECKING:
    import logging

    from services.protocols import DownloadFileStore


def safe_local_file_name(rom_detail: dict[str, Any], logger: logging.Logger) -> str:
    """The on-disk name for this ROM: the server's, coerced to one safe component.

    Strips any directory portion and rejects a degenerate result — ``..``
    would escape the platform directory and ``.`` or ``""`` would resolve to
    it — falling back to the synthetic ``rom_<id>`` identity. Both repairs
    are logged, because the name the user sees on disk then differs from the
    one the server states.
    """
    file_name, files_missing = resolve_local_file_name(rom_detail)
    if files_missing:
        logger.warning(f"has_nested_single_file=true but files list is empty; falling back to fs_name='{file_name}'")
    safe_name, name_changed = coerce_safe_component(file_name, synthetic_rom_name(rom_detail))
    if name_changed:
        logger.warning(f"Sanitized fs_name from '{file_name}' to '{safe_name}'")
    return safe_name


def safe_extract_dir_name(rom_detail: dict[str, Any], logger: logging.Logger) -> str:
    """Resolve the sanitized base name for the extract dir an unpacked ROM lands in.

    Names the directory after the ROM's identity via
    ``resolve_extract_dir_name`` (never ``files[0]``), then coerces the
    server-supplied value to a single safe path component
    (``coerce_safe_component``): a directory portion (``../evil``, an
    absolute path) is stripped to its basename, AND a degenerate result
    (``..``/``.``/empty/whitespace — which would resolve to the roms root
    or the platform dir and turn a later ``remove_tree`` into a
    library-wide delete) falls back to the synthetic ``rom_<id>`` identity.
    Mirrors :func:`safe_local_file_name`.
    """
    raw = resolve_extract_dir_name(rom_detail)
    safe, changed = coerce_safe_component(raw, synthetic_rom_name(rom_detail))
    if changed:
        logger.warning(f"Sanitized extract dir name from '{raw}' to '{safe}'")
    return safe


def extract_dir_name_for(rom_detail: dict[str, Any], target_path: str, logger: logging.Logger) -> str:
    """The extract dir's name where a download may be unpacked — a multi-file ROM or a single zip — else ``""``."""
    may_unpack = is_multi_file_download(rom_detail) or is_zip_name(target_path)
    return safe_extract_dir_name(rom_detail, logger) if may_unpack else ""


def partial_tmp_size(download_file_store: DownloadFileStore, target_path: str, rom_detail: dict[str, Any]) -> int:
    """Bytes already on disk in the partial ``.tmp`` for *target_path*.

    Single-file ROMs stream to ``target_path + .tmp``; multi-file ROMs to
    ``target_path + .zip.tmp``. Returns 0 when no partial exists (the file
    store reports a missing path as size 0).
    """
    tmp_ext = ZIP_TMP_EXT if is_multi_file_download(rom_detail) else TMP_EXT
    return download_file_store.file_size(target_path + tmp_ext)
