import { useCallback, useEffect, useRef, useState } from "react";
import { getSlotSaves, logWarn } from "../api/backend";
import type { SaveStatus, SlotDeleteInfo, SlotSaveFile } from "../types";
import { detach } from "./detach";
import { useExpandedPanel, useMountedRef, useRomSaveSyncListener } from "./savePanelHooks";
import { executeSlotSwitch, loadSlotDeleteInfo } from "./saveSlotFlow";
import { slotSavesUnreadMessage } from "./saveSlotWording";
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
  /** Why the slot's saves could not be read, which stands in place of the list. */
  slotLoadError: string | null;
  switching: boolean;
  switchError: string | null;
  deleting: boolean;
  isMounted: () => boolean;
  handleToggle: () => void;
  handleActivate: () => Promise<void>;
  handleDelete: (runConfirmAndDelete: (info: SlotDeleteInfo) => Promise<void> | void) => Promise<void>;
}

/** An inactive slot's saves, or the line saying why they could not be read; a failure is logged, never thrown. */
async function readSlotSaves(
  romId: number,
  slotName: string,
  logPrefix: string,
): Promise<{ files: SlotSaveFile[] } | { error: string }> {
  try {
    const result = await getSlotSaves(romId, slotName);
    if (result.success) return { files: result.saves };
    logWarn(`${logPrefix}: slot '${slotName}' saves could not be read: ${result.message}`);
    return { error: slotSavesUnreadMessage(result.message) };
  } catch (e) {
    logWarn(`${logPrefix}: slot '${slotName}' saves could not be read: ${e}`);
    return { error: slotSavesUnreadMessage() };
  }
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
  const [slotLoadError, setSlotLoadError] = useState<string | null>(null);
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

  const slotFilesRef = useRef(slotFiles);
  useEffect(() => {
    slotFilesRef.current = slotFiles;
  }, [slotFiles]);

  // A failed read leaves the saves unread, so the next expand asks again.
  const loadInactiveSlotSaves = useCallback(async () => {
    const seq = ++slotSavesSeqRef.current;
    setLoadingSlot(true);
    setSlotLoadError(null);
    const answer = await readSlotSaves(romId, slotName, logPrefix);
    if (!mountedRef.current || seq !== slotSavesSeqRef.current) return;
    if ("files" in answer) setSlotFiles(answer.files);
    else setSlotLoadError(answer.error);
    setLoadingSlot(false);
  }, [logPrefix, mountedRef, romId, slotName]);

  // The list on screen stays through the re-read; a failed one keeps it.
  const rereadShownSlotSaves = useCallback(async () => {
    const seq = ++slotSavesSeqRef.current;
    const answer = await readSlotSaves(romId, slotName, logPrefix);
    if (!mountedRef.current || seq !== slotSavesSeqRef.current) return;
    if ("files" in answer) setSlotFiles(answer.files);
  }, [logPrefix, mountedRef, romId, slotName]);

  const onRomSaveSync = useCallback(() => {
    if (expandedRef.current && slotFilesRef.current !== null) {
      detach(rereadShownSlotSaves());
      return;
    }
    slotSavesSeqRef.current += 1;
    setSlotFiles(null);
    setSlotLoadError(null);
    if (expandedRef.current) {
      detach(loadInactiveSlotSaves());
    } else {
      setLoadingSlot(false);
    }
  }, [loadInactiveSlotSaves, rereadShownSlotSaves, expandedRef]);
  useRomSaveSyncListener(romId, refreshOnSaveSync && !isActive, onRomSaveSync);

  const handleToggle = () => {
    toggle(() => {
      if (!isActive && slotFiles === null) {
        detach(loadInactiveSlotSaves());
      }
    });
  };

  const handleActivate = async () => {
    if (switching || deleting) return;
    setSwitching(true);
    setSwitchError(null);
    try {
      const outcome = await executeSlotSwitch(romId, slotName, "activate");
      // Even after an unmount: a tab switch unmounts this panel, and the callback writes the new active slot.
      if (outcome.ok) {
        await onSlotActivated(outcome.saveStatus);
        return;
      }
      if (!mountedRef.current) return;
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
    if (switching || deleting) return;
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
    slotLoadError,
    switching,
    switchError,
    deleting,
    isMounted,
    handleToggle,
    handleActivate,
    handleDelete,
  };
}
