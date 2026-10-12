"""A source's root folders as the resolver answers them, and the rule that decides which of them Tender may use.

The resolver answers every root — the installation root, the ROM root, the
BIOS folder and the saves root — with where its path came from: ``read`` from
the source's settings, ``frontend-default`` where the frontend's own default
applies, or ``assumed`` where nothing on the machine names it and the
resolver fell back on the source's usual layout. Tender uses a root that was
read or that the frontend uses by default, and none that was only assumed: no
download lands in it, no removal is bounded by it, and nothing is linked into
it. A system's ROM folder lies below the ROM root and goes with it.

The resolver states an assumed root as that provenance and as no finding of
its own, so Tender words it as a finding of its own, :data:`ROOTS_ASSUMED`,
which Main's notice, the source's card and a refused press all show.

The rule knows no source: every source the resolver answers roots for is
judged by it, whoever asks.

Pure compute — no I/O, no state mutation.
"""

from __future__ import annotations

from dataclasses import dataclass

from domain.emulator_sources import SourceFinding

# The four roots of a source, in the resolver's order.
ROOT_INSTALLATION = "installation"
ROOT_ROMS = "roms"
ROOT_BIOS = "bios"
ROOT_SAVES = "saves"
_ROOT_ORDER = (ROOT_INSTALLATION, ROOT_ROMS, ROOT_BIOS, ROOT_SAVES)

# Where a root's path came from, as the resolver spells it.
PROVENANCE_READ = "read"
PROVENANCE_FRONTEND_DEFAULT = "frontend-default"
PROVENANCE_ASSUMED = "assumed"
_USABLE_PROVENANCES = frozenset({PROVENANCE_READ, PROVENANCE_FRONTEND_DEFAULT})

# Tender's own finding naming the roots a source's settings file leaves out.
# ``data`` holds the settings file under ``path`` and the roots, in the order
# above and joined by commas, under ``roots``.
ROOTS_ASSUMED = "roots-assumed"

# The resolver's findings that already say why a source's roots were not read:
# its settings file is missing, unreadable or damaged, or it is not set up yet.
# A ``marker-invalid`` on the ``version`` key alone leaves every root read, so
# it explains none.
_EXPLAINING_CODES = frozenset({"marker-missing", "marker-unreadable", "marker-invalid", "not-set-up"})
_VERSION_KEY = "version"

# The resolver's findings for a root that does not exist, by the root each is
# about. The resolver states them for an assumed root too, where they would
# name a folder nobody configured, so they count only for a root Tender uses.
_MISSING_ROOT_FINDINGS = {
    "root-missing": ROOT_INSTALLATION,
    "bios-root-missing": ROOT_BIOS,
    "saves-root-missing": ROOT_SAVES,
}


@dataclass(frozen=True, slots=True)
class SourceRoot:
    """One root of a source: its path, where the path came from, and the settings file that would name it.

    ``path`` and ``provenance`` are ``None`` together, where the resolver
    states no root at all; ``settings_file`` is ``None`` where no setting
    decides the root.
    """

    name: str
    path: str | None
    provenance: str | None
    settings_file: str | None

    @property
    def usable(self) -> bool:
        """Whether Tender may use this root: it has a path, read or the frontend's own default."""
        return self.path is not None and self.provenance in _USABLE_PROVENANCES

    @property
    def assumed(self) -> bool:
        """Whether the resolver only assumed this root's path."""
        return self.path is not None and self.provenance == PROVENANCE_ASSUMED


@dataclass(frozen=True, slots=True)
class SourceRoots:
    """A source's roots, and the findings the resolver stated beside them, from one answer."""

    roots: tuple[SourceRoot, ...]
    findings: tuple[SourceFinding, ...]

    def root(self, name: str) -> SourceRoot:
        """The root called *name*; one with no path where the answer holds none of that name."""
        found = next((root for root in self.roots if root.name == name), None)
        return found if found is not None else SourceRoot(name=name, path=None, provenance=None, settings_file=None)


def explaining_finding(roots: SourceRoots) -> SourceFinding | None:
    """The resolver's finding that already says why the source's roots were not read, or ``None``."""
    return next((finding for finding in roots.findings if _explains(finding)), None)


def _explains(finding: SourceFinding) -> bool:
    if finding.code not in _EXPLAINING_CODES:
        return False
    return not (finding.code == "marker-invalid" and finding.data.get("key") == _VERSION_KEY)


def assumed_finding(roots: SourceRoots) -> SourceFinding | None:
    """Tender's :data:`ROOTS_ASSUMED` finding naming every root the resolver only assumed, or ``None``.

    ``None`` where no root was assumed, and where a finding of the resolver's
    already explains why they were not read, which then speaks for them.
    """
    assumed = [roots.root(name) for name in _ROOT_ORDER if roots.root(name).assumed]
    if not assumed or explaining_finding(roots) is not None:
        return None
    settings_file = next((root.settings_file for root in assumed if root.settings_file), "")
    return SourceFinding(
        code=ROOTS_ASSUMED,
        data={"path": settings_file, "roots": ",".join(root.name for root in assumed)},
    )


def unusable_reason(roots: SourceRoots, name: str) -> SourceFinding | None:
    """The finding a refusal of the root *name* answers with, or ``None`` where none speaks for it.

    ``None`` too where the root may be used. A root the resolver only assumed
    is always spoken for — by the resolver's finding that explains it, or by
    Tender's own; a root with no path only by the resolver's.
    """
    root = roots.root(name)
    if root.usable:
        return None
    explaining = explaining_finding(roots)
    if explaining is not None:
        return explaining
    return assumed_finding(roots) if root.assumed else None


def counted_findings(roots: SourceRoots, findings: tuple[SourceFinding, ...]) -> tuple[SourceFinding, ...]:
    """*findings* without a missing-root finding about a root Tender does not use."""
    return tuple(
        finding
        for finding in findings
        if finding.code not in _MISSING_ROOT_FINDINGS or roots.root(_MISSING_ROOT_FINDINGS[finding.code]).usable
    )


def reported_findings(roots: SourceRoots, findings: tuple[SourceFinding, ...]) -> tuple[SourceFinding, ...]:
    """What Main and the source's card show of *findings*: those that count, and Tender's own for its assumed roots."""
    assumed = assumed_finding(roots)
    return (*counted_findings(roots, findings), *(() if assumed is None else (assumed,)))
