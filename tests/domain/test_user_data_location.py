"""Tests for the pure data-location ladder and the two roots it decides between."""

from __future__ import annotations

from domain.user_data_location import (
    DATA_HALF,
    SETTINGS_HALF,
    SourceFacts,
    config_root,
    data_root,
    plan_migration,
)

_OLD = "decky-romm-sync"
_NEW = "romm-tender"


def _source(
    name: str,
    *,
    present: bool = True,
    library: bool = False,
    settings_mtime: float | None = None,
) -> SourceFacts:
    return SourceFacts(name=name, present=present, has_library=library, settings_mtime=settings_mtime)


class _Probe:
    """A source probe that records whether the ladder ever reached it."""

    def __init__(self, sources: list[SourceFacts]) -> None:
        self._sources = sources
        self.calls = 0

    def __call__(self) -> list[SourceFacts]:
        self.calls += 1
        return self._sources


def _plan(
    *,
    settings_occupied: bool = False,
    data_occupied: bool = False,
    recorded: str | None = None,
    sources: list[SourceFacts] | None = None,
    probe: _Probe | None = None,
):
    return plan_migration(
        settings_target_occupied=settings_occupied,
        data_target_occupied=data_occupied,
        recorded_answer=recorded,
        probe_sources=probe if probe is not None else _Probe(sources if sources is not None else []),
    )


class TestRoots:
    def test_config_root_is_under_the_users_own_home(self):
        assert config_root("/home/deck") == "/home/deck/.config/romm-tender"

    def test_data_root_is_under_the_users_own_home(self):
        assert data_root("/home/deck") == "/home/deck/.local/share/romm-tender"


class TestOutstandingHalves:
    """Each half is guarded by its OWN target, so one can settle without the other."""

    def test_both_targets_empty_leaves_both_halves_outstanding(self):
        plan = _plan(sources=[_source(_OLD, library=True)])
        assert plan.outstanding == (SETTINGS_HALF, DATA_HALF)

    def test_an_occupied_settings_target_leaves_only_the_data_half(self):
        plan = _plan(settings_occupied=True, sources=[_source(_OLD, library=True)])
        assert plan.outstanding == (DATA_HALF,)
        assert plan.source_name == _OLD

    def test_an_occupied_data_target_leaves_only_the_settings_half(self):
        plan = _plan(data_occupied=True, sources=[_source(_OLD, library=True)])
        assert plan.outstanding == (SETTINGS_HALF,)

    def test_both_targets_occupied_ask_nothing_even_with_two_libraries(self):
        """Rung 1 is unconditional: the sources are never deleted, so a rule that
        kept looking at them would raise the same question forever."""
        plan = _plan(
            settings_occupied=True,
            data_occupied=True,
            sources=[_source(_OLD, library=True), _source(_NEW, library=True)],
        )
        assert plan == plan.__class__(outstanding=(), source_name=None, choice_required=False)

    def test_a_settled_start_never_looks_at_a_source(self):
        """Probing opens both older databases; a settled install must stop paying it."""
        probe = _Probe([_source(_OLD, library=True), _source(_NEW, library=True)])

        _plan(settings_occupied=True, data_occupied=True, probe=probe)

        assert probe.calls == 0

    def test_an_outstanding_half_probes_the_sources_once(self):
        probe = _Probe([_source(_OLD, library=True)])

        _plan(settings_occupied=True, probe=probe)

        assert probe.calls == 1


class TestRecordedAnswer:
    def test_a_recorded_answer_names_the_source_and_asks_nothing(self):
        plan = _plan(
            recorded=_NEW,
            sources=[_source(_OLD, library=True), _source(_NEW, library=True)],
        )
        assert plan.source_name == _NEW
        assert plan.choice_required is False

    def test_a_recorded_answer_outranks_the_only_library(self):
        plan = _plan(recorded=_NEW, sources=[_source(_OLD, library=True), _source(_NEW)])
        assert plan.source_name == _NEW

    def test_an_answer_naming_nothing_here_is_ignored_and_the_ladder_carries_on(self):
        plan = _plan(recorded="some-other-plugin", sources=[_source(_OLD, library=True), _source(_NEW)])
        assert plan.source_name == _OLD
        assert plan.choice_required is False

    def test_an_answer_naming_a_location_that_is_gone_is_ignored(self):
        """The folder names are known in advance, so a location is always NAMED.

        Obeying an answer that names one nothing stands behind would copy an
        empty directory into place, settle rung 1 for the life of the install and
        strand the surviving library with no question ever raised again.
        """
        plan = _plan(
            recorded=_NEW,
            sources=[_source(_OLD, library=True), _source(_NEW, present=False)],
        )
        assert plan.source_name == _OLD
        assert plan.choice_required is False


class TestTheLibraryDecides:
    def test_the_one_location_with_a_library_wins_silently(self):
        plan = _plan(sources=[_source(_OLD, library=True), _source(_NEW, settings_mtime=99.0)])
        assert plan.source_name == _OLD
        assert plan.choice_required is False

    def test_two_libraries_migrate_nothing_and_raise_the_choice(self):
        plan = _plan(sources=[_source(_OLD, library=True), _source(_NEW, library=True)])
        assert plan.choice_required is True
        assert plan.source_name is None
        assert plan.outstanding == (SETTINGS_HALF, DATA_HALF)


class TestNoLibraryAnywhere:
    def test_the_configured_location_wins_so_credentials_survive(self):
        plan = _plan(sources=[_source(_OLD), _source(_NEW, settings_mtime=10.0)])
        assert plan.source_name == _NEW

    def test_two_configured_locations_resolve_to_the_newer_one(self):
        plan = _plan(sources=[_source(_OLD, settings_mtime=10.0), _source(_NEW, settings_mtime=20.0)])
        assert plan.source_name == _NEW

    def test_an_identical_mtime_resolves_to_the_first_source(self):
        plan = _plan(sources=[_source(_OLD, settings_mtime=10.0), _source(_NEW, settings_mtime=10.0)])
        assert plan.source_name == _OLD

    def test_nothing_anywhere_names_no_source_and_asks_nothing(self):
        plan = _plan(sources=[_source(_OLD), _source(_NEW)])
        assert plan.source_name is None
        assert plan.choice_required is False
        assert plan.outstanding == (SETTINGS_HALF, DATA_HALF)

    def test_no_sources_at_all_names_no_source(self):
        plan = _plan(sources=[])
        assert plan.source_name is None
        assert plan.choice_required is False
