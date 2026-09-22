/**
 * The right-hand pane of the Data Management page: what one population IS, the
 * numbers about it, and what can be done with it.
 *
 * A pane never repeats its row's name as a heading — the row is on screen
 * beside it — so each opens with the sentence that says what the population is
 * and what removing it costs. One pane offers nothing at all (Recovery
 * bundles), which is the shape this page exists to allow: a row that shows and
 * offers nothing is still something this device holds.
 *
 * The panes are renderers — every read, every handler and the busy state live
 * in `useDataPage`.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Data
 * Management.
 */

import { useState, type FC, type ReactNode } from "react";
import { ButtonItem, DialogButton, Field, PanelSection, PanelSectionRow, TextField, ToggleField } from "@decky/ui";
import { detach } from "../../utils/detach";
import { formatBytes } from "../../utils/formatters";
import { fuzzyMatch } from "../../utils/fuzzyMatch";
import { pluralize } from "../../utils/pluralize";
import { SYNC_RUNNING_HINT, useSyncRunning } from "../../utils/syncRunning";
import { LoadingRow } from "../LoadingRow";
import { RemovedGamesCleanupSection } from "../RemovedGamesCleanup";
import { ButtonRow, FLAT_BUTTON, Muted, RED, SECONDARY_FONT } from "../layout/pane";
import { DEFAULT_WHITELIST_PATTERNS, type DataPageState, type NonSteamApp } from "./useDataPage";
import type { DataRowId } from "./rows";

/**
 * The one alarm colour on this page, brighter than the pane palette's `RED`.
 * It marks the single press that would remove RetroDECK, which breaks every
 * game this plugin launches — the page has no second thing to say this about.
 */
const ALARM = "#ff4444";

/** The line under a pane's sentence that carries its figures. */
const Figures: FC<{ children: ReactNode }> = ({ children }) => (
  <div style={{ fontSize: SECONDARY_FONT, color: "#dcdedf", padding: "0 16px 8px" }}>{children}</div>
);

/** A pane's own action result, under its buttons. */
const Status: FC<{ text: string; testId: string }> = ({ text, testId }) =>
  text ? (
    <div data-testid={testId} style={{ fontSize: "12px", color: "#dcdedf", padding: "0 16px 8px" }}>
      {text}
    </div>
  ) : null;

/**
 * A button that asks once before it acts.
 *
 * The confirm is the label rather than a modal because the second press then
 * lands on the button focus is already standing on.
 */
const ConfirmButton: FC<{
  label: string;
  confirmLabel: string;
  disabled: boolean;
  onConfirm: () => void;
}> = ({ label, confirmLabel, disabled, onConfirm }) => {
  const [armed, setArmed] = useState(false);
  return (
    <DialogButton
      style={FLAT_BUTTON}
      disabled={disabled}
      onClick={() => {
        if (!armed) {
          setArmed(true);
          return;
        }
        // Disarm BEFORE the awaited work: a removal yields for seconds on a
        // large library, so a stray press while it runs must not re-enter.
        setArmed(false);
        onConfirm();
      }}
    >
      <span style={{ color: RED }}>{armed ? confirmLabel : label}</span>
    </DialogButton>
  );
};

const ShortcutsPane: FC<{ state: DataPageState }> = ({ state }) => {
  const syncRunning = useSyncRunning();
  return (
    <>
      <Muted>
        The Steam entries this plugin created for your RomM games. Removing them leaves downloaded ROM files and save
        files where they are; the next sync puts the shortcuts back.
      </Muted>
      <Figures>
        {state.shortcutCount === null ? "Counting…" : pluralize(state.shortcutCount, "shortcut")} in Steam
      </Figures>
      <ButtonRow padding="2px 16px 6px">
        <ConfirmButton
          label="Remove all shortcuts"
          confirmLabel="Remove every RomM shortcut?"
          disabled={state.busy || syncRunning}
          onConfirm={() => detach(state.removeAllShortcuts())}
        />
      </ButtonRow>
      {syncRunning && <Muted>{SYNC_RUNNING_HINT}</Muted>}
      <Status text={state.shortcutStatus} testId="status-shortcuts" />
    </>
  );
};

