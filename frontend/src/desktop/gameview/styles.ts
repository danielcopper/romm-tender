import type { CSSProperties } from "react";

export const SOLID_PLAY_BAR_BG = "rgb(39, 44, 53)";
export const GLASS_PLAY_BAR_BG = "rgba(36, 40, 47, 0.15)";
export const GLASS_PLAY_BAR_GRADIENT =
  "radial-gradient(100% 80% at 64% 95%, rgba(107, 115, 127, 0.15) 0%, rgba(62, 70, 80, 0.25) 20%, rgba(36, 40, 47, 0.25) 100%)";
export const PINNED_PLAY_BAR_SHADOW = "rgba(0, 0, 0, 0.267) 0px 6px 16px, rgba(0, 0, 0, 0.533) 0px 2px 6px";

const STEAM_CARD_BG =
  "radial-gradient(100% 80% at 64% 95%, rgba(153, 174, 204, 0.3) 0%, rgba(125, 150, 184, 0.13) 40%, rgba(83, 104, 104, 0.1) 100%)";
const STEAM_CARD_SHADOW = "0 0 12px rgba(0, 0, 0, 0.145)";

// Steam draws a card's edge with an image (`border-image`, sliced at 7px, drawn
// 1px wide). These are that image's four edges as gradients, so no image of
// Steam's is copied here.
const CARD_EDGES = [
  "linear-gradient(to right, rgba(217, 225, 243, 0.04), rgba(217, 225, 243, 0.12) 25%, rgba(217, 225, 243, 0.21), rgba(217, 225, 243, 0.08) 75%, rgba(217, 225, 243, 0.03)) top / 100% 1px",
  "linear-gradient(to right, rgba(62, 79, 109, 0.04), rgba(62, 79, 109, 0.06) 25%, rgba(63, 80, 110, 0.07), rgba(106, 120, 147, 0.06) 75%, rgba(145, 157, 181, 0.04)) bottom / 100% 1px",
  "linear-gradient(rgba(217, 225, 243, 0.04), rgba(217, 225, 243, 0.06) 25%, rgba(217, 225, 243, 0.06), rgba(217, 225, 243, 0.04) 75%, rgba(217, 225, 243, 0.02)) left / 1px 100%",
  "linear-gradient(rgba(217, 225, 243, 0.03), rgba(217, 225, 243, 0.05) 25%, rgba(217, 225, 243, 0.05), rgba(217, 225, 243, 0.06) 75%, rgba(217, 225, 243, 0.11)) right / 1px 100%",
].map((edge) => `${edge} no-repeat border-box`);

export const CARD_STYLE: CSSProperties = {
  boxSizing: "border-box",
  padding: "20px",
  border: "1px solid transparent",
  background: [...CARD_EDGES, `${STEAM_CARD_BG} padding-box border-box`].join(", "),
  borderRadius: "2px",
  boxShadow: STEAM_CARD_SHADOW,
  color: "#ffffff",
  fontFamily: '"Motiva Sans", Arial, Helvetica, sans-serif',
};

export const WARNING_COLOR = "#ffaa00";

export const WARNING_CARD_STYLE: CSSProperties = {
  ...CARD_STYLE,
  display: "flex",
  alignItems: "flex-start",
  gap: "16px",
  border: "1px solid rgba(255, 170, 0, 0.35)",
  borderLeft: `4px solid ${WARNING_COLOR}`,
  background: `${STEAM_CARD_BG} padding-box border-box`,
};

export const MODAL_CONTAINER_STYLE: CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 10000,
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
};

export const BACKDROP_BUTTON_STYLE: CSSProperties = {
  position: "absolute",
  inset: 0,
  backgroundColor: "rgba(0, 0, 0, 0.65)",
  backdropFilter: "blur(4px)",
  WebkitBackdropFilter: "blur(4px)",
  border: "none",
  margin: 0,
  padding: 0,
  cursor: "default",
};

export const BUTTON_STYLE: CSSProperties = {
  padding: "5px 12px",
  fontSize: "12px",
  fontWeight: 500,
  borderRadius: "3px",
  border: "1px solid rgba(255, 255, 255, 0.15)",
  backgroundColor: "rgba(255, 255, 255, 0.08)",
  color: "#ffffff",
  cursor: "pointer",
  transition: "all 0.15s ease",
  outline: "none",
  userSelect: "none",
};

export const CONTAINER_STYLE: CSSProperties = {
  display: "inline-flex",
  flexDirection: "row",
  alignItems: "center",
  height: "48px",
  position: "relative",
  paddingBottom: "2px",
  boxSizing: "border-box",
  userSelect: "none",
  overflow: "visible",
  zIndex: 20,
};

export const BUTTON_GROUP_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "row",
  alignItems: "center",
  width: "200px",
  minWidth: "200px",
  maxWidth: "200px",
  height: "48px",
  position: "relative",
  borderRadius: "2px",
  boxShadow: "0 1px 4px rgba(0, 0, 0, 0.4)",
  overflow: "visible",
  zIndex: 20,
};

export const BUTTON_BASE_STYLE: CSSProperties = {
  height: "100%",
  flex: "1 1 auto",
  padding: "0 16px",
  border: "none",
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  gap: "8px",
  fontSize: "15px",
  fontWeight: 700,
  letterSpacing: "0.5px",
  color: "#ffffff",
  borderRadius: "2px",
  textShadow: "0 1px 2px rgba(0, 0, 0, 0.4)",
  transition: "filter 0.15s ease, background 0.15s ease",
};

export const SIDE_ACTION_STYLE: CSSProperties = {
  height: "48px",
  width: "36px",
  minWidth: "36px",
  maxWidth: "36px",
  flex: "0 0 36px",
  border: "none",
  borderRadius: "0 2px 2px 0",
  background: "rgba(0, 0, 0, 0.25)",
  borderLeft: "1px solid rgba(255, 255, 255, 0.15)",
  color: "#ffffff",
  cursor: "pointer",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  transition: "background 0.15s ease",
};
