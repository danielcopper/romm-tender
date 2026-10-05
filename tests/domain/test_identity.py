"""Tests for the names this program goes by, and for the distances between them."""

from __future__ import annotations

import ast
from pathlib import Path

from domain.identity import DISPLAY_NAME, PACKAGE_NAME, VERSION
from domain.user_data_location import APP_DIR_NAME

_BACKEND = Path(__file__).resolve().parents[2] / "backend"
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

        The QAM header reads the frontend's ``PLUGIN_NAME``; the RomM token
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
