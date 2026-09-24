/**
 * Everything the Library page's Collections tab knows and does: the one
 * collections read, the owner switch, which kind is selected, the search over
 * its table, and the four writes.
 *
 * It lives above the tab boundary for the reason `usePlatformsPage` does:
 * Steam's tabbed page renders only the active tab, so a tab owning its state
 * would re-read on every switch back and thaw the order it froze.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Library.
 */

import { useCallback, useRef, useState } from "react";
import {
  getCollections,
  getSettings,
  saveCollectionSync,
  saveCollectionsSync,
  setCollectionOwnerScope,
} from "../../api/backend";
import type { CollectionOwnerScope, CollectionSyncSetting } from "../../types";
import { detach } from "../../utils/detach";
import {
  collectionKey,
  freezeOrder,
  isKindId,
  kindMembers,
  resolveFavorites,
  searchMembers,
  wireKind,
  type CollectionsKindId,
} from "./collectionKinds";
import { SYNC_WRITE_FAILED } from "./syncWriteFailed";

/** Where the collections read stands. A failure is not final: entering the tab
 *  again asks again, which is what the failure line tells the reader. */
export type CollectionsLoad =
  { state: "idle" } | { state: "loading" } | { state: "loaded" } | { state: "failed"; message: string | null };

/**
 * Which column a refused write is reported in — where it was made. The owner
 * switch and the Favorites switch are in the list column, a table row and
 * Enable all / Disable all in the pane.
 */
export type WritePlace = "list" | "pane";

export interface CollectionsPageState {
  load: CollectionsLoad;
  /** The listing, in the order frozen when it arrived; writes change values in
   *  place and never the order. */
  collections: CollectionSyncSetting[];
  ownerScope: CollectionOwnerScope;
  selectedKind: CollectionsKindId;
  select: (id: string) => void;
  search: string;
  setSearch: (value: string) => void;
  /** Why the last list-column write did not take, or `null`. */
  listStatus: string | null;
  /** Why the last pane write did not take, or `null`. */
  paneStatus: string | null;
  /** The tab was entered: ask for what is not held, and start from a clean view. */
  enter: () => void;
  setOwnerScope: (scope: CollectionOwnerScope) => void;
  toggleCollection: (collection: CollectionSyncSetting, enabled: boolean, place: WritePlace) => void;
  /** Switch every collection the selected kind's table lists — the search
   *  included, the render cap not — in one write. */
  setAllShown: (enabled: boolean) => void;
}

/** The selected kind's table, as `setAllShown` writes it and the pane lists it. */
export function shownCollections(
  state: Pick<CollectionsPageState, "collections" | "ownerScope" | "selectedKind" | "search">,
): CollectionSyncSetting[] {
  const members = kindMembers(
    state.collections,
    state.selectedKind,
    state.ownerScope,
    resolveFavorites(state.collections),
  );
  return searchMembers(members, state.search);
}

/** The owner switch's key among the targets of `issueValueWrite`; a collection's
 *  is its `collectionKey`, which always holds a colon, so the two cannot meet. */
const OWNER_SWITCH = "owner-switch";

