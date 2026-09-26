import { FC } from "react";
import { WarningCard } from "./WarningCard";

interface VersionErrorCardProps {
  message: string;
  /** Compact mode for narrow contexts (QAM panel). */
  compact?: boolean;
}

/**
 * Polished error card shown when server version is below plugin minimum.
 *
 * It replaces whatever page it is shown on, and carries nothing beside the card
 * itself. It used to carry a second warning about an older plugin install; that
 * condition went with the plugin loader.
 */
export const VersionErrorCard: FC<VersionErrorCardProps> = ({ message, compact = false }) => (
  <WarningCard title="RomM Server Update Required" message={message} compact={compact} />
);
