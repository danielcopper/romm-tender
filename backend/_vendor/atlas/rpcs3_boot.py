"""What RPCS3 boots from the path an ES-DE entry hands it — RPCS3's own boot, read step for step.

Every citation below is at RPCS3 build d5213de6, the build RetroDECK ships
and the RPCS3 save card pins, which ``tests/test_standalone_build_tripwire.py``
holds against RetroDECK's deployed binary. EmuDeck runs RPCS3 from an
AppImage atlas does not open, so its build is not pinned, and the reading is
applied to it as that revision boots. ES-DE's
``ps3`` system declares four entries; two of them hand RPCS3 a path,
``%EMULATOR_RPCS3% --no-gui %ROM%`` (the Directory and the ISO entry), and this
module judges that path the way RPCS3 boots it:

1. ``rpcs3 --no-gui <path>`` makes the path absolute without resolving links
   (``rpcs3/rpcs3.cpp:1337-1340``) and hands it to ``Emulator::BootGame``. A
   directory is searched for exactly four boot files, in order —
   ``EBOOT.BIN``, ``USRDIR/EBOOT.BIN``, ``USRDIR/ISO.BIN.EDAT``,
   ``PS3_GAME/USRDIR/EBOOT.BIN`` (``GetElfPathFromDir``,
   ``rpcs3/Emu/System.cpp:1036-1063``) — and none of them is nothing to boot:
   RPCS3 looks no further down (:1131-1147). Anything else goes to
   ``Emulator::Load`` as it is (:1118-1129).
2. ``Load`` opens a file named ``.SAVESTAT``, ``.zst`` or ``.gz`` as a
   savestate to resume where it carries the ``RPCS3SAV`` magic
   (``make_savestate_reader``, ``rpcs3/Emu/savestate_utils.cpp:178-227``),
   which this reading does not follow; an ISO 9660 image by its content, not its name (``is_iso_file``,
   ``rpcs3/Loader/ISO.cpp:63-118``; ``Load``, System.cpp:1674-1698); and
   anything else as an executable, a SELF or an ELF (:2543-2559, :2561-2815), refusing
   every other file (:2802-2815).
3. An image boots ``PS3_GAME/USRDIR/EBOOT.BIN`` inside it, mounted as the disc.
   Whether it is encrypted, and whether a key decrypts it, is RPCS3's key
   check (``check_disc_key``, System.cpp:1165-1189): an image declaring one
   region has nothing encrypted (``iso_file_decryption::init``,
   ISO.cpp:514-551 — only odd regions are) and a 3k3y image that says it is
   decrypted needs no key (:606-650, :425-456); every other image is
   :meth:`_Reading._iso_encryption`'s question — the blocks inside it
   (:mod:`atlas.ps3_iso`) and the key file RPCS3 looks for (issue #618).
4. An executable's disc root is ``GetBdvdDir``'s (System.cpp:4884-4927): of
   the directories above it, the outermost one named exactly ``PS3_GAME`` or
   ``PS3_GMnn`` (``is_ps3_gm_dir_name``, ``rpcs3/Emu/system_utils.hpp:86-89``)
   whose parent holds a ``PS3_DISC.SFB`` opening with ``.SFB``
   (``IsValidSfb``, :5052-5067). That parent is mounted as ``/dev_bdvd`` and
   the game boots (:2189-2230).
5. Without a root, the PARAM.SFO decides (:1713-1746, with
   ``get_sfo_dir_from_game_path`` at ``system_utils.cpp:516-566``): the
   directory above the executable's own, or its ``PS3_GAME`` where a
   ``PS3_DISC.SFB`` file stands in it. ``DG``, a disc game, fails to mount
   (:2322-2326); ``GD`` boots through RPCS3's own game registry, which is not
   read here (:2151-2187); ``1P`` and ``PE`` take routes this reading does not
   follow; every other category — and an SFO the loader keeps nothing of,
   which RPCS3 reads as no category at all (``psf::load``,
   ``rpcs3/Loader/PSF.cpp:186-196``, :273-280) — boots without a disc
   (:2272-2286).
6. An executable inside RPCS3's own ``dev_hdd0/game`` takes other routes
   (``from_hdd0_game``, :2100-2137 and :2287-2321): a disc game found there is
   moved, and one without a root boots through the registry. That is stated
   as not established rather than read.

Two things a boot can still fail on are outside this reading, and stated in
the guide: an executable is taken by its first four bytes, so a SELF that
needs a licence it does not find fails later on decryption (:2543-2559), and
an ELF its loaders do not take is refused there too.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Literal

from .launch import ENABLESHORTCUTS, PROBE_HIT, PROBE_MISS, PROBE_UNKNOWN, RUNINBACKGROUND
from .launch_command import CommandView, rom_file
from .machine import PS3_FILE_MISSING, PS3_FILE_OK, Ps3FileResult
from .placement import Caveat
from .ps3_iso import BLOCKS_CLEAR, BLOCKS_NONE
from .ps3_file import (
    ISO_REGIONS_MAXIMUM,
    ISO_REGIONS_MINIMUM,
    MAGIC_SFB,
    WATERMARK_DECRYPTED,
    WATERMARK_ENCRYPTED,
    Ps3FileFacts,
    Ps3IsoHeader,
)

# What a boot reading established about one entry's take on one path.
Outcome = Literal["boots", "refuses", "unestablished"]
BOOTS: Outcome = "boots"
REFUSES: Outcome = "refuses"
UNESTABLISHED: Outcome = "unestablished"

# The reasons, one code each. A refusal is established from RPCS3's own boot;
# an unestablished answer names the step this reading could not take.
CAVEAT_BOOT_PATH_MISSING: Final = "boot-path-missing"
CAVEAT_BOOT_PATH_UNESTABLISHED: Final = "boot-path-unestablished"
CAVEAT_BOOT_NOTHING_FOUND: Final = "boot-nothing-found"
CAVEAT_BOOT_FILE_UNBOOTABLE: Final = "boot-file-unbootable"
CAVEAT_BOOT_DISC_ROOT_MISSING: Final = "boot-disc-root-missing"
CAVEAT_BOOT_REGISTRY_UNREAD: Final = "boot-registry-unread"
CAVEAT_BOOT_CATEGORY_UNESTABLISHED: Final = "boot-category-unestablished"
CAVEAT_BOOT_INSIDE_INTERNAL_DRIVE: Final = "boot-inside-internal-drive"
CAVEAT_BOOT_DRIVE_UNRESOLVED: Final = "boot-drive-unresolved"
CAVEAT_BOOT_SAVESTATE_UNREAD: Final = "boot-savestate-unread"
CAVEAT_BOOT_ISO_REGIONS_INVALID: Final = "boot-iso-regions-invalid"
CAVEAT_BOOT_ISO_ENCRYPTION_UNREAD: Final = "boot-iso-encryption-unread"
CAVEAT_BOOT_ISO_KEY_UNTESTED: Final = "boot-iso-key-untested"
CAVEAT_BOOT_KEY_FOLDER_UNRESOLVED: Final = "boot-key-folder-unresolved"
CAVEAT_BOOT_DISC_KEY_MISSING: Final = "boot-disc-key-missing"
CAVEAT_BOOT_DISC_DIR_CONFIGURED: Final = "boot-disc-dir-configured"
CAVEAT_BOOT_DISC_DIR_UNRESOLVED: Final = "boot-disc-dir-unresolved"
CAVEAT_ENTRY_TAKES_NO_DIRECTORY: Final = "entry-takes-no-directory"

# The registry a serial boot and a GD title resolve through
# (``games_config``, System.cpp:1526-1614, :2151-2187).
GAME_REGISTRY: Final = "games.yml"

_EMULATOR = "%EMULATOR_RPCS3%"
_SHELL = "%EMULATOR_OS-SHELL%"
_ROM = "%ROM%"
_NO_GUI = "--no-gui"
# The serial argument ES-DE's Game Serial entry hands RPCS3, as the catalogue spells it.
_GAME_ID_ARGUMENT = "%RPCS3_GAMEID%:%INJECT%=%BASENAME%.ps3"
_BOOT_LIST = ("EBOOT.BIN", "USRDIR/EBOOT.BIN", "USRDIR/ISO.BIN.EDAT", "PS3_GAME/USRDIR/EBOOT.BIN")
_SAVESTATE_SUFFIXES = (".SAVESTAT", ".zst", ".gz")
_KEY_SUFFIXES = (".dkey", ".key")
_ASCII_DIGITS = "0123456789"
_GAME_DIR = "PS3_GAME"
_SFB = "PS3_DISC.SFB"
_SFO = "PARAM.SFO"
_DISC_GAME = "DG"
_REGISTERED_GAME = "GD"
_UNFOLLOWED_CATEGORIES = ("1P", "PE")
_SOURCE = "RPCS3 d5213de6"

ReadFile = Callable[[str], Ps3FileResult]


@dataclass(frozen=True, slots=True)
class Dirs:
    """RPCS3's own directories the reading asks about, in RPCS3's own spelling — ``None`` where one is not known.

    ``drive`` is its internal drive, ``dev_hdd0``, read from ``vfs.yml`` the
    way the save answer reads it; ``config`` is that file, ``None`` where no
    file was read because RPCS3's configuration tree is not established.
    ``redump`` is the folder RPCS3 looks for disc keys in,
    ``get_data_dir() + "redump/"`` (system_utils.cpp:256-259), its data
    directory being the configuration directory's ``data/`` (:291-294).
    ``bdvd`` is the disc directory ``vfs.yml`` names, or its default
    ``$(EmulatorDir)dev_bdvd/`` (vfs_config.h:18), ``None`` where it is not
    established: the drive not known, or the file stating the directory, or
    the variable its default is composed off, in a way not read.
    """

    drive: str | None
    config: str | None
    redump: str | None = None
    bdvd: str | None = None


@dataclass(frozen=True, slots=True)
class BootReading:
    """One entry's take on one path: the outcome, and what boots where it does.

    ``file`` is the path RPCS3's loader is handed — the boot file a directory
    yields, or the path itself — and ``disc_root`` what it mounts as the disc:
    the directory holding ``PS3_DISC.SFB``, the image for an ISO, ``None`` for
    a game that boots without one. Both are set only where the outcome is
    :data:`BOOTS`.
    """

    outcome: Outcome
    sources: tuple[str, ...] = ()
    caveats: tuple[Caveat, ...] = ()
    file: str | None = None
    disc_root: str | None = None

    def __post_init__(self) -> None:
        if (self.file is not None) != (self.outcome == BOOTS):
            raise ValueError("BootReading: a boot file stands exactly where the outcome is boots")
        if self.disc_root is not None and self.file is None:
            raise ValueError("BootReading: a disc root stands only beside a boot file")


def _words(command: str) -> list[str]:
    """*command*'s words with the flags ES-DE removes before it runs anything (``FileData.cpp:1057-1093``)."""
    return [word for word in command.split() if word not in (ENABLESHORTCUTS, RUNINBACKGROUND)]


