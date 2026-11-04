"""Tests for domain/emulator_choice — which entry a scope starts with, and what stands in its way."""

from __future__ import annotations

import dataclasses

from domain.emulator_choice import (
    CANNOT_START_STATE,
    CHOOSE,
    DEFAULT,
    GAME_PICK,
    NO_ENTRY,
    PLATFORM_PICK,
    STARTS,
    choose_emulator,
    start_payload,
    vanished_payload,
)
from domain.emulator_commands import (
    NOT_ESTABLISHED_NO_FIND_RULE,
    NOT_INSTALLED,
    PROGRAM,
    EmulatorOption,
    emulator_option,
)
from domain.emulator_pick import StoredPick
from domain.start_command import (
    FRONTEND_BUILD_UNREAD,
    PLACEHOLDER_UNFILLED,
    StartCommand,
    StartNote,
    StartRefused,
)


def _entry(label: str, index: int, *, installation: str | None = None) -> EmulatorOption:
    """An emulator program's entry, declared at *index*, with what the resolver states about it."""
    return dataclasses.replace(emulator_option(label, kind=PROGRAM, installation=installation), declared_index=index)


SHIPPED = _entry("Shipped", 0)
OTHER = _entry("Other", 1)
THIRD = _entry("Third", 2)
OPTIONS = [SHIPPED, OTHER, THIRD]


class TestChooseEmulator:
    def test_the_game_pick_wins_over_the_platform_pick(self):
        choice = choose_emulator(
            OPTIONS, game=StoredPick("Third", "retrodeck"), platform=StoredPick("Other", "retrodeck")
        )
        assert (choice.option, choice.layer, choice.vanished) == (THIRD, GAME_PICK, None)

    def test_the_platform_pick_wins_over_the_default(self):
        choice = choose_emulator(OPTIONS, game=None, platform=StoredPick("Other", "retrodeck"))
        assert (choice.option, choice.layer) == (OTHER, PLATFORM_PICK)

    def test_with_no_pick_the_shipped_default(self):
        choice = choose_emulator(OPTIONS, game=None, platform=None)
        assert (choice.option, choice.layer, choice.vanished) == (SHIPPED, DEFAULT, None)

    def test_a_pick_stands_whatever_its_state(self):
        missing = _entry("Missing", 1, installation=NOT_INSTALLED)
        choice = choose_emulator([SHIPPED, missing], game=StoredPick("Missing", "retrodeck"), platform=None)
        assert (choice.option, choice.layer) == (missing, GAME_PICK)

    def test_a_vanished_game_pick_gives_way_and_is_named(self):
        gone = StoredPick("Gone", "retrodeck")
        choice = choose_emulator(OPTIONS, game=gone, platform=StoredPick("Other", "retrodeck"))
        assert (choice.option, choice.layer) == (OTHER, PLATFORM_PICK)
        assert choice.vanished is not None
        assert (choice.vanished.pick, choice.vanished.layer) == (gone, GAME_PICK)

    def test_the_topmost_vanished_pick_is_the_one_named(self):
        choice = choose_emulator(
            OPTIONS, game=StoredPick("Gone", "retrodeck"), platform=StoredPick("Also gone", "retrodeck")
        )
        assert (choice.option, choice.layer) == (SHIPPED, DEFAULT)
        assert choice.vanished is not None
        assert choice.vanished.layer == GAME_PICK

    def test_a_shipped_default_not_established_is_no_default(self):
        unseen = _entry("Unseen", 0, installation=NOT_ESTABLISHED_NO_FIND_RULE)
        choice = choose_emulator([unseen, OTHER], game=None, platform=None)
        assert (choice.option, choice.layer) == (None, None)


