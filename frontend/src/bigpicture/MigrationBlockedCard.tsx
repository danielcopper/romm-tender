import { FC } from "react";
import { MIGRATION_BLOCKED_MESSAGE, MIGRATION_BLOCKED_TITLE } from "../utils/migrationWording";
import { WarningCard } from "./WarningCard";

interface MigrationBlockedCardProps {
  /** Compact mode for narrow contexts (QAM panel). */
  compact?: boolean;
}

/** Polished warning card shown on the game detail page when a RetroDECK migration is pending. */
export const MigrationBlockedCard: FC<MigrationBlockedCardProps> = ({ compact = false }) => (
  <WarningCard title={MIGRATION_BLOCKED_TITLE} message={MIGRATION_BLOCKED_MESSAGE} compact={compact} />
);
