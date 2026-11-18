"""The resolver's platform table, run through Tender's reading of the platform question (#2223 D16, #2751).

Two families of the vendored copy (``tests/adapters/atlas_vectors/``) state
what the table answers. ``platform-table/translations.json`` is pairs in and a
translation out; ``machines/*.json`` holds platform questions put to a whole
recorded machine (``platform_systems_query``). Each is asked live of the
vendored resolver and compared, in the projection the copy's
``vectors/README.md`` defines, with what the vector recorded — so a release
that moves a platform's mapping shows as a vector diff — and its answer is read
through :func:`adapters.atlas_platforms.read_platform_answer`, the one reading
of the platform question Tender has.

Tender's reading is held against what the recorded answer states as data: a
translation none of whose pairs the table holds is a platform Tender cannot
match, one whose pairs share no system a contradiction (#2751 D2); an agreed
one comes to the systems
the recorded answer lists under the status that decides — one or several
switched on, else the switched-off ones, else the ones the machine has not set
up — and to no system where it lists none. Nothing is picked among several.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from _atlas_machines import MACHINES, fixture_machine, select, translation_projection
from _vendor import atlas
from _vendor.atlas import translate
from _vendor.atlas.contract import platform_systems_contract, platform_translation_contract
from _vendor.atlas.installations import PlatformSystemsAnswer

from adapters.atlas_platforms import read_platform_answer
from domain.platform_system import (
    CONTRADICTED,
    FOUND,
    NO_SYSTEM,
    NOT_HERE,
    SEVERAL,
    SWITCHED_OFF,
    UNMATCHED,
    SystemPair,
)

_TRANSLATIONS = MACHINES.parent / "platform-table" / "translations.json"


def _translation_vectors() -> list[dict[str, Any]]:
    return json.loads(_TRANSLATIONS.read_text(encoding="utf-8"))["vectors"]


def _platform_question_vectors() -> list[tuple[str, dict[str, Any]]]:
    return [
        (path.stem, vector)
        for path in sorted(MACHINES.glob("*.json"))
        for vector in json.loads(path.read_text(encoding="utf-8"))["vectors"]
        if "platform_systems_query" in vector["input"]
    ]


_TRANSLATION_VECTORS = _translation_vectors()
_PLATFORM_VECTORS = _platform_question_vectors()

# Tender's state for each status a recorded match states, in the order they
# decide: a switched-on system first, then a switched-off one, then one the
# machine has not set up.
_STATE_OF_STATUS = (("declared", FOUND), ("disabled", SWITCHED_OFF), ("absent", NOT_HERE))


def _pairs(raw: list[list[str | None]]) -> tuple[tuple[str | None, ...], ...]:
    return tuple(tuple(pair) for pair in raw)


def _expected_reading(translation: dict[str, Any], matches: list[dict[str, Any]]) -> tuple[str, tuple[SystemPair, ...]]:
    """What a recorded answer comes to in Tender's words, read off the data the resolver states."""
    if translation["intersection_status"] == "unknown":
        return UNMATCHED, ()
    if translation["intersection_status"] == "conflict":
        return CONTRADICTED, ()
    for status, state in _STATE_OF_STATUS:
        named = tuple(SystemPair(m["system"]["source"], m["system"]["key"]) for m in matches if m["status"] == status)
        if named:
            return (SEVERAL if state == FOUND and len(named) > 1 else state), named
    return NO_SYSTEM, ()


class TestTheCopy:
    def test_every_vector_records_the_vendored_tables_version(self):
        """The pin itself is ``tests/test_vendored_atlas.py``'s; the copy is of the same table."""
        version = translate((("romm", "snes"),)).table_version
        assert {vector["expected"]["table_version"] for vector in _TRANSLATION_VECTORS} == {version}

    def test_the_copy_holds_both_families(self):
        """A family that stops shipping would pass on nothing."""
        assert (len(_TRANSLATION_VECTORS), len(_PLATFORM_VECTORS)) == (29, 36)


@pytest.mark.parametrize("vector", [pytest.param(v, id=v["name"]) for v in _TRANSLATION_VECTORS])
class TestTheTranslations:
    def test_the_live_translation_is_the_recorded_one(self, vector):
        translation = translate(_pairs(vector["input"]["pairs"]))

        assert translation_projection(platform_translation_contract(translation)) == vector["expected"]

    def test_tender_matches_a_platform_exactly_where_the_table_agrees(self, vector):
        answer = PlatformSystemsAnswer(translate(_pairs(vector["input"]["pairs"])))

        state, named = read_platform_answer(answer)

        assert (state, named) == _expected_reading(vector["expected"], [])


@pytest.mark.parametrize("vector", [pytest.param(v, id=f"{stem}:{v['name']}") for stem, v in _PLATFORM_VECTORS])
class TestThePlatformQuestionOnRecordedMachines:
    def _answer(self, vector: dict[str, Any]) -> Any:
        inp = vector["input"]
        query = inp["platform_systems_query"]
        installation = select(atlas.detect(inp["home"], fixture_machine(inp)), query.get("installation"))
        return installation.systems_for_platform(query["vocabulary"], query["value"])

    def test_the_live_answer_is_the_recorded_one(self, vector):
        contract = platform_systems_contract(self._answer(vector))
        projected = {**contract, "translation": translation_projection(contract["translation"])}

        assert projected == vector["expected"]["systems_for_platform"]

    def test_tender_reads_the_systems_the_answer_states_and_picks_none(self, vector):
        expected = vector["expected"]["systems_for_platform"]

        assert read_platform_answer(self._answer(vector)) == _expected_reading(
            expected["translation"], expected["matches"]
        )
