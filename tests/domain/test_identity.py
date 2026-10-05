"""Tests for the names this program goes by, and for the distances between them."""

from __future__ import annotations

import ast
import functools
from pathlib import Path

import pytest

from domain.identity import DISPLAY_NAME, PACKAGE_NAME, VERSION
from domain.user_data_location import APP_DIR_NAME

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND = _REPO_ROOT / "backend"
_USER_DATA_LOCATION = _BACKEND / "domain" / "user_data_location.py"
_BOOTSTRAP_ADAPTERS = _BACKEND / "bootstrap" / "adapters.py"


def _module_level_assignments(path: Path, name: str) -> list[ast.Assign]:
    """Every module-level assignment to *name* in the module at *path*, read from its syntax tree."""
    module = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in module.body
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
    ]


def _names_in(tree: ast.AST) -> list[tuple[int, str]]:
    """Every name *tree* binds or reads, with its line — the names a reader takes for the code's own words."""
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Name):
            found.append((line, node.id))
        elif isinstance(node, ast.Attribute):
            found.append((line, node.attr))
        elif isinstance(node, (ast.arg, ast.keyword)) and node.arg is not None:
            found.append((line, node.arg))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            found.append((line, node.name))
        elif isinstance(node, ast.alias):
            found.extend((line, name) for name in (node.name, node.asname) if name is not None)
        elif isinstance(node, (ast.ExceptHandler, ast.MatchAs, ast.MatchStar)) and node.name is not None:
            found.append((line, node.name))
        elif isinstance(node, ast.MatchMapping) and node.rest is not None:
            found.append((line, node.rest))
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            found.extend((line, name) for name in node.names)
    return found


def _misnames_tender(name: str) -> bool:
    """Whether *name* carries the word "plugin", in any case, and is not someone else's plugin."""
    return "plugin" in name.lower() and name not in _NAMES_NOT_ABOUT_TENDER


def _names_misnaming_tender(source: str) -> list[tuple[int, str]]:
    return [(line, name) for line, name in _names_in(ast.parse(source)) if _misnames_tender(name)]


@functools.cache
def _python_sources() -> tuple[Path, ...]:
    """Every Python module this repository writes: ``backend/`` less its vendored copies, ``tests/``, ``scripts/``."""
    vendored = (_BACKEND / "_vendor", _BACKEND / "native")
    return tuple(
        sorted(
            path
            for top in (_BACKEND, _REPO_ROOT / "tests", _REPO_ROOT / "scripts")
            for path in top.rglob("*.py")
            if not any(path.is_relative_to(tree) for tree in vendored)
        )
    )


@functools.cache
def _names_in_python_sources() -> tuple[tuple[Path, int, str], ...]:
    """Every name in every module of :func:`_python_sources`, with the module and the line it stands on."""
    return tuple(
        (path, line, name)
        for path in _python_sources()
        for line, name in _names_in(ast.parse(path.read_text(encoding="utf-8")))
    )


# Names that carry "plugin" because they name someone else's plugin, each with
# the reason it keeps the word. Matched whole.
_NAMES_NOT_ABOUT_TENDER = {
    "test_tender_still_installed_as_a_decky_plugin_is_refused": "the install it refuses is Decky Loader's plugin",
    "test_another_decky_plugin_is_not_mistaken_for_ours": "the program it leaves alone is Decky Loader's plugin",
}


class TestTheDisplayName:
    def test_it_is_the_name_the_frontend_shows(self):
        """The value itself, pinned where it lives rather than only where it is spent.

        Two other files assert this name against something the code produced,
        and each is incidental to the surface it belongs to rather than about
        the name itself: the RomM token label
        (``tests/services/test_connection.py``) and the registered-device client
        (``tests/services/saves/test_service.py``). The two heading tests read
        like pins and are not — they derive their expectation from this constant
        in order to hold an underline to its headline, and say nothing about
        what either one spells.

        The QAM header reads the frontend's ``DISPLAY_NAME``; the RomM token
        label, the registered device's client and the two README headlines read
        this. Nothing checks that the two agree.
        """
        assert DISPLAY_NAME == "Tender"

    def test_it_is_never_one_of_the_identifiers(self):
        """The display name and the identifier are two values, and stay two.

        The tempting tidy-up is to derive one from the other — they are, after
        all, "the program's name" twice. Folding them makes one question's
        answer decide another's: the display name is prose a human reads and can
        be rewritten at will, while ``APP_DIR_NAME`` is where the user's library
        lives. A rebrand would then move a library, and nothing would fail.

        Both ends are pinned by value elsewhere, so this asserts the seam
        between them — and only a fold written as an identity. The assertion
        compares values, so a fold through a transform passes:
        ``DISPLAY_NAME.lower()``, and ``f"romm-{DISPLAY_NAME.lower()}"``, which
        reproduces today's value exactly and is for that reason the likelier of
        the two to be written. The rule that needs is stated at ``APP_DIR_NAME``
        itself, where such a diff would land.
        """
        assert DISPLAY_NAME != APP_DIR_NAME


