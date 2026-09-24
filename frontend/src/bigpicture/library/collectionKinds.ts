/**
 * What the Library page's Collections tab shows, worked out from the one
 * `get_collections` answer: which kind a collection is listed under, which one
 * is the Favorites row, what a kind row counts, and what its pane says turning
 * a collection on does.
 *
 * Pure, because every one of those answers is a rule a reader can be wrong
 * about in silence — a collection counted under a kind whose table does not
 * show it, a favorites collection that belongs to someone else switched as if
 * it were yours — and a rule is cheapest to pin where no component stands
 * around it.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Library.
 */

import type { CollectionKind, CollectionOwnerScope, CollectionSyncSetting } from "../../types";
import { fuzzyMatch } from "../../utils/fuzzyMatch";

/** The rows of the list column, in the order they are drawn. The wire keys
 *  stay `standard` / `smart` / `virtual` + `virtual_type`; these name the
 *  page's rows, two of which are halves of the one virtual kind. */
export type CollectionsKindId = "favorites" | "standard" | "smart" | "franchise" | "igdb";

export const KIND_ORDER: readonly CollectionsKindId[] = ["favorites", "standard", "smart", "franchise", "igdb"];

export function isKindId(id: string): id is CollectionsKindId {
  return (KIND_ORDER as readonly string[]).includes(id);
}

export interface KindText {
  /** The row's name, and the pane's title. */
  name: string;
  /** What the kind is, beside the pane's title. */
  about: string;
  /** Whether the kind has owners, which decides the Owner column. */
  owned: boolean;
}

export const KIND_TEXT: Record<CollectionsKindId, KindText> = {
  favorites: { name: "Favorites", about: "the games you marked as favorites in RomM", owned: true },
  standard: { name: "Collections", about: "picked by hand in RomM", owned: true },
  smart: { name: "Smart collections", about: "saved searches in RomM", owned: true },
  franchise: {
    name: "Franchises",
    about: "grouped by RomM from IGDB · no owner",
    owned: false,
  },
  igdb: {
    name: "IGDB collections",
    about: "series grouped by RomM from IGDB · no owner",
    owned: false,
  },
};

/**
 * One sentence per kind on what turning one of its collections on does: every
 * one says that the games come to Steam whether or not their platform is
 * synced. What the Steam collection is called exactly is the user guide's to
 * say, not the pane's.
 */
export function kindSentence(kind: CollectionsKindId): string {
  const tail =
    "to Steam at the next sync, including games on platforms you do not sync, and groups them in a Steam collection named after it.";
  switch (kind) {
    case "favorites":
      return `Turning it on adds all its games ${tail}`;
    case "standard":
      return `Turning one on adds all its games ${tail}`;
    case "smart":
      return `Turning one on adds every game its search matches ${tail}`;
    case "franchise":
      return `Turning one on adds every game of that franchise ${tail}`;
    case "igdb":
      return `Turning one on adds every game of that IGDB collection ${tail}`;
  }
}

/** Which bucket a batch write for this kind goes to on the wire. */
export function wireKind(kind: Exclude<CollectionsKindId, "favorites">): CollectionKind {
  if (kind === "standard" || kind === "smart") return kind;
  return "virtual";
}

/** A collection's identity on this page. Each kind comes from its own RomM
 *  listing with its own ids, so an id alone is not unique across kinds. */
export function collectionKey(c: Pick<CollectionSyncSetting, "id" | "kind">): string {
  return `${c.kind}:${c.id}`;
}

/** Whether the owner scope keeps a collection: an unestablished owner (`null`)
 *  is kept, as the sync keeps it while it does not yet know who the user is. */
const ownOrUnknown = (c: CollectionSyncSetting): boolean => c.is_own !== false;

/**
 * What the Favorites row stands for.
 *
 * Only the signed-in user's OWN favorites collection is the row: another user's
 * public one is an ordinary collection of theirs, listed with its owner and
 * governed by the owner switch like any other. A collection whose owner is not
 * established yet (`is_own: null`, before the user's id is known) is a
 * candidate. Where more than one is — two marked own, or two not yet
 * established — a single switch cannot stand for them, so the row names none
 * and they are listed under Collections instead.
 */
