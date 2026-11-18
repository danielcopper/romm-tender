"""Tests for ``scripts/dev_ui_scale.py``'s restore: the last line it ends with.

``scripts/dev_target.sh`` waits for that line after it stops the unit on a
target, so each outcome of the three halves is held to the line it gives. The
halves themselves talk to Steam and KWin and are stood in for here.
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
