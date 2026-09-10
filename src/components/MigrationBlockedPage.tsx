import { FC, useState } from "react";
import { PanelSection, PanelSectionRow, ButtonItem, Field, ConfirmModal, showModal } from "@decky/ui";
import { showToast } from "../utils/toast";
import { migrateRetroDeckFiles, dismissRetrodeckMigration } from "../api/backend";
import type { MigrationStatus } from "../types";
import { clearMigration } from "../utils/migrationStore";
import { LegacyInstallNotice } from "./LegacyInstallBanner";
import { DataLocationNoticeSection } from "./DataLocationNotice";
import { MigrationConflictModal } from "./MigrationConflictModal";
import { scrollToTop } from "../utils/scrollHelpers";
import { detach } from "../utils/detach";

interface MigrationBlockedPageProps {
  migration: MigrationStatus;
}

export const MigrationBlockedPage: FC<MigrationBlockedPageProps> = ({ migration }) => {
  const [migrating, setMigrating] = useState(false);
  const [migrateResult, setMigrateResult] = useState("");

  const runMigration = async (strategy: "overwrite" | "skip" | null) => {
    setMigrating(true);
    setMigrateResult("");
    try {
      const result = await migrateRetroDeckFiles(strategy);
      if (result.needs_confirmation) {
        setMigrating(false);
        showModal(
          <MigrationConflictModal
            conflictCount={result.conflict_count ?? 0}
            onChoice={(s) => {
              detach(runMigration(s));
            }}
          />,
        );
        return;
      }
      setMigrateResult(result.message);
      if (result.success) {
        clearMigration();
        showToast(result.message || "Migration complete.");
      }
    } catch {
      setMigrateResult("Migration failed");
    }
    setMigrating(false);
  };

  const handleMigrate = () => {
    detach(runMigration(null));
  };

  const handleDismiss = () => {
    showModal(
      <ConfirmModal
        strTitle="Dismiss Migration?"
        strDescription={
          "This will accept that some ROMs and saves remain at the old location. " +
          "The plugin will continue with the new path. Save data may be inconsistent " +
          "across sessions. Are you sure?"
        }
        strOKButtonText="Dismiss"
        strCancelButtonText="Cancel"
        onOK={() => {
          detach(
            (async () => {
              try {
                const result = await dismissRetrodeckMigration();
                if (result.success) {
                  clearMigration();
                  showToast("Migration dismissed.");
                }
              } catch {
                setMigrateResult("Dismiss failed");
              }
            })(),
          );
        }}
      />,
    );
  };

  return (
    <>
      <PanelSection title="RetroDECK Migration Required">
        <PanelSectionRow>
          <div
            style={{
              padding: "8px 12px",
              backgroundColor: "rgba(212, 167, 44, 0.15)",
              borderLeft: "3px solid #d4a72c",
              borderRadius: "4px",
            }}
          >
            <div
              style={{
                fontSize: "13px",
                fontWeight: "bold",
                color: "#d4a72c",
                marginBottom: "6px",
              }}
            >
              {"⚠️"} RetroDECK location changed
            </div>
            <div style={{ fontSize: "12px", color: "rgba(255, 255, 255, 0.7)", marginBottom: "4px" }}>
              From: {migration.old_path ?? "unknown"}
            </div>
            <div style={{ fontSize: "12px", color: "rgba(255, 255, 255, 0.7)", marginBottom: "4px" }}>
              To: {migration.new_path ?? "unknown"}
            </div>
            <div style={{ fontSize: "12px", color: "rgba(255, 255, 255, 0.9)" }}>
              {migration.roms_count ?? 0} ROM(s), {migration.bios_count ?? 0} BIOS, {migration.saves_count ?? 0} save(s)
              to migrate
            </div>
          </div>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={migrating}
            onClick={handleMigrate}
            // @ts-expect-error onFocus works at runtime; not in Decky's ButtonItem types
            onFocus={scrollToTop}
          >
            {migrating ? "Migrating..." : "Migrate Files"}
          </ButtonItem>
        </PanelSectionRow>
        <PanelSectionRow>
          <ButtonItem layout="below" disabled={migrating} onClick={handleDismiss}>
            Dismiss
          </ButtonItem>
        </PanelSectionRow>
        {migrateResult && (
          <PanelSectionRow>
            <Field label={migrateResult} />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <div style={{ fontSize: "11px", color: "rgba(255, 255, 255, 0.55)", padding: "4px 0" }}>
            Or revert RetroDECK to its previous location — the plugin will detect it automatically.
          </div>
        </PanelSectionRow>
      </PanelSection>
      {/* This page replaces the panel, so it carries the pre-rename install
          warning itself — a blocked plugin is what makes a user tidy the older
          one out of Decky, and their games all launch through it. It sits after
          the migration's own explanation and actions, as a section of its own
          rather than a section nested inside this one, and renders nothing
          while no older install stands beside this one. */}
      <LegacyInstallNotice />
      {/* And the data-location notice, for a stronger reason than the one
          above: leaving this page needs a user action, so a condition invisible
          here is invisible for as long as the user takes to migrate RetroDECK —
          and one of its two conditions is itself a question only the user can
          answer. It renders nothing while neither stands. */}
      <DataLocationNoticeSection />
    </>
  );
};