class TestTheIdentifierStaysInSeparatePlaces:
    """``PACKAGE_NAME`` and ``APP_DIR_NAME`` spell one string and answer two questions; ``DB_FILENAME`` a third.

    ``PACKAGE_NAME`` names the package a server is told about and the recovery
    folder a bundle is written into; ``APP_DIR_NAME`` names the directories the
    user's own library lives in, and ``DB_FILENAME`` the file it is in there.
    The first is free to be renamed with the package. The other two are not: a
    rename moves every user's library on the next start, or leaves it under a
    name nothing opens, with nothing failing and nothing said.

    The first two spell the same string today, and the third carries it as its
    stem, which is what makes a fold invisible. A value comparison cannot see it
    — after ``APP_DIR_NAME = PACKAGE_NAME`` the two are still equal and every
    such assertion stays green — so what is asserted here is that each of the
    two names over persisted state is its own literal.
    """

    def test_they_spell_the_same_string_today(self):
        """Stated so the test below is read as being about the seam, not the value."""
        assert PACKAGE_NAME == APP_DIR_NAME == "romm-tender"

    def test_app_dir_name_is_its_own_literal_rather_than_the_package_name(self):
        """``APP_DIR_NAME = PACKAGE_NAME`` must fail here, and only here.

        Read from the syntax tree because the fold is invisible to every runtime
        assertion: the module is asked what it ASSIGNS, not what it resolves to.
        A string literal is the one answer that cannot be another constant's.
        """
        assignments = _module_level_assignments(_USER_DATA_LOCATION, "APP_DIR_NAME")

        assert len(assignments) == 1, "APP_DIR_NAME is assigned exactly once, at module level"
        assert isinstance(assignments[0].value, ast.Constant), (
            "APP_DIR_NAME must be a literal of its own — deriving it from PACKAGE_NAME "
            "makes a package rename move every user's library, silently"
        )
        assert assignments[0].value.value == APP_DIR_NAME

    def test_db_filename_is_its_own_literal_rather_than_a_derived_name(self):
        """``DB_FILENAME = f"{PACKAGE_NAME}.db"`` must fail here, for the reason ``APP_DIR_NAME``'s test gives.

        It reproduces today's value exactly, so only the syntax tree can tell.
        """
        from bootstrap.adapters import DB_FILENAME

        assignments = _module_level_assignments(_BOOTSTRAP_ADAPTERS, "DB_FILENAME")

        assert len(assignments) == 1, "DB_FILENAME is assigned exactly once, at module level"
        assert isinstance(assignments[0].value, ast.Constant), (
            "DB_FILENAME must be a literal of its own — deriving it from PACKAGE_NAME or APP_DIR_NAME "
            "makes a rename of either start every user on an empty database, silently"
        )
        assert assignments[0].value.value == DB_FILENAME == "romm-tender.db"

    def test_the_identity_module_does_not_import_the_directory_name(self):
        """The fold read the other way round — ``PACKAGE_NAME = APP_DIR_NAME``.

        Equally silent and equally wrong: it would tie the package a server is
        told about to a name that may never move.
        """
        import domain.identity as identity

        assert not hasattr(identity, "APP_DIR_NAME")


