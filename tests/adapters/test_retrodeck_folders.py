"""``RetroDeckFoldersAdapter`` against the real resolver, over RetroDECK trees laid down under ``tmp_path``.

Every folder Tender uses in RetroDECK is the resolver's answer, so these tests
build what the resolver reads — the marker, ES-DE's settings, the deploy's
bundled systems file — and assert what the adapter hands on. Nothing here is a
stand-in for the resolver: a stand-in would answer whatever the test assumes
the resolver answers, which is the assumption under test.
"""

from __future__ import annotations

import ast
import json
import os
from pathlib import Path

import pytest
from _vendor.atlas.installations import RetroDeck

from adapters.emulator_sources import EmulatorSourcesAdapter
from adapters.retrodeck_folders import RetroDeckFoldersAdapter
from domain.retrodeck_folders import EveryFolderRefused, FindingRefused, FolderRefused, MoveRoots

_MARKER = Path(".var") / "app" / "net.retrodeck.retrodeck" / "config" / "retrodeck" / "retrodeck.json"
_ES_SETTINGS = Path(".var") / "app" / "net.retrodeck.retrodeck" / "config" / "ES-DE" / "settings" / "es_settings.xml"
_BUNDLED_SYSTEMS = (
    Path(".local")
    / "share"
    / "flatpak"
    / "app"
    / "net.retrodeck.retrodeck"
    / "current"
    / "active"
    / "files"
    / "retrodeck"
    / "components"
    / "es-de"
    / "share"
    / "es-de"
    / "resources"
    / "systems"
    / "linux"
    / "es_systems.xml"
)
_SYSTEMS_XML = """\
<?xml version="1.0"?>
<systemList>
  <system>
    <name>gba</name>
    <path>%ROMPATH%/gba</path>
    <extension>.gba</extension>
    <command label="mGBA">%EMULATOR_RETROARCH% -L %CORE_RETROARCH%/mgba_libretro.so %ROM%</command>
  </system>
</systemList>
"""


@pytest.fixture(autouse=True)
def _no_host_deploy(tmp_path, monkeypatch):
    """Never resolve ``/app`` out of the dev box's own RetroDECK deploy (as ``test_atlas_catalogue.py``)."""
    monkeypatch.setattr(
        "_vendor.atlas.installations._FLATPAK_DEPLOY_SYSTEM", str(tmp_path / "no_system_flatpak" / "app")
    )


class _Tree:
    """A RetroDECK as the resolver finds it under one home, built up by the test."""

    def __init__(self, tmp_path: Path) -> None:
        self.home = tmp_path / "home"
        self.home.mkdir()

    def deploy(self) -> None:
        """RetroDECK's Flatpak deploy, with ES-DE's bundled systems file declaring ``gba``."""
        bundled = self.home / _BUNDLED_SYSTEMS
        bundled.parent.mkdir(parents=True)
        bundled.write_text(_SYSTEMS_XML, encoding="utf-8")

    def marker(self, paths: dict[str, str]) -> None:
        """Write ``retrodeck.json`` naming *paths*, as RetroDECK's setup writes it."""
        marker = self.home / _MARKER
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(json.dumps({"paths": paths}), encoding="utf-8")

    def rom_directory(self, path: Path) -> None:
        """Write ES-DE's ``ROMDirectory``, which RetroDECK points at its own ROM folder."""
        settings = self.home / _ES_SETTINGS
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(f'<string name="ROMDirectory" value="{path}" />\n', encoding="utf-8")

    def set_up(self, rd_home: Path, *, folders: tuple[str, ...] = ("roms", "saves", "bios")) -> Path:
        """A RetroDECK deployed and set up at *rd_home* with its own settings, and its *folders* created."""
        self.deploy()
        self.marker({"rd_home_path": str(rd_home)})
        self.rom_directory(rd_home / "roms")
        for folder in folders:
            (rd_home / folder).mkdir(parents=True, exist_ok=True)
        return rd_home

    def sources(self, *, switched_off: bool = False) -> EmulatorSourcesAdapter:
        settings = {"emulator_sources_off": ["retrodeck"] if switched_off else []}
        return EmulatorSourcesAdapter(user_home=str(self.home), settings=settings, log_debug=lambda _line: None)

    def adapter(self, *, switched_off: bool = False) -> RetroDeckFoldersAdapter:
        return RetroDeckFoldersAdapter(sources=self.sources(switched_off=switched_off), log_debug=lambda _line: None)


