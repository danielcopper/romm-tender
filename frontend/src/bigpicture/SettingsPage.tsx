/**
 * The Settings page: eight sections on the left, the focused section's controls
 * on the right.
 *
 * Every section's state and every handler lives here rather than in the section
 * components, which are pure renderers — the pane mounts only the focused
 * section, so a section owning its own reads would re-issue them on each move
 * through the list.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Settings.
 */

import { useState, useEffect, useRef, FC, type ReactNode } from "react";
import { ConfirmModal, Field, showModal } from "@decky/ui";
import { showToast } from "../utils/toast";
import {
  getSettings,
  saveServerUrl,
  saveCustomHeaders,
  connectWithCredentials,
  connectWithToken,
  connectWithPairingCode,
  signOut,
  saveSgdbApiKey,
  removeSgdbApiKey,
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
  setQamEntryPosition,
  getKnownRegions,
  ensureDeviceRegistered,
  listDevices,
  logError,
  getEmulatorSources,
  setEmulatorSourceEnabled,
  moveEmulatorSource,
  isEndpointFailure,
  type EmulatorSourceRefusal,
} from "../api/backend";
import type {
  RegisteredDevice,
  CollectionNamingMode,
  QamEntryPosition,
  CustomHeaderEntry,
  SaveSyncSettings as SaveSyncSettingsType,
  SettingsSection,
  EmulatorSourceDirection,
  EmulatorSourcesListing,
} from "../types";
import { SETTINGS_SECTIONS } from "../utils/settingsSections";
import { detach } from "../utils/detach";
import { setQamEntryPositionState } from "../utils/qamEntryPositionStore";
import {
  runUpdateCheckNow,
  setUpdateCheckSwitch,
  useUpdateNoticeState,
  type UpdateCheckOutcome,
} from "../utils/updateNoticeStore";
import { useUpdateOutcomeState } from "../utils/updateOutcomeStore";
import { UPDATE_CHECK_FAILED, UPDATE_CHECK_UNREACHABLE } from "../utils/updateAvailableView";
import { useSeenAfterDwell } from "../utils/updateDot";
import { trimServerUrl, isValidServerUrl } from "../utils/serverUrl";
import { WidePage } from "./layout/WidePage";
import { ListDetail, type ListDetailItem } from "./layout/ListDetail";
import { ROW_MARKER_GAP, ROW_MARKER_WIDTH, SELECTION_ACCENT } from "./layout/pane";
import { pendingEdits } from "./settings/TextInputModal";
import { ConnectionSection } from "./settings/ConnectionSection";
import { SteamGridDBSection } from "./settings/SteamGridDBSection";
import { SgdbKeySaveRefused } from "./settings/SgdbApiKeyModal";
import { SaveSyncSection } from "./settings/SaveSyncSection";
import { RegisteredDevicesSection } from "./settings/RegisteredDevicesSection";
import { ControllerSection } from "./settings/ControllerSection";
import { AdvancedSection } from "./settings/AdvancedSection";
import { InterfaceSection } from "./settings/InterfaceSection";
import { UpdatesSection } from "./settings/UpdatesSection";
import { EmulatorSourcesSection } from "./settings/EmulatorSourcesSection";
import { TenderFolderSection } from "./settings/TenderFolderSection";
import { useTenderFolder } from "./useTenderFolder";
import { TENDER_FOLDER_SECTION } from "../utils/tenderFolderWording";
import { WithUpdateDot } from "./UpdateDot";
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
// inline) when the sign-in can't even be attempted or the endpoint call throws. The
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
  "emulator-sources": "Emulator sources",
  "tender-folder": TENDER_FOLDER_SECTION,
  updates: "Updates",
  interface: "Interface",
  advanced: "Advanced",
};

