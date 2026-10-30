/**
 * One component's {@link AskDialog}, drawn in that component's document. One
 * dialog is open at a time.
 *
 * Every question ends: each `ask` names its `dismissed` answer up front, and the
 * host settles with it when a newer question replaces this one or the component
 * unmounts — so a flow awaiting a dialog on a page the user has left runs to its
 * cancel exit instead of waiting forever.
 */

import { Fragment, useCallback, useEffect, useRef, useState, type FC, type ReactNode } from "react";
import { createPortal } from "react-dom";
import type { AskDialog } from "./askDialog";

interface DialogHost {
  ask: AskDialog;
  /** Render this once, anywhere in the asking component's tree. */
  element: ReactNode;
}

/**
 * Draws the dialog into the body of the document the host is mounted in. Not in
 * place: the play bar can carry a `backdrop-filter` (`stickyPlayBarController`
 * sets one), which makes it the containing block of every `position: fixed`
 * descendant, so a dialog drawn inside it would be clipped to the bar.
 */
const DialogPortal: FC<{ children: ReactNode }> = ({ children }) => {
  const [anchor, setAnchor] = useState<HTMLSpanElement | null>(null);
  const target = anchor?.ownerDocument.body ?? null;
  return (
    <>
      <span ref={setAnchor} hidden />
      {target ? createPortal(children, target) : null}
    </>
  );
};

export function useDialogHost(): DialogHost {
  const [dialog, setDialog] = useState<ReactNode>(null);
  const dismissOpenRef = useRef<(() => void) | null>(null);
  const mountedRef = useRef(false);
  const questionSeqRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const dismissOpen = dismissOpenRef.current;
      dismissOpenRef.current = null;
      dismissOpen?.();
    };
  }, []);

  const ask = useCallback<AskDialog>(<T,>(dismissed: T, render: (resolve: (value: T) => void) => ReactNode) => {
    dismissOpenRef.current?.();
    if (!mountedRef.current) return Promise.resolve(dismissed);
    return new Promise<T>((outer) => {
      let settled = false;
      const settle = (value: T) => {
        if (settled) return;
        settled = true;
        if (dismissOpenRef.current === dismiss) {
          dismissOpenRef.current = null;
          if (mountedRef.current) setDialog(null);
        }
        outer(value);
      };
      const dismiss = () => settle(dismissed);
      dismissOpenRef.current = dismiss;
      // Keyed per question: two dialogs of one kind asked back to back — one save
      // conflict after another — would otherwise land in the same React batch and
      // the second would inherit the first's state.
      setDialog(<Fragment key={++questionSeqRef.current}>{render(settle)}</Fragment>);
    });
  }, []);

  return { ask, element: dialog === null ? null : <DialogPortal>{dialog}</DialogPortal> };
}
