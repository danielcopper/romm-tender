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
    classify_command,
)
from domain.emulator_pick import StoredPick


def _entry(
    label: str, index: int, *, installation: str | None = None, start_script_runs_nothing: bool = False
) -> EmulatorOption:
    """An emulator program's entry, declared at *index*, with what the resolver states about it."""
    option = classify_command(
        label,
        f"%EMULATOR_E{index}% %ROM%",
        kind=PROGRAM,
        installation=installation,
        start_script_runs_nothing=start_script_runs_nothing,
    )
    return dataclasses.replace(option, declared_index=index)


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
            "folder_entry": None,
        }

    def test_an_entry_tender_cannot_bake_bakes_nothing_and_is_not_refused(self):
        """Nothing stands in its way; it renders no invocation (#2195 D7)."""
        script = dataclasses.replace(
            classify_command("Shortcut", "%ENABLESHORTCUTS% %EMULATOR_OS-SHELL% %ROM%", kind=PROGRAM),
            declared_index=0,
        )
        choice = choose_emulator([script], game=None, platform=None)
        assert start_payload(choice, [script])["state"] == STARTS

    def test_an_entry_not_installed_cannot_start(self):
        missing = _entry("Missing", 0, installation=NOT_INSTALLED)
        choice = choose_emulator([missing], game=None, platform=None)
        assert start_payload(choice, [missing]) == {
            "state": CANNOT_START_STATE,
            "reason": NOT_INSTALLED,
            "entry": "Missing",
            "folder_entry": None,
        }

    def test_an_entry_the_start_script_runs_nothing_for_cannot_start(self):
        nothing = _entry("PICO-8", 0, start_script_runs_nothing=True)
        choice = choose_emulator([nothing], game=None, platform=None)
        assert start_payload(choice, [nothing])["reason"] == "start_script_runs_nothing"

    def test_a_folder_its_entry_cannot_start_names_the_entry_that_can(self):
        choice = choose_emulator(OPTIONS, game=None, platform=None)
        assert start_payload(choice, OPTIONS, folder_unstartable=True) == {
            "state": CANNOT_START_STATE,
            "reason": "folder_install",
            "entry": "Shipped",
            "folder_entry": "RPCS3 Directory (Standalone)",
        }

    def test_a_shipped_default_not_established_asks_for_a_choice_with_its_note(self):
        unseen = _entry("Unseen", 0, installation=NOT_ESTABLISHED_NO_FIND_RULE)
        choice = choose_emulator([unseen], game=None, platform=None)
        assert start_payload(choice, [unseen]) == {
            "state": CHOOSE,
            "reason": NOT_ESTABLISHED_NO_FIND_RULE,
            "entry": "Unseen",
            "folder_entry": None,
        }

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
