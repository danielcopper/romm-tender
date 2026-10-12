"""The rule that decides which of a source's roots Tender may use (#2294)."""

from __future__ import annotations

from domain.emulator_sources import SourceFinding
from domain.source_roots import (
    ROOTS_ASSUMED,
    SourceRoot,
    SourceRoots,
    assumed_finding,
    counted_findings,
    reported_findings,
    unusable_reason,
)

_MARKER = "/h/retrodeck.json"


def _root(name: str, provenance: str | None, path: str | None = "/p") -> SourceRoot:
    return SourceRoot(
        name=name,
        path=None if provenance is None else path,
        provenance=provenance,
        settings_file=_MARKER,
    )


def _roots(*findings: SourceFinding, **provenances: str | None) -> SourceRoots:
    """Every root read, but those named in *provenances*."""
    order = ("installation", "roms", "bios", "saves")
    return SourceRoots(
        roots=tuple(_root(name, provenances.get(name, "read")) for name in order),
        findings=findings,
    )


def _finding(code: str, **data: str) -> SourceFinding:
    return SourceFinding(code=code, data=data)


class TestWhichRootIsUsable:
    def test_a_read_root_is_used(self):
        assert _roots().root("bios").usable

    def test_the_frontend_s_own_default_counts_as_read(self):
        # D5: ES-DE starts games from it.
        assert _roots(roms="frontend-default").root("roms").usable

    def test_an_assumed_root_is_not_used(self):
        root = _roots(bios="assumed").root("bios")

        assert not root.usable
        assert root.assumed

    def test_a_root_with_no_path_is_not_used(self):
        root = _roots(roms=None).root("roms")

        assert not root.usable
        assert not root.assumed

    def test_a_root_the_answer_does_not_hold_has_no_path(self):
        missing = SourceRoots(roots=(), findings=()).root("saves")

        assert (missing.path, missing.usable) == (None, False)


class TestTendersOwnFindingForAssumedRoots:
    def test_names_every_assumed_root_in_order_and_the_settings_file(self):
        roots = _roots(saves="assumed", installation="assumed", bios="assumed")

        assert assumed_finding(roots) == _finding(ROOTS_ASSUMED, path=_MARKER, roots="installation,bios,saves")

    def test_none_where_every_root_was_read(self):
        assert assumed_finding(_roots(roms="frontend-default")) is None

    def test_none_where_a_finding_of_the_resolver_s_already_explains_them(self):
        # D7: left out where a marker-* or not-set-up finding speaks for the same roots.
        for code in ("marker-missing", "marker-unreadable", "marker-invalid", "not-set-up"):
            roots = _roots(_finding(code, path=_MARKER), bios="assumed", saves="assumed")

            assert assumed_finding(roots) is None, code

    def test_a_settings_file_whose_version_alone_cannot_be_read_explains_nothing(self):
        roots = _roots(_finding("marker-invalid", path=_MARKER, key="version"), bios="assumed")

        assert assumed_finding(roots) == _finding(ROOTS_ASSUMED, path=_MARKER, roots="bios")


class TestTheReasonARootIsRefusedWith:
    def test_a_usable_root_is_refused_with_nothing(self):
        assert unusable_reason(_roots(bios="assumed"), "roms") is None

    def test_an_assumed_root_is_refused_with_tender_s_own_finding(self):
        assert unusable_reason(_roots(bios="assumed"), "bios") == _finding(ROOTS_ASSUMED, path=_MARKER, roots="bios")

    def test_an_assumed_root_is_refused_with_the_resolver_s_finding_that_explains_it(self):
        missing = _finding("marker-missing", path=_MARKER)

        assert unusable_reason(_roots(missing, bios="assumed"), "bios") == missing

    def test_under_a_damaged_settings_file_a_read_root_is_still_used(self):
        # D4: each root by its own provenance alone.
        damaged = _finding("marker-invalid", path=_MARKER)

        assert unusable_reason(_roots(damaged, bios="assumed"), "roms") is None

    def test_a_root_with_no_path_is_refused_with_not_set_up(self):
        not_set_up = _finding("not-set-up", path=_MARKER)

        assert unusable_reason(_roots(not_set_up, roms=None), "roms") == not_set_up

    def test_a_root_with_no_path_and_no_explaining_finding_is_left_to_the_caller(self):
        assert unusable_reason(_roots(_finding("roms-root-not-absolute", value="r"), roms=None), "roms") is None


class TestMissingRootFindingsCountOnlyForAUsableRoot:
    def test_a_missing_root_finding_about_an_assumed_root_does_not_count(self):
        # D6: the resolver states it for the folder it assumed.
        findings = (
            _finding("root-missing", path="/a"),
            _finding("bios-root-missing", path="/b"),
            _finding("saves-root-missing", path="/s"),
        )
        roots = _roots(*findings, installation="assumed", bios="assumed", saves="assumed")

        assert counted_findings(roots, roots.findings) == ()

    def test_a_missing_root_finding_about_a_read_root_counts(self):
        findings = (_finding("root-missing", path="/a"), _finding("saves-root-missing", path="/s"))
        roots = _roots(*findings, saves="assumed")

        assert counted_findings(roots, roots.findings) == (findings[0],)

    def test_every_other_finding_counts(self):
        other = _finding("catalogue-invalid", path="/c", problem="parse-error")
        roots = _roots(other, bios="assumed")

        assert counted_findings(roots, roots.findings) == (other,)


class TestWhatMainAndTheCardShow:
    def test_the_counted_findings_then_tender_s_own(self):
        bios_missing = _finding("bios-root-missing", path="/b")
        catalogue = _finding("catalogue-invalid", path="/c", problem="parse-error")
        roots = _roots(bios="assumed")

        assert reported_findings(roots, (catalogue, bios_missing)) == (
            catalogue,
            _finding(ROOTS_ASSUMED, path=_MARKER, roots="bios"),
        )
