"""Tests for the names this program goes by, and for the distances between them."""

from __future__ import annotations

import ast
from pathlib import Path

from domain.identity import DISPLAY_NAME, PACKAGE_NAME, VERSION
from domain.user_data_location import APP_DIR_NAME

_USER_DATA_LOCATION = Path(__file__).resolve().parents[2] / "backend" / "domain" / "user_data_location.py"


class TestTheDisplayName:
    def test_it_is_the_name_the_frontend_shows(self):
        """The value itself, pinned where it lives rather than only where it is spent.

        Three other files assert this name against something the code produced,
        and each is incidental to the surface it belongs to rather than about
        the name itself: the toast sender
        (``tests/services/test_launch_gate.py``), the RomM token label
        (``tests/services/test_connection.py``) and the registered-device client
        (``tests/services/saves/test_service.py``). The three heading tests read
        like pins and are not — they derive their expectation from this constant
        in order to hold an underline to its headline, and say nothing about
        what either one spells.

        The QAM header reads the frontend's ``PLUGIN_NAME`` and everything the
        backend writes reads this. Nothing checks that the two agree.
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


class TestTheIdentifierStaysInTwoPlaces:
    """``PACKAGE_NAME`` and ``APP_DIR_NAME`` spell one string and answer two questions.

    ``PACKAGE_NAME`` names the package a server is told about and the recovery
    folder a bundle is written into; ``APP_DIR_NAME`` names the directories the
    user's own library lives in. The first is free to be renamed with the
    package. The second is not: a rename moves every user's library on the next
    start, with nothing failing and nothing said.

    They spell the same string today, which is what makes the fold invisible.
    A value comparison cannot see it — after ``APP_DIR_NAME = PACKAGE_NAME``
    the two are still equal and every such assertion stays green — so what is
    asserted here is that ``user_data_location`` states its own literal.
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
        module = ast.parse(_USER_DATA_LOCATION.read_text(encoding="utf-8"))
        assignments = [
            node
            for node in module.body
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "APP_DIR_NAME" for t in node.targets)
        ]

        assert len(assignments) == 1, "APP_DIR_NAME is assigned exactly once, at module level"
        assert isinstance(assignments[0].value, ast.Constant), (
            "APP_DIR_NAME must be a literal of its own — deriving it from PACKAGE_NAME "
            "makes a package rename move every user's library, silently"
        )
        assert assignments[0].value.value == APP_DIR_NAME

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
