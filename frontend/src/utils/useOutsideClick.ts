import { useEffect, type RefObject } from "react";

/**
 * Invokes `onOutsideClick` when a mousedown occurs outside the element referenced by `ref`.
 *
 * Listens on the element's own document while `active` is true: the element may
 * belong to another document than the one this code runs in.
 */
export function useOutsideClick<T extends HTMLElement = HTMLElement>(
  ref: RefObject<T | null>,
  onOutsideClick: () => void,
  active: boolean,
): void {
  useEffect(() => {
    if (!active) return;

    const handleOutsideClick = (e: globalThis.MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        onOutsideClick();
      }
    };

    const targetDoc = ref.current?.ownerDocument;
    if (!targetDoc) return;

    targetDoc.addEventListener("mousedown", handleOutsideClick);
    return () => {
      targetDoc.removeEventListener("mousedown", handleOutsideClick);
    };
  }, [ref, onOutsideClick, active]);
}
