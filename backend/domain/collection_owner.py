"""Owner-scope classification for RomM collections.

RomM's collection listings return the signed-in user's own collections plus
every other user's PUBLIC collection. This module owns the rule that decides
whether a collection is the user's own — once to drop foreign units from the
sync work queue under the ``own`` owner scope (:func:`is_own_collection`), and
once to tag the rows ``get_collections`` answers with (:func:`listing_is_own`).
No I/O, no state: identity and the collection's owner id come in as arguments.

Two invariants make the scope safe and non-breaking:

* **Virtual collections have no owner.** They are global/derived — RomM's
  ``VirtualCollection`` model carries no ``user_id`` column and returns them
  identically to every user — so they are always own and always survive the
  ``own`` scope.
* **Unknown identity hides nothing.** When the plugin does not yet know its own
  user id (never fetched / offline), the sync treats every collection as own,
  so the ``own`` scope drops nothing rather than dropping against the wrong
  identity. The listing says the same thing differently: it answers ``None``
  for a standard or smart collection, because "not known" is not "yours".
"""

from __future__ import annotations


def is_own_collection(collection_user_id: object, own_user_id: int | None, *, kind: str) -> bool:
    """Whether a collection is the signed-in user's own (survives the ``own`` scope).

    Parameters
    ----------
    collection_user_id:
        The collection's owner id (``user_id``) from the RomM listing dict, or
        ``None``/absent. Compared by value against *own_user_id*. Only standard
        and smart collections carry it; virtual collections never do.
    own_user_id:
        The signed-in user's own id (``settings["romm_user_id"]``), or ``None``
        when identity is not yet known.
    kind:
        ``"standard"``, ``"smart"`` or ``"virtual"``.

    Returns ``True`` (own) when the collection is a virtual collection (no
    owner), when our own identity is unknown (the non-breaking fallback), or
    when the collection's owner id equals ours. ``False`` (foreign) only when a
    standard/smart collection is owned by a different known id.
    """
    if kind == "virtual":
        return True
    if own_user_id is None:
        return True
    return collection_user_id == own_user_id


def listing_is_own(collection_user_id: object, own_user_id: int | None, *, kind: str) -> bool | None:
    """The ``is_own`` a ``get_collections`` row carries.

    ``True`` or ``False`` where ownership is established, as
    :func:`is_own_collection` answers it; ``None`` for a standard or smart
    collection while the signed-in user's id is unknown, where the sync's own
    rule treats the collection as own but nothing established that it is. A
    virtual collection has no owner and is always ``True``.
    """
    if kind != "virtual" and own_user_id is None:
        return None
    return is_own_collection(collection_user_id, own_user_id, kind=kind)
