"""Tests for domain.collection_owner: the sync's ownership rule and the listing's tag."""

from __future__ import annotations

import pytest

from domain.collection_owner import is_own_collection, listing_is_own


class TestVirtualAlwaysOwn:
    """Virtual collections have no owner and always survive the ``own`` scope."""

    def test_virtual_is_own_even_with_foreign_id(self):
        # A virtual collection carries no user_id; even a mismatched value is ignored.
        assert is_own_collection(999, own_user_id=1, kind="virtual") is True

    def test_virtual_is_own_when_identity_unknown(self):
        assert is_own_collection(None, own_user_id=None, kind="virtual") is True

    def test_virtual_is_own_with_no_owner_field(self):
        assert is_own_collection(None, own_user_id=1, kind="virtual") is True


class TestUnknownIdentityDropsNothing:
    """Unknown own identity → every collection is own (the non-breaking fallback)."""

    @pytest.mark.parametrize("kind", ["standard", "smart"])
    def test_own_when_identity_unknown(self, kind):
        assert is_own_collection(42, own_user_id=None, kind=kind) is True

    def test_own_when_identity_unknown_and_owner_missing(self):
        assert is_own_collection(None, own_user_id=None, kind="standard") is True


class TestKnownIdentityOwnership:
    """With a known own id, standard/smart collections split on the owner id."""

    @pytest.mark.parametrize("kind", ["standard", "smart"])
    def test_own_when_ids_match(self, kind):
        assert is_own_collection(7, own_user_id=7, kind=kind) is True

    @pytest.mark.parametrize("kind", ["standard", "smart"])
    def test_foreign_when_ids_differ(self, kind):
        assert is_own_collection(8, own_user_id=7, kind=kind) is False

    def test_foreign_when_owner_id_missing_but_identity_known(self):
        # A standard/smart collection with no user_id and a known own id can't be
        # confirmed as ours — treated as foreign (RomM always sends user_id here).
        assert is_own_collection(None, own_user_id=7, kind="standard") is False


class TestListingIsOwn:
    """The tag a get_collections row carries.

    Unknown identity leaves a standard or smart collection None; a virtual one stays own.
    """

    @pytest.mark.parametrize("kind", ["standard", "smart"])
    def test_none_while_identity_unknown(self, kind):
        assert listing_is_own(42, own_user_id=None, kind=kind) is None

    def test_virtual_is_own_while_identity_unknown(self):
        assert listing_is_own(None, own_user_id=None, kind="virtual") is True

    @pytest.mark.parametrize(("owner", "expected"), [(7, True), (8, False)])
    def test_known_identity_answers_like_the_sync(self, owner, expected):
        assert listing_is_own(owner, own_user_id=7, kind="standard") is expected
