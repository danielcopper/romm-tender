/**
 * Shared state and lifecycle hook for collapsible save slot accordion panels.
 * Owns expand/collapse state, lazy-loading inactive slot saves with sequence
 * ordering, optional `save_sync` cache invalidation, and slot activate/delete
 * transitions.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getSlotSaves, logWarn } from "../api/backend";
import type { SaveStatus, SlotDeleteInfo, SlotSaveFile } from "../types";
import { detach } from "./detach";
import { useExpandedPanel, useMountedRef, useRomSaveSyncListener } from "./savePanelHooks";
import { executeSlotSwitch, loadSlotDeleteInfo } from "./saveSlotFlow";
import { showToast } from "./toast";

interface UseSlotPanelStateOptions {
  romId: number;
  slotName: string;
  isActive: boolean;
  defaultExpanded: boolean;
  refreshOnSaveSync?: boolean;
  logPrefix: string;
  onSlotActivated: (newStatus: SaveStatus) => Promise<void> | void;
}

export interface SlotPanelState {
  expanded: boolean;
  slotFiles: SlotSaveFile[] | null;
  loadingSlot: boolean;
  switching: boolean;
  switchError: string | null;
  deleting: boolean;
  isMounted: () => boolean;
  handleToggle: () => void;
  handleActivate: () => Promise<void>;
  handleDelete: (runConfirmAndDelete: (info: SlotDeleteInfo) => Promise<void> | void) => Promise<void>;
}

export function useSlotPanelState({
  romId,
  slotName,
  isActive,
  defaultExpanded,
  refreshOnSaveSync = false,
  logPrefix,
  onSlotActivated,
}: UseSlotPanelStateOptions): SlotPanelState {
  const { expanded, expandedRef, toggle } = useExpandedPanel(defaultExpanded);
  const [slotFiles, setSlotFiles] = useState<SlotSaveFile[] | null>(null);
  const [loadingSlot, setLoadingSlot] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [switchError, setSwitchError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const switchErrorTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useMountedRef();
  const slotSavesSeqRef = useRef(0);

  useEffect(() => {
    return () => {
      if (switchErrorTimerRef.current) clearTimeout(switchErrorTimerRef.current);
    };
  }, []);

  const isMounted = useCallback(() => mountedRef.current, [mountedRef]);

  const loadInactiveSlotSaves = useCallback(async () => {
    const seq = ++slotSavesSeqRef.current;
    setLoadingSlot(true);
    try {
      const result = await getSlotSaves(romId, slotName);
      if (!mountedRef.current || seq !== slotSavesSeqRef.current) return;
      if (result.success) {
        setSlotFiles(result.saves);
      } else {
        logWarn(
          `${logPrefix}: getSlotSaves for romId=${romId} slot='${slotName}' failed (${result.reason}): ${result.message}`,
        );
        setSlotFiles([]);
      }
    } catch (e) {
      logWarn(`${logPrefix}: failed to load slot saves for ${slotName}: ${e}`);
      if (!mountedRef.current || seq !== slotSavesSeqRef.current) return;
      setSlotFiles([]);
    } finally {
      if (mountedRef.current && seq === slotSavesSeqRef.current) setLoadingSlot(false);
    }
  }, [logPrefix, mountedRef, romId, slotName]);

  const onRomSaveSync = useCallback(() => {
    slotSavesSeqRef.current += 1;
    setSlotFiles(null);
    if (expandedRef.current) {
      detach(loadInactiveSlotSaves());
    } else {
      setLoadingSlot(false);
    }
  }, [loadInactiveSlotSaves, expandedRef]);
  useRomSaveSyncListener(romId, refreshOnSaveSync && !isActive, onRomSaveSync);

  const handleToggle = () => {
    toggle(() => {
      if (!isActive && slotFiles === null) {
        detach(loadInactiveSlotSaves());
      }
    });
  };

  const handleActivate = async () => {
    setSwitching(true);
    setSwitchError(null);
    try {
      const outcome = await executeSlotSwitch(romId, slotName, "activate");
      if (!mountedRef.current) return;
      if (outcome.ok) {
        await onSlotActivated(outcome.saveStatus);
        return;
      }
      // Partial switches toast rather than setting inline switchError:
      // `switch_incomplete` flips `isActive` after announcing the sync,
      // remounting the keyed panel and dropping local state; `switch_stopped`
      // toasts for parity.
      if (outcome.partial) {
        showToast(outcome.message);
        return;
      }
      setSwitchError(outcome.message);
      if (switchErrorTimerRef.current) clearTimeout(switchErrorTimerRef.current);
      switchErrorTimerRef.current = setTimeout(() => {
        if (mountedRef.current) setSwitchError(null);
      }, 5000);
    } finally {
      if (mountedRef.current) setSwitching(false);
    }
  };

  const handleDelete = async (runConfirmAndDelete: (info: SlotDeleteInfo) => Promise<void> | void) => {
    setDeleting(true);
    try {
      const info = await loadSlotDeleteInfo(romId, slotName);
      if (!info || !mountedRef.current) return;
      await runConfirmAndDelete(info);
    } finally {
      if (mountedRef.current) setDeleting(false);
    }
  };

  return {
    expanded,
    slotFiles,
    loadingSlot,
    switching,
    switchError,
    deleting,
    isMounted,
    handleToggle,
    handleActivate,
    handleDelete,
  };
}
