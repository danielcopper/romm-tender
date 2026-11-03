"""The focus watcher's rules: when it acts, which input id a window gets, and the URL that carries it."""

from __future__ import annotations

import pytest

from domain.controller_focus import (
    BIG_PICTURE_INPUT_ID,
    DESKTOP_INPUT_ID,
    FocusedWindow,
    RunningSteam,
    SteamIdentity,
    XDisplay,
    focus_watcher_on,
    force_input_url,
    input_id_for,
    watcher_applies,
)

GAME_APP_ID = 2510548680
STEAM_64 = "/home/deck/.local/share/Steam/steamrt64/steam"
STEAM_32 = "/home/deck/.local/share/Steam/ubuntu12_32/steam"


def _steam(exe: str, *, under_gamescope: bool) -> RunningSteam:
    return RunningSteam(
        identity=SteamIdentity(pid=4242, start_time=99),
        exe=exe,
        under_gamescope=under_gamescope,
        display=XDisplay(name=":0", authority_path="/run/user/1000/xauth_abc"),
    )


class TestTheSwitch:
    @pytest.mark.parametrize("value", ["off", "OFF", " off ", "Off\n"])
    def test_off_in_any_case_and_padding_turns_it_off(self, value: str) -> None:
        assert focus_watcher_on({"TENDER_FOCUS_WATCHER": value}) is False

    @pytest.mark.parametrize("value", ["", "on", "of", "0", "false", "disabled"])
    def test_anything_else_leaves_it_on(self, value: str) -> None:
        assert focus_watcher_on({"TENDER_FOCUS_WATCHER": value}) is True

    def test_unset_leaves_it_on(self) -> None:
        assert focus_watcher_on({}) is True


class TestWhenTheWatcherApplies:
    @pytest.mark.parametrize(
        ("exe", "under_gamescope", "applies"),
        [
            pytest.param(STEAM_64, False, True, id="64-bit-desktop"),
            pytest.param(STEAM_64, True, False, id="64-bit-game-mode"),
            pytest.param(STEAM_32, False, False, id="32-bit-desktop"),
            pytest.param(STEAM_32, True, False, id="32-bit-game-mode"),
        ],
    )
    def test_only_the_64_bit_client_outside_gamescope(self, exe: str, under_gamescope: bool, applies: bool) -> None:
        assert watcher_applies(_steam(exe, under_gamescope=under_gamescope)) is applies

    def test_no_steam_never_applies(self) -> None:
        assert watcher_applies(None) is False

    def test_a_directory_merely_named_like_it_further_up_does_not_count(self) -> None:
        assert watcher_applies(_steam("/home/deck/steamrt64/Steam/ubuntu12_32/steam", under_gamescope=False)) is False


def _window(*, steam_game: int | None = None, input_focus: bool = False, pid: int | None = 1234) -> FocusedWindow:
    return FocusedWindow(host_pid=pid, steam_game=steam_game, steam_input_focus=input_focus)


class TestWhichIdAWindowGets:
    def test_a_window_under_a_steam_launch_gets_that_launch_s_app_id(self) -> None:
        assert input_id_for(_window(), GAME_APP_ID) == GAME_APP_ID

    def test_the_launch_wins_over_big_picture_s_properties(self) -> None:
        window = _window(steam_game=BIG_PICTURE_INPUT_ID, input_focus=True)

        assert input_id_for(window, GAME_APP_ID) == GAME_APP_ID

    def test_big_picture_needs_both_properties(self) -> None:
        assert input_id_for(_window(steam_game=BIG_PICTURE_INPUT_ID, input_focus=True), None) == BIG_PICTURE_INPUT_ID

    def test_the_desktop_client_s_window_carries_steam_game_alone_and_gets_the_desktop(self) -> None:
        assert input_id_for(_window(steam_game=BIG_PICTURE_INPUT_ID, input_focus=False), None) == DESKTOP_INPUT_ID

    def test_input_focus_on_another_steam_game_value_gets_the_desktop(self) -> None:
        assert input_id_for(_window(steam_game=12345, input_focus=True), None) == DESKTOP_INPUT_ID

    def test_any_other_window_gets_the_desktop(self) -> None:
        assert input_id_for(_window(), None) == DESKTOP_INPUT_ID

    def test_no_focused_window_gets_the_desktop(self) -> None:
        assert input_id_for(None, None) == DESKTOP_INPUT_ID

    def test_a_window_whose_pid_could_not_be_given_gets_the_desktop(self) -> None:
        assert input_id_for(_window(pid=None), None) == DESKTOP_INPUT_ID

    def test_a_launch_id_of_zero_never_releases_the_pin(self) -> None:
        assert input_id_for(_window(), 0) == DESKTOP_INPUT_ID


class TestTheUrl:
    @pytest.mark.parametrize(
        ("input_id", "url"),
        [
            (GAME_APP_ID, "steam://forceinputappid/2510548680"),
            (BIG_PICTURE_INPUT_ID, "steam://forceinputappid/769"),
            (DESKTOP_INPUT_ID, "steam://forceinputappid/413080"),
            (0, "steam://forceinputappid/0"),
        ],
    )
    def test_it_carries_the_id_and_nothing_else(self, input_id: int, url: str) -> None:
        assert force_input_url(input_id) == url

    @pytest.mark.parametrize("value", [-1, True, "769", 769.0, None])
    def test_anything_but_a_non_negative_int_is_refused(self, value: object) -> None:
        with pytest.raises(ValueError, match="non-negative int"):
            force_input_url(value)  # pyright: ignore[reportArgumentType]