def hands_path(command: str) -> bool:
    """Whether *command* is the entry that hands RPCS3 the file's path: ``%EMULATOR_RPCS3% --no-gui %ROM%``."""
    return _words(command) == [_EMULATOR, _NO_GUI, _ROM]


def boots_by_serial(command: str) -> bool:
    """Whether *command* is the entry that hands RPCS3 a serial to look up rather than a path:
    ``%EMULATOR_RPCS3% --no-gui %RPCS3_GAMEID%:%INJECT%=%BASENAME%.ps3``."""
    return _words(command) == [_EMULATOR, _NO_GUI, _GAME_ID_ARGUMENT]


def shell_runs_rom(command: str) -> bool:
    """Whether *command* hands the file's path to the shell: ``%EMULATOR_OS-SHELL% %ROM%``."""
    return _words(command) == [_SHELL, _ROM]


def by_serial(label: str) -> BootReading:
    """The Game Serial entry: RPCS3 resolves the serial through its own registry, which is not read here."""
    return BootReading(
        UNESTABLISHED,
        caveats=(
            Caveat(
                CAVEAT_BOOT_REGISTRY_UNREAD,
                f"{label!r} hands RPCS3 a serial, which it resolves through its own game registry and the "
                f"games installed on its internal drive, the last match winning ({_SOURCE}, System.cpp:1526-1614) "
                "— that registry is not read here, so what boots is not established",
                {"entry": label, "registry": GAME_REGISTRY},
            ),
        ),
    )