// What a Check now found, as the line under the button says it — only where
// the Available row above does not say it already, so a release found, or none
// newer, is no line. A check that a later switch press or a notice the backend
// pushed overtook reports nothing: its answer was never written, and the store
// holds the newer one.
const CHECK_OUTCOME_LINES: Record<Exclude<UpdateCheckOutcome, "superseded">, string> = {
  found: "",
  none: "",
  unreachable: UPDATE_CHECK_UNREACHABLE,
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
  // The URL and SSL flag the backend holds, which the field and the switch go
  // back to when it refuses a save; null until the settings have loaded.
  const storedUrl = useRef<string | null>(null);
  const storedSsl = useRef<boolean | null>(null);
  const [hasToken, setHasToken] = useState(false);
  const [status, setStatus] = useState("");
  // What the URL save last put on the status line, the one message an accepted
  // save may clear: the line also carries what loading and signing in said.
  const saveStatus = useRef<string | null>(null);
  const [allowInsecureSsl, setAllowInsecureSsl] = useState(false);
  // Names only — a configured header's value is a proxy credential the backend
  // never sends back (#1822).
  const [customHeaderNames, setCustomHeaderNames] = useState<string[]>([]);

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

  // Advanced state
  const [logLevel, setLogLevel] = useState("warn");

  // Updates state — the answer itself lives in the module store, which panel
  // load fills and Main's notice reads too.
  const update = useUpdateNoticeState();
  const updateOutcome = useUpdateOutcomeState();
  const [checkingForUpdate, setCheckingForUpdate] = useState(false);
  const [updateCheckResult, setUpdateCheckResult] = useState("");
  // However Updates came to be on screen — from the list, or opened on it by
  // the card's Open Updates — a second of it counts as seeing the release.
  useSeenAfterDwell(selectedSection === "updates");

  // Library state (preferred sibling-group region, ADR-0021)
  const [preferredRegion, setPreferredRegion] = useState(AUTO_REGION);
  const [libraryRegions, setLibraryRegions] = useState<string[]>([]);
  // Collection platform-groups toggle (relocated from the Collections tab, #1539).
  const [platformGroups, setPlatformGroups] = useState(false);
  // Steam-collection naming mode (#1539): "merge" (default) or "by_label".
  const [namingMode, setNamingMode] = useState<CollectionNamingMode>("merge");

  // Interface state
  const [qamPosition, setQamPosition] = useState<QamEntryPosition>("above_decky");

  // Emulator sources: `undefined` until the listing answers, `null` where it failed.
  const [emulatorSources, setEmulatorSources] = useState<EmulatorSourcesListing | null | undefined>(undefined);
  const [sourcesBusy, setSourcesBusy] = useState(false);
  const tenderFolder = useTenderFolder();

  useEffect(() => {
    getSettings()
      .then((s) => {
        // Apply any pending edits that survived a remount, fall back to backend values
        storedUrl.current = s.romm_url;
        storedSsl.current = s.romm_allow_insecure_ssl;
        setUrl(pendingEdits.url ?? s.romm_url);
        setHasToken(s.has_token);
        setAllowInsecureSsl(s.romm_allow_insecure_ssl);
        setCustomHeaderNames(s.romm_custom_header_names ?? []);
        setSgdbApiKey(s.sgdb_api_key_masked);
        setSteamInputMode(s.steam_input_mode);
        setLogLevel(s.log_level);
        setPreferredRegion(s.preferred_region ?? AUTO_REGION);
        setPlatformGroups(!!s.collection_create_platform_groups);
        setNamingMode(s.collection_naming_mode ?? "merge");
        setQamPosition(s.qam_entry_position ?? "above_decky");
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
          setDevicesError(result.message);
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
          "Tender keeps a game's saves in step with your RomM server: before a game starts it fetches a newer " +
          "save from the server, after you quit it uploads yours, and if both sides changed you choose which one " +
          "wins. Which files make up a game's save is asked of the emulator that launches it, game by game; a game " +
          "whose save it cannot tell apart from others, such as a shared memory card or a save inside the game " +
          "file, is left alone.\n\n" +
          "Before a save on this device is overwritten, Tender keeps a copy of the old file.\n\n" +
          "Use this with your own RomM account, not a shared one such as admin or guest: saves on the server " +
          "belong to the user who uploaded them."
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
  const showSaveStatus = (message: string) => {
    saveStatus.current = message;
    setStatus(message);
  };
  const clearSaveStatus = () => {
    const shown = saveStatus.current;
    saveStatus.current = null;
    if (shown !== null) setStatus((current) => (current === shown ? "" : current));
  };
  const handleUrlChange = async (value: string) => {
    const trimmed = trimServerUrl(value);
    setUrl(trimmed);
    try {
      if (!isValidServerUrl(trimmed)) {
        showSaveStatus(INVALID_URL_MESSAGE);
        return;
      }
      const sentSsl = allowInsecureSsl;
      const result = await saveServerUrl(trimmed, sentSsl);
      if (result.success) {
        storedUrl.current = trimmed;
        storedSsl.current = sentSsl;
        setAllowInsecureSsl(sentSsl);
        clearSaveStatus();
      } else {
        if (storedUrl.current !== null) setUrl(storedUrl.current);
        showSaveStatus(result.message);
      }
    } catch {
      showSaveStatus("Failed to save settings");
    } finally {
      // The pending value exists to carry an edit across the remount closing
      // the modal can cause, not to outlive the attempt: left behind, a value
      // the backend refused is what every later open of the page shows in the
      // field, over the URL actually saved (#1020).
      delete pendingEdits.url;
    }
  };
  // The modal owns closing (on success) and error display (on failure), so the
  // verdict — and a rejection — is handed straight back to it rather than caught
  // here. Only the row's own count is updated, from the list that was accepted.
  //
  // Trimmed, because the backend stores a name stripped of its surrounding
  // whitespace: taking the sent spelling would leave the row showing "  X-Token  "
  // until the next settings read, over a header stored as "X-Token".
  const handleSaveCustomHeaders = async (headers: CustomHeaderEntry[]) => {
    const result = await saveCustomHeaders(headers);
    if (result.success) {
      setCustomHeaderNames(headers.map((h) => h.name.trim()));
    }
    return result;
  };
  const handleAllowInsecureSslChange = (val: boolean) => {
    setAllowInsecureSsl(val);
    // Auto-save the URL with the new SSL setting
    saveServerUrl(url, val)
      .then((result) => {
        if (result.success) {
          storedSsl.current = val;
          clearSaveStatus();
          return;
        }
        setAllowInsecureSsl(storedSsl.current ?? !val);
        showSaveStatus(result.message);
      })
      .catch(() => {
        showSaveStatus("Failed to save settings");
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
    const result = await saveSgdbApiKey(value);
    if (!result.success) throw new SgdbKeySaveRefused(result.message);
    setSgdbApiKey("set");
  };
  const handleRemoveSgdbKey = async () => {
    try {
      const result = await removeSgdbApiKey();
      if (result.success) setSgdbApiKey("");
      else showToast(result.message);
    } catch (e) {
      logError(`Failed to remove the SteamGridDB API key: ${e}`);
      showToast("The SteamGridDB API key could not be removed");
    }
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

  // --- Advanced handlers ---
  const handleLogLevelChange = (level: string) => {
    setLogLevel(level);
    detach(saveLogLevel(level));
  };

  // --- Updates handlers ---
  const handleCheckForUpdateNow = async () => {
    // Refused rather than queued, and in the handler rather than only on the
    // button: a disabled control still reports a press on the device.
    if (checkingForUpdate) return;
    setCheckingForUpdate(true);
    setUpdateCheckResult("");
    try {
      const outcome = await runUpdateCheckNow();
      if (outcome !== "superseded") {
        setUpdateCheckResult(CHECK_OUTCOME_LINES[outcome]);
      }
    } catch (e) {
      logError(`Failed to check for updates: ${e}`);
      setUpdateCheckResult(UPDATE_CHECK_FAILED);
    } finally {
      setCheckingForUpdate(false);
    }
  };
  const handleUpdateCheckEnabledChange = (enabled: boolean) => {
    setUpdateCheckResult("");
    setUpdateCheckSwitch(enabled).catch((e) => logError(`Failed to save the update check switch: ${e}`));
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

  // --- Interface handler ---
  // The store the Quick Access render handler reads is written only once the
  // backend has taken the value, so a change the backend refused or never
  // received never reaches it.
  const handleQamEntryPositionChange = (position: QamEntryPosition) => {
    const previous = qamPosition;
    setQamPosition(position);
    detach(
      (async () => {
        try {
          const result = await setQamEntryPosition(position);
          if (result.success) setQamEntryPositionState(position);
          else setQamPosition(previous);
        } catch {
          setQamPosition(previous);
        }
      })(),
    );
  };

  /** The listing, or `null` where it could not be read — the one read both the
   *  page's mount and a refused change go through. */
  const fetchEmulatorSources = (): Promise<EmulatorSourcesListing | null> =>
    getEmulatorSources().catch((e: unknown) => {
      logError(`Failed to read the emulator sources: ${e}`);
      return null;
    });

  /** A switch or a move, whose answer is the listing as it now stands. An
   *  accepted one can change which source answers for a game, so every open game
   *  page is told to read its answers again. A refusal means the sources changed
   *  under the page (a source went away), so the page reads them again rather
   *  than keep a row that no longer exists. */
  const writeEmulatorSources = async (write: () => Promise<EmulatorSourcesListing | EmulatorSourceRefusal>) => {
    if (sourcesBusy) return;
    setSourcesBusy(true);
    try {
      const answer = await write();
      if (isEndpointFailure(answer)) {
        logError(`An emulator source change was refused: ${answer.reason}`);
        setEmulatorSources(await fetchEmulatorSources());
      } else {
        setEmulatorSources(answer);
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "emulator_sources" } }));
      }
    } catch (e) {
      logError(`Failed to change the emulator sources: ${e}`);
      setEmulatorSources(await fetchEmulatorSources());
    } finally {
      setSourcesBusy(false);
    }
  };

  useEffect(() => {
    detach(fetchEmulatorSources().then(setEmulatorSources));
  }, []);

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
              customHeaderNames={customHeaderNames}
              onUrlChange={(value) => {
                detach(handleUrlChange(value));
              }}
              onSaveCustomHeaders={handleSaveCustomHeaders}
              onConnect={handleConnect}
              onConnectToken={handleConnectToken}
              onConnectPairing={handleConnectPairing}
              onAllowInsecureSslChange={handleAllowInsecureSslChange}
              onSignOut={() => {
                detach(handleSignOut());
              }}
            />
            <SteamGridDBSection
              sgdbApiKey={sgdbApiKey}
              onVerifyKey={verifySgdbApiKey}
              onSaveKey={handleSaveSgdbKey}
              onRemoveKey={handleRemoveSgdbKey}
            />
          </>
        );
      case "save-sync":
        return (
          <>
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
            applying={applyingSteamInput}
            onModeChange={handleSteamInputModeChange}
            onApplyMode={() => {
              detach(handleApplySteamInput());
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
      case "emulator-sources":
        return (
          <EmulatorSourcesSection
            listing={emulatorSources}
            busy={sourcesBusy}
            onSwitch={(kind, enabled) => {
              detach(writeEmulatorSources(() => setEmulatorSourceEnabled(kind, enabled)));
            }}
            onMove={(kind, direction: EmulatorSourceDirection) => {
              detach(writeEmulatorSources(() => moveEmulatorSource(kind, direction)));
            }}
          />
        );
      case "tender-folder":
        return <TenderFolderSection folder={tenderFolder} />;
      case "updates":
        return (
          <UpdatesSection
            update={update}
            outcome={updateOutcome}
            checking={checkingForUpdate}
            result={updateCheckResult}
            onEnabledChange={handleUpdateCheckEnabledChange}
            onCheckNow={() => {
              detach(handleCheckForUpdateNow());
            }}
          />
        );
      case "interface":
        return (
          <InterfaceSection qamEntryPosition={qamPosition} onQamEntryPositionChange={handleQamEntryPositionChange} />
        );
      case "advanced":
        return <AdvancedSection logLevel={logLevel} onLogLevelChange={handleLogLevelChange} />;
    }
  };

  const items: ListDetailItem[] = SETTINGS_SECTIONS.map((id) => ({
    id,
    render: (selected: boolean) => (
      // The marker bar and the label — Updates' with the update dot beside it —
      // and nothing else: these rows carry no control, which is what
      // `selectOnActivate` below is for — the activate handler it adds to the
      // wrapper is what makes the row a focus stop at all.
      <div
        data-testid={`settings-section-${id}`}
        style={{
          borderLeft: `${ROW_MARKER_WIDTH}px solid ${selected ? SELECTION_ACCENT : "transparent"}`,
          paddingLeft: `${ROW_MARKER_GAP}px`,
        }}
      >
        <Field
          label={id === "updates" ? <WithUpdateDot>{SECTION_LABELS[id]}</WithUpdateDot> : SECTION_LABELS[id]}
          bottomSeparator="none"
        />
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
