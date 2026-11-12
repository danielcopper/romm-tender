"""Property-based tests for ``domain.platform_prefs`` — the per-platform sync toggles.

Hand-enumerated cases (``test_platform_prefs.py``) pin specific shapes; these
properties state the invariants across the whole space of toggles and listings.

Invariants encoded here:
- The move from RomM's platform id to ``fs_slug`` switches no listed platform
  on or off: each listed platform with an ``fs_slug`` and no toggle of its own
  answers after the move what its id answered before it (#2452 D22, D24).
- Switching one platform's toggle never changes another platform's answer
  (#1007).
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from domain.platform_prefs import platform_sync_enabled, resolve_sync_enabled, switches_by_folder

_folders = st.text(alphabet="abcdefghijklmnopqrstuvwxyz-", min_size=1, max_size=8)

# A listing: distinct ids, each platform with a distinct folder or none.
_listings = st.lists(
    st.tuples(st.integers(min_value=1, max_value=99), st.one_of(st.none(), _folders)),
    min_size=1,
    max_size=10,
    unique_by=(lambda p: p[0], lambda p: p[1] or f"none-{p[0]}"),
).map(lambda pairs: [{"id": pid, "slug": "snes", "fs_slug": folder} for pid, folder in pairs])

# A map kept by RomM id: ids the listing may or may not name.
_by_id = st.dictionaries(st.integers(min_value=1, max_value=99).map(str), st.booleans(), max_size=10)

_switches = st.dictionaries(_folders, st.booleans(), max_size=10)


@given(platforms=_listings, by_id=_by_id, switches=_switches)
def test_the_move_switches_no_listed_platform_on_or_off(platforms, by_id, switches) -> None:
    move = switches_by_folder(by_id, switches, platforms)

    listed = [(str(p["id"]), p["fs_slug"]) for p in platforms if p["fs_slug"] is not None]
    if not listed:
        assert move is None
        return
    assert move is not None
    for pid, folder in listed:
        before = switches[folder] if folder in switches else resolve_sync_enabled(by_id, pid)
        assert platform_sync_enabled(move.switches, folder) is before


@given(switches=_switches, target=_folders, on=st.booleans(), other=_folders)
def test_switching_one_platform_changes_no_other_platform(switches, target, on, other) -> None:
    switched = {**switches, target: on}

    assert platform_sync_enabled(switched, target) is on
    if other != target:
        assert platform_sync_enabled(switched, other) is platform_sync_enabled(switches, other)
