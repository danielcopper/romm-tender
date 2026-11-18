"""Tests for ``scripts/dev_ui_scale.py``'s restore: the last line it ends with.

``scripts/dev_target.sh`` waits for that line after it stops the unit on a
target, so each outcome of its three parts — the scale, the window and the
check — is held to the line it gives, and each part to what it reports. Steam
and KWin are stood in for here.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "dev_ui_scale.py"
_spec = importlib.util.spec_from_file_location("dev_ui_scale", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
dev_ui_scale = importlib.util.module_from_spec(_spec)
sys.modules["dev_ui_scale"] = dev_ui_scale
_spec.loader.exec_module(dev_ui_scale)


@pytest.mark.parametrize(
    ("scale", "window", "verified", "last"),
    [
        (True, True, True, dev_ui_scale.RESTORE_DONE),
        (False, True, True, dev_ui_scale.RESTORE_INCOMPLETE),
        (True, False, True, dev_ui_scale.RESTORE_INCOMPLETE),
        (True, True, False, dev_ui_scale.RESTORE_INCOMPLETE),
    ],
    ids=["all-landed", "scale-not", "window-not", "not-verified"],
)
def test_the_restore_ends_with_whether_all_of_it_landed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    scale: bool,
    window: bool,
    verified: bool,
    last: str,
) -> None:
    monkeypatch.setattr(dev_ui_scale, "_restore_scale", lambda _prior: scale)
    monkeypatch.setattr(dev_ui_scale, "_restore_window", lambda _prior: window)
    monkeypatch.setattr(dev_ui_scale, "_verify_restore", lambda _prior: verified)

    dev_ui_scale._restore(None, None)

    assert capsys.readouterr().out.splitlines()[-1] == last


_PRIOR_WINDOW = dev_ui_scale.BpmWindow(
    output="DP-2",
    output_geometry=None,
    fullscreen=True,
    frame=dev_ui_scale.Rect(0, 0, 2560, 1440),
    client=dev_ui_scale.Rect(0, 0, 2560, 1440),
)


@pytest.mark.parametrize(
    ("put_back", "landed"),
    [
        (None, False),
        (_PRIOR_WINDOW, True),
        (
            dev_ui_scale.BpmWindow(
                "DP-2", None, False, dev_ui_scale.Rect(0, 0, 1282, 829), dev_ui_scale.Rect(1, 28, 1280, 800)
            ),
            False,
        ),
    ],
    ids=["kwin-unreachable", "as-before", "still-windowed"],
)
def test_the_window_part_says_whether_it_landed(
    monkeypatch: pytest.MonkeyPatch, put_back: object, landed: bool
) -> None:
    monkeypatch.setattr(dev_ui_scale, "_kwin_call", lambda _action, _payload=None: put_back)

    assert dev_ui_scale._restore_window(_PRIOR_WINDOW) is landed


def test_the_scale_part_says_it_did_not_land_when_steam_does_not_take_it(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_factor: object) -> None:
        raise dev_ui_scale.DevUiScaleError("no CEF debugger")

    monkeypatch.setattr(dev_ui_scale, "_set_scale", refuse)

    assert dev_ui_scale._restore_scale(dev_ui_scale.SteamScaleState(True, None, 1.9, "live")) is False


def test_the_scale_part_lands_when_steam_takes_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dev_ui_scale, "_set_scale", lambda _factor: None)

    assert dev_ui_scale._restore_scale(dev_ui_scale.SteamScaleState(True, None, 1.9, "live")) is True


@pytest.mark.parametrize(
    ("live", "dpr", "verified"),
    [
        ((True, None), 1.9, True),
        ((False, 1.5), 1.5, False),
        (None, 1.9, False),
        ((True, None), None, False),
    ],
    ids=["as-before", "still-forced", "store-unreadable", "dpr-unreadable"],
)
def test_the_check_verifies_only_what_it_could_read(
    monkeypatch: pytest.MonkeyPatch, live: tuple[bool, float | None] | None, dpr: float | None, verified: bool
) -> None:
    monkeypatch.setattr(dev_ui_scale, "_SETTLE_SEC", 0)
    monkeypatch.setattr(dev_ui_scale, "_read_live_scale_state", lambda: live)
    monkeypatch.setattr(dev_ui_scale, "_current_dpr", lambda: dpr)

    assert dev_ui_scale._verify_restore(dev_ui_scale.SteamScaleState(True, None, 1.9, "live")) is verified