export type FavoritesAnswer =
  { state: "one"; collection: CollectionSyncSetting } | { state: "none" } | { state: "several"; count: number };

export function resolveFavorites(collections: readonly CollectionSyncSetting[]): FavoritesAnswer {
  const own = collections.filter((c) => c.kind === "standard" && c.is_favorite && ownOrUnknown(c));
  if (own.length === 0) return { state: "none" };
  if (own.length > 1) return { state: "several", count: own.length };
  return { state: "one", collection: own[0] as CollectionSyncSetting };
}

/**
 * The collections a kind's table lists, in the order they are held — which is
 * the order they were frozen in when the listing arrived.
 *
 * The owner switch applies to the two kinds that have owners: with it off,
 * another user's collection is not listed, so it is neither counted nor written
 * by Enable all. The resolved favorites collection is the Favorites row and not
 * a Collections one; an unresolved one stays where it is, under Collections.
 */
export function kindMembers(
  collections: readonly CollectionSyncSetting[],
  kind: CollectionsKindId,
  ownerScope: CollectionOwnerScope,
  favorites: FavoritesAnswer,
): CollectionSyncSetting[] {
  const shown = (c: CollectionSyncSetting) => ownerScope === "all" || ownOrUnknown(c);
  switch (kind) {
    case "favorites":
      return [];
    case "standard": {
      const rowKey = favorites.state === "one" ? collectionKey(favorites.collection) : null;
      return collections.filter((c) => c.kind === "standard" && shown(c) && collectionKey(c) !== rowKey);
    }
    case "smart":
      return collections.filter((c) => c.kind === "smart" && shown(c));
    case "franchise":
      return collections.filter((c) => c.kind === "virtual" && c.virtual_type === "franchise");
    case "igdb":
      return collections.filter((c) => c.kind === "virtual" && c.virtual_type === "collection");
  }
}

/** How many of a kind's collections the owner switch is hiding: another
 *  user's, while it is off. */
export function hiddenForeignCount(
  collections: readonly CollectionSyncSetting[],
  kind: CollectionsKindId,
  ownerScope: CollectionOwnerScope,
  favorites: FavoritesAnswer,
): number {
  if (ownerScope === "all" || !KIND_TEXT[kind].owned) return 0;
  return (
    kindMembers(collections, kind, "all", favorites).length -
    kindMembers(collections, kind, ownerScope, favorites).length
  );
}

/** The kind row's "N of M on", over what that kind's table lists under the
 *  owner switch, before any search. */
export function onCount(members: readonly CollectionSyncSetting[]): string {
  return `${members.filter((c) => c.sync_enabled).length} of ${members.length} on`;
}

export function searchMembers(members: readonly CollectionSyncSetting[], search: string): CollectionSyncSetting[] {
  return search === "" ? [...members] : members.filter((c) => fuzzyMatch(search, c.name));
}

/** Those that are on above those that are off, each in the order
 *  `get_collections` answered in. Applied once, when the listing arrives, and never
 *  again while the page is open, so a switched row stays where the focus is. */
export function freezeOrder(collections: readonly CollectionSyncSetting[]): CollectionSyncSetting[] {
  return [...collections.filter((c) => c.sync_enabled), ...collections.filter((c) => !c.sync_enabled)];
}

/** The Owner column: "you", or the owner's RomM user name, or a dash where the
 *  listing did not carry one. */
export function ownerLabel(c: CollectionSyncSetting): string {
  if (c.is_own === true) return "you";
  return c.owner_username ?? "—";
}

/** The In Steam column. Absent is unknown, never zero. */
export function inSteamLabel(c: CollectionSyncSetting): string {
  return c.in_steam_count === undefined ? "—" : `${c.in_steam_count}`;
}

export function romCountLabel(count: number): string {
  return count === 1 ? "1 ROM" : `${count} ROMs`;
}
