import { useCallback, useEffect, useRef, useState, type MutableRefObject } from "react";
import { isSaveSyncForRom } from "./saveHelpers";

/** Track whether the owning component is currently mounted. */
export function useMountedRef(): MutableRefObject<boolean> {
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  return mountedRef;
}

/**
 * Open/closed state mirrored in a ref, so code later in the same turn reads the
 * new value before React has re-rendered.
 */
export function useExpandedPanel(defaultExpanded: boolean) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  const expandedRef = useRef(defaultExpanded);
  const setOpen = useCallback((open: boolean) => {
    expandedRef.current = open;
    setExpanded(open);
  }, []);
  const toggle = useCallback(
    (onWillExpand?: () => void) => {
      const willExpand = !expandedRef.current;
      setOpen(willExpand);
      if (willExpand) onWillExpand?.();
    },
    [setOpen],
  );
  return { expanded, expandedRef, setOpen, toggle };
}

/**
 * Subscribe to `romm_data_changed` events matching `save_sync` for `romId`.
 * Pass a memoized `onSaveSync` callback so the window listener is only
 * re-registered when `enabled`, `romId`, or `onSaveSync` changes.
 */
export function useRomSaveSyncListener(romId: number, enabled: boolean, onSaveSync: () => void): void {
  useEffect(() => {
    if (!enabled) return;
    const onDataChanged = ({ detail }: WindowEventMap["romm_data_changed"]) => {
      if (!isSaveSyncForRom(detail, romId)) return;
      onSaveSync();
    };
    globalThis.addEventListener("romm_data_changed", onDataChanged);
    return () => globalThis.removeEventListener("romm_data_changed", onDataChanged);
  }, [enabled, romId, onSaveSync]);
}