@pytest.fixture
def tree(tmp_path) -> _Tree:
    return _Tree(tmp_path)


def _real(path: Path) -> str:
    return os.path.realpath(path)


class TestEveryFolderIsTheResolversAnswer:
    def test_with_retrodeck_s_own_settings_every_folder_lies_on_the_drive_of_its_home(self, tree, tmp_path):
        # rd_home_path on another drive and every sub-path unset: RetroDECK lays
        # its tree out under the home it was pointed at, and ES-DE's ROM folder
        # is set to the one under it.
        drive = tmp_path / "run" / "media" / "sdcard"
        rd_home = tree.set_up(drive / "retrodeck")
        adapter = tree.adapter()

        folders = [
            adapter.download_folder("gba"),
            adapter.bios_download_folder(),
            adapter.rom_root(),
            adapter.bios_folder(),
            adapter.saves_root(),
        ]

        assert folders == [
            _real(rd_home / "roms" / "gba"),
            _real(rd_home / "bios"),
            _real(rd_home / "roms"),
            _real(rd_home / "bios"),
            _real(rd_home / "saves"),
        ]
        assert all(isinstance(folder, str) and folder.startswith(_real(drive) + os.sep) for folder in folders)
        assert adapter.move_roots() == MoveRoots(
            home=_real(rd_home), bios=_real(rd_home / "bios"), saves=_real(rd_home / "saves")
        )

    def test_the_rom_folders_follow_es_de_where_it_points_elsewhere(self, tree, tmp_path):
        # The ROM root is ES-DE's ROMDirectory, not retrodeck.json's roms_path:
        # where a user moved the two apart, ES-DE's is the one games start from.
        tree.set_up(tmp_path / "retrodeck")
        other = tmp_path / "other-drive" / "roms"
        other.mkdir(parents=True)
        tree.rom_directory(other)
        adapter = tree.adapter()

        assert adapter.download_folder("gba") == _real(other / "gba")
        assert adapter.rom_root() == _real(other)

    def test_a_system_folder_not_created_yet_is_answered_for_the_download_to_create(self, tree, tmp_path):
        # As ES-DE would create it: the root it lies below exists.
        rd_home = tree.set_up(tmp_path / "retrodeck")

        assert not (rd_home / "roms" / "gba").exists()
        assert tree.adapter().download_folder("gba") == _real(rd_home / "roms" / "gba")

    def test_a_bios_folder_not_created_yet_inside_retrodeck_s_folder_is_answered(self, tree, tmp_path):
        rd_home = tree.set_up(tmp_path / "retrodeck", folders=("roms", "saves"))

        assert tree.adapter().bios_download_folder() == _real(rd_home / "bios")

    def test_every_folder_is_symlink_resolved(self, tree, tmp_path):
        # A root left as the resolver spells it would make one directory look
        # like two wherever /home is a link, and the guards would refuse a ROM
        # recorded inside it.
        real = tree.set_up(tmp_path / "real" / "retrodeck")
        link = tmp_path / "linked"
        link.symlink_to(tmp_path / "real")
        tree.marker({"rd_home_path": str(link / "retrodeck")})
        tree.rom_directory(link / "retrodeck" / "roms")
        adapter = tree.adapter()

        assert adapter.rom_root() == _real(real / "roms")
        assert adapter.download_folder("gba") == _real(real / "roms" / "gba")


