import { FC } from "react";
import { FaExclamationTriangle } from "react-icons/fa";

interface WarningCardProps {
  title: string;
  /** Left out where the title says all there is to say. */
  message?: string;
  /** Compact mode for narrow contexts (QAM panel). */
  compact?: boolean;
  /** Left out for a notice that reports a fact rather than something wrong. */
  showIcon?: boolean;
}

/** Shared warning card layout: amber-bordered panel with icon, title and message. */
export const WarningCard: FC<WarningCardProps> = ({ title, message, compact = false, showIcon = true }) => {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        padding: compact ? "24px 16px" : "40px 32px",
        gap: "14px",
        textAlign: "center",
        background: "rgba(14, 20, 27, 0.55)",
        border: "1px solid rgba(255, 170, 0, 0.35)",
        borderRadius: "6px",
        margin: compact ? "8px 4px" : "24px 2.8vw",
      }}
    >
      {showIcon && <FaExclamationTriangle style={{ color: "#ffaa00", fontSize: compact ? "28px" : "42px" }} />}
      <div
        style={{
          fontSize: compact ? "15px" : "19px",
          fontWeight: 600,
          color: "rgba(255, 255, 255, 0.95)",
        }}
      >
        {title}
      </div>
      {message && (
        <div
          style={{
            fontSize: compact ? "12px" : "14px",
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
