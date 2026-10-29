"""Sibling-group key derivation — connected components over RomM's sibling edges.

Two RomM ROMs are the same game (siblings) when they share **any one** non-null
external-metadata id over seven sources (IGDB, ScreenScraper, Moby, RA, Hasheous,
LaunchBox, TGDB), scoped per platform. The client mirrors that relation by
building connected components over each fetch's ``sibling_roms`` edges and keying
a whole component by its **canonical source** — the highest-priority source
(``_META_ID_SOURCES`` order) whose value the component agrees on. Uneven id
coverage (the norm for regional variants with different titles, matched unevenly
by scrapers) then still lands every version of one game in a single group, while
a genuine cross-game bridge (two distinct values at the canonical source, smuggled
in via a shared lower-priority id) refuses to merge and every member keeps its own
solo key. An unmatched ROM with no edges is a solo group, exactly as on the server.

:func:`compute_group_keys_with_unfetched` is the component kernel used at sync
time, and :func:`compute_component_group_keys` the same over a view no component
of which is complete; :func:`compute_sibling_group_key` is the per-ROM
coalesce-first fallback (a lone ROM, a bridged member, an un-edged incremental
row) and the membership fallback in :mod:`services.version_switch`.
:func:`target_in_sibling_group` is the single membership authority the version
picker and the switch decide by, and :func:`target_bound_elsewhere` the single
authority on a target another shortcut of the game is on. Pure compute, no I/O, stdlib only (see the ADR
superseding ADR-0021 §1).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping
    from collections.abc import Set as AbstractSet

# (RomM dict field, key-source label) in RomM's coalesce order — the priority the
# canonical-source pick walks, highest first. The label prefixes the key so two
# ROMs that agree on different services never collide onto the same group.
_META_ID_SOURCES: tuple[tuple[str, str], ...] = (
    ("igdb_id", "igdb"),
    ("ss_id", "ss"),
    ("moby_id", "moby"),
    ("ra_id", "ra"),
    ("hasheous_id", "hasheous"),
    ("launchbox_id", "launchbox"),
    ("tgdb_id", "tgdb"),
    ("flashpoint_id", "flashpoint"),
)

# Reverse lookup source-label → RomM dict field, so a bound key parsed to its
# canonical source can read that source's id off a server stub.
_FIELD_BY_SOURCE: dict[str, str] = {source: field for field, source in _META_ID_SOURCES}


def compute_sibling_group_key(rom: dict[str, Any]) -> str:
    """Return the per-platform coalesce-first sibling-group key for a *rom* dict.

    Coalesces the external-metadata ids in ``_META_ID_SOURCES`` order and formats
    ``"{source}:{id}:{platform_id}"`` (e.g. ``"igdb:3404:57"``). When the ROM
    matched no service, falls back to ``"romm:{rom_id}:{platform_id}"`` — its own
    id, a solo group. ``platform_id`` scopes the key so the same metadata id on two
    platforms yields two groups. A missing id is treated as unmatched (its field
    simply carries no non-null value).

    This is the fallback the component kernel drops to for a bridged member and the
    membership fallback in :mod:`services.version_switch`; the component kernel
    itself keys agreeing members together even when their top source differs.
    """
    platform_id = rom.get("platform_id")
    for field, source in _META_ID_SOURCES:
        value = rom.get(field)
        if value is not None:
            return f"{source}:{value}:{platform_id}"
    return f"romm:{rom.get('id')}:{platform_id}"


def _parse_group_key(key: str) -> tuple[str, str] | None:
    """Split a ``"{source}:{value}:{platform}"`` key into ``(source, value)``.

    Returns ``None`` for a ``romm:``-fallback key (an unmatched/solo group carries
    no metadata source) or any key that doesn't parse to a known metadata source —
    such a key contributes no canonical candidate and cannot make a stub compatible.
    ``source`` and ``value`` never contain ``:`` (labels are alphanumeric, ids are
    integers), so a well-formed key splits into exactly three parts.
    """
    parts = key.split(":")
    if len(parts) != 3:
        return None
    source, value, _platform = parts
    if source not in _FIELD_BY_SOURCE:
        return None
    return source, value


class _UnionFind:
    """Deterministic union-find whose component root is always the smallest id.

    A min-root union keeps the partition independent of the order edges arrive in
    (fetch order is not stable), so the same input yields the same components under
    any permutation.
    """

    def __init__(self, ids: set[int]) -> None:
        self._parent: dict[int, int] = {i: i for i in ids}

    def find(self, x: int) -> int:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        lo, hi = (ra, rb) if ra < rb else (rb, ra)
        self._parent[hi] = lo


def compute_component_group_keys(
    unit_roms: list[dict[str, Any]],
    resident_keys: Mapping[int, str],
) -> dict[int, str]:
    """Derive a sibling-group key for every fresh ROM in a fetched sync unit.

    *unit_roms* is the raw RomM payload the fetcher hands through (``id``,
    ``platform_id``, the ``*_id`` metadata fields, ``sibling_roms``). A dict that
    already carries a non-null ``sibling_group_key`` is **resident** — its key is
    authoritative and preserved verbatim (an incremental unit reconstructs
    unchanged DB rows this way); every other dict is **fresh** and gets a computed
    key. *resident_keys* maps ``rom_id → persisted key`` for DB-resident ROMs that
    are NOT in the unit, so a fresh member edging into one still sees its canonical
    summary.

    Returns ``{rom_id: key}`` for the fresh members only (residents keep the key
    already on their dict). The algorithm:

    * Union-find over ``sibling_roms`` edges **between two fresh members** builds
      the connected components; processing is sorted by ``rom_id`` for determinism.
    * An edge from a fresh member to a resident ROM (in-unit resident dict or
      *resident_keys*) does NOT union — instead the resident's key, parsed to
      ``{source}:{value}``, contributes a canonical **candidate** to that member's
      component (a ``romm:``-fallback key contributes nothing).
    * Per component the **canonical source** is the highest-priority source
      present on any member's ids or resident candidates. Exactly one distinct
      value there → every fresh member gets ``{source}:{value}:{platform_id}``,
      including members that lack the source. Multiple distinct values (a genuine
      cross-game bridge) or no source at all → every fresh member falls back to its
      own :func:`compute_sibling_group_key`.

    The keying of a view no component of which is complete:
    :func:`compute_group_keys_with_unfetched` with nothing complete.
    """
    return compute_group_keys_with_unfetched(
        unit_roms, resident_keys, complete_rom_ids=frozenset(), unfetched_bound_ids=()
    ).fresh


class GroupKeys(NamedTuple):
    """What :func:`compute_group_keys_with_unfetched` answers.

    ``fresh`` is the key of every fresh ROM — :func:`compute_component_group_keys`'
    answer, but where a complete component's stale summary was not counted;
    ``unfetched`` the new key of every stored row the view did not fetch whose
    group it follows.
    """

    fresh: dict[int, str]
    unfetched: dict[int, str]


class _StoredEdge(NamedTuple):
    """A ``sibling_roms`` edge from a fresh *member* to a stored row *target* holding *key*."""

    member: int
    target: int
    key: str


def compute_group_keys_with_unfetched(
    unit_roms: list[dict[str, Any]],
    resident_keys: Mapping[int, str],
    *,
    complete_rom_ids: AbstractSet[int],
    unfetched_bound_ids: Collection[int],
) -> GroupKeys:
    """Key a view's fresh ROMs, and every stored row the view did not fetch that follows its group.

    *unit_roms* and *resident_keys* are :func:`compute_component_group_keys`' —
    *resident_keys* the keys stored before this view is keyed, so a fresh member's
    entry there is its **prior key**. A component is **complete** when every member
    is in *complete_rom_ids*: the ROMs a platform listing returned, which holds
    every version of a game RomM lists (a group is per-platform).
    *unfetched_bound_ids* are the bound rows of those platforms the listings left
    out — versions deleted or hidden in RomM, which RomM's answers do not tell
    apart. A component that is not complete is keyed exactly as
    :func:`compute_component_group_keys` keys it; a complete one, three ways more:

    * Where its canonical source holds more than one value and every member
      holding a prior key held the same one — the component's own key at the
      last sync — a candidate equal to that key is not counted: it is the
      summary the stored row shared with the component, not an id of the row's
      own. The gate then judges again.
    * A stored row one of its edges reaches takes its key, unless another
      complete component's edge reaches the row under another key.
    * A row of *unfetched_bound_ids* no edge reached takes its key where that
      component is the one component a member of which held the row's stored key
      as its prior key, every member holding a prior key held that one, and the
      component holds no other value at the stored key's source. A ``romm:`` key
      names no source and is never followed.

    ``unfetched`` holds only rows whose key changes.
    """
    fresh_by_id, in_unit_resident = _partition_unit(unit_roms)
    resident_lookup: dict[int, str] = {int(k): v for k, v in resident_keys.items()}
    resident_lookup.update(in_unit_resident)

    components, stored_edges = _connect_components(fresh_by_id, resident_lookup)

    fresh: dict[int, str] = {}
    reached: dict[int, set[str]] = {}
    agreed: dict[int, tuple[str, dict[str, set[str]]]] = {}
    for root, member_ids in components.items():
        edges = [edge for member_id in member_ids for edge in stored_edges.get(member_id, [])]
        complete = all(member_id in complete_rom_ids for member_id in member_ids)
        values = _values_by_source(member_ids, fresh_by_id, edges)
        if complete:
            values = _without_stale_summary(values, member_ids, fresh_by_id, edges, resident_lookup)
        key = _assign_component_keys(member_ids, fresh_by_id, values, fresh)
        if complete and key is not None:
            agreed[root] = (key, values)
            for edge in edges:
                reached.setdefault(edge.target, set()).add(key)

    unfetched = {
        target: next(iter(keys))
        for target, keys in reached.items()
        if len(keys) == 1 and resident_lookup[target] not in keys
    }
    unreached = [rom_id for rom_id in unfetched_bound_ids if rom_id not in reached and rom_id not in fresh_by_id]
    unfetched.update(_followed_keys(unreached, components, agreed, resident_lookup))
    return GroupKeys(fresh, unfetched)


def _partition_unit(unit_roms: list[dict[str, Any]]) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    """Split a fetched unit into ``{id: fresh dict}`` and ``{id: in-unit resident key}``.

    A dict carrying a non-null ``sibling_group_key`` is resident (its key stays);
    every other dict is fresh and gets a computed key.
    """
    fresh_by_id: dict[int, dict[str, Any]] = {}
    in_unit_resident: dict[int, str] = {}
    for rom in unit_roms:
        rom_id = int(rom["id"])
        key = rom.get("sibling_group_key")
        # Strict on purpose: a truthiness test would treat an empty key as fresh
        # and silently recompute it. `VersionMetadata` forbids an empty key at
        # the writer, so there is nothing left to self-heal here.
        if key is not None:
            in_unit_resident[rom_id] = key
        else:
            fresh_by_id[rom_id] = rom
    return fresh_by_id, in_unit_resident


def _connect_components(
    fresh_by_id: dict[int, dict[str, Any]],
    resident_lookup: dict[int, str],
) -> tuple[dict[int, list[int]], dict[int, list[_StoredEdge]]]:
    """Union fresh members over their ``sibling_roms`` edges into connected components.

    Returns ``(components_by_root, stored_edges)``: components maps each root to
    its member ids, and ``stored_edges`` maps a fresh member id to its edges into
    stored ROMs. Processing is sorted by ``rom_id`` so the partition is
    order-independent.
    """
    fresh_ids = set(fresh_by_id)
    uf = _UnionFind(fresh_ids)
    stored_edges: dict[int, list[_StoredEdge]] = {}
    for rom_id in sorted(fresh_ids):
        for sibling in fresh_by_id[rom_id].get("sibling_roms") or []:
            _apply_edge(rom_id, sibling, fresh_ids, resident_lookup, uf, stored_edges)

    components: dict[int, list[int]] = {}
    for rom_id in sorted(fresh_ids):
        components.setdefault(uf.find(rom_id), []).append(rom_id)
    return components, stored_edges


def _apply_edge(
    rom_id: int,
    sibling: dict[str, Any],
    fresh_ids: set[int],
    resident_lookup: dict[int, str],
    uf: _UnionFind,
    stored_edges: dict[int, list[_StoredEdge]],
) -> None:
    """Apply one ``sibling_roms`` edge: union two fresh members, or record an edge into a stored ROM.

    An edge between two fresh members unions them; an edge to a resident ROM (in-unit
    or DB) is recorded for ``rom_id``; an edge to an unknown ROM does neither.
    """
    target_id = int(sibling.get("id", 0) or 0)
    if target_id <= 0 or target_id == rom_id:
        return
    if target_id in fresh_ids:
        uf.union(rom_id, target_id)
    elif target_id in resident_lookup:
        stored_edges.setdefault(rom_id, []).append(_StoredEdge(rom_id, target_id, resident_lookup[target_id]))


def _values_by_source(
    member_ids: list[int], fresh_by_id: dict[int, dict[str, Any]], edges: list[_StoredEdge]
) -> dict[str, set[str]]:
    """Every value one component holds per source: its members' ids, and the candidates *edges* carry.

    An edge into a stored row parses that row's key to a candidate; a ``romm:``
    fallback key parses to none and contributes nothing.
    """
    values_by_source: dict[str, set[str]] = {}
    for member_id in member_ids:
        rom = fresh_by_id[member_id]
        for field, source in _META_ID_SOURCES:
            value = rom.get(field)
            if value is not None:
                values_by_source.setdefault(source, set()).add(str(value))
    for edge in edges:
        parsed = _parse_group_key(edge.key)
        if parsed is not None:
            values_by_source.setdefault(parsed[0], set()).add(parsed[1])
    return values_by_source


def _agreed_value(values_by_source: dict[str, set[str]]) -> tuple[str, str] | None:
    """The canonical ``(source, value)`` of *values_by_source*; ``None`` without a source or with two values there."""
    canonical = next((source for _field, source in _META_ID_SOURCES if source in values_by_source), None)
    if canonical is None or len(values_by_source[canonical]) != 1:
        return None
    return canonical, next(iter(values_by_source[canonical]))


def _assign_component_keys(
    member_ids: list[int],
    fresh_by_id: dict[int, dict[str, Any]],
    values_by_source: dict[str, set[str]],
    result: dict[int, str],
) -> str | None:
    """Stamp one component's members into *result*; answer the component key, or ``None`` when it fell back."""
    agreed = _agreed_value(values_by_source)
    if agreed is None:
        # No metadata source, or a genuine cross-game bridge (conflicting values at
        # the canonical source) — no assumption-merge, each member keeps its own key.
        for member_id in member_ids:
            result[member_id] = compute_sibling_group_key(fresh_by_id[member_id])
        return None
    canonical, value = agreed
    for member_id in member_ids:
        platform_id = fresh_by_id[member_id].get("platform_id")
        result[member_id] = f"{canonical}:{value}:{platform_id}"
    return result[member_ids[0]]


