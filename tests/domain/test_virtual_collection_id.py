"""Tests for domain.virtual_collection_id — reading the type out of a RomM virtual-collection id."""

import base64
import json

import pytest

from domain.virtual_collection_id import virtual_type_of, virtual_types_to_list

_SUPPORTED = ("franchise", "collection")


def _romm_id(name: str, virtual_type: str) -> str:
    """The id RomM gives a virtual collection (``VirtualCollection.id``)."""
    return base64.urlsafe_b64encode(json.dumps({"name": name, "type": virtual_type}).encode()).decode()


class TestVirtualTypeOf:
    @pytest.mark.parametrize("virtual_type", ["franchise", "collection", "genre"])
    def test_reads_the_type_romm_encoded(self, virtual_type):
        assert virtual_type_of(_romm_id("Mario / Zelda?", virtual_type)) == virtual_type

    @pytest.mark.parametrize(
        "collection_id",
        [
            "42",  # not base64
            "fr-1",  # base64 of bytes that are not text
            base64.urlsafe_b64encode(b"not json").decode(),
            base64.urlsafe_b64encode(b'["franchise"]').decode(),
            base64.urlsafe_b64encode(b'{"name": "Mario"}').decode(),
            base64.urlsafe_b64encode(b'{"name": "Mario", "type": 3}').decode(),
            "",
        ],
    )
    def test_an_id_that_does_not_decode_to_a_type_is_none(self, collection_id):
        assert virtual_type_of(collection_id) is None


class TestVirtualTypesToList:
    def test_only_the_types_an_enabled_id_belongs_to(self):
        assert virtual_types_to_list({_romm_id("Halo", "collection")}, _SUPPORTED) == ("collection",)

    def test_keeps_the_supported_order(self):
        enabled = {_romm_id("Halo", "collection"), _romm_id("Mario", "franchise")}
        assert virtual_types_to_list(enabled, _SUPPORTED) == ("franchise", "collection")

    def test_a_type_outside_the_supported_set_is_not_listed(self):
        assert virtual_types_to_list({_romm_id("RPG", "genre")}, _SUPPORTED) == ()

    def test_every_supported_type_when_an_id_does_not_decode(self):
        enabled = {_romm_id("Halo", "collection"), "42"}
        assert virtual_types_to_list(enabled, _SUPPORTED) == _SUPPORTED
