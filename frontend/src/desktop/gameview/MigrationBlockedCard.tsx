import type { CSSProperties, FC } from "react";
import { FaExclamationTriangle } from "react-icons/fa";
import { MIGRATION_BLOCKED_DESKTOP_MESSAGE, MIGRATION_BLOCKED_TITLE } from "../../utils/migrationWording";
import { WARNING_CARD_STYLE, WARNING_COLOR } from "./styles";

const ICON_STYLE: CSSProperties = {
  color: WARNING_COLOR,
  fontSize: "32px",
  flexShrink: 0,
  marginTop: "2px",
};

const TEXT_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "6px",
  minWidth: 0,
};

const TITLE_STYLE: CSSProperties = {
  fontSize: "17px",
  fontWeight: 600,
  color: "rgba(255, 255, 255, 0.95)",
};

const MESSAGE_STYLE: CSSProperties = {
  fontSize: "13px",
  color: "rgba(255, 255, 255, 0.75)",
  lineHeight: 1.5,
};

export const MigrationBlockedCard: FC = () => (
  <div style={WARNING_CARD_STYLE}>
    <FaExclamationTriangle style={ICON_STYLE} aria-hidden="true" />
    <div style={TEXT_STYLE}>
      <div style={TITLE_STYLE}>{MIGRATION_BLOCKED_TITLE}</div>
      <div style={MESSAGE_STYLE}>{MIGRATION_BLOCKED_DESKTOP_MESSAGE}</div>
    </div>
  </div>
);
