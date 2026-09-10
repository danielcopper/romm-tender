"""Tests for the display name, and for its distance from the identifier."""

from __future__ import annotations

from domain.identity import DISPLAY_NAME
from domain.user_data_location import APP_DIR_NAME, SOURCE_FOLDER_NAMES


class TestTheDisplayName:
    def test_it_is_the_name_decky_and_the_frontend_show(self):
        """The value itself, pinned where it lives rather than only where it is spent.

        Every other assertion on this name is incidental to something else — a
        toast's sender in ``test_launch_gate.py``, a bundle README's heading in
        ``test_prune.py`` — so deleting any of those for its own reasons would
        leave the constant with no pin at all. It is what a user sees in Decky's
        plugin list, in the QAM header, on a token in their own RomM server and
        at the top of a file they open by hand, and those four have to agree.
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
        between them: it is the one that fails when the fold is written as a
        derivation rather than as a new literal.
        """
        assert DISPLAY_NAME != APP_DIR_NAME
        assert DISPLAY_NAME not in SOURCE_FOLDER_NAMES