class TestADownloadNeedsRetroDeck:
    def test_without_retrodeck_a_download_is_refused_with_its_sentence(self, tree):
        adapter = tree.adapter()

        game, bios = adapter.download_folder("gba"), adapter.bios_download_folder()

        assert isinstance(game, FolderRefused)
        assert game.message == "Downloads need RetroDECK, which is not installed."
        assert isinstance(bios, FolderRefused)
        assert bios.message == "BIOS downloads need RetroDECK, which is not installed."

    def test_switched_off_a_download_is_refused_and_a_removal_still_has_its_bound(self, tree, tmp_path):
        rd_home = tree.set_up(tmp_path / "retrodeck")
        adapter = tree.adapter(switched_off=True)

        game, bios = adapter.download_folder("gba"), adapter.bios_download_folder()

        assert isinstance(game, FolderRefused)
        assert game.message == "Downloads need RetroDECK, which is switched off in Settings → Emulator sources."
        assert isinstance(bios, FolderRefused)
        assert bios.message == "BIOS downloads need RetroDECK, which is switched off in Settings → Emulator sources."
        assert adapter.rom_root() == _real(rd_home / "roms")
        assert adapter.bios_folder() == _real(rd_home / "bios")
        assert adapter.saves_root() == _real(rd_home / "saves")

    def test_a_system_es_de_does_not_declare_has_no_folder(self, tree, tmp_path):
        tree.set_up(tmp_path / "retrodeck")

        refused = tree.adapter().download_folder("../../etc")

        assert isinstance(refused, FolderRefused)
        assert refused.reason == "no_rom_folder"
        assert refused.message == "RetroDECK names no ROM folder for ../../etc, so Tender cannot download this game."

    def test_while_retrodeck_s_own_folder_is_missing_a_download_is_refused_for_that_finding(self, tree, tmp_path):
        # An SD card that is out: nothing is created where it belongs.
        tree.deploy()
        tree.marker({"rd_home_path": str(tmp_path / "card" / "retrodeck")})
        tree.rom_directory(tmp_path / "card" / "retrodeck" / "roms")
        adapter = tree.adapter()

        for refused in (adapter.download_folder("gba"), adapter.bios_download_folder()):
            assert isinstance(refused, FindingRefused)
            assert refused.details["finding"]["code"] == "root-missing"

    def test_a_rom_root_that_is_missing_without_a_finding_is_never_created(self, tree, tmp_path):
        # ES-DE pointed at another drive that is out; RetroDECK's own folder is there.
        tree.set_up(tmp_path / "retrodeck")
        tree.rom_directory(tmp_path / "other-drive" / "roms")

        refused = tree.adapter().download_folder("gba")

        assert isinstance(refused, FolderRefused)
        assert refused.reason == "rom_root_missing"
        assert refused.details["path"] == str(tmp_path / "other-drive" / "roms")

    def test_a_bios_folder_outside_retrodeck_s_that_is_missing_is_never_created(self, tree, tmp_path):
        rd_home = tmp_path / "retrodeck"
        tree.set_up(rd_home, folders=("roms", "saves"))
        tree.marker({"rd_home_path": str(rd_home), "bios_path": str(tmp_path / "other-drive" / "bios")})

        refused = tree.adapter().bios_download_folder()

        assert isinstance(refused, FolderRefused)
        assert refused.reason == "bios_folder_missing"


class TestARemovalNeedsARomRoot:
    def test_without_retrodeck_an_uninstall_says_so(self, tree):
        refused = tree.adapter().rom_root()

        assert isinstance(refused, FolderRefused)
        assert refused.message == "Uninstalling needs RetroDECK, which is not installed."

    def test_where_es_de_names_no_rom_folder_an_uninstall_says_so(self, tree, tmp_path):
        tree.set_up(tmp_path / "retrodeck")
        (tree.home / _ES_SETTINGS).write_text('<string name="ROMDirectory" value="relative/roms" />\n')

        refused = tree.adapter().rom_root()

        assert isinstance(refused, FolderRefused)
        assert refused.message == "RetroDECK names no ROM folder, so Tender cannot uninstall this game."

    def test_without_retrodeck_the_other_roots_are_none(self, tree):
        adapter = tree.adapter()

        assert adapter.bios_folder() is None
        assert adapter.saves_root() is None
        assert adapter.move_roots() is None


def _marker_missing(tree: _Tree) -> RetroDeckFoldersAdapter:
    # The marker is what detection found RetroDECK by, and it went away after:
    # the handle is live and reads it again for every answer.
    tree.marker({"rd_home_path": str(tree.home / "retrodeck")})
    sources = tree.sources()
    reading = sources.read()
    (tree.home / _MARKER).unlink()
    sources.read = lambda: reading  # type: ignore[method-assign]
    return RetroDeckFoldersAdapter(sources=sources, log_debug=lambda _line: None)


def _marker_unreadable(tree: _Tree) -> RetroDeckFoldersAdapter:
    (tree.home / _MARKER).mkdir(parents=True)
    return tree.adapter()


def _marker_invalid(tree: _Tree) -> RetroDeckFoldersAdapter:
    marker = tree.home / _MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("not json", encoding="utf-8")
    return tree.adapter()


def _not_set_up(tree: _Tree) -> RetroDeckFoldersAdapter:
    """The deploy alone, as before RetroDECK's first run."""
    return tree.adapter()


