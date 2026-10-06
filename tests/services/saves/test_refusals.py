"""Tests for services/saves/_refusals.py — the saves package's shared refusals."""

from domain.refusal import DomainRefused
from domain.save_answer import BENIGN_SYNC_SKIP_REASONS
from lib.errors import Refused
from services.saves._refusals import SavefilesInContentDir, SaveShapeUnsupported


class TestSaveShapeUnsupported:
    def test_its_reason_is_a_benign_skip(self):
        assert SaveShapeUnsupported.reason in BENIGN_SYNC_SKIP_REASONS

    def test_it_is_a_refusal_the_session_lifecycle_catches(self):
        refusal = SaveShapeUnsupported("The save is inside the game file.")

        assert isinstance(refusal, Refused)
        assert not isinstance(refusal, DomainRefused)
        assert (refusal.reason, refusal.message) == ("save_shape_unsupported", "The save is inside the game file.")


class TestSavefilesInContentDir:
    def test_its_reason_is_a_benign_skip(self):
        assert SavefilesInContentDir.reason in BENIGN_SYNC_SKIP_REASONS

    def test_it_is_a_refusal_the_session_lifecycle_catches(self):
        refusal = SavefilesInContentDir("Saves are written beside the game file.", synced=0)

        assert isinstance(refusal, Refused)
        assert not isinstance(refusal, DomainRefused)
        assert (refusal.reason, refusal.message, refusal.details) == (
            "savefiles_in_content_dir",
            "Saves are written beside the game file.",
            {"synced": 0},
        )