def shell_reading(label: str, content_path: str, view: CommandView) -> BootReading | None:
    """An OS-SHELL entry handed a directory refuses it — ``None`` wherever that is not what ``%ROM%`` names.

    The shell runs a file; a directory is nothing it can run. Whether
    ``%ROM%`` is one is ES-DE's own reading of the content path, a directory
    holding a file of its own name taken as that file
    (:func:`atlas.launch_command.rom_file`). Every other path is left to the
    entry's ordinary reading.
    """
    path = rom_file(content_path, view)
    if path is None or view.is_directory(path) != PROBE_HIT:
        return None
    return BootReading(
        REFUSES,
        (f"entry {label!r}: its command hands the shell {path}, a directory, which no shell can run",),
        (
            Caveat(
                CAVEAT_ENTRY_TAKES_NO_DIRECTORY,
                f"{label!r} runs %ROM% through the shell, and {path} is a directory — the shell cannot "
                "start it, so this entry launches nothing for it",
                {"entry": label, "path": path},
            ),
        ),
    )


class _Reading:
    """One path judged the way RPCS3 boots it, every probe through the frontend's view."""

    def __init__(self, label: str, view: CommandView, read_file: ReadFile, dirs: Callable[[], Dirs]) -> None:
        self._label = label
        self._view = view
        self._read_file = read_file
        self._dirs = dirs

    def _caveat(self, code: str, message: str, path: str) -> Caveat:
        return Caveat(code, message, {"entry": self._label, "path": path})

    def _unestablished(self, code: str, message: str, path: str) -> BootReading:
        return BootReading(UNESTABLISHED, caveats=(self._caveat(code, message, path),))

    def _refuses(self, code: str, message: str, path: str) -> BootReading:
        return BootReading(
            REFUSES, (f"entry {self._label!r}: {message} ({_SOURCE})",), (self._caveat(code, message, path),)
        )

    def _untold(self, path: str) -> BootReading:
        return self._unestablished(
            CAVEAT_BOOT_PATH_UNESTABLISHED,
            f"RPCS3 would look at {path}, and atlas cannot tell what is there or read it",
            path,
        )

    def content(self, content_path: str) -> BootReading:
        """The content path's ``%ROM%``, then that path booted."""
        path = rom_file(content_path, self._view)
        return self._untold(content_path) if path is None else self._handed(path)

    def _handed(self, path: str) -> BootReading:
        """The path ``BootGame`` is handed: a directory searched for its boot file, or a file loaded as it is."""
        directory = self._view.is_directory(path)
        if directory == PROBE_UNKNOWN:
            return self._untold(path)
        if directory == PROBE_HIT:
            return self._directory(path)
        present = self._view.regular_file(path)
        if present == PROBE_UNKNOWN:
            return self._untold(path)
        if present == PROBE_MISS:
            return self._unestablished(
                CAVEAT_BOOT_PATH_MISSING,
                f"nothing RPCS3 could boot is at {path} — what the entry boots once the content is there is "
                "not established",
                path,
            )
        return self._file(path)

    def _directory(self, path: str) -> BootReading:
        """``GetElfPathFromDir``: the first of the four boot files present, none of them nothing to boot."""
        for relative in _BOOT_LIST:
            candidate = f"{path}/{relative}"
            probe = self._view.regular_file(candidate)
            if probe == PROBE_UNKNOWN:
                return self._untold(candidate)
            if probe == PROBE_HIT:
                return self._file(candidate)
        return self._refuses(
            CAVEAT_BOOT_NOTHING_FOUND,
            f"RPCS3 looks for {', '.join(_BOOT_LIST)} in {path} and nowhere else (GetElfPathFromDir, "
            "System.cpp:1036-1063), and none of them is there — nothing to boot",
            path,
        )

    def _facts(self, path: str) -> Ps3FileFacts | BootReading | None:
        """The facts of the file at *path* — ``None`` where no file is there, a reading where that cannot be told."""
        probe = self._view.regular_file(path)
        if probe == PROBE_UNKNOWN:
            return self._untold(path)
        if probe == PROBE_MISS:
            return None
        host = self._view.host_path(path)
        if host is None:
            return self._untold(path)
        result = self._read_file(host)
        if result.status == PS3_FILE_MISSING:
            return None
        if result.status != PS3_FILE_OK or result.facts is None:
            return self._untold(path)
        return result.facts

    def _file(self, path: str) -> BootReading:
        """``Load`` over a file: a savestate, an image, an executable, or nothing it boots."""
        if path.endswith(_SAVESTATE_SUFFIXES):
            return self._unestablished(
                CAVEAT_BOOT_SAVESTATE_UNREAD,
                f"RPCS3 opens {path} as a savestate to resume where it carries the RPCS3SAV magic "
                "(make_savestate_reader, savestate_utils.cpp:178-227), which this reading "
                "does not follow",
                path,
            )
        facts = self._facts(path)
        if isinstance(facts, BootReading):
            return facts
        if facts is None:
            return self._untold(path)
        if facts.iso is not None:
            return self._image(path, facts.iso)
        if not facts.executable:
            return self._refuses(
                CAVEAT_BOOT_FILE_UNBOOTABLE,
                f"{path} is neither an ISO 9660 image nor a SELF or ELF, and RPCS3 refuses any other file "
                "(Emulator::Load, System.cpp:2802-2815)",
                path,
            )
        return self._executable(path)

    def _image(self, path: str, iso: Ps3IsoHeader) -> BootReading:
        """An image: its region count and watermark, then the key check where those leave one."""
        regions = iso.regions
        if not ISO_REGIONS_MINIMUM <= regions <= ISO_REGIONS_MAXIMUM:
            return self._unestablished(
                CAVEAT_BOOT_ISO_REGIONS_INVALID,
                f"{path} states {regions} regions where a PS3 image holds 1 to 127, and RPCS3 then drops the "
                "image's whole layout (iso_file_decryption::init, ISO.cpp:539-543, :1303-1309) — what the boot "
                "does after that is not followed here",
                path,
            )
        if regions == 1:
            return self._boots_image(path, "one region, so nothing in it is encrypted (ISO.cpp:514-551)")
        if iso.watermark == WATERMARK_DECRYPTED:
            return self._boots_image(path, "the 3k3y mark of a decrypted image (ISO.cpp:606-650)")
        return self._iso_encryption(path, iso)

    def _boots_image(self, path: str, why: str) -> BootReading:
        """The image boots as its own disc — unless the disc directory RPCS3 is configured with is mounted instead.

        The key check comes first (System.cpp:1674-1698), so a refusal stands
        whatever that directory holds; only a boot is subject to it.
        """
        configured = self._configured_disc()
        if configured is not None:
            return configured
        return BootReading(
            BOOTS,
            (
                f"entry {self._label!r}: {path} is an ISO 9660 image (is_iso_file, ISO.cpp:63-118) with {why}, and "
                f"RPCS3 boots the disc it holds ({_SOURCE}, System.cpp:1674-1698)",
            ),
            file=path,
            disc_root=path,
        )

    def _iso_encryption(self, path: str, iso: Ps3IsoHeader) -> BootReading:
        """An image that may be encrypted: more than one region and no 3k3y mark of a decrypted one.

        RPCS3's key check (``check_disc_key``, System.cpp:1165-1189) passes
        where the licence and executable blocks lie in the clear, whatever
        key file there is — one beside a decrypted image is ignored
        (``verify_key_file``, ISO.cpp:425-456). Otherwise the key file it
        finds decides (``check_type``, :458-494): one found is put to the test
        by AES decryption, which atlas does not run — or, where the image
        holds neither block, applied without a test, ``verify_key_file``
        returning before it (:429) — so either way the answer is not
        established; with none, an image carrying
        its own key in its 3k3y sector (:614-639) or one with no block to
        judge boots, and an encrypted one is refused, ``disc_key_missing``.
        """
        if iso.blocks is None:
            return self._unestablished(
                CAVEAT_BOOT_ISO_ENCRYPTION_UNREAD,
                f"{path} declares encrypted regions, and its ISO 9660 tree is one this reading does not follow to "
                "the licence and executable RPCS3 tells an encrypted image by",
                path,
            )
        if iso.blocks == BLOCKS_CLEAR:
            return self._boots_image(
                path, "encrypted regions whose licence and executable lie in the clear (ISO.cpp:228-289, :686-708)"
            )
        key = self._key_file(path)
        if isinstance(key, BootReading):
            return key
        if key is not None:
            return self._unestablished(
                CAVEAT_BOOT_ISO_KEY_UNTESTED,
                f"RPCS3 decrypts {path} with the key file {key}, which it tests by AES decryption "
                "(verify_key_file, ISO.cpp:425-456) — atlas runs no AES, so whether the key fits is not established",
                key,
            )
        if iso.watermark == WATERMARK_ENCRYPTED:
            return self._boots_image(path, "the 3k3y mark of an image carrying its own key (ISO.cpp:614-639)")
        if iso.blocks == BLOCKS_NONE:
            return self._boots_image(
                path, "encrypted regions but no licence or executable block to judge, so RPCS3 finds nothing "
                "encrypted (ISO.cpp:228-279, :686-708)"
            )
        return self._refuses(
            CAVEAT_BOOT_DISC_KEY_MISSING,
            f"{path} is encrypted and no key file is where RPCS3 looks for one, so it refuses the disc "
            "(disc_key_missing: check_type, ISO.cpp:458-494; check_disc_key, System.cpp:1165-1189)",
            path,
        )

    def _key_file(self, path: str) -> str | BootReading | None:
        """The key file ``check_type`` takes for the image at *path* — the first of four present, ``None`` for none.

        Beside the image first, its name with the extension cut at the path's
        last dot and ``.dkey`` or ``.key`` added, then the same name in
        RPCS3's ``redump`` folder (ISO.cpp:465-491).
        """
        cut = path.rfind(".")
        name_path = path if cut == -1 else path[:cut]
        name = posixpath.basename(name_path)
        for candidate in (f"{name_path}{suffix}" for suffix in _KEY_SUFFIXES):
            found = self._present(candidate)
            if found is not None:
                return found
        redump = self._dirs().redump
        if redump is None:
            return BootReading(
                UNESTABLISHED,
                caveats=(
                    Caveat(
                        CAVEAT_BOOT_KEY_FOLDER_UNRESOLVED,
                        f"no key file is beside {path}, and RPCS3 looks in its redump folder next, which cannot "
                        "be told where RPCS3's configuration tree is not established",
                        {"entry": self._label},
                    ),
                ),
            )
        for candidate in (posixpath.join(redump, f"{name}{suffix}") for suffix in _KEY_SUFFIXES):
            found = self._present(candidate)
            if found is not None:
                return found
        return None

    def _present(self, path: str) -> str | BootReading | None:
        """*path* where a file stands there (``fs::is_file``), a reading where that cannot be told."""
        probe = self._view.regular_file(path)
        if probe == PROBE_UNKNOWN:
            return self._untold(path)
        return path if probe == PROBE_HIT else None

    def _executable(self, path: str) -> BootReading:
        """A SELF or ELF: its disc root, else the category its PARAM.SFO states."""
        inside = self._inside_drive(path)
        if inside is not None:
            return inside
        configured = self._configured_disc()
        if configured is not None:
            return configured
        root = self._disc_root(posixpath.dirname(path))
        if isinstance(root, BootReading):
            return root
        if root is not None:
            return BootReading(
                BOOTS,
                (
                    f"entry {self._label!r}: RPCS3 boots {path}, and {root} holds a valid {_SFB} beside the "
                    f"outermost {_GAME_DIR} or PS3_GMnn directory above it, so it mounts that as the disc "
                    f"(GetBdvdDir, System.cpp:4884-4927; {_SOURCE})",
                ),
                file=path,
                disc_root=root,
            )
        return self._without_root(path)

    def _inside_drive(self, path: str) -> BootReading | None:
        """The reading's answer where *path* lies inside RPCS3's ``dev_hdd0/game`` or that cannot be told."""
        drive = self._dirs()
        if drive.drive is None:
            where = "where its configuration tree is not established"
            if drive.config is not None:
                where = f"from {drive.config}"
            return BootReading(
                UNESTABLISHED,
                caveats=(
                    Caveat(
                        CAVEAT_BOOT_DRIVE_UNRESOLVED,
                        f"RPCS3 boots a game inside its internal drive's game folder another way, and where that "
                        f"drive is cannot be told {where}",
                        {"entry": self._label},
                    ),
                ),
            )
        games = posixpath.join(drive.drive, "game")
        if not (_beneath(path, games) or _beneath(self._view.host_path(path), self._view.host_path(games))):
            return None
        return self._unestablished(
            CAVEAT_BOOT_INSIDE_INTERNAL_DRIVE,
            f"{path} lies inside RPCS3's own {games}, where it boots a game another way (from_hdd0_game, "
            "System.cpp:2100-2137, :2287-2321) — that route is not followed here",
            path,
        )

    def _configured_disc(self) -> BootReading | None:
        """The disc directory RPCS3 is configured with, where it holds a ``PS3_DISC.SFB`` — ``None`` where it does not.

        ``Load`` takes ``/dev_bdvd/`` from ``vfs.yml``, or its compiled default
        ``$(EmulatorDir)dev_bdvd/``, for every boot, an image's included, and
        keeps it where a ``PS3_DISC.SFB`` file stands in it
        (System.cpp:1893-1919). A directory kept that way is mounted as the
        disc in place of the one ``GetBdvdDir`` finds or the image's own
        (:2114-2143, :2189-2199), which this reading does not follow. Where
        that directory cannot be told, neither can the disc.
        """
        dirs = self._dirs()
        bdvd = dirs.bdvd
        if bdvd is None:
            where = "where its configuration tree is not established"
            if dirs.config is not None:
                where = f"from {dirs.config}"
            return BootReading(
                UNESTABLISHED,
                caveats=(
                    Caveat(
                        CAVEAT_BOOT_DISC_DIR_UNRESOLVED,
                        "RPCS3 mounts its configured disc directory — the one vfs.yml names, or its compiled "
                        "default $(EmulatorDir)dev_bdvd/ — as the disc where a PS3_DISC.SFB stands in it "
                        f"(System.cpp:1893-1919), and where that directory is cannot be told {where}",
                        {"entry": self._label},
                    ),
                ),
            )
        sfb = posixpath.join(bdvd, _SFB)
        probe = self._view.regular_file(sfb)
        if probe == PROBE_UNKNOWN:
            return self._untold(sfb)
        if probe == PROBE_MISS:
            return None
        return self._unestablished(
            CAVEAT_BOOT_DISC_DIR_CONFIGURED,
            f"{bdvd} is RPCS3's configured disc directory — the one vfs.yml names, or its compiled default "
            f"$(EmulatorDir)dev_bdvd/ — and a {_SFB} stands in it, so RPCS3 mounts that directory as the disc in "
            "place of the one it would otherwise mount (System.cpp:1893-1919, :2114-2143, :2189-2199) — which "
            "disc that boots is not followed here",
            bdvd,
        )

    def _disc_root(self, elf_dir: str) -> str | BootReading | None:
        """``GetBdvdDir``: the outermost ``PS3_GAME`` or ``PS3_GMnn`` above *elf_dir* whose parent holds a valid SFB."""
        root: str | None = None
        search = elf_dir
        while search not in ("/", ""):
            parent = posixpath.dirname(search)
            if _is_game_dir(posixpath.basename(search)):
                valid = self._valid_sfb(posixpath.join(parent, _SFB))
                if isinstance(valid, BootReading):
                    return valid
                if valid:
                    root = parent
            search = parent
        return root

    def _valid_sfb(self, path: str) -> bool | BootReading:
        """``IsValidSfb``: a file at *path* whose first four bytes read ``.SFB``."""
        facts = self._facts(path)
        if isinstance(facts, BootReading):
            return facts
        return facts is not None and facts.magic == MAGIC_SFB

    def _without_root(self, path: str) -> BootReading:
        """No disc root: the category of the PARAM.SFO ``Load`` reads decides."""
        game = posixpath.dirname(posixpath.dirname(path))
        has_sfb = self._view.regular_file(posixpath.join(game, _SFB))
        if has_sfb == PROBE_UNKNOWN:
            return self._untold(posixpath.join(game, _SFB))
        sfo = posixpath.join(game, _GAME_DIR if has_sfb == PROBE_HIT else "", _SFO)
        facts = self._facts(sfo)
        if isinstance(facts, BootReading):
            return facts
        category = facts.category if facts is not None else ""
        if category == _DISC_GAME:
            return self._refuses(
                CAVEAT_BOOT_DISC_ROOT_MISSING,
                f"{sfo} names a disc game, and no directory above {path} is a {_GAME_DIR} beside a valid "
                f"{_SFB} — RPCS3 fails to mount the disc (System.cpp:2322-2326)",
                path,
            )
        if category == _REGISTERED_GAME:
            return BootReading(
                UNESTABLISHED,
                caveats=(
                    Caveat(
                        CAVEAT_BOOT_REGISTRY_UNREAD,
                        f"{sfo} names game data, which RPCS3 boots from the disc its game registry records for "
                        "the title (System.cpp:2151-2187) — that registry is not read here",
                        {"entry": self._label, "registry": GAME_REGISTRY},
                    ),
                ),
            )
        if category in _UNFOLLOWED_CATEGORIES:
            return BootReading(
                UNESTABLISHED,
                caveats=(
                    Caveat(
                        CAVEAT_BOOT_CATEGORY_UNESTABLISHED,
                        f"{sfo} states category {category}, whose boot this reading does not follow",
                        {"entry": self._label, "category": category},
                    ),
                ),
            )
        stated = f"states category {category}" if category else "yields no category, as RPCS3's loader reads it"
        return BootReading(
            BOOTS,
            (
                f"entry {self._label!r}: RPCS3 boots {path} with no disc mounted — {sfo} {stated}, and every "
                f"category but DG, GD, 1P and PE boots that way (System.cpp:2272-2286; PSF.cpp:186-196, :273-280; "
                f"{_SOURCE})",
            ),
            file=path,
        )


