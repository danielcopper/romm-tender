"""Directories a distribution's installer fills by downloading something into them.

:mod:`atlas.distribution_supplied` beside this one answers *whose file is at
this destination* and answers it from the machine: the distribution ships a
copy, both files are hashed, and equal bytes state it. EmuDeck ships no such
copies. Where RetroDECK's RetroArch component copies a tree out of its own
deploy, EmuDeck's installer fetches one over the network and unpacks it into
the firmware root, so there is nothing on the machine to compare the placed
bytes against — no checksum in the script, no version pinned in the URL, and
the archive's member list written nowhere but the archive.

That leaves a statement a consumer still needs and a statement atlas may not
make. It may not say the file at such a destination is the distribution's:
nothing establishes it, and ``supplied_by`` stays ``None`` under a download
exactly as it does under an unread distribution. What it can say is about the
**directory**, whether or not a file is in it: the installer fills this
directory by downloading that URL, so a file missing here is one the installer
would fetch rather than one a user must find, and a file present here is of
unestablished provenance rather than the user's own dump. That is the caveat
this table feeds (``firmware-installer-download``), and it is why the two
tables are separate rather than one table with two evidence classes — an
unverifiable provenance on ``supplied_by`` would dilute a field whose whole
content is a measured equality.

``invoked`` is the reachability half, and it has two words because the reading
found two states and neither is "it runs". ``retroarch-setup`` is a step the
RetroArch component's own setup path reaches — and the word says setup rather
than install because that is what was measured: EmuDeck's ``RetroArch_init``
and ``RetroArch_update`` reach it, its ``RetroArch_install`` does not.
``unestablished`` is a step
written in the script that nothing in the read repository calls, which is not
the same as dead — a frontend the reading did not cover may invoke it by name
— and not the same as live either. Only an established step reaches an answer
(:attr:`DistributionDownloads.established`); an unestablished one is recorded
so the next reading starts from what the last one measured, and states nothing
in the meantime.

The version pin carries more weight here than in the copy list next door.
There, every statement rests on a hash read from the live machine and a stale
table merely under-reports. Here nothing on the machine confirms the step at
all, so the entry's ``version`` — the distribution revision the citations were
read at — is the whole warrant for what a statement claims, and a release that
stops downloading a tree leaves the table saying something no longer true. Read
the script again at a release; this is a table to re-read, not one to grow by
guesswork.

A distribution with no card, and a destination no entry covers, are the same
answer: nothing is stated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from ._data import packaged_text

DOWNLOADS_SCHEMA = 1

# What one download step places. Every step read so far fetches an archive and
# fills a directory with it, which is a ``tree``: the files that land are the
# archive's, and the card states none of them, because the script names none of
# them either. The vocabulary is closed and one word long for the same reason
# the copy list's is two words long — the card records the kind that was read,
# and a kind nobody has read is one nobody may write.
DOWNLOAD_TREE = "tree"
DOWNLOAD_KINDS = (DOWNLOAD_TREE,)

# Which step runs the download, as far as the reading established. The first
# word names a step the component's own setup path reaches — EmuDeck's
# ``RetroArch_init`` and ``RetroArch_update``, and deliberately not
# ``RetroArch_install``, which installs the flatpak and its cores and reaches
# no download of this kind. The second word is the refusal to claim either way.
INVOKED_RETROARCH_SETUP = "retroarch-setup"
INVOKED_UNESTABLISHED = "unestablished"
DOWNLOAD_INVOCATIONS = (INVOKED_RETROARCH_SETUP, INVOKED_UNESTABLISHED)


@dataclass(frozen=True, slots=True)
class DownloadEntry:
    """One download step: what it fills, from where, when, and the lines that say so.

    ``destination`` is relative to the firmware root — the directory the step
    fills, never a file in it, because which files the archive holds is the
    archive's business and the script states none of them. Filling it is not
    the same as unpacking *into* it: EmuDeck's step unpacks into the firmware
    root, and the tree lands here only if the archive carries the prefix —
    which the script never names and no reading here has opened.

    ``condition`` is prose on purpose: it says when the step does its work
    (EmuDeck's PPSSPP step only where the directory is empty), which a reader
    weighs and no resolver branches on, because atlas cannot know whether the
    directory was empty at the time the installer last ran.
    """

    kind: str
    destination: str
    url: str
    condition: str
    invoked: str
    citation: str

    def covers(self, relative: str) -> bool:
        """Is *relative* — a path below the firmware root — inside this destination?

        Below it, never equal to it: the destination is the directory the step
        fills, and a requirement whose own path *is* that directory is a
        declaration about the folder rather than about something the download
        put in it.
        """
        return relative.startswith(f"{self.destination}/")


@dataclass(frozen=True, slots=True)
class DistributionDownloads:
    """One distribution's download steps, with everything a statement about them carries.

    ``version`` pins the distribution revision the citations were read at and
    ``card_version`` the revision of this table. **Both** travel into the
    answer, where a ``supplied_by`` statement carries only the second: that
    statement rests on an equality measured on the machine, so which release
    was read is provenance, while a statement made from this table rests on
    the reading alone — so the release it was read at is the warrant, and a
    consumer weighing the statement has to be able to see it.
    """

    distribution: str
    card_version: str
    reviewed: str
    version: str
    entries: tuple[DownloadEntry, ...]

    @property
    def established(self) -> tuple[DownloadEntry, ...]:
        """The entries whose step some read code path reaches.

        An entry with ``invoked`` ``unestablished`` is recorded and answers
        nothing: stating that an installer fills a directory, when no reading
        found the code that would run the step, would be a claim about a
        machine nobody looked at.
        """
        return tuple(entry for entry in self.entries if entry.invoked != INVOKED_UNESTABLISHED)

    def covering(self, relative: str) -> DownloadEntry | None:
        """The established entry whose destination holds *relative*, or ``None``.

        ``None`` is the ordinary answer: most of a firmware root is the user's
        own, and this table covers only the directories an installer fills
        itself.
        """
        for entry in self.established:
            if entry.covers(relative):
                return entry
        return None


def _expect_str(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where}: expected a non-empty string, got {value!r}")
    return value


def _expect_relpath(value: Any, where: str) -> str:
    text = _expect_str(value, where)
    if text.startswith("/") or text.endswith("/") or ".." in text.split("/"):
        raise ValueError(f"{where}: expected a clean relative path, got {text!r}")
    return text


def _expect_url(value: Any, where: str) -> str:
    """The fetch this entry describes, which is an HTTPS one or nothing.

    Not a general URL check: it is the one property that makes the recorded
    address the address a reader can go and look at, and a plain-HTTP or
    file-scheme spelling in this table would describe a step nobody read.
    """
    text = _expect_str(value, where)
    if not text.startswith("https://"):
        raise ValueError(f"{where}: expected an https:// url, got {text!r}")
    return text


_ENTRY_KEYS = {"kind", "destination", "url", "condition", "invoked", "citation"}


def _entry(raw: Any, where: str) -> DownloadEntry:
    if not isinstance(raw, dict) or set(raw) != _ENTRY_KEYS:
        raise ValueError(f"{where}: an entry names exactly {sorted(_ENTRY_KEYS)}, got {raw!r}")
    kind = _expect_str(raw["kind"], f"{where}: kind")
    if kind not in DOWNLOAD_KINDS:
        raise ValueError(f"{where}: kind must be one of {DOWNLOAD_KINDS}, got {kind!r}")
    invoked = _expect_str(raw["invoked"], f"{where}: invoked")
    if invoked not in DOWNLOAD_INVOCATIONS:
        raise ValueError(
            f"{where}: invoked must be one of {DOWNLOAD_INVOCATIONS}, got {invoked!r}"
        )
    return DownloadEntry(
        kind=kind,
        destination=_expect_relpath(raw["destination"], f"{where}: destination"),
        url=_expect_url(raw["url"], f"{where}: url"),
        condition=_expect_str(raw["condition"], f"{where}: condition"),
        invoked=invoked,
        citation=_expect_str(raw["citation"], f"{where}: citation"),
    )


def _refuse_overlapping_destinations(entries: tuple[DownloadEntry, ...], where: str) -> None:
    """No path may be reached by two entries — one directory, one statement, one URL.

    Two forms of collision, one rule, and the copy list next door refuses the
    same pair for the same reason: the same destination twice, and a
    destination that contains another entry's (``rtp`` beside ``rtp/2000``).
    Either leaves one path covered by two entries while the statement about it
    is made once, so the URL a reader is shown would be whichever entry
    :meth:`DistributionDownloads.covering` met first — a silent pick between
    two recorded facts, and one that changes with the order they were written
    in.
    """
    destinations = [entry.destination for entry in entries]
    if len(set(destinations)) != len(destinations):
        raise ValueError(f"{where}: two entries claim the same destination")
    for entry in entries:
        for other in destinations:
            if other != entry.destination and other.startswith(f"{entry.destination}/"):
                raise ValueError(
                    f"{where}: the destination {entry.destination!r} contains the destination "
                    f"{other!r}, so a path below it would be covered by two entries"
                )


_DISTRIBUTION_KEYS = {"version", "entries"}


def _distribution(
    name: str, raw: Any, *, card_version: str, reviewed: str
) -> DistributionDownloads:
    where = f"distribution-downloads card {name!r}"
    if not isinstance(raw, dict) or set(raw) != _DISTRIBUTION_KEYS:
        raise ValueError(f"{where}: expected exactly {sorted(_DISTRIBUTION_KEYS)}, got {raw!r}")
    raw_entries = raw["entries"]
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError(f"{where}: entries must be a non-empty list, got {raw_entries!r}")
    entries = tuple(_entry(entry, f"{where}: entries[{i}]") for i, entry in enumerate(raw_entries))
    _refuse_overlapping_destinations(entries, where)
    return DistributionDownloads(
        distribution=name,
        card_version=card_version,
        reviewed=reviewed,
        version=_expect_str(raw["version"], f"{where}: version"),
        entries=entries,
    )


def load_distribution_downloads(text: str | None = None) -> dict[str, DistributionDownloads]:
    """Load the packaged download lists (or *text* when supplied, for tests)."""
    if text is None:
        text = packaged_text("distribution_downloads.json")
    raw = json.loads(text)
    if not isinstance(raw, dict) or raw.get("schema") != DOWNLOADS_SCHEMA:
        raise ValueError(
            f"distribution_downloads: unsupported schema "
            f"{raw.get('schema') if isinstance(raw, dict) else None!r} "
            f"(this atlas reads schema {DOWNLOADS_SCHEMA})"
        )
    card_version = _expect_str(raw.get("version"), "distribution_downloads: version")
    reviewed = _expect_str(raw.get("reviewed"), "distribution_downloads: reviewed")
    distributions = raw.get("distributions", {})
    if not isinstance(distributions, dict):
        raise ValueError(
            f"distribution_downloads: distributions must be an object, got {distributions!r}"
        )
    return {
        name: _distribution(name, entry, card_version=card_version, reviewed=reviewed)
        for name, entry in distributions.items()
    }


_PACKAGED: dict[str, DistributionDownloads] | None = None


def lookup_distribution_downloads(distribution: str | None) -> DistributionDownloads | None:
    """The packaged download list for one distribution, or ``None`` — no fuzzy matching."""
    global _PACKAGED
    if _PACKAGED is None:
        _PACKAGED = load_distribution_downloads()
    if distribution is None:
        return None
    return _PACKAGED.get(distribution)
