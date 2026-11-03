"""Tender's judgement of a launch answer is the resolver's, on every recorded launch machine (#2698 D6).

Each vector in ``tests/adapters/atlas_vectors/`` whose expectations hold a
``launchable`` answer is a whole machine, whatever family it stands in. The
installation it names, else the first detected, is asked the vector's own
question live, and that answer is read through Tender's reading module
(:class:`adapters.atlas_catalogue.AtlasCatalogueAdapter`). What is compared is
keyed on what the resolver states as data, and on nothing Tender reads into a
code:

- the verdict: a refusal of the file turns Play off, a launchable answer leaves
  it on, and only a verdict the resolver could not establish reads as not
  established;
- the ``effect`` it states for a failure of the open: the stop is the refusal's
  reason and names its file, and every other failure the answer states is said
  among Tender's statements (#2698 D8), in the resolver's order, in its own
  group — a file the emulator goes on without as said beside the start or the
  refusal, never as not established, and an effect not established as not
  established.

Its limit: a note whose being "not established" the resolver states only
through what its code means — ``entry-format-unestablished``, the boot codes,
the opens reading's unread codes — carries no such data, so the test does not
judge it; the reading module's classification and its own tests stand for it.
"""

from __future__ import annotations

import os
from typing import Any

import pytest
from _atlas_machines import fixture_machine, select, vectors_asking
from _vendor import atlas
from _vendor.atlas import (
    EFFECT_GOES_ON,
    EFFECT_STOPS,
    EFFECT_UNESTABLISHED,
    VERDICT_ENTRY_NOT_ACCEPTED,
    VERDICT_LAUNCHABLE,
    VERDICT_NEEDS_INSTALLATION,
    VERDICT_NOT_ACCEPTED,
)

from adapters.atlas_catalogue import AtlasCatalogueAdapter
from adapters.emulator_sources import EmulatorSourcesAdapter
from domain.launchability import (
    FORMAT_REFUSED,
    GOES_ON_FILE_MISSING,
    GOES_ON_FORMAT_REFUSED,
    GOES_ON_PATH_TOO_LONG,
    GOES_ON_SHEET_REFUSED,
    GOES_ON_TRACK_REFUSED,
    NEEDS_INSTALLING,
    NOT_A_LAUNCH_FILE,
    NOT_ESTABLISHED,
    NOT_THIS_EMULATOR,
    OPENED_FILE_MISSING,
    PATH_TOO_LONG,
    PATH_TOO_LONG_UNCHECKED,
    PLAYLIST_REFUSED,
    SHEET_REFUSED,
    STARTS,
    TRACK_REFUSED,
)

# Tender's states that turn Play off (``frontend/src/utils/gameLaunch.ts``,
# ``REFUSING``), each the reading of one refusing verdict.
_REFUSING = {
    VERDICT_ENTRY_NOT_ACCEPTED: NOT_THIS_EMULATOR,
    VERDICT_NOT_ACCEPTED: NOT_A_LAUNCH_FILE,
    VERDICT_NEEDS_INSTALLATION: NEEDS_INSTALLING,
}

# The words of a failure of the open, by the group each effect reads into.
_STOP_WORDS = {OPENED_FILE_MISSING, TRACK_REFUSED, SHEET_REFUSED, FORMAT_REFUSED, PATH_TOO_LONG, PLAYLIST_REFUSED}
_GOES_ON_WORDS = {
    GOES_ON_FILE_MISSING,
    GOES_ON_TRACK_REFUSED,
    GOES_ON_SHEET_REFUSED,
    GOES_ON_FORMAT_REFUSED,
    GOES_ON_PATH_TOO_LONG,
}
_NOT_ESTABLISHED_WORDS = {PATH_TOO_LONG_UNCHECKED}

_VECTORS = vectors_asking("launchable")


class _Answering:
    """The vector's installation, answering with the answer it gave the vector's question."""

    def __init__(self, kind: str, answer: Any) -> None:
        self.kind = kind
        self._answer = answer

    def launchable(self, system: str, content_path: str, *, label: str | None = None) -> Any:
        return self._answer