class TestTheVersion:
    def test_it_is_the_release_this_is(self):
        """Pinned by shape, not by value — release-please rewrites the value.

        The line carries the ``x-release-please-version`` marker so the release
        run can find it; asserting today's number here would turn every release
        PR red on a line nobody edited.
        """
        assert VERSION.count(".") == 2
        assert all(part.isdigit() for part in VERSION.split("."))

    def test_it_is_not_one_of_the_names(self):
        assert VERSION != DISPLAY_NAME
        assert VERSION != PACKAGE_NAME


class TestNoNameMisnamesTender:
    """Tender is not a plugin, and no name in its Python says it is.

    Read from the syntax tree, so what is looked at is names alone. A string
    literal is not one — the ``"plugin_version"`` key a recovery bundle's
    manifest carries stays — and neither is a comment or a docstring, which are
    prose and need a reader. Not seen either: a shell script, a module's file
    name or the dotted path a ``from`` import names, a type parameter
    (``def f[Plugin]()``), and a name built at run time
    (``getattr(obj, "plugin_" + suffix)``).
    """

    def test_no_python_module_has_one(self):
        found = [
            f"{path.relative_to(_REPO_ROOT)}:{line}: {name}"
            for path, line, name in _names_in_python_sources()
            if _misnames_tender(name)
        ]

        assert found == [], (
            "Tender is not a plugin: name these after what they are (the panel, the backend, Tender). "
            "A third-party name that must keep the word goes on _NAMES_NOT_ABOUT_TENDER, with its reason."
        )

    def test_every_name_let_through_is_still_there(self):
        """An exception nothing carries any more would let the next name of that spelling through unread."""
        present = {name for _, _, name in _names_in_python_sources()}

        assert set(_NAMES_NOT_ABOUT_TENDER) <= present

    def test_the_walk_reaches_all_three_trees_and_skips_the_vendored_ones(self):
        sources = _python_sources()

        assert _BACKEND / "domain" / "identity.py" in sources
        assert Path(__file__).resolve() in sources
        assert _REPO_ROOT / "scripts" / "check_seam_owner.py" in sources
        assert not any(path.is_relative_to(_BACKEND / "_vendor") for path in sources)

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("PLUGIN_NAME = 'Tender'\n", id="a name bound"),
            pytest.param("print(plugin_version)\n", id="a name read"),
            pytest.param("obj.plugin = 1\n", id="an attribute"),
            pytest.param("def f(plugin): ...\n", id="a parameter"),
            pytest.param("f(plugin_version=1)\n", id="a keyword argument"),
            pytest.param("def define_plugin(): ...\n", id="a function"),
            pytest.param("async def load_plugin(): ...\n", id="an async function"),
            pytest.param("class PluginSettings: ...\n", id="a class"),
            pytest.param("import plugin_loader\n", id="an imported module"),
            pytest.param("from x import y as plugin\n", id="an import alias"),
            pytest.param("try:\n    pass\nexcept Exception as plugin_error:\n    pass\n", id="an exception name"),
            pytest.param("match x:\n    case plugin_name:\n        pass\n", id="a capture pattern"),
            pytest.param("match x:\n    case [*plugins]:\n        pass\n", id="a star pattern"),
            pytest.param("match x:\n    case {**plugin_rest}:\n        pass\n", id="a mapping pattern's rest"),
            pytest.param("def f():\n    global PLUGIN\n", id="a global declaration"),
            pytest.param("def f():\n    nonlocal plugin\n", id="a nonlocal declaration"),
        ],
    )
    def test_it_finds_a_name_in_every_position(self, source):
        assert _names_misnaming_tender(source) != []

    def test_it_finds_the_word_in_any_case(self):
        assert [name for _, name in _names_misnaming_tender("Plugin = pLuGiN = 1\n")] == ["Plugin", "pLuGiN"]

    def test_it_leaves_strings_comments_and_docstrings_alone(self):
        source = 'def f():\n    """Reads the plugin_version key."""\n    return {"plugin_version": 1}  # a plugin\n'

        assert _names_misnaming_tender(source) == []

    def test_it_lets_a_listed_name_through_whole_and_only_whole(self):
        source = (
            "def test_another_decky_plugin_is_not_mistaken_for_ours(): ...\n"
            "def test_another_decky_plugin_is_not_mistaken_for_ours_too(): ...\n"
        )

        assert _names_misnaming_tender(source) == [(2, "test_another_decky_plugin_is_not_mistaken_for_ours_too")]