def _is_game_dir(name: str) -> bool:
    """``PS3_GAME``, or ``PS3_GM`` and two ASCII digits (``is_ps3_gm_dir_name``, system_utils.hpp:86-89)."""
    if name == _GAME_DIR:
        return True
    return len(name) == 8 and name.startswith("PS3_GM") and all(c in _ASCII_DIGITS for c in name[6:])


def _beneath(path: str | None, directory: str | None) -> bool:
    """``IsPathInsideDir``'s comparison (System.cpp:4828-4834): *path* below *directory*, or *directory* itself."""
    if not path or not directory:
        return False
    return (path.rstrip("/") + "/").startswith(directory.rstrip("/") + "/")


def read_boot(
    label: str, content_path: str, view: CommandView, read_file: ReadFile, dirs: Callable[[], Dirs]
) -> BootReading:
    """What the entry *label* boots when ES-DE hands RPCS3 *content_path* — the outcome and what boots.

    The path RPCS3 is handed is ``%ROM%`` as ES-DE fills it, a directory
    holding a file of its own name taken as that file
    (:func:`atlas.launch_command.rom_file`); every probe goes through *view*,
    the frontend's own, which is RPCS3's too wherever these arrangements run
    it. *read_file* reads a file's bytes on the host and *dirs* names RPCS3's
    own directories, asked only where the reading needs one.
    """
    return _Reading(label, view, read_file, dirs).content(content_path)

