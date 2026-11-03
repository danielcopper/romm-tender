"""Pure functions for building shortcut data dicts.

No I/O, no imports from services, adapters, or lib.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

from domain.rom_file import has_file_on_romm
from domain.sibling_group import compute_sibling_group_key
from domain.user_data_location import LAUNCHER_EXE_SUFFIX

# RetroDECK's flatpak application id — the single source of the string across
# Tender. The catalogue reader compares a launcher's app id against it.
RETRODECK_APP_ID = "net.retrodeck.retrodeck"


def shortcut_launch_options(app_id: int) -> str:
    """The launch options every bound shortcut carries: its own appId.

    Steam passes them to the launcher as its first argument, and the launcher
    claims the start hand-over by it; the command itself is asked at each start
    (``services/game_start.py``). The panel composes the same value for a
    shortcut it has just created, before Tender knows the appId
    (``frontend/src/utils/steamShortcuts.ts``).
    """
    return str(app_id)


# How an invocation is baked: through RetroDECK's ``run_game.sh -e``, by
# running the emulator's launcher directly in the sandbox (ADR-0019), or not at
# all, where the entry in effect is one Tender cannot bake or none is in effect
# though the system lists entries (#2195 D43).
RUN_GAME = "run_game"
DIRECT = "direct"
NOTHING = "nothing"


@dataclass(frozen=True)
class EmulatorInvocation:
    """What a ROM launches with, and how that is baked into its shortcut.

    Tender resolves one of these per ROM and bakes it into the shortcut's
    ``launch_options`` via :func:`resolve_emulator_invocation`. ``form`` says
    how:

    - :data:`RUN_GAME` → ``command`` is the ES-DE ``<command>`` as Tender bakes
      it (ending in ``%ROM%``, e.g. ``%EMULATOR_RPCS3% --no-gui %ROM%``), handed
      to RetroDECK's ``run_game.sh`` through ``-e``, which resolves
      ``%EMULATOR_*%`` and substitutes ``%ROM%`` with the trailing rom path.
      ``launcher``, where set, is the sandbox path the resolver states ES-DE
      runs for it; the renderer ignores it, and it is what a folder-boot install
      turns this into a ``direct`` invocation with.
    - :data:`DIRECT` → the folder-boot form (ADR-0019): ``command`` is the
      same full ES-DE standalone command AND ``launcher`` is the emulator's
      sandbox launcher path (e.g.
      ``/app/retrodeck/components/rpcs3/component_launcher.sh``). The renderer
      emits ``flatpak run … --command=<launcher> <app> <args>`` — running the
      emulator launcher directly INSIDE the sandbox, bypassing ``run_game.sh``,
      because ``run_game.sh`` reinterprets any directory ``%ROM%`` as an ES-DE
      "directory as a file" and can never launch a bare game folder. The ``<args>``
      are the standalone command's middle (``%EMULATOR_*%`` and ``%ROM%``
      stripped, e.g. ``--no-gui``); the game folder is appended by
      :func:`build_launch_options`.
    - :data:`NOTHING` → the entry in effect is one Tender cannot bake, or none
      is in effect though the system lists entries, and the shortcut gets the
      empty start command, so a start Tender does not see
      runs nothing (#2195 D43; why, docs/architecture/core-emulator-selection.md, "What starting comes to"). It names no
      emulator, so a read of the emulator a ROM launches with takes it for
      none, as it did the plain launch.

    ``core_so`` is the RetroArch core the resolver states the command loads,
    its bare name (no ``.so``), and ``None`` for anything else; it is read by
    the ``.so``-space consumers and never by the renderer. ``label`` is the
    ES-DE display label (diagnostics only).

    ``emulator`` is the resolver's identity for this emulator, and it is the one
    field that names it for every kind of entry — the join a firmware answer is scoped
    on. It is not a second spelling of ``label``: ES-DE lists one
    ``pcsx2_libretro.so`` under two labels, so a label identifies a launch row
    and not an emulator. ``None`` is an emulator the resolver could not identify,
    which no answer may be scoped to.
    """

    form: str
    label: str | None = None
    core_so: str | None = None
    command: str | None = None
    launcher: str | None = None
    emulator: str | None = None

    @classmethod
    def run_game(
        cls,
        command: str,
        label: str | None = None,
        emulator: str | None = None,
        *,
        core_so: str | None = None,
        launcher: str | None = None,
    ) -> EmulatorInvocation:
        """A *command* started through ``run_game.sh -e``, *core_so* the core it loads where it loads one."""
        return cls(form=RUN_GAME, label=label, command=command, launcher=launcher, emulator=emulator, core_so=core_so)

    @classmethod
    def direct(
        cls, command: str, launcher: str, label: str | None = None, emulator: str | None = None
    ) -> EmulatorInvocation:
        """A standalone emulator launched directly via its sandbox *launcher* (folder-boot form).

        *command* is the full ES-DE standalone ``<command>`` (its middle args are
        recovered at render time); *launcher* is the emulator's sandbox launcher
        path handed to ``flatpak run --command=``.
        """
        return cls(form=DIRECT, label=label, command=command, launcher=launcher, emulator=emulator)

    @classmethod
    def nothing(cls) -> EmulatorInvocation:
        """No entry Tender can bake is in effect though the system lists entries: the empty start command."""
        return cls(form=NOTHING)


def extract_version_metadata(rom: dict[str, Any]) -> dict[str, Any]:
    """Extract a raw RomM ROM dict's sibling-group identity + version dimensions.

    The server-derived facts (ADR-0021) persisted on the ``Rom`` aggregate: the
    sibling-group key plus the ``regions`` / ``languages`` / ``revision`` /
    ``tags`` / ``is_main_sibling`` dimensions. Shared by the sync shortcut build
    (:func:`build_shortcuts_data`) and the version-switch persist path so both
    derive the group key and ``is_main_sibling`` identically — one source of
    truth for the extraction, no drift between the two call sites.

    Prefers a ``sibling_group_key`` already on the dict (the incremental-skip
    path carries the authoritative key) and recomputes only when absent.
    ``is_main_sibling`` sits under ``rom_user``; a missing or ``null``
    ``rom_user`` degrades to ``False``.
    """
    return {
        "sibling_group_key": rom.get("sibling_group_key") or compute_sibling_group_key(rom),
        "regions": list(rom.get("regions") or []),
        "languages": list(rom.get("languages") or []),
        "revision": rom.get("revision") or "",
        "tags": list(rom.get("tags") or []),
        "is_main_sibling": bool((rom.get("rom_user") or {}).get("is_main_sibling", False)),
    }


def select_shortcuts_to_relocate(exes: Mapping[int, str], launcher_exe: str) -> list[int]:
    """Pick the app IDs whose ``exe`` is ours but is not *launcher_exe* yet.

    *exes* maps a live shortcut's app ID to the ``exe`` it currently carries —
    every non-Steam shortcut, ours and foreign. Ownership is the
    :data:`~domain.user_data_location.LAUNCHER_EXE_SUFFIX` ending and nothing
    else, which is what lets a shortcut of ours be recognised and repointed
    wherever it was written, without anything having recorded where it came
    from. A shortcut naming a launcher that does not end that way is foreign,
    whoever wrote it, and is never selected.

    Surrounding quotes are stripped before both tests. ``AddShortcut`` stores
    the path unquoted, so ours are bare; a hand-added shortcut can be quoted,
    and reading one as foreign would leave it behind while counting the run
    complete.

    Sorted, so two readings of one unchanged library produce the same plan in
    the same order — the app IDs cross to the frontend as a list it writes in
    order, and neither log line prints them, so nothing else would show a
    difference.
    """
    selected = []
    for app_id, exe in exes.items():
        bare = exe.strip('"')
        if bare.endswith(LAUNCHER_EXE_SUFFIX) and bare != launcher_exe:
            selected.append(app_id)
    return sorted(selected)


def build_shortcuts_data(roms: list[dict[str, Any]], launcher_exe: str) -> list[dict[str, Any]]:
    """Transform ROM list into shortcut data dicts for frontend AddShortcut calls.

    *launcher_exe* is the launcher every built shortcut's ``exe`` names, and the
    directory holding it is the ``start_dir`` written beside it. It is handed in
    rather than composed here because its home is outside every directory this
    program owns, which one start's install settles and only the composition
    root knows.

    No launch command is built: a shortcut's launch options are its own appId,
    which Steam assigns when the shortcut is created, so the panel writes them
    (``frontend/src/utils/steamShortcuts.ts``), and what starts the game is asked
    at each start (``services/game_start.py``).

    The sibling-group key (ADR-0021) and RomM's version dimensions (``regions`` /
    ``languages`` / ``revision`` / ``tags`` / ``is_main_sibling``) are derived
    from each raw ROM dict here and carried through so the commit persists them
    on the ``Rom`` aggregate. ``is_main_sibling`` sits under ``rom_user``; the
    lookup is guarded so a missing or ``null`` ``rom_user`` degrades to ``False``.
    """
    start_dir = os.path.dirname(launcher_exe)
    return [
        {
            "rom_id": rom["id"],
            "name": rom["name"],
            "fs_name": rom.get("fs_name", ""),
            # Alphabetical tie-break key for sibling-group representative
            # resolution (ADR-0021): RomM ships it on the list endpoint; fall
            # back to the filename stem when absent. No DB column — carried only
            # through the sync pipeline, never persisted.
            "fs_name_no_ext": rom.get("fs_name_no_ext") or os.path.splitext(rom.get("fs_name", ""))[0],
            "exe": launcher_exe,
            "start_dir": start_dir,
            "platform_name": rom.get("platform_name", "Unknown"),
            "platform_slug": rom.get("platform_slug", ""),
            "igdb_id": rom.get("igdb_id"),
            "sgdb_id": rom.get("sgdb_id"),
            "ra_id": rom.get("ra_id"),
            # Server-reported ROM size in bytes (#1395), carried through so the
            # commit persists it on the Rom aggregate (it rides the sync UPSERT
            # like the version dimensions). Absent/None → "size unknown".
            "fs_size_bytes": rom.get("fs_size_bytes"),
            # Persisted like the size; the sibling collapse reads it to keep a
            # ROM RomM holds no file for from becoming the bound version.
            "has_file_on_disk": has_file_on_romm(rom),
            "cover_path": "",
            # The sibling-group key + version dimensions (ADR-0021), extracted by
            # the shared helper so the sync build and the version-switch persist
            # path derive them identically. The incremental-skip path reconstructs
            # ROM dicts from persisted rows carrying the authoritative key (real
            # platform_id baked in) but no ``platform_id`` field, so the helper
            # prefers that key over recomputing (which would yield ``…:None`` and
            # split the group's bucket, #1296).
            **extract_version_metadata(rom),
        }
        for rom in roms
    ]