export function useCollectionsPage(): CollectionsPageState {
  const [collections, setCollections] = useState<CollectionSyncSetting[]>([]);
  const [load, setLoad] = useState<CollectionsLoad>({ state: "idle" });
  const [ownerScope, setOwnerScopeState] = useState<CollectionOwnerScope>("all");
  const [selectedKind, setSelectedKind] = useState<CollectionsKindId>("standard");
  const [search, setSearch] = useState("");
  const [listStatus, setListStatus] = useState<string | null>(null);
  const [paneStatus, setPaneStatus] = useState<string | null>(null);

  // Refs, not state: `enter` decides whether to ask from what is in flight
  // right now, and a render-time snapshot of that could be one read behind.
  const collectionsRead = useRef<CollectionsLoad["state"]>("idle");
  const settingsRead = useRef<"idle" | "loading" | "done" | "failed">("idle");

  // What each control's value is known to be stored as, and the latest write
  // issued to it — a collection by its key, the owner switch by OWNER_SWITCH.
  // Touched only in handlers and in answers, never during render.
  const confirmedSync = useRef(new Map<string, boolean>());
  const confirmedScope = useRef<CollectionOwnerScope>("all");
  const latestValueWrite = useRef(new Map<string, number>());
  const issueValueWrite = useCallback((target: string) => {
    const seq = (latestValueWrite.current.get(target) ?? 0) + 1;
    latestValueWrite.current.set(target, seq);
    return seq;
  }, []);
  const isLatestValueWrite = useCallback(
    (target: string, seq: number) => latestValueWrite.current.get(target) === seq,
    [],
  );
  const readCollections = useCallback(() => {
    collectionsRead.current = "loading";
    setLoad({ state: "loading" });
    getCollections()
      .then((result) => {
        if (result.success) {
          confirmedSync.current = new Map(result.collections.map((c) => [collectionKey(c), c.sync_enabled]));
          setCollections(freezeOrder(result.collections));
          collectionsRead.current = "loaded";
          setLoad({ state: "loaded" });
          return;
        }
        collectionsRead.current = "failed";
        setLoad({ state: "failed", message: result.message || null });
      })
      .catch(() => {
        collectionsRead.current = "failed";
        setLoad({ state: "failed", message: null });
      });
  }, []);

  const readOwnerScope = useCallback(() => {
    settingsRead.current = "loading";
    const since = latestValueWrite.current.get(OWNER_SWITCH) ?? 0;
    getSettings()
      .then((settings) => {
        const scope: CollectionOwnerScope = settings.collection_owner_scope === "own" ? "own" : "all";
        confirmedScope.current = scope;
        // A switch written since the read was issued shows what the reader
        // chose; the read is older than that and must not put it back.
        if ((latestValueWrite.current.get(OWNER_SWITCH) ?? 0) === since) setOwnerScopeState(scope);
        settingsRead.current = "done";
      })
      .catch(() => {
        // The switch stays at its default and says nothing about the stored
        // value; entering the tab again asks again.
        settingsRead.current = "failed";
      });
  }, []);

  // Which answer may still speak on each line — `docs/architecture/qam-panel.md`
  // § Library, "Only the latest write speaks".
  const latestWrite = useRef<Record<WritePlace, number>>({ list: 0, pane: 0 });
  const entries = useRef(0);
  const shownKind = useRef<CollectionsKindId>("standard");

  const lineFor = useCallback((place: WritePlace) => {
    latestWrite.current[place] += 1;
    const ticket = latestWrite.current[place];
    const entry = entries.current;
    const kind = shownKind.current;
    return (text: string | null) => {
      if (latestWrite.current[place] !== ticket || entries.current !== entry) return;
      if (place === "pane" && shownKind.current !== kind) return;
      if (place === "list") setListStatus(text);
      else setPaneStatus(text);
    };
  }, []);

  const enter = useCallback(() => {
    entries.current += 1;
    setSearch("");
    setListStatus(null);
    setPaneStatus(null);
    if (settingsRead.current === "idle" || settingsRead.current === "failed") readOwnerScope();
    if (collectionsRead.current === "idle" || collectionsRead.current === "failed") readCollections();
  }, [readCollections, readOwnerScope]);

  // A search and a pane refusal are both about the kind they were made on, so
  // another kind is entered without either.
  const select = useCallback((id: string) => {
    if (!isKindId(id) || id === shownKind.current) return;
    shownKind.current = id;
    setSelectedKind(id);
    setSearch("");
    setPaneStatus(null);
  }, []);

  // The writes are optimistic; what an answer may change on its control and on
  // its line is `docs/architecture/qam-panel.md` § Library, "Only the latest
  // write speaks". A refusal and a rejection are one outcome here — neither
  // leaves the write standing; none of these callables throws to refuse.
  const toggleCollection = useCallback(
    (collection: CollectionSyncSetting, enabled: boolean, place: WritePlace) => {
      const key = collectionKey(collection);
      const show = (want: boolean) =>
        setCollections((prev) => prev.map((c) => (collectionKey(c) === key ? { ...c, sync_enabled: want } : c)));
      const seq = issueValueWrite(key);
      const line = lineFor(place);
      const failed = (text: string) => {
        if (isLatestValueWrite(key, seq)) show(confirmedSync.current.get(key) ?? !enabled);
        line(text);
      };
      show(enabled);
      detach(
        saveCollectionSync(collection.id, collection.kind, enabled)
          .then((result) => {
            if (result.success) {
              confirmedSync.current.set(key, enabled);
              line(null);
              return;
            }
            failed(result.message || SYNC_WRITE_FAILED);
          })
          .catch(() => failed(SYNC_WRITE_FAILED)),
      );
    },
    [isLatestValueWrite, issueValueWrite, lineFor],
  );

  const setAllShown = useCallback(
    (enabled: boolean) => {
      if (selectedKind === "favorites") return;
      const targets = shownCollections({ collections, ownerScope, selectedKind, search });
      if (targets.length === 0) return;
      const seqs = new Map(targets.map((c) => [collectionKey(c), issueValueWrite(collectionKey(c))]));
      const line = lineFor("pane");
      const failed = (text: string) => {
        setCollections((prev) =>
          prev.map((c) => {
            const key = collectionKey(c);
            const seq = seqs.get(key);
            if (seq === undefined || !isLatestValueWrite(key, seq)) return c;
            return { ...c, sync_enabled: confirmedSync.current.get(key) ?? c.sync_enabled };
          }),
        );
        line(text);
      };
      setCollections((prev) => prev.map((c) => (seqs.has(collectionKey(c)) ? { ...c, sync_enabled: enabled } : c)));
      detach(
        saveCollectionsSync(
          targets.map((c) => c.id),
          wireKind(selectedKind),
          enabled,
        )
          .then((result) => {
            if (result.success) {
              for (const key of seqs.keys()) confirmedSync.current.set(key, enabled);
              line(null);
              return;
            }
            failed(result.message || SYNC_WRITE_FAILED);
          })
          .catch(() => failed(SYNC_WRITE_FAILED)),
      );
    },
    [collections, isLatestValueWrite, issueValueWrite, lineFor, ownerScope, search, selectedKind],
  );

  const setOwnerScope = useCallback(
    (scope: CollectionOwnerScope) => {
      if (scope === ownerScope) return;
      const seq = issueValueWrite(OWNER_SWITCH);
      const line = lineFor("list");
      const failed = (text: string) => {
        if (isLatestValueWrite(OWNER_SWITCH, seq)) setOwnerScopeState(confirmedScope.current);
        line(text);
      };
      setOwnerScopeState(scope);
      detach(
        setCollectionOwnerScope(scope)
          .then((result) => {
            if (result.success) {
              confirmedScope.current = scope;
              line(null);
              return;
            }
            failed(result.message || SYNC_WRITE_FAILED);
          })
          .catch(() => failed(SYNC_WRITE_FAILED)),
      );
    },
    [isLatestValueWrite, issueValueWrite, lineFor, ownerScope],
  );

  return {
    load,
    collections,
    ownerScope,
    selectedKind,
    select,
    search,
    setSearch,
    listStatus,
    paneStatus,
    enter,
    setOwnerScope,
    toggleCollection,
    setAllShown,
  };
}