def _without_stale_summary(
    values_by_source: dict[str, set[str]],
    member_ids: list[int],
    fresh_by_id: dict[int, dict[str, Any]],
    edges: list[_StoredEdge],
    resident_lookup: dict[int, str],
) -> dict[str, set[str]]:
    """A complete component's *values_by_source*, its stale summary not counted.

    The summary is the one key every member holding a prior key held; it is not
    counted only where the gate would otherwise split the component
    (:func:`compute_group_keys_with_unfetched`).
    """
    prior_keys = {resident_lookup[m] for m in member_ids if m in resident_lookup}
    if _agreed_value(values_by_source) is not None or len(prior_keys) != 1:
        return values_by_source
    return _values_by_source(member_ids, fresh_by_id, [edge for edge in edges if edge.key not in prior_keys])


def _followed_keys(
    rom_ids: Collection[int],
    components: dict[int, list[int]],
    agreed: dict[int, tuple[str, dict[str, set[str]]]],
    resident_lookup: dict[int, str],
) -> dict[int, str]:
    """The key each bound row of *rom_ids*, which no edge reached, follows; a row that follows none is left out."""
    followed: dict[int, str] = {}
    for rom_id in sorted(rom_ids):
        key = _followed_key(rom_id, components, agreed, resident_lookup)
        if key is not None:
            followed[rom_id] = key
    return followed


