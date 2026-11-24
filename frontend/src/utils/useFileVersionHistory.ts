import { useCallback, useEffect, useRef, useState } from "react";
import type { SaveVersionEntry, SyncConflict } from "../types";
import { detach } from "./detach";
import { useExpandedPanel, useMountedRef, useRomSaveSyncListener } from "./savePanelHooks";
import { executeVersionRestore, loadFileVersions } from "./saveSlotFlow";

export interface FileVersionHistoryTarget {
  romId: number;
  slot: string;
  filename: string;
  isOffline: boolean;
  onRestored: () => void;
}

interface FileVersionHistoryState {
  expanded: boolean;
  versions: SaveVersionEntry[] | null;
  loading: boolean;
  restoringVersionId: number | null;
  loadError: string | null;
  handleToggle: () => void;
  fetchVersions: () => Promise<void>;
  handleRestore: (version: SaveVersionEntry) => Promise<void>;
}

export function useFileVersionHistory(
  target: FileVersionHistoryTarget,
  showConflict: (conflict: SyncConflict) => Promise<unknown>,
): FileVersionHistoryState {
  const { romId, slot, filename, onRestored, isOffline } = target;
  // Synchronous ref avoids re-loading when a successful restore collapses the
  // subsection and announces `save_sync` in the same turn, before React re-renders.
  const { expanded, expandedRef, setOpen, toggle } = useExpandedPanel(false);
  const [versions, setVersions] = useState<SaveVersionEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [restoringVersionId, setRestoringVersionId] = useState<number | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const mountedRef = useMountedRef();
  const readSeqRef = useRef(0);
  const prevOfflineRef = useRef(isOffline);

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
  }, [romId, slot, filename, mountedRef]);

  const handleToggle = () => {
    toggle(() => {
      if (!isOffline && versions === null && loadError === null) {
        detach(fetchVersions());
      }
    });
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

  // Self-refresh when a save-change event fires for this ROM: invalidate cache
  // immediately, and reload when expanded and online.
  const onRomSaveSync = useCallback(() => {
    readSeqRef.current += 1;
    setVersions(null);
    setLoadError(null);
    if (expandedRef.current && !isOffline) {
      detach(fetchVersions());
    } else {
      setLoading(false);
    }
  }, [isOffline, fetchVersions, expandedRef]);
  useRomSaveSyncListener(romId, true, onRomSaveSync);

  const handleRestore = async (version: SaveVersionEntry) => {
    setRestoringVersionId(version.id);
    try {
      if (await executeVersionRestore(romId, slot, version, showConflict)) {
        if (mountedRef.current) {
          setVersions(null);
          setOpen(false);
        }
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