class TestWhileRetroDecksFoldersAreDefaults:
    """Under one of the four findings Tender uses none of RetroDECK's folders."""

    @pytest.mark.parametrize(
        ("code", "make"),
        [
            ("marker-missing", _marker_missing),
            ("marker-unreadable", _marker_unreadable),
            ("marker-invalid", _marker_invalid),
            ("not-set-up", _not_set_up),
        ],
    )
    def test_every_folder_is_refused_for_the_finding_and_no_move_is_seen(self, tree, tmp_path, code, make):
        # The defaults exist, so a refusal is the rule's and not a missing folder's.
        for folder in ("roms", "saves", "bios"):
            (tree.home / "retrodeck" / folder).mkdir(parents=True, exist_ok=True)
        tree.rom_directory(tree.home / "retrodeck" / "roms")
        tree.deploy()
        adapter = make(tree)

        answers = [
            adapter.download_folder("gba"),
            adapter.bios_download_folder(),
            adapter.rom_root(),
            adapter.bios_folder(),
            adapter.saves_root(),
        ]

        for answer in answers:
            assert isinstance(answer, FindingRefused)
            assert answer.reason == "retrodeck_finding"
            assert answer.details["finding"]["code"] == code
        assert adapter.move_roots() is None


_UNANSWERED = "RetroDECK's folders could not be established, so Tender downloads into and removes from none of them."
_FOLDER_QUESTIONS = ("download_folder", "bios_download_folder", "rom_root", "bios_folder", "saves_root")


def _raises(*_args: object) -> None:
    raise RuntimeError("the resolver failed")


def _answer(adapter: RetroDeckFoldersAdapter, question: str) -> object:
    return adapter.download_folder("gba") if question == "download_folder" else getattr(adapter, question)()


class TestWhereAQuestionToTheResolverRaises:
    """A raise establishes nothing, so the answer it ended refuses every press that needs it — never "not installed"."""

    @pytest.mark.parametrize(
        ("raising", "refused", "move_seen"),
        [
            ("health", _FOLDER_QUESTIONS, False),
            ("rom_location", ("download_folder",), True),
            ("roms_dir", ("download_folder", "rom_root"), True),
            ("bios_dir", ("bios_download_folder", "bios_folder"), False),
            ("root", ("bios_download_folder",), False),
            ("saves_root", ("saves_root",), False),
        ],
    )
    def test_the_answer_it_ended_is_that_retrodeck_s_folders_could_not_be_established(
        self, tree, tmp_path, monkeypatch, raising, refused, move_seen
    ):
        tree.set_up(tmp_path / "retrodeck")
        adapter = tree.adapter()
        monkeypatch.setattr(RetroDeck, raising, _raises)

        for question in _FOLDER_QUESTIONS:
            answer = _answer(adapter, question)
            if question in refused:
                assert isinstance(answer, EveryFolderRefused), question
                assert answer.reason == "retrodeck_unanswered", question
                assert answer.message == _UNANSWERED, question
            else:
                assert isinstance(answer, str), question
        assert (adapter.move_roots() is not None) is move_seen


_BACKEND = Path(__file__).resolve().parents[2] / "backend"
_SETTINGS_FILES = ("retrodeck.json", "es_settings.xml")


def _docstrings(tree: ast.AST) -> set[int]:
    """The ids of every docstring constant in *tree*, which name a file without reading it."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _modules_naming_settings_files(root: Path) -> list[str]:
    """Every backend module, the vendored trees aside, whose code spells one of RetroDECK's settings files."""
    hits: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative.parts[0] in {"_vendor", "native"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstrings(tree)
        hits.extend(
            f"{relative}:{node.lineno}"
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and any(name in node.value for name in _SETTINGS_FILES)
        )
    return hits


class TestNoModuleReadsRetroDecksSettings:
    """RetroDECK's settings are the resolver's to read; no Tender module spells their file names in code."""

    def test_no_backend_module_names_them(self):
        assert _modules_naming_settings_files(_BACKEND) == []

    def test_the_scan_sees_a_module_that_does(self, tmp_path):
        # The control: a scan that never matched would pass the test above too.
        (tmp_path / "adapters").mkdir()
        (tmp_path / "adapters" / "paths.py").write_text(
            '"""Reads retrodeck.json, says the docstring."""\nMARKER = "config/retrodeck/retrodeck.json"\n',
            encoding="utf-8",
        )

        assert _modules_naming_settings_files(tmp_path) == ["adapters/paths.py:2"]
