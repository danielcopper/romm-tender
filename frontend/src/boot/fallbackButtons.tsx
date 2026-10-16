/**
 * The buttons the fallback page presses with, chosen once from the start-up
 * report by {@link fallbackButtons}.
 *
 * Steam's own — `DialogButton` in a `Focusable` column — are what the
 * controller reaches: Steam's navigation moves between the nodes its own
 * components register, and a plain element registers none. They are used only
 * where neither search missed, because each is a lookup like any other and the
 * page exists because one came back empty. Otherwise the page presses with
 * plain buttons and asks for the touch screen, as Decky Loader's own error page
 * does (`DeckyErrorBoundary.tsx`).
 *
 * The page receives the set rather than importing it, so that it stays free of
 * `@decky/ui` itself (`StartupFailurePanel.tsx`).
 */

import { DialogButton, Focusable } from "@decky/ui";
import type { CSSProperties, FC, ReactNode } from "react";

import type { StartupReport } from "./steamModules";

export interface FallbackButtonProps {
  onClick: () => void;
  disabled: boolean;
  children: ReactNode;
}

export interface FallbackButtons {
  /** Holds the page's buttons, one under the other. */
  Column: FC<{ children: ReactNode }>;
  Button: FC<FallbackButtonProps>;
  /** The controller cannot reach these, so the page asks for the touch screen above them. */
  touchOnly: boolean;
}

const column: CSSProperties = { display: "flex", flexDirection: "column", gap: "8px", margin: "0 0 10px" };
const plainButton: CSSProperties = { padding: "8px 12px", fontSize: "14px", width: "100%" };

export const STEAM_BUTTONS: FallbackButtons = {
  Column: ({ children }) => (
    <Focusable flow-children="vertical" style={column}>
      {children}
    </Focusable>
  ),
  Button: ({ onClick, disabled, children }) => (
    <DialogButton onClick={onClick} disabled={disabled}>
      {children}
    </DialogButton>
  ),
  touchOnly: false,
};

export const PLAIN_BUTTONS: FallbackButtons = {
  Column: ({ children }) => <div style={column}>{children}</div>,
  Button: ({ onClick, disabled, children }) => (
    <button type="button" style={plainButton} onClick={onClick} disabled={disabled}>
      {children}
    </button>
  ),
  touchOnly: true,
};

/** Steam's buttons where both of their searches answered; plain ones otherwise. */
export function fallbackButtons(report: StartupReport): FallbackButtons {
  const missed = report.missing.includes("DialogButton") || report.missing.includes("Focusable");
  return missed ? PLAIN_BUTTONS : STEAM_BUTTONS;
}