def _followed_key(
    rom_id: int,
    components: dict[int, list[int]],
    agreed: dict[int, tuple[str, dict[str, set[str]]]],
    resident_lookup: dict[int, str],
) -> str | None:
    """The key the bound row *rom_id* the view left out follows, under :func:`compute_group_keys_with_unfetched`' rule.

    ``None`` where the row holds no stored key, or follows no component.
    """
    stored_key = resident_lookup.get(rom_id)
    if stored_key is None:
        return None
    parsed = _parse_group_key(stored_key)
    if parsed is None:
        return None
    carriers = [
        root for root, member_ids in components.items() if any(resident_lookup.get(m) == stored_key for m in member_ids)
    ]
    if len(carriers) != 1 or carriers[0] not in agreed:
        return None
    if any(resident_lookup.get(m) not in (None, stored_key) for m in components[carriers[0]]):
        return None
    key, values = agreed[carriers[0]]
    source, value = parsed
    if key == stored_key or not values.get(source, {value}) <= {value}:
        return None
    return key


def target_in_sibling_group(
    *,
    bound_group_key: str | None,
    target_group_key: str | None = None,
    target_ids: Mapping[str, Any] | None = None,
    target_is_local: bool,
    target_is_server_sibling: bool,
) -> bool:
    """Whether a switch target belongs to the bound ROM's sibling group.

    The single membership authority shared by the version picker's per-version
    ``switchable`` flag (``get_version_list``) and ``switch_version``'s group guard,
    so the two surfaces can never disagree. A target must first be *eligible* — it
    has a local row (``target_is_local``) OR RomM's symmetric ``sibling_roms`` view
    lists it (``target_is_server_sibling``). A NULL ``bound_group_key`` (an
    unbackfilled / solo bound row) can't discriminate, so it accepts any eligible
    target.

    A **local** target is judged by key equality: ``target_group_key`` is its own
    persisted ``sibling_group_key``, and the component keys now encode group
    membership, so a differing key is a different group (#1359).

    A **server-only** target (no local row yet) is judged by **canonical
    compatibility** against the bound group's persisted key: parse it to
    ``{source}:{value}`` and require the stub's id value at that ``source`` — read
    from ``target_ids`` (the RomM detail dict) — to be **absent or equal**. The key
    doubles as the group's canonical summary, so a stub that simply lacks the
    canonical id is still in-group (it joins under the bound key on switch-persist,
    and the next sync re-canonicalizes the whole component), while a stub carrying a
    *different* value there is a genuine metadata conflict and is rejected (#1360).
    A ``romm:``-fallback bound key (no metadata source) admits no server-only
    target; ``target_ids`` of ``None`` (an unfetched detail) can't be judged and is
    likewise rejected.
    """
    if not (target_is_local or target_is_server_sibling):
        return False
    if bound_group_key is None:
        return True
    if target_is_local:
        return target_group_key == bound_group_key
    parsed = _parse_group_key(bound_group_key)
    if parsed is None or target_ids is None:
        return False
    source, value = parsed
    target_value = target_ids.get(_FIELD_BY_SOURCE[source])
    return target_value is None or str(target_value) == value


def target_bound_elsewhere(*, target_app_id: int | None, app_id: int) -> bool:
    """Whether a switch target is bound to a shortcut other than *app_id*.

    Shared by the version picker's per-version ``bound_elsewhere`` flag
    (``get_version_list``) and ``switch_version``'s ``bound_elsewhere`` guard,
    so the picker never offers a target the switch refuses. Moving such a target
    onto *app_id* would leave the other shortcut with no version.
    """
    return target_app_id is not None and target_app_id != app_id
