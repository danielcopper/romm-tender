/**
 * The Settings page: five sections on the left, the focused section's controls
 * on the right.
 *
 * Every section's state and every handler lives here rather than in the section
 * components, which are pure renderers — the pane mounts only the focused
 * section, so a section owning its own reads would re-issue them on each move
 * through the list.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Settings.
 */

import { useState, useEffect, FC, type ReactNode } from "react";
import { ConfirmModal, Field, showModal } from "@decky/ui";
import { showToast } from "../utils/toast";
import {
  getSettings,
  saveServerUrl,
  connectWithCredentials,
  connectWithToken,
  connectWithPairingCode,
  signOut,
  saveSgdbApiKey,
  verifySgdbApiKey,
  saveSteamInputSetting,
  applySteamInputSetting,
  getSaveSyncSettings,
  updateSaveSyncSettings,
  syncAllSaves,
  saveLogLevel,
  savePreferredRegion,
  saveCollectionPlatformGroups,
  setCollectionNamingMode,
  getKnownRegions,
  fixRetroarchInputDriver,
  ensureDeviceRegistered,
  listDevices,
  getSaveSortMigrationStatus,
  migrateSaveSortFiles,
  dismissSaveSortMigration,
  logError,
} from "../api/backend";
import type {
  RegisteredDevice,
  CollectionNamingMode,
  SaveSyncSettings as SaveSyncSettingsType,
  RetroArchInputCheck,
  SettingsSection,
} from "../types";
import { SETTINGS_SECTIONS } from "../types";
import {
  setSaveSortMigrationStatus as setStoreSaveSortStatus,
  clearSaveSortMigration,
  useSaveSortMigrationState,
} from "../utils/saveSortMigrationStore";
import { setUpdateCheckSwitch, useUpdateNoticeState } from "../utils/updateNoticeStore";
import { detach } from "../utils/detach";
import { trimServerUrl, isValidServerUrl } from "../utils/serverUrl";
import { WidePage } from "./qam/WidePage";
import { ListDetail, type ListDetailItem } from "./qam/ListDetail";
import { ROW_MARKER_GAP, ROW_MARKER_WIDTH, SELECTION_ACCENT } from "./qam/pane";
import { pendingEdits } from "./settings/TextInputModal";
import { SaveSortMigrationSection } from "./settings/SaveSortMigrationSection";
import { ConnectionSection } from "./settings/ConnectionSection";
import { SteamGridDBSection } from "./settings/SteamGridDBSection";
import { SaveSyncSection } from "./settings/SaveSyncSection";
import { RegisteredDevicesSection } from "./settings/RegisteredDevicesSection";
import { ControllerSection } from "./settings/ControllerSection";
import { AdvancedSection } from "./settings/AdvancedSection";
import { LibrarySection, AUTO_REGION, DEFAULT_REGION_LABEL } from "./settings/LibrarySection";
import { showPreferredRegionModal } from "./settings/PreferredRegionModal";

interface SettingsPageProps {
  onBack: () => void;
  /**
   * The section the page opens on — a navigation from one of Main's notices
   * names the one that holds the action it is about. It is the OPENING
   * selection and nothing more: the panel reaches Settings only from Main, so
   * every navigation here mounts the page afresh.
   */
  section?: SettingsSection;
}

// Messages the connect handlers return to the ConnectModal (which surfaces them
// inline) when the sign-in can't even be attempted or the callable throws. The
// URL guard message mirrors the one the URL editor already shows.
const INVALID_URL_MESSAGE = "Enter a valid http:// or https:// server URL";
const GENERIC_SIGN_IN_ERROR = "Sign-in failed. Check your connection and try again.";

// What the list calls each section. The ids and their order are the navigation
// module's, so a section reachable by a jump is a section the list shows.
const SECTION_LABELS: Record<SettingsSection, string> = {
  connections: "Connections",
  "save-sync": "Save Sync",
  controller: "Controller",
  "steam-library": "Steam Library",
  advanced: "Advanced",
};

/** The list hands its ids back as plain strings; this is where one becomes a
 *  section again — by lookup rather than by assertion, so an id no section
 *  answers to opens the first section instead of typing as one that is absent. */
