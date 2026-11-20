/**
 * Shared state and lifecycle hook for expandable per-save version history
 * panels. Lazy-loads prior server versions on expand, auto-fetches if an open
 * panel reconnects from offline before loading, invalidates/refreshes on
 * matching `romm_data_changed` (`save_sync`) events, and guards against
 * unmounted or superseded responses.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { SaveVersionEntry, SyncConflict } from "../types";
import { detach } from "./detach";
import { isSaveSyncForRom } from "./saveHelpers";
import { executeVersionRestore, loadFileVersions } from "./saveSlotFlow";

interface UseFileVersionHistoryOptions {
  romId: number;
  slot: string;
  filename: string;
  isOffline: boolean;
  showConflict: (conflict: SyncConflict) => Promise<unknown>;
  onRestored: () => void;
}

export interface FileVersionHistoryState {
  expanded: boolean;
  versions: SaveVersionEntry[] | null;
  loading: boolean;
  restoringVersionId: number | null;
  loadError: string | null;
  handleToggle: () => void;
  fetchVersions: () => Promise<void>;
  handleRestore: (version: SaveVersionEntry) => Promise<void>;
}

export function useFileVersionHistory({
  romId,
  slot,
  filename,
  isOffline,
  showConflict,
  onRestored,
}: UseFileVersionHistoryOptions): FileVersionHistoryState {
  const [expanded, setExpanded] = useState(false);
  // Synchronous ref avoids re-loading when a successful restore collapses the
  // subsection and announces `save_sync` in the same turn, before React re-renders.
  const expandedRef = useRef(false);
  const setOpen = (open: boolean) => {
    expandedRef.current = open;
    setExpanded(open);
  };

  const [versions, setVersions] = useState<SaveVersionEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [restoringVersionId, setRestoringVersionId] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const readSeqRef = useRef(0);
  const prevOfflineRef = useRef(isOffline);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const fetchVersions = useCallback(async () => {
    const seq = ++readSeqRef.current;
    setLoading(true);
    setLoadError(null);
    const outcome = await loadFileVersions(romId, slot, filename);
    if (!mountedRef.current || seq !== readSeqRef.current) return;
    if (outcome.ok) {
      setVersions(outcome.versions);
    } else {
      setVersions(null);
      setLoadError(outcome.error);
    }
    setLoading(false);
  }, [romId, slot, filename]);

  const handleToggle = () => {
    const willExpand = !expanded;
    setOpen(willExpand);
    if (willExpand && !isOffline && versions === null && loadError === null) {
      detach(fetchVersions());
    }
  };

  // Auto-fetch if the user expanded this subsection while offline (so nothing
  // was loaded yet) and connectivity subsequently recovers.
  useEffect(() => {
    const cameOnline = prevOfflineRef.current && !isOffline;
    prevOfflineRef.current = isOffline;
    if (cameOnline && expanded && versions === null && loadError === null && !loading) {
      detach(fetchVersions());
    }
  }, [isOffline, expanded, versions, loadError, loading, fetchVersions]);

  // Self-refresh when a save-change event (`romm_data_changed` DOM CustomEvent)
  // fires for this ROM: invalidate cache immediately, and reload when expanded
  // and online.
  useEffect(() => {
    const onDataChanged = ({ detail }: WindowEventMap["romm_data_changed"]) => {
      if (!isSaveSyncForRom(detail, romId)) return;
      readSeqRef.current += 1;
      setVersions(null);
      setLoadError(null);
      if (expandedRef.current && !isOffline) {
        detach(fetchVersions());
      } else {
        setLoading(false);
      }
    };
    globalThis.addEventListener("romm_data_changed", onDataChanged);
    return () => globalThis.removeEventListener("romm_data_changed", onDataChanged);
  }, [romId, isOffline, fetchVersions]);

  const handleRestore = async (version: SaveVersionEntry) => {
    setRestoringVersionId(version.id);
    try {
      if (await executeVersionRestore(romId, slot, version, showConflict)) {
        if (!mountedRef.current) return;
        setVersions(null);
        setOpen(false);
        onRestored();
      }
    } finally {
      if (mountedRef.current) setRestoringVersionId(null);
    }
  };

  return {
    expanded,
    versions,
    loading,
    restoringVersionId,
    loadError,
    handleToggle,
    fetchVersions,
    handleRestore,
  };
}
