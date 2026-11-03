"""The focus watcher's rules: when it acts, which window it pins, what leaving a pin sends, and the URL."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from domain.controller_focus import (
    BIG_PICTURE_INPUT_ID,
    DESKTOP_INPUT_ID,
    FocusAnswer,
    FocusedWindow,
    RunningSteam,
    SteamIdentity,
    XDisplay,
    answer_focus,
    focus_watcher_on,
    force_input_url,
    pin_for,
    steam_choice_for,
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
            pytest.param(STEAM_64, True, False, id="64-bit-gaming-mode"),
            pytest.param(STEAM_32, False, False, id="32-bit-desktop"),
            pytest.param(STEAM_32, True, False, id="32-bit-gaming-mode"),
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


NATIVE_GAME_APP_ID = 1663850
BIG_PICTURE = _window(steam_game=BIG_PICTURE_INPUT_ID, input_focus=True)
DESKTOP_CLIENT = _window(steam_game=BIG_PICTURE_INPUT_ID, input_focus=False)
RETROARCH = _window()
NATIVE_GAME = _window(steam_game=NATIVE_GAME_APP_ID)


class TestWhichWindowIsPinned:
    def test_a_window_under_a_steam_launch_without_steam_game_is_pinned_to_the_launch(self) -> None:
        assert pin_for(RETROARCH, GAME_APP_ID) == GAME_APP_ID

    def test_a_window_steam_attributes_itself_is_never_pinned(self) -> None:
        # A native game's window: Steam set its own STEAM_GAME, and a pin would
        # keep the game's overlay from taking the controller.
        assert pin_for(NATIVE_GAME, NATIVE_GAME_APP_ID) is None

    def test_big_picture_under_a_launch_is_not_pinned(self) -> None:
        assert pin_for(BIG_PICTURE, GAME_APP_ID) is None

    def test_a_window_under_no_launch_is_not_pinned(self) -> None:
        assert pin_for(RETROARCH, None) is None

    def test_no_focused_window_is_not_pinned(self) -> None:
        assert pin_for(None, GAME_APP_ID) is None

    def test_a_launch_id_of_zero_is_no_pin(self) -> None:
        assert pin_for(RETROARCH, 0) is None


class TestWhatSteamWouldChooseItself:
    def test_big_picture_needs_both_properties(self) -> None:
        assert steam_choice_for(BIG_PICTURE) == BIG_PICTURE_INPUT_ID

    def test_the_desktop_client_s_window_carries_steam_game_alone_and_gets_the_desktop(self) -> None:
        assert steam_choice_for(DESKTOP_CLIENT) == DESKTOP_INPUT_ID

    def test_a_game_window_gets_the_game_steam_attributes_it_to(self) -> None:
        assert steam_choice_for(NATIVE_GAME) == NATIVE_GAME_APP_ID

    def test_any_other_window_gets_the_desktop(self) -> None:
        assert steam_choice_for(RETROARCH) == DESKTOP_INPUT_ID

    def test_no_focused_window_gets_the_desktop(self) -> None:
        assert steam_choice_for(None) == DESKTOP_INPUT_ID

    def test_a_steam_game_of_zero_gets_the_desktop(self) -> None:
        assert steam_choice_for(_window(steam_game=0)) == DESKTOP_INPUT_ID


class TestWhatAFocusChangeSends:
    def test_a_window_to_pin_is_pinned_and_the_pin_stands(self) -> None:
        assert answer_focus(False, RETROARCH, GAME_APP_ID) == FocusAnswer(sends=(GAME_APP_ID,), pin=GAME_APP_ID)

    def test_the_same_pin_is_sent_again(self) -> None:
        assert answer_focus(True, RETROARCH, GAME_APP_ID) == FocusAnswer(sends=(GAME_APP_ID,), pin=GAME_APP_ID)

    def test_another_window_to_pin_replaces_the_pin(self) -> None:
        assert answer_focus(True, RETROARCH, 7) == FocusAnswer(sends=(7,), pin=7)

    @pytest.mark.parametrize(
        ("window", "choice"),
        [
            pytest.param(BIG_PICTURE, BIG_PICTURE_INPUT_ID, id="big-picture"),
            pytest.param(DESKTOP_CLIENT, DESKTOP_INPUT_ID, id="desktop-client"),
            pytest.param(NATIVE_GAME, NATIVE_GAME_APP_ID, id="native-game"),
            pytest.param(_window(pid=None), DESKTOP_INPUT_ID, id="other-window"),
            pytest.param(None, DESKTOP_INPUT_ID, id="no-window"),
        ],
    )
    def test_focus_leaving_a_pin_sends_steam_s_choice_then_the_release(
        self, window: FocusedWindow | None, choice: int
    ) -> None:
        launched = NATIVE_GAME_APP_ID if window is NATIVE_GAME else None

        assert answer_focus(True, window, launched) == FocusAnswer(sends=(choice, 0), pin=None)

    @pytest.mark.parametrize(
        "window",
        [
            pytest.param(BIG_PICTURE, id="big-picture"),
            pytest.param(DESKTOP_CLIENT, id="desktop-client"),
            pytest.param(NATIVE_GAME, id="native-game"),
            pytest.param(None, id="no-window"),
        ],
    )
    def test_with_no_pin_standing_nothing_is_sent(self, window: FocusedWindow | None) -> None:
        launched = NATIVE_GAME_APP_ID if window is NATIVE_GAME else None

        assert answer_focus(False, window, launched) == FocusAnswer(sends=(), pin=None)


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


_BACKEND = Path(__file__).resolve().parents[2] / "backend"


def _docstrings(tree: ast.AST) -> set[int]:
    """The ids of the string nodes that are docstrings, which describe the URL rather than send it."""
    found: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _modules_spelling_the_url(root: Path) -> list[str]:
    """Every backend module, the vendored trees aside, whose code holds a string naming ``forceinputappid``.

    Docstrings are left out; comments are not code and never seen.
    """
    hits: list[str] = []
    for path in sorted(root.rglob("*.py")):
        relative = path.relative_to(root)
        if relative.parts[0] in {"_vendor", "native"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings = _docstrings(tree)
        if any(
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and "forceinputappid" in node.value
            and id(node) not in docstrings
            for node in ast.walk(tree)
        ):
            hits.append(str(relative))
    return hits


class TestOnlyTheRulesSpellTheUrl:
    """Every pin goes through ``force_input_url``, so no other module can send Steam an id of its own choosing."""

    def test_no_backend_module_but_the_rules_names_it(self) -> None:
        assert _modules_spelling_the_url(_BACKEND) == ["domain/controller_focus.py"]

    def test_the_scan_sees_a_module_that_does(self, tmp_path: Path) -> None:
        # The control: a scan that never matched would pass the test above too.
        (tmp_path / "services").mkdir()
        (tmp_path / "services" / "pinning.py").write_text('URL = "steam://forceinputappid/769"\n', encoding="utf-8")
        (tmp_path / "services" / "described.py").write_text(
            '"""Sends ``steam://forceinputappid/<id>``."""\n', encoding="utf-8"
        )

        assert _modules_spelling_the_url(tmp_path) == ["services/pinning.py"]
