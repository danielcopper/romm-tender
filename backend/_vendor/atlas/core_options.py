"""Reading a libretro core option the way RetroArch reads one.

One question with one answer and two callers, so it lives beside neither. The
save, texture and mod families ask it about a rule card's governing option
while a content path and a core are known; the firmware route asks it about the
options a core composes a firmware name out of, where no content is known at
all. Both must walk the same files in the same order, because the value that
governs is a property of RetroArch's own priority rather than of who is asking
— and a second copy of that walk is a second answer waiting to disagree with
the first.

What a caller supplies is where the walk starts (:class:`CoreOptionsChain`) and
what it is asking about; what it gets back is the value, the provenance
sentence that names where it came from, and the file a caller would edit to
change it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .machine import Machine
from .oddities import RetiredOption
from .placement import Caveat
from .retroarch_cfg import parse_cfg_text


@dataclass(frozen=True, slots=True)
class CoreOptionsChain:
    """Where RetroArch would read a core option on this installation, resolved once.

    The preamble every option read shares, assembled by whoever read the
    configuration rather than by the route that asks a question of it — the
    firmware context carries one so that a route reading an option re-reads no
    config of its own.

    ``global_file`` is the options file ``core_options_path`` names, or the
    platform default beside ``retroarch.cfg``. ``override_config_dir`` is the
    tree the per-core ``.opt`` sits in, one directory per ``library_name``.
    ``per_core_options`` is ``global_core_options`` inverted, which is how
    RetroArch itself reads it: the per-core file governs unless the global one
    was switched on. ``caveats`` are what resolving those three cost — a line
    the parser refused, a value outside RetroArch's boolean vocabulary, a
    sandbox spelling with no host path — stated by the route that actually
    reads an option, because an installation whose answer never consults one
    was never degraded by them.
    """

    global_file: str
    override_config_dir: str
    per_core_options: bool
    core_dir: str | None = None
    """Where the installed cores live, or ``None`` where the installation did not establish it.

    The per-core file is named for a core's ``library_name``, which lives only
    in the binary — so reading an option the per-core file could govern means
    loading the core and asking it, the same read RetroArch performs. This is
    where that binary is found, and it is the directory the enumeration was
    limited to, so the probe asks the build the answer is about.
    """
    caveats: tuple[Caveat, ...] = ()


def _option_file_candidates(
    *,
    override_config_dir: str,
    global_file: str,
    library_name: str | None,
    content_dir_name: str | None,
    rom_stem: str | None,
    game_specific_options: bool,
    per_core_options: bool,
) -> list[str]:
    """The options files that could govern an option, in RetroArch's priority order.

    Game ``.opt``, folder ``.opt``, per-core ``.opt`` (when
    ``global_core_options`` is off), then the global options file — the same
    order ``validate_per_core_options`` walks.

    Every path but the global file is keyed by ``library_name``, so an unknown
    one leaves only the global file to read. That is not a degradation this
    function has to state any more: ``library_name`` is unknown exactly when the
    core could not be queried, and :func:`atlas.installations._select_card` does not let a card
    reach this code path at all in that case.
    """
    candidates: list[str] = []
    if library_name and game_specific_options:
        if rom_stem:
            candidates.append(os.path.join(override_config_dir, library_name, f"{rom_stem}.opt"))
        if content_dir_name:
            candidates.append(os.path.join(override_config_dir, library_name, f"{content_dir_name}.opt"))
    if library_name and per_core_options:
        candidates.append(os.path.join(override_config_dir, library_name, f"{library_name}.opt"))
    candidates.append(global_file)
    return candidates


def core_options_value(
    machine: Machine,
    *,
    override_config_dir: str,
    global_file: str,
    library_name: str | None,
    content_dir_name: str | None,
    rom_stem: str | None,
    option_key: str,
    option_default: str | None,
    game_specific_options: bool,
    per_core_options: bool,
    retired: tuple[RetiredOption, ...] = (),
) -> tuple[str | None, str, str, tuple[tuple[RetiredOption, str], ...]]:
    """Read a core option the way RetroArch does — first existing file is THE source.

    Priority (``runloop.c`` ``validate_per_core_options``): game ``.opt``,
    folder ``.opt``, per-core ``.opt`` (when ``global_core_options`` is off),
    then *global_file*. A key absent from the governing file falls back to the
    core default — it does not fall through to another file.

    Returns ``(value, provenance, options_file, retired_found)``, where
    ``options_file`` is the file a caller would edit to change the option. The
    value is ``None`` when the governing file states none and *option_default*
    is ``None`` too: the core itself did not state a default and none is
    recorded, so what governs here was never established. Substituting the
    empty string would put a value nobody read into the answer's own
    provenance.

    ``retired_found`` are the entries of *retired* the governing file carries,
    with the value each states — read off the same parse the value lookup
    already made, so stating them costs no second read of anything. Only the
    governing file is checked: a stale entry in a file RetroArch would not
    read for this core is dead twice over, and naming it would tell a caller
    to prune a file that decides nothing here.
    """
    candidates = _option_file_candidates(
        override_config_dir=override_config_dir,
        global_file=global_file,
        library_name=library_name,
        content_dir_name=content_dir_name,
        rom_stem=rom_stem,
        game_specific_options=game_specific_options,
        per_core_options=per_core_options,
    )

    for path in candidates:
        text = machine.read_text(path).text
        if text is None:
            continue
        parsed = parse_cfg_text(text)
        retired_found = tuple(
            (option, parsed[option.key]) for option in retired if option.key in parsed
        )
        if option_key in parsed:
            return (
                parsed[option_key],
                f'{os.path.basename(path)}: {option_key} = "{parsed[option_key]}"',
                path,
                retired_found,
            )
        if option_default is None:
            return (
                None,
                f"{os.path.basename(path)} has no entry for {option_key} and no default for it was "
                "established — the installed core states none and none is recorded",
                path,
                retired_found,
            )
        return (
            option_default,
            f'core default: {option_key} = "{option_default}" ({os.path.basename(path)} has no entry)',
            path,
            retired_found,
        )
    if option_default is None:
        return (
            None,
            f"no options file states {option_key} and no default for it was established — the "
            "installed core states none and none is recorded",
            global_file,
            (),
        )
    return (
        option_default,
        f'core default: {option_key} = "{option_default}" (no options file present)',
        global_file,
        (),
    )
