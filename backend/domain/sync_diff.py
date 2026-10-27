"""Sync diffs — pure delta computations between current state and last-synced state.

Anything that compares "what the next sync should produce" against "what we
recorded last sync" lives here: per-ROM bucketing (new / changed / unchanged /
stale), enabled-collection add/remove diffs, the platform-collection
membership predicate that drives the platform-collection diff, and the platform
name a ROM is filed under.

State reads (registry, last_synced_*), setting reads, network fetches, and
Steam interaction stay in LibraryService; this module receives the relevant
slices as primitive parameters and returns primitives or NamedTuple results.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, NamedTuple

from domain.collection_name import fold_collection_name
from domain.rom_file import has_file_on_romm
from domain.sibling_resolution import AUTO_REGION, canonical_group_name, fs_name_stem, resolve_group_representative

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence
    from collections.abc import Set as AbstractSet

    from domain.rom import DeliveryRoute, GoneEvidence

# Marker key a rebind entry carries so the per-unit commit moves the DB binding
# from the bound sibling it is keyed by (the entry's ``rom_id``, kept so the
# frontend reuses its existing shortcut) onto the same dump under the sibling it
# names. Absent on normal / grandfathered entries. See
# :func:`collapse_sibling_groups`.
BIND_ROM_ID_KEY = "bind_rom_id"


class ClassificationResult(NamedTuple):
    new: list[dict[str, Any]]
    changed: list[dict[str, Any]]
    unchanged_ids: list[int]
    stale: list[int]
    disabled_count: int


def classify_roms(
    shortcuts_data: list[dict[str, Any]],
    registry: dict[str, Any],
    fetched_platform_names: set[str],
    *,
    skipped_rom_ids: frozenset[int] = frozenset(),
) -> ClassificationResult:
    """Bucket fetched ROMs against the saved shortcut registry.

    Returns the ROMs split into new (not in registry), changed (registry
    entry exists but a *persisted* identity field — name, platform_slug, or
    fs_name — differs, OR, for an entry outside *skipped_rom_ids*, either the
    target ``launch_options`` differs from the recorded
    ``applied_launch_options`` or the entry is a rebind — it carries
    ``bind_rom_id`` — whose shortcut keeps its name while its binding and launch
    command move to the target, which is work the apply does and the preview has
    to offer),
    unchanged_ids (otherwise), stale (in registry but not in the current
    fetch), and the count of stale ROMs whose stored platform no longer appears
    in fetched_platform_names.

    ``applied_launch_options`` is the launch command last written to the ROM's
    Steam shortcut (recorded by the six writer sites, #1383). Comparing the
    built target ``launch_options`` against it is what lets the delta-restricted
    apply skip a content-correct shortcut rather than re-touching it: an identity
    match with a launch-options match is genuinely unchanged, while an
    install/uninstall (or core/disc pin change) that leaves identity untouched
    still flips the item to "changed". Outside *skipped_rom_ids*, no skip is
    ever taken on unknown recorded state — no data is invented: a NULL recorded
    value (``None`` — a pre-migration-015 row, or a freshly created row not yet
    recorded) never matches a target string, so such a row is always "changed"
    and re-applied once; the writer sites then record the value and the next
    sync skips it.

    *skipped_rom_ids* are the ROMs the preview took from a unit the fetcher
    reported as skipped. Apply returns before it classifies such a unit, so it
    never rewrites their shortcuts and never records their launch command; a
    launch command that differs from the recorded one there — NULL included —
    is no change the apply would make, and counting it would show the same
    phantom "changed" on every preview. Such an entry is "changed" only for an
    identity difference; Steam's copy of an installed game's command is kept
    current by the launch-options reconcile instead. A rebind keyed by one is
    no change either, for the same reason: the apply moves no binding for a
    unit it skipped. That also keeps the stem a skipped platform's rows are
    rebuilt with — the file name without its extension, since the stored row
    keeps no ``fs_name_no_ext`` — from deciding anything. The apply path
    classifies only units it fetched and passes none.

    ``platform_name`` is deliberately excluded from the changed comparison:
    it is a derived display field, never persisted on the ``roms`` row. The
    registry side resolves it from the enabled-platforms-only slug→name map
    (falling back to the bare slug for a disabled platform), while an entry
    carries RomM's display name, so a divergence between the two can never be
    healed by an apply (the upsert writes only platform_slug/name/fs_name) —
    comparing it produced a permanent phantom "changed" delta (#1292). Platform
    display renames are diffed separately by
    ``compute_platform_collection_diff``. ``platform_name`` is still read here
    for the disabled-platform stale count, which intentionally keys on the
    live-resolved display name.

    Changed ROMs are returned as fresh dicts with an added ``existing_app_id``
    key — the caller's shortcuts_data is not mutated.
    """
    new: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []
    unchanged_ids: list[int] = []

    for sd in shortcuts_data:
        reg = registry.get(str(sd["rom_id"]))
        if not reg or not reg.get("app_id"):
            new.append(sd)
        elif _identity_differs(reg, sd) or (
            sd["rom_id"] not in skipped_rom_ids
            and (BIND_ROM_ID_KEY in sd or reg.get("applied_launch_options") != sd.get("launch_options", ""))
        ):
            changed.append({**sd, "existing_app_id": reg["app_id"]})
        else:
            unchanged_ids.append(sd["rom_id"])

    current_ids = {sd["rom_id"] for sd in shortcuts_data}
    stale = [int(rid) for rid in registry if int(rid) not in current_ids]
    disabled_count = count_disabled_platform_removals(stale, registry, fetched_platform_names)
    return ClassificationResult(new, changed, unchanged_ids, stale, disabled_count)


def count_disabled_platform_removals(
    stale: Iterable[int], registry: dict[str, Any], fetched_platform_names: set[str]
) -> int:
    """How many of the *stale* rom ids sit on a platform whose name is not in *fetched_platform_names*."""
    return sum(1 for rid in stale if registry.get(str(rid), {}).get("platform_name") not in fetched_platform_names)


def _identity_differs(reg: dict[str, Any], sd: dict[str, Any]) -> bool:
    """Whether a persisted identity field — name, platform_slug, fs_name — differs."""
    return (
        reg.get("name") != sd["name"]
        or reg.get("platform_slug") != sd.get("platform_slug")
        or reg.get("fs_name") != sd.get("fs_name", "")
    )


def platform_breakdown(
    new: list[dict[str, Any]],
    changed: list[dict[str, Any]],
    stale: list[int],
    registry: dict[str, Any],
    synced_platforms: Collection[str],
    platform_names: Mapping[str, str],
) -> list[dict[str, Any]]:
    """Regroup one classification's buckets into per-platform rows.

    *new* and *changed* are the built entries :func:`classify_roms` returned,
    each carrying ``platform_slug``; *stale* are its stale rom ids, whose
    platform is read off their *registry* entry. *registry* is the
    ``do_read_preview_baseline`` projection keyed by ``str(rom_id)``.
    *synced_platforms* are the slugs of the run's platform units, so membership
    in it is what ``synced`` reports.

    Returns one row per platform holding at least one of the three counts —
    ``{"slug", "name", "synced", "new_count", "changed_count", "remove_count"}``
    — ordered by name (case-insensitively, the slug breaking a tie). The name
    is the one *platform_names* holds for the slug, else the slug: the names
    the preview's platform-collection diff files by, so a row carries the name
    the apply gives the platform's Steam collection, synced or not.
    The ``platform_name`` an entry or the registry carries is not read.

    ``synced`` is False for a platform outside the run's platform list. Its
    toggle went off, RomM stopped listing it, or the only route to it is an
    enabled collection — collection units are not filtered by platform
    enablement. The causes compose, so one row can carry removals for the ROMs
    the run no longer fetches and new or changed counts for the ROMs a
    collection still reaches.

    The counts mirror the summary's library-wide ``new_count`` /
    ``changed_count`` / ``remove_count``, over the same three buckets, so a
    column's sum is that total.
    """
    counts: dict[str, dict[str, int]] = {}
    for column, entries in (("new_count", new), ("changed_count", changed)):
        for sd in entries:
            counts.setdefault(sd.get("platform_slug") or "", _zero_counts())[column] += 1
    for rom_id in stale:
        slug = registry.get(str(rom_id), {}).get("platform_slug") or ""
        counts.setdefault(slug, _zero_counts())["remove_count"] += 1

    names = {slug: platform_names.get(slug, slug) for slug in counts}
    return [
        {
            "slug": slug,
            "name": names[slug],
            "synced": slug in synced_platforms,
            **counts[slug],
        }
        for slug in sorted(counts, key=lambda slug: (names[slug].casefold(), slug))
    ]


def _zero_counts() -> dict[str, int]:
    """A fresh all-zero count row for one platform."""
    return {"new_count": 0, "changed_count": 0, "remove_count": 0}


def collapse_sibling_groups(
    shortcuts_data: list[dict[str, Any]],
    registry: dict[str, dict[str, Any]],
    installed_rom_ids: set[int],
    *,
    complete_group_view: bool | AbstractSet[str | None],
    preferred_region: str = AUTO_REGION,
) -> list[dict[str, Any]]:
    """Collapse per-ROM shortcut entries to one Steam shortcut per sibling group.

    A sibling group (same ``sibling_group_key``) is one game = at most one NEW
    Steam shortcut (ADR-0021 §2). *shortcuts_data* is the built entry for every
    fetched ROM; *registry* is the bound ``roms`` rows keyed by ``str(rom_id)``
    (each carrying ``app_id``, ``name``, ``fs_name``, ``platform_slug`` and
    ``sibling_group_key``); *installed_rom_ids* are the ROMs currently on disk.

    *complete_group_view* asserts whether *shortcuts_data* holds the WHOLE
    membership of a group a bound row belongs to — the load-bearing safety
    input: ``True`` for every group, ``False`` for none, or the set of group keys
    it holds whole. A sibling group is per-platform, so a **platform** unit fetch
    is a complete view: it holds every member its platform listing returns, and
    a bound version hidden in RomM, which that listing leaves out, is collapsed
    as one gone from RomM; a **collection** unit is a PARTIAL view (it spans
    platforms and may fetch just one unbound sibling of a group whose other
    members — including the bound one — were never in this fetch); the
    whole-library **preview** union holds some groups whole and others not, so
    it passes the keys. Only a complete view may conclude "bound sibling absent
    from members ⇒ vanished from the server"; inferring that from a partial view
    would rebind a live installed game's shortcut onto an uninstalled sibling
    (#1296 CRITICAL).

    Returns the subset of entries to actually emit as shortcuts, one lane per
    group:

    * **Grandfathered** — the group holds a binding: every surviving bound
      sibling keeps its own entry (ADR-0021 §5). No new shortcut is minted, and
      an unbound fetched sibling becomes a tracked row with no shortcut. A bound
      sibling that vanished from the server emits nothing here and keeps its
      shortcut and its binding: the stale removal keeps it too, and the game page
      says the version is gone.
    * **Rebind onto the same dump** (complete view only) — a bound sibling moves
      its binding onto a fetched sibling that has a file, holds no shortcut
      (its own, or one rebound to it earlier in this group) and carries the same
      ``fs_name_no_ext``. One synthetic entry (see :func:`_rebind_entry`) keeps
      the shortcut. Two bound siblings qualify: one that vanished, whether
      installed or not, whose stem is read off its stored ``fs_name`` — the
      server reissued its id, as a re-import or a server switch does; and a
      surviving one RomM holds no file for and that is not installed — the same
      dump in a new shape, as when a rescan turns ``Game (USA).zip`` into a
      folder ``Game (USA)``. A sibling whose name differs at all is another
      version the user did not choose — RomM does not send every dimension it
      parses, so comparing the dimensions would cross versions — and the
      binding then stays where it is.
    * **New** — no binding anywhere in the group: emit the single representative
      chosen by :func:`resolve_group_representative`, but with its display
      ``name`` replaced by :func:`canonical_group_name` — the region-preferred
      member's name mints the sticky Steam shortcut (ADR-0021 §2/§3), while the
      representative's ``rom_id`` / launch bake stay the bind target. This is the
      only lane that renames: an already-bound group (grandfathered / rebind)
      carries its persisted name verbatim, so a live shortcut is never renamed.
    * **Grandfathered untouched** (partial view only) — the group holds a binding
      that is not in THIS fetch: emit nothing. Absence from a partial view is not
      absence from the server, so the binding is left alone (no rebind, no second
      shortcut). Where the group's platform is synced, the bound member rides
      that platform unit in the same run; where only collections reach the
      group, the stale removal keeps the binding, since the run delivered a
      member of its group (:func:`sort_stale_rows`). The unbound fetched members
      still persist via ``pending_all_roms`` and the reporter's collection
      group-fallback places the group's shortcut in the Steam collection.

    Bound rows are grouped by their FETCHED sibling key when the row is in this
    fetch (so a legacy row whose stored key is still NULL is placed in its real
    group and grandfathered rather than churned), falling back to the stored key
    for a vanished bound row.

    *preferred_region* re-heads the region ranking that both the representative
    and the canonical name fall back to (``"auto"`` = the build-time default
    order); it is read from settings by the caller and must be the same value at
    every collapse call site within one sync run.
    """
    # Keys are ``str | None``: a built entry always carries a real key, but a
    # legacy bound row may still hold NULL (its own solo/unmatched group).
    by_group: dict[str | None, list[dict[str, Any]]] = {}
    for sd in shortcuts_data:
        by_group.setdefault(sd.get("sibling_group_key"), []).append(sd)

    bound_by_group = _bound_rows_by_group(
        {sd["rom_id"]: sd.get("sibling_group_key") for sd in shortcuts_data}, registry
    )
    bound_rom_ids = {rid for rows in bound_by_group.values() for rid, _ in rows}

    emitted: list[dict[str, Any]] = []
    for group_key, members in by_group.items():
        fetched_ids = {m["rom_id"] for m in members}
        fileless = {m["rom_id"] for m in members if not has_file_on_romm(m)}
        bound_here = sorted(bound_by_group.get(group_key, []), key=lambda item: item[0])
        if not bound_here:
            # No binding anywhere in the group → mint the single representative,
            # named after the region-preferred (canonical) member. The bind
            # target stays the representative's rom_id + launch bake; only the
            # sticky Steam name follows the pure region ranking (ADR-0021 §2/§3).
            # Copy so the original member dict (also staged in pending_all_roms
            # for the per-sibling identity upsert) keeps its own real name.
            rep_id = resolve_group_representative(
                members, installed_rom_ids, bound_rom_ids, preferred_region, fileless_rom_ids=fileless
            )
            emitted.append({**_member_by_id(members, rep_id), "name": canonical_group_name(members, preferred_region)})
            continue
        # A partial view (collection unit) may not read a bound sibling's absence
        # as absence from the server: the group's platform unit owns it.
        whole = _holds_whole(complete_group_view, group_key)
        emitted.extend(
            _bound_sibling_entries(
                members,
                surviving=[item for item in bound_here if item[0] in fetched_ids],
                vanished=[item for item in bound_here if item[0] not in fetched_ids] if whole else [],
                chain=_GroupChain(installed_rom_ids, bound_rom_ids, fileless, preferred_region),
                complete_group_view=whole,
            )
        )
    return emitted


def gather_unbound_groups(
    deliveries: Sequence[Sequence[dict[str, Any]]], registry: dict[str, dict[str, Any]]
) -> list[list[dict[str, Any]]]:
    """*deliveries* with every member of a group no bound row holds moved into the first delivery holding one.

    *deliveries* is what the collection units of one run deliver, in queue order:
    each a list of raw ROM dicts carrying their ``sibling_group_key``. *registry*
    is the bound ``roms`` rows keyed by ``str(rom_id)``, as
    :func:`collapse_sibling_groups` takes it, and a group counts as bound exactly
    as that collapse groups it. The unit holding the first delivery of an unbound
    group then mints it over every member the run delivers, as the preview's
    union does, instead of over its own members alone. A bound group's members
    stay where they were delivered. A moved member follows the delivery's own
    ROMs, in delivery order.
    """
    bound_keys = set(
        _bound_rows_by_group(
            {rom["id"]: rom.get("sibling_group_key") for delivery in deliveries for rom in delivery}, registry
        )
    )
    first_delivery: dict[str | None, int] = {}
    for index, delivery in enumerate(deliveries):
        for rom in delivery:
            key = rom.get("sibling_group_key")
            if key not in bound_keys:
                first_delivery.setdefault(key, index)
    gathered: list[list[dict[str, Any]]] = [[] for _ in deliveries]
    for index, delivery in enumerate(deliveries):
        for rom in delivery:
            gathered[first_delivery.get(rom.get("sibling_group_key"), index)].append(rom)
    return gathered


def _bound_rows_by_group(
    fetched_key_by_id: Mapping[int, str | None], registry: dict[str, dict[str, Any]]
) -> dict[str | None, list[tuple[int, dict[str, Any]]]]:
    """The bound rows of *registry*, as ``(rom_id, row)`` pairs grouped by sibling-group key.

    A row in *fetched_key_by_id* is grouped by its fetched key, so a legacy row
    whose stored key is still NULL lands in its real group; any other row by its
    stored key.
    """
    bound_by_group: dict[str | None, list[tuple[int, dict[str, Any]]]] = {}
    for rid_str, reg in registry.items():
        if not reg.get("app_id"):
            continue
        rid = int(rid_str)
        bound_by_group.setdefault(fetched_key_by_id.get(rid, reg.get("sibling_group_key")), []).append((rid, reg))
    return bound_by_group


def _holds_whole(complete_group_view: bool | AbstractSet[str | None], group_key: str | None) -> bool:
    """Whether :func:`collapse_sibling_groups`' *complete_group_view* holds the group *group_key* whole."""
    if isinstance(complete_group_view, bool):
        return complete_group_view
    return group_key in complete_group_view


class _GroupChain(NamedTuple):
    """What :func:`resolve_group_representative` is asked with for one group."""

    installed_rom_ids: set[int]
    bound_rom_ids: set[int]
    fileless_rom_ids: set[int]
    preferred_region: str


def _bound_sibling_entries(
    members: list[dict[str, Any]],
    *,
    surviving: list[tuple[int, dict[str, Any]]],
    vanished: list[tuple[int, dict[str, Any]]],
    chain: _GroupChain,
    complete_group_view: bool,
) -> list[dict[str, Any]]:
    """A bound group's entries: each surviving bound sibling kept or rebound, each vanished one rebound or nothing.

    *surviving* and *vanished* are the group's bound ``(rom_id, registry row)``
    pairs in and out of *members*, each in ``rom_id`` order, so two siblings that
    would move onto the same target resolve the same way every run: the first
    takes it, and the second stays where it is, since a ROM carries one shortcut.
    *vanished* is empty for a partial view.
    """
    taken = set(chain.bound_rom_ids)
    entries: list[dict[str, Any]] = []
    for rom_id, reg in surviving:
        own = _member_by_id(members, rom_id)
        target = None
        if complete_group_view and rom_id in chain.fileless_rom_ids and rom_id not in chain.installed_rom_ids:
            target = _same_dump_with_file(own.get("fs_name_no_ext"), members, chain, taken)
        if target is None:
            entries.append(own)
            continue
        taken.add(target["rom_id"])
        entries.append(_rebind_entry(target, rom_id, reg))
    for rom_id, reg in vanished:
        target = _same_dump_with_file(fs_name_stem(reg.get("fs_name") or ""), members, chain, taken)
        if target is not None:
            taken.add(target["rom_id"])
            entries.append(_rebind_entry(target, rom_id, reg))
    return entries


def _same_dump_with_file(
    stem: str | None, members: list[dict[str, Any]], chain: _GroupChain, taken: set[int]
) -> dict[str, Any] | None:
    """The sibling a shortcut on the dump named *stem* may move to, or ``None``.

    One with a file, no shortcut, and *stem* as its ``fs_name_no_ext``; of
    several, the one the chain ranks first. An empty stem names no file, so it
    matches nothing.
    """
    if not stem:
        return None
    same_dump = [
        m
        for m in members
        if m["rom_id"] not in chain.fileless_rom_ids and m["rom_id"] not in taken and m.get("fs_name_no_ext") == stem
    ]
    if not same_dump:
        return None
    pick = resolve_group_representative(same_dump, chain.installed_rom_ids, set(), chain.preferred_region)
    return _member_by_id(same_dump, pick)


def _member_by_id(members: list[dict[str, Any]], rom_id: int) -> dict[str, Any]:
    """Return the group member whose ``rom_id`` matches (the resolver picks from *members*)."""
    return next(m for m in members if m["rom_id"] == rom_id)


def _rebind_entry(rep: dict[str, Any], kept_rom_id: int, kept_reg: dict[str, Any]) -> dict[str, Any]:
    """Synthesize the emitted entry for a rebinding group (ADR-0021 §2).

    The bound sibling it is keyed by has vanished from RomM, or is still listed
    with no file, and *rep* is the same dump under another id; the commit treats
    both alike, because the binding moves by ``bind_rom_id`` and the old row is
    only ever unbound, never removed.

    Keyed by the bound sibling (*kept_rom_id*) so the frontend REUSES that
    sibling's existing Steam shortcut (its appId is already in the rom_id→appId
    map); outside a unit the run skipped, :func:`classify_roms` counts it as
    changed, because the binding moves even where the name and launch command
    stay. The entry carries *rep*'s launch bake (its installed path, or the
    empty placeholder when it is uninstalled) plus a ``bind_rom_id`` marker so
    the per-unit commit moves the DB binding onto *rep* — the shortcut's appId,
    artwork, collections and playtime all survive; only the active version
    changes. Identity fields stay the bound sibling's (sticky name — never
    rename automatically, which would change the appId).
    """
    return {
        **rep,
        "rom_id": kept_rom_id,
        "name": kept_reg.get("name", rep.get("name", "")),
        "fs_name": kept_reg.get("fs_name", ""),
        "platform_slug": kept_reg.get("platform_slug", rep.get("platform_slug", "")),
        BIND_ROM_ID_KEY: rep["rom_id"],
    }


def select_stale_removals(
    candidate_stale: list[tuple[int, int]],
    synced_app_ids: set[int],
) -> list[tuple[int, int]]:
    """Stale ``(rom_id, app_id)`` removals minus any app_id re-bound this run.

    Safety invariant: never remove a Steam shortcut whose appId was bound by a
    ROM synced this run. Steam **assigns** an appId at creation and it is stable
    for the shortcut's lifetime (the ``CRC32(exe + name)`` derivation is
    disproven — see ``docs/architecture/steam-non-steam-shortcuts.md`` §App IDs),
    so reuse never comes from re-hashing. It comes from the REBIND lane
    (:func:`collapse_sibling_groups`): on a server switch / re-import, an
    unchanged game's ``rom_id`` is reissued, and the rebind moves the vanished
    sibling's still-live appId onto the same dump's new id in the SAME run. The
    old ``rom_id`` then reads stale while its appId lives on under the new
    binding, so emitting it for removal would wipe the shortcut the run just
    re-bound (#1036).
    """
    return [(rid, aid) for rid, aid in candidate_stale if aid not in synced_app_ids]


class StaleRow(NamedTuple):
    """A bound row no unit of the run returned: its id, its appId, its platform, how it last arrived, and its group."""

    rom_id: int
    app_id: int
    platform_slug: str
    delivered_through: DeliveryRoute | None = None
    sibling_group_key: str | None = None


def group_keys_of(rom_ids: Iterable[int], key_by_rom_id: Mapping[int, str]) -> frozenset[str]:
    """The sibling-group keys *key_by_rom_id* holds for *rom_ids*; a ROM it holds none for adds none."""
    return frozenset(key_by_rom_id[rom_id] for rom_id in rom_ids if rom_id in key_by_rom_id)


# What one exact-id question to RomM established about a ROM: a proven 404
# (``gone``), the very ROM (``present``), or nothing (``unknown``).
RomAnswer = Literal["gone", "present", "unknown"]


class StaleSort(NamedTuple):
    """The stale rows split by what the run knows about each.

    :attr:`ask_only` holds the rows RomM is asked about for their mark alone:
    their game was synced, so they stay bound whatever RomM answers.
    """

    remove: list[StaleRow]
    keep: list[StaleRow]
    ask: list[StaleRow]
    ask_only: tuple[StaleRow, ...] = ()

    def gone_from_romm_marks(
        self, answers: Mapping[int, RomAnswer], read_platform_slugs: Collection[str]
    ) -> dict[int, GoneEvidence | None]:
        """What an apply records on each stale row about RomM still serving it: a mark, ``None`` to clear one.

        *answers* are RomM's to the exact ids of :attr:`ask` and :attr:`ask_only`, and
        *read_platform_slugs* the platforms :func:`sort_stale_rows` was given as
        read whole. A kept row whose platform the run did not read is on a
        platform RomM no longer lists, and is marked ``delisted``. An asked row
        is marked ``not_found`` on a proven 404 and has its mark cleared when
        RomM returned the very ROM; an answer that established nothing is
        absent, so nothing is written for it. A row kept on a platform the run
        read whole is absent too: its platform's stamp already says the fetch
        did not return it.
        """
        marks: dict[int, GoneEvidence | None] = {
            row.rom_id: "delisted" for row in self.keep if row.platform_slug not in read_platform_slugs
        }
        for row in (*self.ask, *self.ask_only):
            answer = answers.get(row.rom_id)
            if answer == "gone":
                marks[row.rom_id] = "not_found"
            elif answer == "present":
                marks[row.rom_id] = None
        return marks


def sort_stale_rows(
    rows: Iterable[StaleRow],
    *,
    read_platform_slugs: Collection[str],
    listed_platform_slugs: Collection[str] | None,
    collection_listed_rom_ids: Collection[int],
    synced_group_keys: Collection[str] = frozenset(),
) -> StaleSort:
    """Split the stale rows into the ones to remove, to keep, and to ask RomM about.

    *read_platform_slugs* are the platforms the run read whole, skipped or
    fetched; *listed_platform_slugs* every platform RomM's listing named this
    run, on in Tender or not (``None`` where that is not known, which reads as
    every platform listed); *collection_listed_rom_ids* the members every
    collection held at its last completed apply, read before the run rewrote
    any of them; *synced_group_keys* the games the run synced — the group keys
    (:func:`group_keys_of`) of every ROM a unit returned, a skipped unit's stamp
    members included.

    * **keep** — the row's platform was read whole and did not return it, so it
      is gone from RomM. A skipped platform rebuilds every bound row, so only a
      fetched one can leave one out. Also kept: a row last delivered through
      its platform, or never delivered since the route was recorded, whose
      platform RomM no longer lists — that platform was on, and RomM's listing
      is a complete answer, so the game is gone from RomM rather than turned
      off.
    * **ask** — the run did not read the row's platform, and the row was last
      delivered through a collection, or, with no route recorded, a
      collection's last completed apply listed it. Only RomM's answer to its
      exact id tells "gone" from "left the collection".
    * **ask_only** — a row the rule above asks about whose game the run
      synced: RomM's answer still sets or clears its mark, and it stays bound
      whatever the answer is.
    * **none of these** — any other row whose game the run synced, on a
      platform the run did not read: the row holds the game's one shortcut. A
      version switch binds a member no unit delivers. A row with no group key
      is judged alone.
    * **remove** — the rest: the row's platform is listed and turned off in
      Tender, no collection reached it, and no unit delivered its game.
    """
    listed = None if listed_platform_slugs is None else set(listed_platform_slugs)
    sort = StaleSort(remove=[], keep=[], ask=[])
    ask_only: list[StaleRow] = []
    for row in rows:
        game_synced = row.sibling_group_key in synced_group_keys
        if row.platform_slug in read_platform_slugs:
            sort.keep.append(row)
        elif _reached_through_a_collection(row, collection_listed_rom_ids):
            (ask_only if game_synced else sort.ask).append(row)
        elif game_synced:
            continue
        elif listed is not None and row.platform_slug not in listed:
            sort.keep.append(row)
        else:
            sort.remove.append(row)
    return sort._replace(ask_only=tuple(ask_only))


def _reached_through_a_collection(row: StaleRow, collection_listed_rom_ids: Collection[int]) -> bool:
    """Whether a collection last delivered *row*, or, with no route recorded, a collection record lists it."""
    return row.delivered_through == "collection" or (
        row.delivered_through is None and row.rom_id in collection_listed_rom_ids
    )


def rebuilt_rows_filed_as_kept(
    rebuilt: Mapping[int, str], read_platform_slugs: Collection[str], platform_rom_ids: Collection[int]
) -> list[int]:
    """The rows of *rebuilt* the run files in their platform's Steam collection as it files a kept stale row.

    *rebuilt* maps each bound row a skipped unit rebuilt from its stored row to
    its platform slug, *read_platform_slugs* and *platform_rom_ids* are what
    :func:`sort_stale_rows` and the platform-collection filing take as the
    platforms the run read whole and the rows they returned. A rebuilt row
    whose platform the run read whole without returning it is filed: no unit
    left it stale, but it is the row :func:`sort_stale_rows` keeps where the
    collection that lists it is fetched instead of skipped. A skipped platform
    returns every row it rebuilds, so only a collection's skip contributes one.
    """
    return [
        rom_id
        for rom_id, platform_slug in rebuilt.items()
        if platform_slug in read_platform_slugs and rom_id not in platform_rom_ids
    ]


def settle_asked_rows(
    asked: Iterable[StaleRow], answers: Mapping[int, RomAnswer]
) -> tuple[list[StaleRow], list[StaleRow]]:
    """``(remove, keep)`` for the rows :func:`sort_stale_rows` asked about.

    Only the ROM RomM returned is removed: it left the collection that reached
    it. A proven 404 keeps the shortcut, gone from RomM, and so does an answer
    that established nothing, for this run, because only a 404 is evidence of
    absence.
    """
    rows = list(asked)
    remove = [row for row in rows if answers.get(row.rom_id) == "present"]
    keep = [row for row in rows if answers.get(row.rom_id) != "present"]
    return remove, keep


def compute_collection_diff(
    current_collection_names: set[str],
    last_synced_collections: list[str],
) -> dict[str, Any]:
    """Diff enabled collections (by Steam-collection key) against the last-synced set.

    ``current_collection_names`` is the set of DISTINCT keys
    (:func:`domain.collection_label.steam_collection_key`, the name-part of each
    Steam collection) of this run's collection accumulator — a key counts as
    present iff at least one collection carries it, so two collections sharing
    one (RomM permits same-named ones across kinds/users, #1503) collapse to one
    entry, matching the by-name Steam collection they merge into.
    ``last_synced_collections`` is the last completed run's record of the same
    keys. The reporter records no key for a collection none of whose members
    resolved to a Steam appId, so such a collection reads as added.

    Names are compared **case-insensitively**
    (:func:`domain.collection_name.fold_collection_name`), because Steam's
    collection identity ignores case: the reporter merges keys that differ only in
    case into one Steam collection and records one spelling of it. So a change of
    case alone is no change, and case variants on one side count once. An added
    name is spelled as the current side has it and a removed one as the record
    has it; of several variants on one side the sorted-first spelling is listed.

    Returns ``{"has_changes": bool, "added": [...], "removed": [...]}``;
    ``has_changes`` is True if there are any added/removed collections, or if
    there are any current collections at all (covers first-sync case).
    """
    current = _spelling_by_fold(current_collection_names)
    previous = _spelling_by_fold(last_synced_collections)
    added = sorted(name for fold, name in current.items() if fold not in previous)
    removed = sorted(name for fold, name in previous.items() if fold not in current)
    return {
        "has_changes": bool(added or removed or current),
        "added": added,
        "removed": removed,
    }


def _spelling_by_fold(names: Iterable[str]) -> dict[str, str]:
    """Map each folded name to its sorted-first spelling among *names*."""
    spelling: dict[str, str] = {}
    for name in sorted(names):
        spelling.setdefault(fold_collection_name(name), name)
    return spelling


def should_include_in_platform_collection(
    rom_id: int,
    platform_rom_ids: set[int] | None,
    create_platform_groups: bool,
) -> bool:
    """Predicate: should this ROM appear in platform-grouped collections?

    If ``create_platform_groups`` is True, every ROM qualifies. Otherwise:
    platform_rom_ids=None means no tracking (legacy sync) so include all;
    platform_rom_ids=set() means no platforms enabled so exclude all;
    otherwise membership in platform_rom_ids decides.
    """
    if create_platform_groups:
        return True
    if platform_rom_ids is None:
        return True
    return rom_id in platform_rom_ids


def platform_collection_name(
    rom_id: int,
    platform_slug: str,
    platform_rom_ids: set[int] | None,
    platform_names: Mapping[str, str],
    create_platform_groups: bool,
) -> str | None:
    """The platform name *rom_id* is filed under, or ``None`` where it is filed under none.

    The name is the platform's display name — the one *platform_names* holds for
    *platform_slug*, else the slug — not the Steam collection's full name; it is
    filed under none where :func:`should_include_in_platform_collection` leaves
    the ROM out.
    """
    if not should_include_in_platform_collection(rom_id, platform_rom_ids, create_platform_groups):
        return None
    return platform_names.get(platform_slug, platform_slug)


def compute_platform_collection_diff(
    shortcuts_data: list[dict[str, Any]],
    platform_rom_ids: set[int] | None,
    last_synced_platforms: list[str],
    create_platform_groups: bool,
    platform_names: Mapping[str, str],
    *,
    kept_platform_slugs: Collection[str] = (),
) -> dict[str, Any]:
    """Diff future platform-group collections against last-synced platforms.

    Returns ``{"has_changes": bool, "added_count": int, "removed_count": int}``.
    Each entry is filed by :func:`platform_collection_name` over
    *platform_names* and its ``platform_slug``, by the rule the apply files its
    bound rows by. The ``platform_name`` an entry carries is not read. Platform
    names are compared case-insensitively, for the reason
    :func:`compute_collection_diff` gives.

    A rebind entry (see :func:`_rebind_entry`) is keyed to the vanished bound
    ``rom_id`` so the frontend reuses its existing shortcut, but that id is never
    in ``platform_rom_ids`` (which carries the fetched server ids). Its platform
    membership follows the same dump it rebinds ONTO — the
    ``bind_rom_id`` target, a fetched id present in ``platform_rom_ids``. Keying
    the predicate on the entry's own ``rom_id`` would drop a fully-rebinding
    platform out of ``future_platforms`` and mis-count it as removed (#1417); the
    apply path, which reads the post-commit bound rows, never had this bug. Its
    name, unlike its membership, comes from its own ``platform_slug``, the bound
    version's; the two slugs differ only where RomM renamed a platform's slug
    under the same platform id, since a sibling group is scoped by platform id,
    and naming it by the target's would need each entry's pre-collapse slug
    passed in for that case alone.

    *kept_platform_slugs* are the platforms of the stale removal's kept rows — a
    version gone from RomM, fetched by nothing — whose collections the apply
    keeps whatever the setting, named through the same *platform_names*.
    """
    future_platforms = {platform_names.get(slug, slug) for slug in kept_platform_slugs}
    for sd in shortcuts_data:
        rid = sd.get(BIND_ROM_ID_KEY, sd["rom_id"])
        name = platform_collection_name(
            rid, sd["platform_slug"], platform_rom_ids, platform_names, create_platform_groups
        )
        if name is not None:
            future_platforms.add(name)

    future = _spelling_by_fold(future_platforms).keys()
    previous = _spelling_by_fold(last_synced_platforms).keys()
    added_count = len(future - previous)
    removed_count = len(previous - future)
    return {
        "has_changes": bool(added_count or removed_count),
        "added_count": added_count,
        "removed_count": removed_count,
    }