class TestStartPayload:
    def test_an_entry_that_starts(self):
        choice = choose_emulator(OPTIONS, game=None, platform=None)
        assert start_payload(choice, OPTIONS) == {
            "state": STARTS,
            "reason": None,
            "entry": "Shipped",
        }

    def test_an_entry_not_installed_cannot_start(self):
        missing = _entry("Missing", 0, installation=NOT_INSTALLED)
        choice = choose_emulator([missing], game=None, platform=None)
        assert start_payload(choice, [missing]) == {
            "state": CANNOT_START_STATE,
            "reason": NOT_INSTALLED,
            "entry": "Missing",
        }

    def test_a_shipped_default_not_established_asks_for_a_choice_with_its_note(self):
        unseen = _entry("Unseen", 0, installation=NOT_ESTABLISHED_NO_FIND_RULE)
        choice = choose_emulator([unseen], game=None, platform=None)
        assert start_payload(choice, [unseen]) == {
            "state": CHOOSE,
            "reason": NOT_ESTABLISHED_NO_FIND_RULE,
            "entry": "Unseen",
        }

    def test_an_entry_the_resolver_gives_no_complete_command_for_cannot_start_with_its_reason(self):
        """The resolver's own reason, whichever layer the entry comes from (#2221 D2)."""
        choice = choose_emulator(OPTIONS, game=StoredPick("Other", "retrodeck"), platform=None)
        assert start_payload(choice, OPTIONS, StartRefused(FRONTEND_BUILD_UNREAD)) == {
            "state": CANNOT_START_STATE,
            "reason": FRONTEND_BUILD_UNREAD,
            "entry": "Other",
        }

    def test_an_entry_not_installed_says_so_whatever_the_command(self):
        missing = _entry("Missing", 0, installation=NOT_INSTALLED)
        choice = choose_emulator([missing], game=None, platform=None)
        assert start_payload(choice, [missing], StartRefused(FRONTEND_BUILD_UNREAD))["reason"] == NOT_INSTALLED

    def test_a_complete_command_starts_and_says_what_it_says_beside_the_start(self):
        """A placeholder ES-DE leaves unfilled is said beside a start that goes ahead (#2708 Q3)."""
        choice = choose_emulator(OPTIONS, game=None, platform=None)
        command = StartCommand(
            argv=("rpcs3", "%RPCS3_GAMEID%"), folder=None, notes=(StartNote(PLACEHOLDER_UNFILLED, "%RPCS3_GAMEID%"),)
        )
        assert start_payload(choice, OPTIONS, command) == {
            "state": STARTS,
            "reason": None,
            "entry": "Shipped",
            "remarks": [{"reason": PLACEHOLDER_UNFILLED, "placeholder": "%RPCS3_GAMEID%"}],
        }

    def test_a_complete_command_with_nothing_to_say_carries_no_remarks(self):
        choice = choose_emulator(OPTIONS, game=None, platform=None)
        assert "remarks" not in start_payload(choice, OPTIONS, StartCommand(argv=("mgba",), folder=None))

    def test_no_entry_at_all(self):
        choice = choose_emulator([], game=None, platform=None)
        assert start_payload(choice, [])["state"] == NO_ENTRY


class TestVanishedPayload:
    def test_none_where_nothing_vanished(self):
        assert vanished_payload(choose_emulator(OPTIONS, game=None, platform=None)) is None

    def test_the_pick_is_named_with_its_own_source_and_what_is_used_instead(self):
        choice = choose_emulator(OPTIONS, game=StoredPick("Gone", "emudeck"), platform=None)
        assert vanished_payload(choice) == {
            "label": "Gone",
            "source": "emudeck",
            "layer": GAME_PICK,
            "used": "Shipped",
            "used_layer": DEFAULT,
        }

    def test_a_platform_pick_used_instead_is_named_as_the_layer_used(self):
        choice = choose_emulator(
            OPTIONS, game=StoredPick("Gone", "retrodeck"), platform=StoredPick("Other", "retrodeck")
        )
        payload = vanished_payload(choice)
        assert payload is not None
        assert (payload["used"], payload["used_layer"]) == ("Other", PLATFORM_PICK)

    def test_nothing_used_instead_names_no_layer(self):
        choice = choose_emulator([], game=StoredPick("Gone", "retrodeck"), platform=None)
        payload = vanished_payload(choice)
        assert payload is not None
        assert (payload["used"], payload["used_layer"]) == (None, None)