const RomFilesPane: FC<{ state: DataPageState }> = ({ state }) => {
  const syncRunning = useSyncRunning();
  const inventory = state.inventory;
  return (
    <>
      <Muted>
        The game files this plugin downloaded to your device. Removing them keeps every shortcut, so the games stay in
        your library and can be downloaded again.
      </Muted>
      <Figures>
        {inventory === null
          ? "Reading…"
          : `${pluralize(inventory.installed_roms, "game")} · ≈ ${formatBytes(inventory.installed_bytes)}`}
      </Figures>
      <Muted>The size is what your RomM server reported for these games, not a measurement of your disk.</Muted>
      <ButtonRow padding="2px 16px 6px">
        <ConfirmButton
          label="Uninstall all ROM files"
          confirmLabel="Delete every downloaded ROM file?"
          disabled={state.busy || syncRunning || inventory?.installed_roms === 0}
          onConfirm={() => detach(state.uninstallAllRoms())}
        />
      </ButtonRow>
      {syncRunning && <Muted>{SYNC_RUNNING_HINT}</Muted>}
      <Status text={state.uninstallStatus} testId="status-rom-files" />
    </>
  );
};

const GridImagesPane: FC<{ state: DataPageState }> = ({ state }) => {
  const syncRunning = useSyncRunning();
  const scanned = state.orphanedGridImages;
  return (
    <>
      <Muted>
        Steam keeps the images it shows for a shortcut after the shortcut is gone. These are the leftovers: images whose
        shortcut no longer exists. Images of games still in your library — including games this plugin did not add — are
        kept.
      </Muted>
      <Figures>{scanned === null ? "Not scanned yet" : `${pluralize(scanned, "orphaned image")} found`}</Figures>
      <ButtonRow padding="2px 16px 6px">
        {scanned === null || scanned === 0 ? (
          <DialogButton
            style={FLAT_BUTTON}
            disabled={state.busy || syncRunning}
            onClick={() => detach(state.cleanupGridImages(false))}
          >
            Scan for orphaned images
          </DialogButton>
        ) : (
          <ConfirmButton
            label={`Remove ${pluralize(scanned, "orphaned image")}`}
            confirmLabel={`Remove ${pluralize(scanned, "image")}?`}
            disabled={state.busy || syncRunning}
            onConfirm={() => detach(state.cleanupGridImages(true))}
          />
        )}
      </ButtonRow>
      {syncRunning && <Muted>{SYNC_RUNNING_HINT}</Muted>}
      <Status text={state.gridStatus} testId="status-grid-images" />
    </>
  );
};

/**
 * The whitelist, kept as the expandable list it has always been.
 *
 * Its search box is the one text input left on a pane, where the panel's rule
 * puts text input in a modal.
 */
