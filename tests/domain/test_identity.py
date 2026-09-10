"""Tests for the display name, and for its distance from the identifier."""

from __future__ import annotations

from domain.identity import DISPLAY_NAME
from domain.user_data_location import APP_DIR_NAME, SOURCE_FOLDER_NAMES


class TestTheDisplayName:
    def test_it_is_the_name_decky_and_the_frontend_show(self):
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

        Decky reads the same name out of ``plugin.json``, the QAM header out of
        the frontend's ``PLUGIN_NAME``, and everything the backend writes out of
        here. Nothing checks that those three agree.
        """
        assert DISPLAY_NAME == "Tender"

    def test_it_is_never_one_of_the_identifiers(self):
        """The display name and the identifier are two values, and stay two.

        The tempting tidy-up is to derive one from the other — they are, after
        all, "the plugin's name" twice. Folding them makes one question's answer
        decide another's: the display name is prose a human reads and can be
        rewritten at will, while ``APP_DIR_NAME`` is where the user's library
        lives and ``SOURCE_FOLDER_NAMES`` is what a migration searches. A
        rebrand would then move a library, and nothing would fail.

        Both ends are pinned by value elsewhere, so this asserts the seam
        between them — and only the fold written as an identity,
        ``APP_DIR_NAME = DISPLAY_NAME``. Both assertions compare values, so a
        fold through a transform passes: ``DISPLAY_NAME.lower()``, and
        ``f"romm-{DISPLAY_NAME.lower()}"``, which reproduces today's value
        exactly and is for that reason the likeliest of the three to be written.
        The rule those two need is stated at ``APP_DIR_NAME`` itself, where such
        a diff would land.
        """
        assert DISPLAY_NAME != APP_DIR_NAME
        assert DISPLAY_NAME not in SOURCE_FOLDER_NAMES
