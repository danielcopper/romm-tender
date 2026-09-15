import { FC, useEffect, useState } from "react";
import { getVersionError, onVersionErrorChange } from "../utils/connectionState";
import { LegacyInstallNotice } from "./LegacyInstallBanner";
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
 * It replaces whatever page it is shown on, so it also carries the pre-rename
 * install warning — this is the state a user is most likely to react to by
 * removing the older plugin, and the shortcuts that older install wrote still
 * launch through it. Below the card rather than above: the card is the
 * explanation of why the plugin cannot work, and a warning several sentences
 * long stacked on top of it pushes that explanation down a panel nothing here
 * can scroll (neither element takes focus). The notice renders nothing while no
 * older install stands beside this one, which is the ordinary case.
 */
export const VersionErrorCard: FC<VersionErrorCardProps> = ({ message, compact = false }) => (
  <>
    <WarningCard title="RomM Server Update Required" message={message} compact={compact} />
    <LegacyInstallNotice />
  </>
);
