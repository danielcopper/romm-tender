/**
 * A label with the update dot beside it — on Main's Settings button and on
 * Updates in the Settings list. Whether it shows, and its one fade, are
 * `utils/updateDot.ts`'s; this is where it sits.
 */

import type { CSSProperties, FC, ReactNode } from "react";
import { UPDATE_AVAILABLE_COLOR } from "../utils/updateAvailableView";
import { DOT_FADE_STYLE, useUpdateDot } from "../utils/updateDot";

const DOT_SIZE_PX = 9;

/**
 * About 8 px past the word's end and raised like a superscript, its centre near
 * the top of the lowercase letters. Positioned absolutely, so it takes no room
 * and the label stays where it is when the dot comes or goes.
 */
const DOT: CSSProperties = {
  position: "absolute",
  left: "100%",
  top: "0.3em",
  marginLeft: "8px",
  marginTop: `${-DOT_SIZE_PX / 2}px`,
  width: `${DOT_SIZE_PX}px`,
  height: `${DOT_SIZE_PX}px`,
  borderRadius: "50%",
  background: UPDATE_AVAILABLE_COLOR,
};

const DOT_FADING: CSSProperties = { ...DOT, ...DOT_FADE_STYLE };

export const WithUpdateDot: FC<{ children: ReactNode }> = ({ children }) => {
  const dot = useUpdateDot();
  return (
    <span style={{ position: "relative" }}>
      {children}
      {dot !== "none" && (
        <span aria-hidden="true" data-testid="update-dot" style={dot === "fading" ? DOT_FADING : DOT} />
      )}
    </span>
  );
};
