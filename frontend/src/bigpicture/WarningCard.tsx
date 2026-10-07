import { FC } from "react";
import { FaExclamationTriangle } from "react-icons/fa";

interface WarningCardProps {
  title: string;
  /** Left out where the title says all there is to say. */
  message?: string;
  /** Compact mode for narrow contexts (QAM panel). */
  compact?: boolean;
  /** Whether the warning sign leads the card. False for a notice that reports a
   *  fact rather than something wrong. */
  showIcon?: boolean;
  /** Smaller type and less padding than `compact`, for a fact that stands beside
   *  the warning cards on the narrow page without competing with them. */
  minor?: boolean;
}

const SPACIOUS = { padding: "40px 32px", titleSize: "19px", titleWeight: 600, iconSize: "42px", messageSize: "14px" };
const COMPACT = { padding: "24px 16px", titleSize: "15px", titleWeight: 600, iconSize: "28px", messageSize: "12px" };
const MINOR = { padding: "8px 12px", titleSize: "12px", titleWeight: 400, iconSize: "16px", messageSize: "11px" };

/** Shared warning card layout: amber-bordered panel with the warning sign (unless
 *  `showIcon` is false), title and message. */
export const WarningCard: FC<WarningCardProps> = ({
  title,
  message,
  compact = false,
  showIcon = true,
  minor = false,
}) => {
  let size = compact ? COMPACT : SPACIOUS;
  if (minor) size = MINOR;
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: size.padding,
        gap: "14px",
        textAlign: "center",
        background: "rgba(14, 20, 27, 0.55)",
        border: "1px solid rgba(255, 170, 0, 0.35)",
        borderRadius: "6px",
        margin: compact ? "8px 4px" : "24px 2.8vw",
      }}
    >
      {showIcon && <FaExclamationTriangle style={{ color: "#ffaa00", fontSize: size.iconSize }} />}
      <div
        style={{
          fontSize: size.titleSize,
          fontWeight: size.titleWeight,
          color: "rgba(255, 255, 255, 0.95)",
        }}
      >
        {title}
      </div>
      {message && (
        <div
          style={{
            fontSize: size.messageSize,
            color: "rgba(255, 255, 255, 0.75)",
            maxWidth: compact ? "100%" : "680px",
            lineHeight: 1.5,
          }}
        >
          {message}
        </div>
      )}
    </div>
  );
};