def _read(kind: str, answer: Any, query: dict[str, Any], label: str) -> Any:
    """*answer* read through Tender's reading module, as the game page asks it."""
    adapter = AtlasCatalogueAdapter(
        sources=EmulatorSourcesAdapter(
            user_home="/home/deck",
            settings={},
            log_debug=lambda _line: None,
            detect_installations=lambda home, machine: [_Answering(kind, answer)],
            machine=object(),
        ),
        log_debug=lambda _line: None,
    )
    return adapter.launchability(query["system"], query["content_path"], label=label)


def _asked(vector: dict[str, Any]) -> tuple[str, Any, dict[str, Any], str]:
    """The installation's kind, its live answer to the vector's question, the question, and Tender's label."""
    inp = vector["input"]
    query = inp["launchable_query"]
    installation = select(atlas.detect(inp["home"], fixture_machine(inp)), query.get("installation"))
    answer = installation.launchable(query["system"], query["content_path"], label=query.get("label"))
    entry = answer.entry
    label = query.get("label") or (entry.label if entry is not None else "")
    return installation.kind, answer, query, label


def _effect_caveats(answer: Any) -> list[Any]:
    return [caveat for caveat in answer.caveats if "effect" in caveat.data]


def test_the_sweep_reaches_every_verdict_and_every_effect():
    stated: set[str] = set()
    verdicts: set[str] = set()
    for _stem, vector in _VECTORS:
        launchable = vector["expected"]["launchable"]
        verdicts.add(launchable["verdict"])
        stated |= {caveat["data"]["effect"] for caveat in launchable["caveats"] if "effect" in caveat.get("data", {})}

    assert len(_VECTORS) == 134
    assert verdicts == {*_REFUSING, VERDICT_LAUNCHABLE, "unknown"}
    assert stated == {EFFECT_STOPS, EFFECT_GOES_ON, EFFECT_UNESTABLISHED}


@pytest.mark.parametrize("vector", [pytest.param(v, id=f"{stem}:{v['name']}") for stem, v in _VECTORS])
class TestTendersJudgementIsTheResolvers:
    def test_the_live_answer_is_the_recorded_one(self, vector):
        _kind, answer, _query, _label = _asked(vector)

        assert answer.verdict == vector["expected"]["launchable"]["verdict"]

    def test_play_is_off_exactly_where_the_resolver_refuses_and_unestablished_only_where_it_says_so(self, vector):
        kind, answer, query, label = _asked(vector)

        result = _read(kind, answer, query, label)

        if answer.verdict in _REFUSING:
            assert result.state == _REFUSING[answer.verdict]
        elif answer.verdict == VERDICT_LAUNCHABLE:
            assert result.state == STARTS
        else:
            assert result.state == NOT_ESTABLISHED

    def test_a_refusal_s_reason_is_the_stop_the_resolver_states(self, vector):
        kind, answer, query, label = _asked(vector)
        stops = [caveat for caveat in _effect_caveats(answer) if caveat.data["effect"] == EFFECT_STOPS]

        result = _read(kind, answer, query, label)

        if answer.verdict == VERDICT_ENTRY_NOT_ACCEPTED and stops:
            assert result.reason in _STOP_WORDS
            assert result.reason_file == _named(stops[0].data["file"])
        else:
            assert not stops
            assert result.reason not in _STOP_WORDS

    def test_every_other_stated_failure_is_said_in_its_own_group_in_order(self, vector):
        kind, answer, query, label = _asked(vector)
        stated = [caveat for caveat in _effect_caveats(answer) if caveat.data["effect"] != EFFECT_STOPS]

        result = _read(kind, answer, query, label)

        said = [
            statement for statement in result.statements if statement.reason in _GOES_ON_WORDS | _NOT_ESTABLISHED_WORDS
        ]
        assert [(_group_of(statement.reason), statement.file) for statement in said] == [
            (caveat.data["effect"], _named(caveat.data["file"])) for caveat in stated
        ]


def _group_of(reason: str) -> str:
    return EFFECT_GOES_ON if reason in _GOES_ON_WORDS else EFFECT_UNESTABLISHED


def _named(file: str) -> str:
    """A file as the sentences name it: its name, a folder's own name for a path that names a folder."""
    return os.path.basename(file.rstrip("/")) or file