const WhitelistSection: FC<{ state: DataPageState; onWhitelistChange: () => void }> = ({
  state,
  onWhitelistChange,
}) => {
  const [showWhitelist, setShowWhitelist] = useState(false);
  const [whitelistSearch, setWhitelistSearch] = useState("");
  // The list is what the removal would take, so it lists the foreign entries
  // and never this plugin's own — protecting one of ours from a removal that
  // cannot reach it would say the two were ever in the same set.
  const listed = state.foreignApps ?? [];
  const filteredApps = whitelistSearch ? listed.filter((app) => fuzzyMatch(whitelistSearch, app.name)) : listed;

  const handleToggle = (app: NonSteamApp, checked: boolean) => {
    const matchingPattern = DEFAULT_WHITELIST_PATTERNS.find((p) => app.name.toLowerCase().includes(p));
    let newDisabled = [...state.disabledDefaults];
    let newCustom = [...state.customNames];

    if (checked) {
      if (matchingPattern && state.disabledDefaults.includes(matchingPattern)) {
        newDisabled = newDisabled.filter((p) => p !== matchingPattern);
      } else if (!matchingPattern && !newCustom.includes(app.name)) {
        newCustom.push(app.name);
      }
    } else {
      if (matchingPattern && !newDisabled.includes(matchingPattern)) {
        newDisabled.push(matchingPattern);
      }
      newCustom = newCustom.filter((n) => n !== app.name);
    }

    state.persistWhitelist(newDisabled, newCustom);
    onWhitelistChange();
  };

  return (
    <>
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          onClick={() => {
            setShowWhitelist(!showWhitelist);
            onWhitelistChange();
          }}
        >
          {showWhitelist ? "Hide whitelist" : `Configure whitelist (${state.whitelistedIds.size} protected)`}
        </ButtonItem>
      </PanelSectionRow>

      {showWhitelist && !state.settingsLoaded && <LoadingRow />}
      {showWhitelist && state.settingsLoaded && (
        <>
          <PanelSectionRow>
            <TextField
              label="Search games"
              value={whitelistSearch}
              onChange={(e) => setWhitelistSearch(e.target.value)}
            />
          </PanelSectionRow>
          <PanelSectionRow>
            <Field label={`Toggle ON to protect (${filteredApps.length}/${listed.length}):`} />
          </PanelSectionRow>
          {filteredApps.map((app) => (
            <PanelSectionRow key={app.appId}>
              <ToggleField
                label={
                  DEFAULT_WHITELIST_PATTERNS.some((p) => app.name.toLowerCase().includes(p))
                    ? `${app.name} (auto)`
                    : app.name
                }
                checked={state.whitelistedIds.has(app.appId)}
                onChange={(checked: boolean) => handleToggle(app, checked)}
              />
            </PanelSectionRow>
          ))}
        </>
      )}
    </>
  );
};

/**
 * What is in the Steam library that neither Steam nor this plugin put there.
 *
 * Tender's own shortcuts are excluded by OWNERSHIP rather than by name: the
 * default whitelist matches launcher names, and a synced library's entries
 * carry game names, so a name-based rule would sweep the whole library away.
 * Removing what this plugin created is the Tender's-shortcuts row's job.
 *
 * Where ownership could not be established the pane offers nothing at all —
 * the same abort the grid cleanup takes when its own scan cannot run.
 */
