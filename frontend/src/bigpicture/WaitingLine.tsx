/**
 * A line saying an answer is still out, behind the throbber, so a reader
 * sees that something is under way rather than a sentence that might be the
 * answer. The platform page's BIOS block and a game's BIOS tab both wait with
 * it, so the two pages wait alike.
 */

import type { FC, ReactNode } from "react";
import { Throbber } from "../shared/Throbber";

export const WaitingLine: FC<{ children: ReactNode }> = ({ children }) => (
  <span data-testid="waiting-line" style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
    <Throbber />
    {children}
  </span>
);
