/**
 * Where focus goes on the platform page's BIOS part when what it stood on
 * changes under it — a row's or a bulk button's work, and a step of
 * the emulator selector.
 *
 * Wherever focus is lost, Steam puts it back where its own pointer resolves —
 * the top of the page — and scrolls the pane there. So every place that can
 * lose it is noted by a key, and focus is put back the way entry focus is
 * placed, once Steam has settled its own pointer.
 *
 * Steam also scrolls the pane to every element focus lands on, by a rule of its
 * own (`utils/scrollHelpers.ts`), so focus put back moves the view. Where a
 * button's work holds the pane's position, that position is put back at once
 * and again once Steam's own scroll has run, each time only while focus is
 * still inside the BIOS part.
 *
 * A button that greys itself keeps the focus: as Steam's shipped client code
 * shows (`steamui/sp.js`), its `DialogButton` never disables its DOM element,
 * it only styles and unwires it, so nothing here moves focus off a greyed
 * button.
 */

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { ENTRY_FOCUS_DELAY_MS, firstBodyStop, placeEntryFocus } from "../../utils/entryFocus";
import { SHOWN_ENTRY_ATTR } from "../EmulatorSelector";

/**
 * The delay `utils/scrollHelpers.ts` takes to land after Steam's own scroll to a
 * focused element — a value that holds on the device, not a measured duration.
 */
const AFTER_STEAMS_OWN_SCROLL_MS = 50;

/** The nearest ancestor of *el* that scrolls. */
function scrollerOf(el: HTMLElement): HTMLElement | null {
  const view = el.ownerDocument.defaultView;
  for (let node = el.parentElement; node && view; node = node.parentElement) {
    const style = view.getComputedStyle(node);
    if (/^(auto|scroll)$/.test(style.overflowY || style.overflow)) return node;
  }
  return null;
}

export interface BiosFocus {
  /** The key of the button whose work is running, which stays enabled through it. */
  pressed: string | null;
  /** Note that the button under *key* was pressed and its work starts. */
  mark: (key: string) => void;
  /** Note where the pane stands before the button under *key* opens a dialog. */
  hold: (key: string) => void;
  /** Put focus back on what *key* holds — its button, or the row itself. */
  refocus: (key: string) => void;
  /** The ref for the element *key* names. */
  slot: (key: string) => (el: HTMLDivElement | null) => void;
  /** The ref for the BIOS part as a whole. */
  attachRoot: (el: HTMLDivElement | null) => void;
  /**
   * Step the selector by *apply*. Where focus was on one of the table's rows
   * (*rowKeys*, in order), it lands afterwards on the same file's row, else on
   * the new table's top row, else on the carousel's shown entry; focus that
   * was anywhere else stays where it is.
   */
  step: (apply: () => void) => void;
}

/**
 * *busy* is whether the page's BIOS work is running; *rowKeys* the keys of the
 * table's rows as now shown, in order.
 */
export function useBiosFocus(busy: boolean, rowKeys: readonly string[]): BiosFocus {
  const [pressed, setPressed] = useState<string | null>(null);
  const [stepped, setStepped] = useState<{ from: string } | null>(null);
  const slots = useRef(new Map<string, HTMLDivElement>());
  const root = useRef<HTMLDivElement | null>(null);
  const held = useRef<{ key: string; scroller: HTMLElement; top: number } | null>(null);
  const rows = useRef(rowKeys);
  useLayoutEffect(() => {
    rows.current = rowKeys;
  });
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());
  const later = useCallback((run: () => void, ms: number) => {
    const timer = setTimeout(() => {
      timers.current.delete(timer);
      run();
    }, ms);
    timers.current.add(timer);
  }, []);
  useEffect(() => {
    const pending = timers.current;
    return () => {
      for (const timer of pending) clearTimeout(timer);
      pending.clear();
    };
  }, []);

  const hold = useCallback((key: string) => {
    const wrapper = slots.current.get(key);
    const scroller = wrapper ? scrollerOf(wrapper) : null;
    held.current = scroller ? { key, scroller, top: scroller.scrollTop } : null;
  }, []);
  const putBackScroll = useCallback(
    (key: string) => {
      const kept = held.current;
      if (kept?.key !== key) return;
      const restore = () => {
        const active = kept.scroller.ownerDocument.activeElement;
        if (root.current?.contains(active)) kept.scroller.scrollTop = kept.top;
      };
      restore();
      later(restore, AFTER_STEAMS_OWN_SCROLL_MS);
    },
    [later],
  );
  const focusKey = useCallback(
    (key: string) => {
      const wrapper = slots.current.get(key);
      if (!wrapper) return;
      placeEntryFocus(wrapper, firstBodyStop);
      putBackScroll(key);
    },
    [putBackScroll],
  );
  const refocus = useCallback(
    (key: string) => {
      later(() => focusKey(key), ENTRY_FOCUS_DELAY_MS);
    },
    [focusKey, later],
  );

  // Once the work is done, focus that went nowhere — the button gone with the
  // work — goes back to it; a reader who walked elsewhere meanwhile is left
  // where they are.
  useEffect(() => {
    if (busy || pressed === null) return;
    const timer = setTimeout(() => {
      setPressed(null);
      const wrapper = slots.current.get(pressed);
      const active = wrapper?.ownerDocument.activeElement ?? null;
      const lost = active === null || active === wrapper?.ownerDocument.body;
      if (wrapper && lost) focusKey(pressed);
      else putBackScroll(pressed);
      held.current = null;
    }, ENTRY_FOCUS_DELAY_MS);
    return () => clearTimeout(timer);
  }, [busy, pressed, focusKey, putBackScroll]);

  // After a step that took focus off a row: the same file's row, the top row,
  // or the carousel's shown entry.
  useEffect(() => {
    if (stepped === null) return;
    const timer = setTimeout(() => {
      setStepped(null);
      const keys = rows.current;
      const target = keys.includes(stepped.from) ? stepped.from : keys[0];
      if (target !== undefined) {
        const wrapper = slots.current.get(target);
        const active = wrapper?.ownerDocument.activeElement ?? null;
        if (wrapper && !wrapper.contains(active)) focusKey(target);
        return;
      }
      const entry = root.current?.querySelector<HTMLElement>(`[${SHOWN_ENTRY_ATTR}]`);
      if (entry) placeEntryFocus(entry, firstBodyStop);
    }, ENTRY_FOCUS_DELAY_MS);
    return () => clearTimeout(timer);
  }, [stepped, focusKey]);

  const step = useCallback((apply: () => void) => {
    const active = root.current?.ownerDocument.activeElement ?? null;
    const from = rows.current.find((key) => slots.current.get(key)?.contains(active));
    if (from !== undefined) setStepped({ from });
    apply();
  }, []);

  const slot = useCallback(
    (key: string) => (el: HTMLDivElement | null) => {
      if (el) slots.current.set(key, el);
      else slots.current.delete(key);
    },
    [],
  );
  const attachRoot = useCallback((el: HTMLDivElement | null) => {
    root.current = el;
  }, []);

  const mark = useCallback(
    (key: string) => {
      // A dialog's button noted its position before the dialog opened.
      if (held.current?.key !== key) hold(key);
      setPressed(key);
    },
    [hold],
  );

  return { pressed, mark, hold, refocus, slot, attachRoot, step };
}