const NonSteamPane: FC<{ state: DataPageState }> = ({ state }) => {
  const [armed, setArmed] = useState(false);
  const [retrodeckArmed, setRetrodeckArmed] = useState(false);
  const disarm = () => {
    setArmed(false);
    setRetrodeckArmed(false);
  };
  const foreign = state.foreignApps;
  const toRemove = (foreign ?? []).filter((a) => !state.whitelistedIds.has(a.appId));
  const retrodeckAtRisk = toRemove.some((a) => a.name.toLowerCase().includes("retrodeck"));

  const press = () => {
    if (!armed) {
      setArmed(true);
      return;
    }
    if (retrodeckAtRisk && !retrodeckArmed) {
      setRetrodeckArmed(true);
      return;
    }
    disarm();
    detach(state.removeNonSteamApps(toRemove));
  };

  const label = () => {
    if (retrodeckArmed) return "!! RETRODECK WILL BE REMOVED !! Press to confirm";
    if (armed && retrodeckAtRisk) return `WARNING: RetroDECK not protected! Remove ${toRemove.length} games?`;
    if (armed) return `Remove ${toRemove.length} games (${state.whitelistedIds.size} whitelisted)?`;
    return `Remove ${pluralize(toRemove.length, "non-Steam game")}`;
  };

  const figures = () => {
    if (foreign === null) return "Could not be read";
    if (foreign.length === 0) return "No other non-Steam games found";
    return `${foreign.length} ${foreign.length === 1 ? "entry" : "entries"} · ${state.whitelistedIds.size} protected · ${toRemove.length} would be removed`;
  };

  return (
    <>
      <Muted>
        Everything in your Steam library that neither Steam nor this plugin installed — emulators, launchers, browsers,
        games you added by hand. Your RomM games are not counted here; they are the Tender&apos;s shortcuts row. The
        whitelist below protects what you keep; everything else is what the button removes.
      </Muted>
      <Figures>{figures()}</Figures>
      {foreign === null && (
        <Muted>
          Steam&apos;s shortcut list could not be read, so nothing here can be told apart from your RomM games. Nothing
          is removed while that is true — open the page again to retry.
        </Muted>
      )}
      {foreign !== null && foreign.length > 0 && (
        <>
          <ButtonRow padding="2px 16px 6px">
            <DialogButton style={FLAT_BUTTON} disabled={state.busy} onClick={press}>
              <span style={{ color: retrodeckArmed ? ALARM : RED, fontWeight: retrodeckArmed ? "bold" : "normal" }}>
                {label()}
              </span>
            </DialogButton>
          </ButtonRow>
          {retrodeckArmed && <Muted>RetroDECK is NOT in the whitelist and will be permanently removed!</Muted>}
          <PanelSection>
            <WhitelistSection state={state} onWhitelistChange={disarm} />
          </PanelSection>
        </>
      )}
      <Status text={state.nonSteamStatus} testId="status-non-steam" />
    </>
  );
};

/**
 * The games this device still keeps that RomM no longer has.
 *
 * The count costs a server round trip, so it is the scan's answer rather than
 * something the page opens with, and the section that runs the scan is what
 * reports it.
 */
const RemovedGamesPane: FC<{ state: DataPageState }> = ({ state }) => (
  <>
    <Muted>
      Games whose entry is gone from your RomM server while this device still holds their shortcut, downloaded files and
      saves. The review below scans for them and shows what removing each one would take with it.
    </Muted>
    <RemovedGamesCleanupSection onScanned={state.recordRemovedGamesScan} />
  </>
);

/**
 * What the cleanup sealed before it deleted anything — listed, and deleted
 * nowhere. The page's claim is what this device holds, so a bundle taking disk
 * has to be visible even where nothing here can remove it.
 */
const RecoveryBundlesPane: FC<{ state: DataPageState }> = ({ state }) => {
  const inventory = state.inventory;
  return (
    <>
      <Muted>
        Before the cleanup deletes a game&apos;s local data it seals a snapshot of it, in{" "}
        <code>~/romm-tender-recovery/</code>. Nothing is ever read back automatically and nothing here removes one —
        they are yours to keep, move or delete in a file manager.
      </Muted>
      <Figures>
        {inventory === null
          ? "Reading…"
          : `${pluralize(inventory.recovery_bundles, "bundle")} · ${formatBytes(inventory.recovery_bytes)}`}
      </Figures>
      <Muted>
        {inventory === null || inventory.recovery_bundles === 0
          ? "Nothing has been sealed under that folder. Bundles an older version wrote elsewhere are not counted here."
          : "Each carries a README explaining what it holds. Bundles an older version sealed under a different folder are not counted here."}
      </Muted>
    </>
  );
};

export const DataDetail: FC<{ rowId: DataRowId; state: DataPageState }> = ({ rowId, state }) => {
  switch (rowId) {
    case "shortcuts":
      return <ShortcutsPane state={state} />;
    case "rom-files":
      return <RomFilesPane state={state} />;
    case "grid-images":
      return <GridImagesPane state={state} />;
    case "non-steam":
      return <NonSteamPane state={state} />;
    case "removed-games":
      return <RemovedGamesPane state={state} />;
    case "recovery-bundles":
      return <RecoveryBundlesPane state={state} />;
  }
};