const asSection = (id: string | null): SettingsSection =>
  SETTINGS_SECTIONS.find((candidate) => candidate === id) ?? SETTINGS_SECTIONS[0];

export const SettingsPage: FC<SettingsPageProps> = ({ onBack, section }) => {
  const [selectedSection, setSelectedSection] = useState<SettingsSection>(section ?? SETTINGS_SECTIONS[0]);
  // Connection state
  const [url, setUrl] = useState("");
  const [hasToken, setHasToken] = useState(false);
  const [status, setStatus] = useState("");
  const [allowInsecureSsl, setAllowInsecureSsl] = useState(false);

  // SteamGridDB state
  const [sgdbApiKey, setSgdbApiKey] = useState("");

  // Save Sync state
  const [saveSyncSettings, setSaveSyncSettings] = useState<SaveSyncSettingsType | null>(null);
  const [saveSyncToggleKey, setSaveSyncToggleKey] = useState(0);
  const [deviceInfo, setDeviceInfo] = useState<{ device_id: string; device_name: string } | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncStatus, setSyncStatus] = useState("");

  // Registered devices state
  const [registeredDevices, setRegisteredDevices] = useState<RegisteredDevice[] | null>(null);
  const [devicesLoading, setDevicesLoading] = useState(false);
  const [devicesError, setDevicesError] = useState<string | null>(null);

  // Controller state
  const [steamInputMode, setSteamInputMode] = useState("default");
  const [steamInputStatus, setSteamInputStatus] = useState("");
  const [applyingSteamInput, setApplyingSteamInput] = useState(false);
  const [retroarchWarning, setRetroarchWarning] = useState<RetroArchInputCheck | null>(null);
  const [retroarchFixStatus, setRetroarchFixStatus] = useState("");

  // Save sort migration state
  const saveSortMigration = useSaveSortMigrationState();
  // The switch's position is on the notice payload, so the store is the one
  // place it is held; this page neither loads nor persists it itself.
  const updateNotice = useUpdateNoticeState();
  const [saveSortMigrating, setSaveSortMigrating] = useState(false);
  const [saveSortResult, setSaveSortResult] = useState("");

  // Advanced state
  const [logLevel, setLogLevel] = useState("warn");

  // Library state (preferred sibling-group region, ADR-0021)
  const [preferredRegion, setPreferredRegion] = useState(AUTO_REGION);
  const [libraryRegions, setLibraryRegions] = useState<string[]>([]);
  // Collection platform-groups toggle (relocated from the Collections tab, #1539).
  const [platformGroups, setPlatformGroups] = useState(false);
  // Steam-collection naming mode (#1539): "merge" (default) or "by_label".
  const [namingMode, setNamingMode] = useState<CollectionNamingMode>("merge");

  useEffect(() => {
    getSettings()
      .then((s) => {
        // Apply any pending edits that survived a remount, fall back to backend values
        setUrl(pendingEdits.url ?? s.romm_url);
        setHasToken(s.has_token);
        setAllowInsecureSsl(s.romm_allow_insecure_ssl);
        setSgdbApiKey(s.sgdb_api_key_masked);
        setSteamInputMode(s.steam_input_mode);
        setLogLevel(s.log_level);
        setPreferredRegion(s.preferred_region ?? AUTO_REGION);
        setPlatformGroups(!!s.collection_create_platform_groups);
        setNamingMode(s.collection_naming_mode ?? "merge");
        if (s.retroarch_input_check) {
          setRetroarchWarning(s.retroarch_input_check);
        }
      })
      .catch((e) => {
        logError(`Failed to load settings: ${e}`);
        setStatus("Failed to load settings");
      });

    // Distinct regions in the locally synced library — the non-anchor options
    // for the Preferred-region dropdown. Failure degrades to anchors only.
    getKnownRegions()
      .then((regions) => setLibraryRegions(regions))
      .catch(() => {});

    // Load save sync settings and conflicts
    getSaveSyncSettings()
      .then((settings) => {
        setSaveSyncSettings(settings);
        if (settings.save_sync_enabled) {
          ensureDeviceRegistered()
            .then((result) => {
              if (result.success) {
                setDeviceInfo({ device_id: result.device_id, device_name: result.device_name });
              }
            })
            .catch(() => {});
          loadDevices();
        }
      })
      .catch((e) => logError(`Failed to load save sync settings: ${e}`));

    getSaveSortMigrationStatus()
      .then((s) => {
        if (s.pending) {
          setStoreSaveSortStatus(s);
        }
      })
      .catch(() => {});
  }, []);

  function loadDevices() {
    setDevicesLoading(true);
    setDevicesError(null);
    listDevices()
      .then((result) => {
        if (result.success) {
          setRegisteredDevices(result.devices);
        } else if (result.disabled) {
          setRegisteredDevices(null);
        } else {
          setDevicesError(result.message ?? "Failed to load devices");
          setRegisteredDevices([]);
        }
      })
      .catch((e: unknown) => {
        setDevicesError(e instanceof Error ? e.message : "Failed to load devices");
        setRegisteredDevices([]);
      })
      .finally(() => {
        setDevicesLoading(false);
      });
  }

  const handleSaveSyncSettingChange = async (partial: Partial<SaveSyncSettingsType>) => {
    if (!saveSyncSettings) return;
    const updated = { ...saveSyncSettings, ...partial };
    setSaveSyncSettings(updated);
    try {
      await updateSaveSyncSettings(updated);
      if ("save_sync_enabled" in partial) {
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", {
            detail: { type: "save_sync_settings", save_sync_enabled: updated.save_sync_enabled },
          }),
        );
        if (updated.save_sync_enabled) {
          loadDevices();
        } else {
          setRegisteredDevices(null);
          setDevicesError(null);
        }
      }
    } catch (e) {
      logError(`Failed to save settings: ${e}`);
    }
  };

  const handleSyncAll = async () => {
    setSyncing(true);
    setSyncStatus("");
    try {
      const result = await syncAllSaves();
      setSyncStatus(result.message);
      globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "save_sync" } }));
    } catch {
      setSyncStatus("Sync failed");
    }
    setSyncing(false);
  };

  const handleEnableSaveSync = () => {
    showModal(
      <ConfirmModal
        strTitle="Enable Save Sync?"
        strDescription={
          "This will sync your RetroArch game saves between this device and your RomM server. " +
          "Save sync covers the per-game save files RetroArch writes for the systems it supports " +
          "- SRAM, RTC, EEPROM, and other per-system formats, not a single file type. " +
          "Coverage varies by system; see the save sync support matrix in the docs.\n\n" +
          "Before enabling, please back up your local save files. " +
          "They are stored in your RetroArch/RetroDECK saves directory.\n\n" +
          "IMPORTANT: Save sync requires RetroArch's save sorting to be set to " +
          '"Sort Saves into Folders by Content Directory = ON" and ' +
          '"Sort Saves into Folders by Core Name = OFF" (RetroDECK default). ' +
          "If you changed these settings, save sync will not find your save files.\n\n" +
          "Also make sure you are not using this on a shared RomM account " +
          "(e.g. admin, romm, guest) - unless you know what you are doing. " +
          "Save sync is intended for single user accounts.\n\n" +
          "Are you sure you want to proceed?"
        }
        strOKButtonText="I am sure"
        strCancelButtonText="Cancel"
        onOK={() => {
          detach(handleSaveSyncSettingChange({ save_sync_enabled: true }));
        }}
        onCancel={() => {
          setSaveSyncToggleKey((k) => k + 1);
        }}
      />,
    );
  };

  const handleDisableSaveSync = () => {
    detach(handleSaveSyncSettingChange({ save_sync_enabled: false }));
  };

  const handleToggleSaveSync = (value: boolean) => {
    // NOSONAR sits on the if-statement line; prettier-ignore keeps the one-liner intact so the
    // suppression isn't relocated to the closing brace (which would break it).
    // prettier-ignore
    if (value) { handleEnableSaveSync(); } else { handleDisableSaveSync(); } // NOSONAR — enable shows confirmation modal
  };

  const saveSyncEnabled = saveSyncSettings?.save_sync_enabled ?? false;

  // --- Connection handlers wired into ConnectionSection ---
  const handleUrlChange = async (value: string) => {
    const trimmed = trimServerUrl(value);
    setUrl(trimmed);
    try {
      if (!isValidServerUrl(trimmed)) {
        setStatus(INVALID_URL_MESSAGE);
        return;
      }
      await saveServerUrl(trimmed, allowInsecureSsl);
    } catch {
      setStatus("Failed to save settings");
    } finally {
      // The pending value exists to carry an edit across the remount closing
      // the modal can cause, not to outlive the attempt: left behind, a value
      // the backend refused is what every later open of the page shows in the
      // field, over the URL actually saved (#1020).
      delete pendingEdits.url;
    }
  };
  const handleAllowInsecureSslChange = (val: boolean) => {
    setAllowInsecureSsl(val);
    // Auto-save the URL with the new SSL setting
    saveServerUrl(url, val).catch(() => {
      setStatus("Failed to save settings");
    });
  };
  // The connect handlers return their result to the ConnectModal, which owns
  // closing (on success) and error display (on failure). The bottom status line
  // is only touched on success — a post-close confirmation — so a failed sign-in
  // shows its message inside the still-open modal, never at the bottom.
  const handleConnect = async (username: string, password: string): Promise<{ success: boolean; message: string }> => {
    const trimmed = trimServerUrl(url);
    if (!isValidServerUrl(trimmed)) {
      return { success: false, message: INVALID_URL_MESSAGE };
    }
    try {
      const result = await connectWithCredentials(trimmed, username, password, allowInsecureSsl);
      if (result.success) {
        setHasToken(true);
        setStatus(result.message);
      }
      return result;
    } catch {
      return { success: false, message: GENERIC_SIGN_IN_ERROR };
    }
  };
  const handleConnectToken = async (token: string): Promise<{ success: boolean; message: string }> => {
    const trimmed = trimServerUrl(url);
    if (!isValidServerUrl(trimmed)) {
      return { success: false, message: INVALID_URL_MESSAGE };
    }
    try {
      const result = await connectWithToken(trimmed, token, allowInsecureSsl);
      if (result.success) {
        setHasToken(true);
        setStatus(result.message);
      }
      return result;
    } catch {
      return { success: false, message: GENERIC_SIGN_IN_ERROR };
    }
  };
  const handleConnectPairing = async (code: string): Promise<{ success: boolean; message: string }> => {
    const trimmed = trimServerUrl(url);
    if (!isValidServerUrl(trimmed)) {
      return { success: false, message: INVALID_URL_MESSAGE };
    }
    try {
      const result = await connectWithPairingCode(trimmed, code, allowInsecureSsl);
      if (result.success) {
        setHasToken(true);
        setStatus(result.message);
      }
      return result;
    } catch {
      return { success: false, message: GENERIC_SIGN_IN_ERROR };
    }
  };
  const handleSignOut = async () => {
    setStatus("");
    try {
      const result = await signOut();
      setStatus(result.message);
      if (result.success) {
        setHasToken(false);
      }
    } catch {
      setStatus("Sign-out failed");
    }
  };

  // --- SteamGridDB handlers ---
  // SgdbApiKeyModal orchestrates verify-then-save: it tests the entered key
  // (verifySgdbApiKey) and only persists a valid one (handleSaveSgdbKey). The
  // modal always submits a non-empty key, so a successful save means a
  // configured key — reflect it as the masked "••••" display.
  const handleSaveSgdbKey = async (value: string) => {
    await saveSgdbApiKey(value);
    setSgdbApiKey("set");
  };

  // --- Save-sync default-slot handlers ---
  const handleResetDefaultSlot = () => {
    setSaveSyncSettings((prev) => (prev ? { ...prev, default_slot: "default" } : prev));
    detach(handleSaveSyncSettingChange({ default_slot: "default" }));
    showToast('Default save slot reset to "default".');
  };
  const handleDefaultSlotSubmit = (value: string) => {
    const trimmed = value.trim();
    if (trimmed) {
      setSaveSyncSettings((prev) => (prev ? { ...prev, default_slot: trimmed } : prev));
      detach(handleSaveSyncSettingChange({ default_slot: trimmed }));
    } else {
      handleResetDefaultSlot();
    }
  };

  // --- Controller handlers ---
  const handleSteamInputModeChange = (mode: string) => {
    setSteamInputMode(mode);
    detach(saveSteamInputSetting(mode));
    setSteamInputStatus("");
  };
  const handleApplySteamInput = async () => {
    // A second press while the first run is in flight is refused rather than
    // queued: the run walks every shortcut Steam holds and two of them
    // interleave their writes, and the button says so while it is dead (#1020).
    // The guard is here rather than only on the button because a press is
    // delivered on activate, and a disabled control still reports one on the
    // device.
    if (applyingSteamInput) return;
    setApplyingSteamInput(true);
    setSteamInputStatus("Applying...");
    try {
      const result = await applySteamInputSetting();
      setSteamInputStatus(result.message);
    } catch {
      setSteamInputStatus("Failed to apply");
    } finally {
      setApplyingSteamInput(false);
    }
  };
  const handleFixInputDriver = async () => {
    setRetroarchFixStatus("Applying...");
    try {
      const result = await fixRetroarchInputDriver();
      setRetroarchFixStatus(result.message);
      if (result.success) {
        setRetroarchWarning(null);
      }
    } catch {
      setRetroarchFixStatus("Failed to apply fix");
    }
  };

  // --- Advanced handlers ---
  const handleLogLevelChange = (level: string) => {
    setLogLevel(level);
    detach(saveLogLevel(level));
  };

  const handleUpdateCheckEnabledChange = (enabled: boolean) => {
    setUpdateCheckSwitch(enabled).catch((e) => logError(`Failed to change the update check: ${e}`));
  };

  // --- Library handlers ---
  const regionLabel = (value: string) => (value === AUTO_REGION ? DEFAULT_REGION_LABEL : value);

  const handlePreferredRegionChange = (region: string) => {
    if (region === preferredRegion) return;
    // Explain the apply-at-next-sync / no-retroactive-rename semantics before
    // persisting. Confirm saves + updates the dropdown; cancel leaves the state
    // (and therefore the dropdown selection) unchanged.
    detach(
      (async () => {
        const proceed = await showPreferredRegionModal(regionLabel(preferredRegion), regionLabel(region));
        if (proceed) {
          setPreferredRegion(region);
          detach(savePreferredRegion(region));
        }
      })(),
    );
  };

  // --- Collection platform-groups handler ---
  const handlePlatformGroupsChange = (value: boolean) => {
    setPlatformGroups(value);
    detach(
      (async () => {
        try {
          await saveCollectionPlatformGroups(value);
        } catch {
          setPlatformGroups(!value);
        }
      })(),
    );
  };

  // --- Collection naming-mode handler (#1539) ---
  const handleNamingModeChange = (mode: CollectionNamingMode) => {
    const previous = namingMode;
    setNamingMode(mode);
    detach(
      (async () => {
        try {
          await setCollectionNamingMode(mode);
        } catch {
          setNamingMode(previous);
        }
      })(),
    );
  };

  // --- Save sort migration handlers ---
  const handleMigrateSaveSort = async () => {
    setSaveSortMigrating(true);
    setSaveSortResult("");
    try {
      const result = await migrateSaveSortFiles(null);
      setSaveSortResult(result.message);
      if (result.success) {
        clearSaveSortMigration();
        showToast(result.message || "Migration complete.");
      }
    } catch {
      setSaveSortResult("Migration failed");
    }
    setSaveSortMigrating(false);
  };
  const handleDismissSaveSort = async () => {
    try {
      await dismissSaveSortMigration();
      clearSaveSortMigration();
    } catch {
      /* ignore */
    }
  };

  const renderSection = (id: SettingsSection): ReactNode => {
    switch (id) {
      case "connections":
        return (
          <>
            <ConnectionSection
              url={url}
              hasToken={hasToken}
              allowInsecureSsl={allowInsecureSsl}
              status={status}
              onUrlChange={(value) => {
                detach(handleUrlChange(value));
              }}
              onConnect={handleConnect}
              onConnectToken={handleConnectToken}
              onConnectPairing={handleConnectPairing}
              onAllowInsecureSslChange={handleAllowInsecureSslChange}
              onSignOut={() => {
                detach(handleSignOut());
              }}
            />
            <SteamGridDBSection sgdbApiKey={sgdbApiKey} onVerifyKey={verifySgdbApiKey} onSaveKey={handleSaveSgdbKey} />
          </>
        );
      case "save-sync":
        return (
          <>
            {/* First in the pane: it is a condition asking to be answered, and
                the two settings groups below it are true whether it stands or
                not. */}
            {saveSortMigration.pending && (
              <SaveSortMigrationSection
                migration={saveSortMigration}
                migrating={saveSortMigrating}
                result={saveSortResult}
                onMigrate={() => {
                  detach(handleMigrateSaveSort());
                }}
                onDismiss={() => {
                  detach(handleDismissSaveSort());
                }}
              />
            )}
            <SaveSyncSection
              saveSyncSettings={saveSyncSettings}
              saveSyncToggleKey={saveSyncToggleKey}
              deviceInfo={deviceInfo}
              syncing={syncing}
              syncStatus={syncStatus}
              onToggleSaveSync={handleToggleSaveSync}
              onSettingChange={(partial) => {
                detach(handleSaveSyncSettingChange(partial));
              }}
              onDefaultSlotSubmit={handleDefaultSlotSubmit}
              onResetDefaultSlot={handleResetDefaultSlot}
              onSyncAll={() => {
                detach(handleSyncAll());
              }}
            />
            {saveSyncEnabled && (devicesLoading || registeredDevices !== null) && (
              <RegisteredDevicesSection
                devicesLoading={devicesLoading}
                devicesError={devicesError}
                registeredDevices={registeredDevices}
              />
            )}
          </>
        );
      case "controller":
        return (
          <ControllerSection
            steamInputMode={steamInputMode}
            steamInputStatus={steamInputStatus}
            retroarchWarning={retroarchWarning}
            retroarchFixStatus={retroarchFixStatus}
            applying={applyingSteamInput}
            onModeChange={handleSteamInputModeChange}
            onApplyMode={() => {
              detach(handleApplySteamInput());
            }}
            onFixInputDriver={() => {
              detach(handleFixInputDriver());
            }}
          />
        );
      case "steam-library":
        return (
          <LibrarySection
            preferredRegion={preferredRegion}
            libraryRegions={libraryRegions}
            onPreferredRegionChange={handlePreferredRegionChange}
            platformGroups={platformGroups}
            onPlatformGroupsChange={handlePlatformGroupsChange}
            namingMode={namingMode}
            onNamingModeChange={handleNamingModeChange}
          />
        );
      case "advanced":
        return (
          <AdvancedSection
            logLevel={logLevel}
            onLogLevelChange={handleLogLevelChange}
            updateCheckEnabled={updateNotice.enabled}
            onUpdateCheckEnabledChange={handleUpdateCheckEnabledChange}
          />
        );
    }
  };

  const items: ListDetailItem[] = SETTINGS_SECTIONS.map((id) => ({
    id,
    render: (selected: boolean) => (
      // The marker bar and the label, and nothing else: these rows carry no
      // control, which is what `selectOnActivate` below is for — the activate
      // handler it adds to the wrapper is what makes the row a focus stop at
      // all.
      <div
        data-testid={`settings-section-${id}`}
        style={{
          borderLeft: `${ROW_MARKER_WIDTH}px solid ${selected ? SELECTION_ACCENT : "transparent"}`,
          paddingLeft: `${ROW_MARKER_GAP}px`,
        }}
      >
        <Field label={SECTION_LABELS[id]} bottomSeparator="none" />
      </div>
    ),
  }));

  return (
    <WidePage title="Settings" onBack={onBack} ownRegions>
      <ListDetail
        items={items}
        selectedId={selectedSection}
        onSelect={(id) => setSelectedSection(asSection(id))}
        selectOnActivate
        renderDetail={(id) => renderSection(asSection(id))}
      />
    </WidePage>
  );
};
