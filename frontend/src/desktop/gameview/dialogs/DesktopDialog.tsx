/**
 * The frame every desktop dialog is drawn in — the backdrop, the box and the
 * title — and the text and button styles its dialogs share. Besides a dialog's
 * own buttons, Escape or a click on the backdrop leaves it, reported as
 * `onDismiss`. A dialog passes no `onDismiss` while it must not be left (a
 * request of its own in flight).
 */

import { useEffect, useRef, type CSSProperties, type FC, type ReactNode } from "react";
import { BACKDROP_BUTTON_STYLE, BUTTON_STYLE, MODAL_CONTAINER_STYLE } from "../styles";

interface DesktopDialogProps {
  /** Unique per dialog kind; ties the box's accessible name to its heading. */
  titleId: string;
  title: string;
  onDismiss?: (() => void) | undefined;
  children: ReactNode;
}

const DIALOG_BOX_STYLE: CSSProperties = {
  width: "520px",
  maxWidth: "92vw",
  maxHeight: "88vh",
  overflowY: "auto",
  background: "linear-gradient(180deg, #243547 0%, #17212b 40%, #0e141b 100%)",
  backgroundColor: "#161e27",
  border: "1px solid rgba(255, 255, 255, 0.15)",
  borderRadius: "6px",
  boxShadow: "0 16px 48px rgba(0, 0, 0, 0.85), inset 0 1px 0 rgba(255, 255, 255, 0.12)",
  position: "relative",
  zIndex: 1,
  padding: "20px 24px",
  boxSizing: "border-box",
  color: "#c7d5e0",
  fontFamily: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif",
  outline: "none",
};

const TITLE_STYLE: CSSProperties = {
  margin: "0 0 6px 0",
  fontSize: "18px",
  fontWeight: 700,
  color: "#ffffff",
};

export const DIALOG_TEXT_STYLE: CSSProperties = {
  fontSize: "13px",
  color: "#c7d5e0",
  lineHeight: 1.45,
  marginBottom: "12px",
};

export const DIALOG_MUTED_STYLE: CSSProperties = { fontSize: "12px", color: "#8f98a0", lineHeight: 1.4 };

export const DIALOG_ACTIONS_STYLE: CSSProperties = { display: "flex", flexDirection: "column", gap: "8px" };

const DIALOG_BUTTON_BASE: CSSProperties = {
  ...BUTTON_STYLE,
  padding: "8px 14px",
  fontSize: "13px",
  textAlign: "left",
};

type DialogButtonTone = "primary" | "secondary" | "quiet";

// Longhands only: `BUTTON_STYLE` sets `backgroundColor`, and a `background`
// shorthand beside it is one React cannot update cleanly between renders.
const TONE_STYLE: Record<DialogButtonTone, CSSProperties> = {
  primary: {
    backgroundImage: "linear-gradient(90deg, #1a9fff 0%, #0078d4 100%)",
    border: "1px solid rgba(26, 159, 255, 0.6)",
  },
  secondary: { backgroundImage: "none" },
  quiet: { backgroundImage: "none", backgroundColor: "transparent", color: "#8f98a0" },
};

/** The style of a dialog button of one tone, dimmed when it cannot be pressed. */
export function dialogButtonStyle(tone: DialogButtonTone, disabled = false): CSSProperties {
  return {
    ...DIALOG_BUTTON_BASE,
    ...TONE_STYLE[tone],
    ...(disabled ? { opacity: 0.5, cursor: "not-allowed" } : {}),
  };
}

export const DesktopDialog: FC<DesktopDialogProps> = ({ titleId, title, onDismiss, children }) => {
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    boxRef.current?.focus();
  }, []);

  // The listener goes on the window that owns the box, not the global one: this
  // code runs in SharedJSContext while the box lives in the desktop client's own
  // document, and a key pressed there never reaches SharedJSContext's window.
  useEffect(() => {
    const view = boxRef.current?.ownerDocument.defaultView;
    if (!view || !onDismiss) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onDismiss();
    };
    view.addEventListener("keydown", onKeyDown);
    return () => view.removeEventListener("keydown", onKeyDown);
  }, [onDismiss]);

  return (
    <div className="tender-desktop-dialog" style={MODAL_CONTAINER_STYLE}>
      <button
        type="button"
        aria-label="Close dialog"
        className="tender-desktop-dialog-backdrop"
        style={BACKDROP_BUTTON_STYLE}
        onClick={() => onDismiss?.()}
      />
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="tender-desktop-dialog-box"
        style={DIALOG_BOX_STYLE}
      >
        <h2 id={titleId} style={TITLE_STYLE}>
          {title}
        </h2>
        {children}
      </div>
    </div>
  );
};
