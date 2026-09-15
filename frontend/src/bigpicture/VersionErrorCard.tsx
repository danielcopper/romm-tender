import { FC, useEffect, useState } from "react";
import { getVersionError, onVersionErrorChange } from "../utils/connectionState";
import { WarningCard } from "./WarningCard";

/**
 * Subscribe to version error state changes.
 * Returns the current version error (or null).
 */
export function useVersionError(): string | null {
  const [err, setErr] = useState<string | null>(getVersionError());
  useEffect(() => onVersionErrorChange(setErr), []);
  return err;
}

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
