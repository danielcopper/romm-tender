/**
 * The Emulation card of the desktop game view: the per-game emulator / core
 * picker above the game's BIOS / firmware status, file table, and actions.
 */

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties, type FC, type ReactNode } from "react";
import { getBiosStatus, logWarn, type BiosAnswer } from "../../api/backend";
import { getBiosStatusShared } from "../../api/sharedReads";
import type { AnsweringSource, BiosLevel, BiosStatus, EmulatorOption, EmulatorTab } from "../../types";
import { BIOS_MISSING_RED, BIOS_MUTED_GREY, BIOS_OK_GREEN, biosColorForLevel } from "../../utils/biosColor";
import { downloadName, isFetchable } from "../../utils/biosFetchable";
import {
  biosDiskMark,
  biosFileDescription,
  biosLegendEntries,
  biosLibraryMark,
  biosTableRowLines,
  contentsWords,
  declaredFolder,
  type BiosTableRow,
} from "../../utils/biosFileNote";
import { needWords, type GroupLineTone } from "../../utils/biosGroup";
import { biosHeldRatio } from "../../utils/biosHeldRatio";
import {
  DOWNLOAD_DAMAGED,
  IDENTIFYING,
  biosPlaceLines,
  libraryRowLines,
  storeOnlySentence,
} from "../../utils/biosPlaceWords";
import { biosAnswerStale, biosFieldsFromCache, biosReadState } from "../../utils/biosReadState";
import { biosSummary, groupBlocks } from "../../utils/biosSummary";
import { isUnclaimedLibraryRow, selectedTab, tabLabel, tabRows } from "../../utils/biosTabs";
import { announceCoreChange, coreChangeIsFor } from "../../utils/coreChangedEvent";
import { executeGameCoreChange } from "../../utils/coreOverride";
import { detach } from "../../utils/detach";
import { entryForLabel } from "../../utils/emulatorEntries";
import { activeEmulatorNote } from "../../utils/emulatorInstallation";
import { askEntryLaunches, type EntryLaunches } from "../../utils/emulatorLaunches";
import {
  CORE_SWITCH_COMPAT_NOTE,
  emulatorEntryNotes,
  entryLaunchVerdictLine,
  isEmulatorEntryActive,
} from "../../utils/emulatorMenu";
import { emulatorDataReasonSentence } from "../../utils/emulatorSourceWording";
import { frontendChoiceSentence, isChoosable, vanishedPickSentence } from "../../utils/emulatorStart";
import { useIdentifications } from "../../utils/firmwareIdentifications";
import { refreshBiosStatus, refreshCoreAndBios, type GameDetailState } from "../../utils/gameDetailStore";
import { usePruneLeaseOwner } from "../../utils/pruneLease";
import { useStrandedAnswer } from "../../utils/strandedPanelStore";
import {
  announceBiosChange,
  biosBulkOffer,
  biosDownloadButtonState,
  biosRowAction,
  deleteBiosFileConfirmCopy,
  deletePlatformBiosConfirmCopy,
  replaceBiosConfirmCopy,
  useBiosActions,
  type BiosActions,
  type BiosConfirmCopy,
  type BiosStatusLine,
  type PlatformKey,
} from "../../utils/useBiosActions";
import { desktopBiosConfirmDialog } from "./dialogs/desktopDialogs";
import { useDialogHost } from "./dialogs/useDialogHost";
import { SvgThrobber } from "./SvgThrobber";
import { BUTTON_STYLE, CARD_STYLE } from "./styles";

interface EmulationSettingsProps {
  appId: number;
  detail: GameDetailState;
}

type ConfirmBiosFn = (copy: BiosConfirmCopy, tone: "primary" | "danger") => Promise<boolean>;

interface RomBiosState {
  romId: number;
  biosStatus: BiosStatus | null;
  biosLevel: BiosLevel | null;
  readFailed: boolean;
}

const ACCENT_BLUE = "#4fa3ff";
const MUTED_COLOR = "#8f98a0";
const BODY_COLOR = "#dfe3e6";

const HEADING_STYLE: CSSProperties = {
  margin: "0 0 16px 0",
  fontSize: "16px",
  fontWeight: 700,
  letterSpacing: "0.04em",
  textTransform: "uppercase",
  color: "#ffffff",
};

const SECTION_DIVIDER_STYLE: CSSProperties = {
  borderTop: "1px solid rgba(255, 255, 255, 0.08)",
  paddingTop: "16px",
  marginTop: "16px",
};

const SUBHEADING_ROW_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "8px",
  marginBottom: "8px",
};

const SUBHEADING_STYLE: CSSProperties = {
  fontSize: "11px",
  fontWeight: 700,
  letterSpacing: "0.06em",
  textTransform: "uppercase",
  color: MUTED_COLOR,
};

const MUTED_TEXT_STYLE: CSSProperties = {
  fontSize: "12px",
  lineHeight: 1.45,
  color: MUTED_COLOR,
};

const ERROR_TEXT_STYLE: CSSProperties = {
  fontSize: "12px",
  lineHeight: 1.45,
  color: BIOS_MISSING_RED,
  marginTop: "6px",
};

const INLINE_ROW_STYLE: CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "8px",
  fontSize: "13px",
  color: BODY_COLOR,
};

const DOT_STYLE: CSSProperties = {
  display: "inline-block",
  width: "8px",
  height: "8px",
  borderRadius: "50%",
  flexShrink: 0,
};

const INLINE_SPINNER_STYLE: CSSProperties = {
  width: "12px",
  height: "12px",
};

const PICKER_LIST_STYLE: CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "4px",
  marginTop: "8px",
};

const PICKER_ROW_BASE: CSSProperties = {
  ...BUTTON_STYLE,
  width: "100%",
  textAlign: "left",
  padding: "7px 10px",
  display: "flex",
  flexDirection: "column",
  gap: "3px",
};

const ACTION_ROW_STYLE: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: "8px",
  marginTop: "8px",
};

const TABLE_COLUMNS = "minmax(0, 1fr) 56px 96px auto";

const TABLE_HEADER_STYLE: CSSProperties = {
  display: "grid",
  gridTemplateColumns: TABLE_COLUMNS,
  gap: "8px",
  padding: "6px 8px",
  fontSize: "11px",
  fontWeight: 600,
  color: MUTED_COLOR,
  borderBottom: "1px solid rgba(255, 255, 255, 0.1)",
};

const TABLE_ROW_STYLE: CSSProperties = {
  padding: "6px 8px",
  borderBottom: "1px solid rgba(255, 255, 255, 0.05)",
  fontSize: "12px",
  color: BODY_COLOR,
};

const TABLE_CELLS_STYLE: CSSProperties = {
  display: "grid",
  gridTemplateColumns: TABLE_COLUMNS,
  gap: "8px",
  alignItems: "center",
};

const GROUP_DOT_COLOR: Readonly<Record<GroupLineTone, string>> = {
  here: BIOS_OK_GREEN,
  missing: BIOS_MISSING_RED,
  other: BIOS_MUTED_GREY,
  unchecked: BIOS_MUTED_GREY,
};

function pickerButtonStyle(active: boolean, disabled: boolean): CSSProperties {
  return {
    ...PICKER_ROW_BASE,
    borderColor: active ? "rgba(79, 163, 255, 0.55)" : "rgba(255, 255, 255, 0.12)",
    backgroundColor: active ? "rgba(79, 163, 255, 0.14)" : "rgba(255, 255, 255, 0.05)",
    color: active ? ACCENT_BLUE : BODY_COLOR,
    opacity: disabled ? 0.5 : 1,
    cursor: disabled ? "not-allowed" : "pointer",
  };
}

function actionButtonStyle(disabled: boolean, active = false): CSSProperties {
  return {
    ...BUTTON_STYLE,
    borderColor: active ? "rgba(79, 163, 255, 0.55)" : "rgba(255, 255, 255, 0.15)",
    backgroundColor: active ? "rgba(79, 163, 255, 0.14)" : "rgba(255, 255, 255, 0.08)",
    opacity: disabled ? 0.5 : 1,
    cursor: disabled ? "not-allowed" : "pointer",
    display: "inline-flex",
    alignItems: "center",
    gap: "6px",
  };
}

function foldBiosAnswer(prev: RomBiosState | null, targetRomId: number, answer: BiosAnswer | null): RomBiosState {
  const fields = answer === null ? null : biosFieldsFromCache(answer);
  const base =
    prev !== null && prev.romId === targetRomId
      ? prev
      : { romId: targetRomId, biosStatus: null, biosLevel: null, readFailed: false };
  if (fields === null) return { ...base, readFailed: true };
  return {
    romId: targetRomId,
    biosStatus: fields.biosStatus,
    biosLevel: fields.biosLevel,
    readFailed: false,
  };
}

type DataChange = WindowEventMap["romm_data_changed"]["detail"];

function shouldRereadBiosOnEvent(
  evt: DataChange,
  slug: string,
  folder: string | undefined,
  ownBios: boolean,
  ownCore: boolean,
): boolean {
  if (evt.type === "bios") return Boolean(evt.platform_slug) && evt.platform_slug === slug && !ownBios;
  if (evt.type === "core_changed") return coreChangeIsFor(evt, folder) && !ownCore;
  return evt.type === "emulator_sources";
}

function useDesktopBiosReadState(
  appId: number,
  romId: number | null,
  platformSlug: string,
  platformFolder: string | undefined,
  isStranded: boolean,
) {
  const [biosState, setBiosState] = useState<RomBiosState | null>(null);
  const mountedRef = useRef(true);
  const romIdRef = useRef(romId);
  const slugRef = useRef(platformSlug);
  const folderRef = useRef(platformFolder);
  const readSeqRef = useRef(0);
  const openedAppRef = useRef<number | null>(null);
  const ownBiosDispatch = useRef(false);
  const ownCoreDispatch = useRef(false);

  useEffect(() => {
    romIdRef.current = romId;
    slugRef.current = platformSlug;
    folderRef.current = platformFolder;
  });

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const runBiosRead = useCallback(async (targetRomId: number, reader: (id: number) => Promise<BiosAnswer>) => {
    const ticket = ++readSeqRef.current;
    let answer: BiosAnswer | null = null;
    try {
      answer = await reader(targetRomId);
    } catch (e) {
      logWarn(`EmulationSettings: BIOS read for romId=${targetRomId} failed: ${e}`);
    }
    if (!mountedRef.current || romIdRef.current !== targetRomId || readSeqRef.current !== ticket) return;
    setBiosState((prev) => foldBiosAnswer(prev, targetRomId, answer));
  }, []);

  const rereadBios = useCallback((targetRomId: number) => runBiosRead(targetRomId, getBiosStatus), [runBiosRead]);

  useEffect(() => {
    if (isStranded || romId === null) return;
    const isInitialOpen = openedAppRef.current !== appId;
    openedAppRef.current = appId;
    detach(runBiosRead(romId, isInitialOpen ? getBiosStatusShared : getBiosStatus));
  }, [appId, romId, platformFolder, isStranded, runBiosRead]);

  useEffect(() => {
    if (isStranded) return;
    const onDataChanged = (e: WindowEventMap["romm_data_changed"]) => {
      const currentRomId = romIdRef.current;
      if (currentRomId === null) return;
      const shouldReread = shouldRereadBiosOnEvent(
        e.detail,
        slugRef.current,
        folderRef.current,
        ownBiosDispatch.current,
        ownCoreDispatch.current,
      );
      if (shouldReread) detach(rereadBios(currentRomId));
    };
    globalThis.addEventListener("romm_data_changed", onDataChanged);
    return () => globalThis.removeEventListener("romm_data_changed", onDataChanged);
  }, [isStranded, rereadBios]);

  const announceOwnBios = useCallback((slug: string, folder: string) => {
    ownBiosDispatch.current = true;
    try {
      announceBiosChange(slug, folder);
    } finally {
      ownBiosDispatch.current = false;
    }
  }, []);

  const announceOwnCore = useCallback((folder: string | undefined) => {
    ownCoreDispatch.current = true;
    try {
      announceCoreChange(folder);
    } finally {
      ownCoreDispatch.current = false;
    }
  }, []);

  const current =
    romId !== null && biosState !== null && biosState.romId === romId
      ? biosState
      : { biosStatus: null, biosLevel: null, readFailed: false };

  return { ...current, rereadBios, announceOwnBios, announceOwnCore };
}

function useSettledEntryLaunches(romId: number | null, enabled: boolean): EntryLaunches | null {
  const [settled, setSettled] = useState<{ romId: number; launches: EntryLaunches } | null>(null);

  useEffect(() => {
    if (!enabled || romId === null) return;
    let active = true;
    const launches = askEntryLaunches(romId, () => active);
    const unsubscribe = launches.subscribe(() => {
      if (active) setSettled({ romId, launches });
    });
    return () => {
      active = false;
      unsubscribe();
    };
  }, [romId, enabled]);

  return enabled && romId !== null && settled !== null && settled.romId === romId ? settled.launches : null;
}

function desktopEmulatorEntryLabel(
  entry: EmulatorOption,
  isActive: boolean,
  isPinned: boolean,
  isPlatformCore: boolean,
): string {
  const defaultMark = entry.is_default ? " (default)" : "";
  const pinnedMark = isPinned ? " (pinned)" : "";
  const frontendMark = entry.chosen_in_frontend ? " (chosen in ES-DE)" : "";
  const systemMark = isPlatformCore ? " (system)" : "";
  const activeMark = isActive ? " ✓" : "";
  return `${entry.label}${defaultMark}${pinnedMark}${frontendMark}${systemMark}${activeMark}${emulatorEntryNotes(entry)}`;
}

const DesktopEmulatorEntryButton: FC<{
  entry: EmulatorOption;
  isActive: boolean;
  isPinned: boolean;
  isPlatformCore: boolean;
  coreBusy: boolean;
  launchesAsked: boolean;
  settledLaunches: EntryLaunches | null;
  source: AnsweringSource | null;
  onToggle: () => void;
}> = ({ entry, isActive, isPinned, isPlatformCore, coreBusy, launchesAsked, settledLaunches, source, onToggle }) => {
  const choosable = isChoosable(entry);
  const disabled = !choosable || coreBusy;
  const labelText = desktopEmulatorEntryLabel(entry, isActive, isPinned, isPlatformCore);
  let launchRow: ReactNode = null;
  if (launchesAsked) {
    if (settledLaunches === null) {
      launchRow = (
        <div>
          <SvgThrobber style={INLINE_SPINNER_STYLE} />
        </div>
      );
    } else {
      const launch = settledLaunches.forLabel(entry.label);
      const line = launch ? entryLaunchVerdictLine(launch, source) : null;
      if (line !== null) launchRow = <div style={MUTED_TEXT_STYLE}>{line}</div>;
    }
  }
  return (
    <button
      type="button"
      disabled={disabled}
      aria-pressed={isPinned}
      style={pickerButtonStyle(isActive, disabled)}
      onClick={onToggle}
    >
      <span>{labelText}</span>
      {launchRow}
    </button>
  );
};

function emulatorExtraLines(detail: GameDetailState): string[] {
  return [
    activeEmulatorNote(detail.emulators, detail.activeCoreLabel),
    detail.vanishedPick === null ? null : vanishedPickSentence(detail.vanishedPick),
    frontendChoiceSentence(detail.emulators, detail.activeCoreLabel),
  ].filter((line): line is string => line !== null);
}

async function performDesktopCorePick(params: {
  appId: number;
  romId: number;
  platformFolder: string | undefined;
  coreLabel: string | null;
  leaseOwner: string;
  rereadBios: (romId: number) => Promise<void>;
  announceOwnCore: (folder: string | undefined) => void;
}): Promise<string | null> {
  const { appId, romId, platformFolder, coreLabel, leaseOwner, rereadBios, announceOwnCore } = params;
  const outcome = await executeGameCoreChange({
    romId,
    coreLabel,
    ...(coreLabel === null ? {} : { coreName: coreLabel }),
    leaseOwner,
    logTag: coreLabel === null ? "desktopResetGameCore" : "desktopChangeGameCore",
    onSuccess: async () => {
      await Promise.all([refreshCoreAndBios(appId), rereadBios(romId)]);
      announceOwnCore(platformFolder);
    },
  });
  return outcome.status === "failed" ? outcome.message : null;
}

const DesktopEmulatorSection: FC<{
  appId: number;
  detail: GameDetailState;
  isStranded: boolean;
  leaseOwner: string;
  rereadBios: (romId: number) => Promise<void>;
  announceOwnCore: (folder: string | undefined) => void;
}> = ({ appId, detail, isStranded, leaseOwner, rereadBios, announceOwnCore }) => {
  const [busyRomId, setBusyRomId] = useState<number | null>(null);
  const [failure, setFailure] = useState<{ romId: number; message: string } | null>(null);
  const romId = detail.romId;
  const coreBusy = romId !== null && busyRomId === romId;
  const failureMessage = romId !== null && failure !== null && failure.romId === romId ? failure.message : null;
  const launchesAsked = !isStranded && detail.installed && detail.emulatorDataAvailable && detail.emulators.length > 0;
  const settledLaunches = useSettledEntryLaunches(romId, launchesAsked);

  const runCorePick = async (coreLabel: string | null) => {
    if (romId === null || !detail.platformSlug || coreBusy) return;
    const targetRomId = romId;
    setBusyRomId(targetRomId);
    setFailure(null);
    try {
      const errorMessage = await performDesktopCorePick({
        appId,
        romId: targetRomId,
        platformFolder: detail.platformFolder,
        coreLabel,
        leaseOwner,
        rereadBios,
        announceOwnCore,
      });
      if (errorMessage !== null) setFailure({ romId: targetRomId, message: errorMessage });
    } finally {
      setBusyRomId((prev) => (prev === targetRomId ? null : prev));
    }
  };

  const platformCore = entryForLabel(detail.emulators, detail.platformCoreLabel);
  const extraLines = emulatorExtraLines(detail);

  return (
    <div>
      <div style={SUBHEADING_ROW_STYLE}>
        <span style={SUBHEADING_STYLE}>EMULATOR / CORE</span>
        {coreBusy && <SvgThrobber style={INLINE_SPINNER_STYLE} />}
      </div>
      {detail.emulatorDataAvailable ? (
        <>
          <div style={MUTED_TEXT_STYLE}>{CORE_SWITCH_COMPAT_NOTE}</div>
          <div style={PICKER_LIST_STYLE}>
            {detail.emulators.map((entry) => {
              const isPinned = detail.hasGameOverride && detail.activeCoreLabel === entry.label;
              return (
                <DesktopEmulatorEntryButton
                  key={entry.label}
                  entry={entry}
                  isActive={isEmulatorEntryActive(detail.emulators, detail.activeCoreLabel, entry)}
                  isPinned={isPinned}
                  isPlatformCore={platformCore === entry}
                  coreBusy={coreBusy}
                  launchesAsked={launchesAsked}
                  settledLaunches={settledLaunches}
                  source={detail.emulatorSource}
                  onToggle={() => detach(runCorePick(isPinned ? null : entry.label))}
                />
              );
            })}
          </div>
        </>
      ) : (
        <div data-testid="desktop-emulator-unavailable" style={MUTED_TEXT_STYLE}>
          {emulatorDataReasonSentence(detail.emulatorDataReason, detail.emulatorSource, detail.emulatorPlatformSystem)}
        </div>
      )}
      {extraLines.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "4px", marginTop: "8px" }}>
          {extraLines.map((line) => (
            <div key={line} style={MUTED_TEXT_STYLE}>
              {line}
            </div>
          ))}
        </div>
      )}
      {failureMessage !== null && (
        <div data-testid="desktop-core-error" style={ERROR_TEXT_STYLE}>
          {failureMessage}
        </div>
      )}
    </div>
  );
};

function renderDownloadFace(
  busyFolder: string | null,
  platformFolder: string,
  actions: BiosActions,
  id: string,
  label: string,
  disabled: boolean,
): ReactNode {
  const showing = biosDownloadButtonState(busyFolder, platformFolder, actions, id);
  if (showing === "spinner") return <SvgThrobber style={INLINE_SPINNER_STYLE} />;
  if (showing === "failed") {
    return disabled ? "Failed" : <span style={{ color: BIOS_MISSING_RED }}>Failed</span>;
  }
  return label;
}

function redText(text: string, disabled: boolean): ReactNode {
  return disabled ? text : <span style={{ color: BIOS_MISSING_RED }}>{text}</span>;
}

const DesktopBiosRowActionButton: FC<{
  file: BiosTableRow;
  rowFetchable: boolean;
  serverOffline: boolean;
  platform: PlatformKey;
  backupRoot: string | undefined;
  busyFolder: string | null;
  pressedKey: string | null;
  buttonKey: string;
  onPressKey: (key: string) => void;
  actions: BiosActions;
  confirmBios: ConfirmBiosFn;
}> = ({
  file,
  rowFetchable,
  serverOffline,
  platform,
  backupRoot,
  busyFolder,
  pressedKey,
  buttonKey,
  onPressKey,
  actions,
  confirmBios,
}) => {
  const offer = biosRowAction(file, rowFetchable, serverOffline);
  if (offer === null) return null;
  const busy = busyFolder !== null;
  const disabled = busy && pressedKey !== buttonKey;

  if (offer.kind === "download") {
    return (
      <button
        type="button"
        disabled={disabled}
        style={actionButtonStyle(disabled)}
        onClick={() => {
          if (busy) return;
          onPressKey(buttonKey);
          actions.downloadOne(platform, offer.name, offer.firmwareId);
        }}
      >
        {renderDownloadFace(busyFolder, platform.folder, actions, offer.name, "Download", disabled)}
      </button>
    );
  }

  if (offer.kind === "replace") {
    const askReplace = async () => {
      if (busy) return;
      const copy = replaceBiosConfirmCopy(offer.fileName, offer.place, backupRoot);
      if (!(await confirmBios(copy, "primary"))) return;
      onPressKey(buttonKey);
      actions.replaceBios(platform, offer.place);
    };
    return (
      <button
        type="button"
        disabled={disabled}
        style={actionButtonStyle(disabled)}
        onClick={() => detach(askReplace())}
      >
        Replace…
      </button>
    );
  }

  const askDelete = async () => {
    if (busy) return;
    const copy = deleteBiosFileConfirmCopy(offer.name);
    if (!(await confirmBios(copy, "danger"))) return;
    onPressKey(buttonKey);
    actions.deleteBiosFile(platform, offer.name);
  };
  return (
    <button type="button" disabled={disabled} style={actionButtonStyle(disabled)} onClick={() => detach(askDelete())}>
      {redText("Delete", disabled)}
    </button>
  );
};

const DesktopBiosFileRow: FC<{
  file: BiosTableRow;
  emulator: string | null;
  placeLines: string[];
  busyLine?: string | null;
  actionButton: ReactNode;
}> = ({ file, emulator, placeLines, busyLine = null, actionButton }) => {
  const disk = biosDiskMark(file);
  const library = biosLibraryMark(file);
  const folder = declaredFolder(file);
  const description = biosFileDescription(file);
  const rowLines = biosTableRowLines(file, emulator, placeLines);

  return (
    <div data-testid="bios-file-row" style={TABLE_ROW_STYLE}>
      <div style={TABLE_CELLS_STYLE}>
        <div style={{ minWidth: 0 }} title={file.declared_path ?? file.file_name}>
          <div data-testid="bios-file-name" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
            {folder && <span style={{ color: MUTED_COLOR }}>{folder}</span>}
            {file.file_name}
          </div>
          <div data-testid="bios-need" style={MUTED_TEXT_STYLE}>
            {needWords(file)}
          </div>
        </div>
        <div style={{ display: "flex", gap: "4px", fontSize: "12px", whiteSpace: "nowrap" }}>
          <span data-testid="disk-mark" style={{ color: disk.color }} title={disk.title}>
            {disk.glyph}
          </span>
          {library && (
            <span data-testid="library-mark" style={{ color: library.color }} title={library.title}>
              {library.glyph}
            </span>
          )}
        </div>
        <div style={{ ...MUTED_TEXT_STYLE, overflowWrap: "break-word" }}>{contentsWords(file, emulator)}</div>
        <div>{actionButton}</div>
      </div>
      {description && <div style={{ ...MUTED_TEXT_STYLE, marginTop: "3px" }}>{description}</div>}
      {rowLines.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: "2px", marginTop: "3px" }}>
          {rowLines.map((line) => (
            <div
              key={line}
              style={{
                ...MUTED_TEXT_STYLE,
                whiteSpace: "pre-wrap",
                display: "flex",
                alignItems: "center",
                gap: "6px",
              }}
            >
              {line === busyLine && <SvgThrobber style={INLINE_SPINNER_STYLE} />}
              <span>{line}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};

const DesktopBiosGroupSection: FC<{ biosStatus: BiosStatus }> = ({ biosStatus }) => {
  const blocks = groupBlocks(biosStatus, biosStatus.files ?? []);
  if (blocks.length === 0) return null;
  return (
    <div data-testid="bios-group" style={{ display: "flex", flexDirection: "column", gap: "6px", marginTop: "8px" }}>
      {blocks.map((block) => (
        <div key={`${block.heading}:${block.lines.map((l) => l.text).join("|")}`}>
          <div style={{ ...SUBHEADING_STYLE, marginBottom: "4px" }}>{block.heading}</div>
          {block.lines.map((line) => (
            <div
              key={line.text}
              style={{ display: "flex", alignItems: "center", gap: "8px", paddingLeft: "6px", fontSize: "12px" }}
            >
              <span style={{ ...DOT_STYLE, backgroundColor: GROUP_DOT_COLOR[line.tone] }} />
              <span style={{ color: BODY_COLOR }}>{line.text}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
};

const DesktopBiosTabPills: FC<{
  tabs: readonly EmulatorTab[] | undefined;
  activeTab: EmulatorTab | null;
  onSelect: (chosen: EmulatorTab) => void;
}> = ({ tabs, activeTab, onSelect }) => {
  if (!tabs || tabs.length <= 1) return null;
  return (
    <div
      data-testid="bios-emulator-tabs"
      style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "10px", marginBottom: "6px" }}
    >
      {tabs.map((tab) => {
        const key = tab.launching ? "launching" : `tab:${tab.emulator ?? ""}`;
        const selected = tab === activeTab;
        let textColor: string | undefined;
        if (tab.installed === false) textColor = BIOS_MISSING_RED;
        else if (tab.launching) textColor = ACCENT_BLUE;
        return (
          <button
            key={key}
            type="button"
            aria-pressed={selected}
            style={actionButtonStyle(false, selected)}
            onClick={() => onSelect(tab)}
          >
            <span style={textColor ? { color: textColor } : undefined}>{tabLabel(tab)}</span>
          </button>
        );
      })}
    </div>
  );
};

const DesktopBiosBulkSection: FC<{
  biosStatus: BiosStatus;
  activeTab: EmulatorTab | null;
  shownRows: readonly BiosTableRow[];
  platform: PlatformKey;
  platformDisplayName: string;
  busyFolder: string | null;
  pressedKey: string | null;
  onPressKey: (key: string) => void;
  actions: BiosActions;
  status: BiosStatusLine | null;
  confirmBios: ConfirmBiosFn;
}> = ({
  biosStatus,
  activeTab,
  shownRows,
  platform,
  platformDisplayName,
  busyFolder,
  pressedKey,
  onPressKey,
  actions,
  status,
  confirmBios,
}) => {
  const serverOffline = biosStatus.server_offline ?? false;
  const offer = biosBulkOffer(biosStatus, activeTab, shownRows, serverOffline, "shown");
  const busy = busyFolder !== null;
  const isDisabled = (key: string, idleAllowed: boolean) => !idleAllowed || (busy && pressedKey !== key);

  const reqDisabled = isDisabled("bulk:required", offer.canDownloadRequired);
  const allDisabled = isDisabled("bulk:all", offer.canDownloadAll);
  const delDisabled = isDisabled("bulk:delete", offer.deletable > 0);

  const askPlatformDelete = async () => {
    if (busy) return;
    const copy = deletePlatformBiosConfirmCopy(platformDisplayName);
    if (!(await confirmBios(copy, "danger"))) return;
    onPressKey("bulk:delete");
    actions.deleteBios(platform);
  };

  return (
    <div style={{ marginTop: "10px" }}>
      {offer.requiredLabel !== null && (
        <div style={ACTION_ROW_STYLE}>
          <button
            type="button"
            disabled={reqDisabled}
            style={actionButtonStyle(reqDisabled)}
            onClick={() => {
              if (busy) return;
              onPressKey("bulk:required");
              actions.downloadRequired(platform, offer.shownEmulator);
            }}
          >
            {renderDownloadFace(
              busyFolder,
              platform.folder,
              actions,
              "required",
              `${offer.requiredLabel} (${offer.requiredMissing})`,
              reqDisabled,
            )}
          </button>
        </div>
      )}
      <div style={{ ...SUBHEADING_STYLE, marginTop: "12px" }}>{`For all of ${platformDisplayName}`}</div>
      <div style={ACTION_ROW_STYLE}>
        <button
          type="button"
          disabled={allDisabled}
          style={actionButtonStyle(allDisabled)}
          onClick={() => {
            if (busy) return;
            onPressKey("bulk:all");
            actions.downloadAll(platform);
          }}
        >
          {renderDownloadFace(busyFolder, platform.folder, actions, "all", "Download all", allDisabled)}
        </button>
        <button
          type="button"
          disabled={delDisabled}
          style={actionButtonStyle(delDisabled)}
          onClick={() => detach(askPlatformDelete())}
        >
          {redText(`Delete downloads (${offer.deletable})`, delDisabled)}
        </button>
      </div>
      {status !== null && status.folder === platform.folder && (
        <div data-testid="bios-action-status" style={{ ...MUTED_TEXT_STYLE, marginTop: "6px", whiteSpace: "pre-line" }}>
          {status.text}
        </div>
      )}
    </div>
  );
};

const DesktopBiosUnclaimedSection: FC<{
  libraryRows: readonly BiosTableRow[];
  activeCoreLabel: string | null;
  serverOffline: boolean;
  platform: PlatformKey;
  backupRoot: string | undefined;
  busyFolder: string | null;
  pressedKey: string | null;
  onPressKey: (key: string) => void;
  actions: BiosActions;
  confirmBios: ConfirmBiosFn;
}> = ({
  libraryRows,
  activeCoreLabel,
  serverOffline,
  platform,
  backupRoot,
  busyFolder,
  pressedKey,
  onPressKey,
  actions,
  confirmBios,
}) => {
  const identifications = useIdentifications(
    libraryRows.flatMap((file) => (file.library?.md5 ? [file.library.md5] : [])),
  );
  if (libraryRows.length === 0) return null;
  const libraryFetchable = new Set(libraryRows.filter(isFetchable).map((f) => f.file_name));

  return (
    <div style={{ marginTop: "14px" }}>
      <div style={{ ...SUBHEADING_STYLE, marginBottom: "6px" }}>
        In your RomM library, asked for by no emulator here
      </div>
      <div style={TABLE_HEADER_STYLE}>
        <span>File</span>
        <span>On disk</span>
        <span>Contents</span>
        <span />
      </div>
      {libraryRows.map((file) => {
        const key = `library:${file.file_name}`;
        const md5 = file.library?.md5;
        const answer = md5 ? identifications.answers[md5] : undefined;
        const damaged = actions.attemptFor(platform.folder, null, downloadName(file)).damaged;
        const placeLines = [...libraryRowLines(file, answer), ...(damaged ? [DOWNLOAD_DAMAGED] : [])];
        const busyLine = md5 && identifications.asking === md5 ? IDENTIFYING : null;
        return (
          <DesktopBiosFileRow
            key={file.file_name}
            file={file}
            emulator={activeCoreLabel}
            placeLines={placeLines}
            busyLine={busyLine}
            actionButton={
              <DesktopBiosRowActionButton
                file={file}
                rowFetchable={libraryFetchable.has(file.file_name)}
                serverOffline={serverOffline}
                platform={platform}
                backupRoot={backupRoot}
                busyFolder={busyFolder}
                pressedKey={pressedKey}
                buttonKey={key}
                onPressKey={onPressKey}
                actions={actions}
                confirmBios={confirmBios}
              />
            }
          />
        );
      })}
    </div>
  );
};

const DesktopBiosAnsweredContent: FC<{
  biosStatus: BiosStatus;
  biosLevel: BiosLevel | null;
  readFailed: boolean;
  detail: GameDetailState;
  platform: PlatformKey;
  busyFolder: string | null;
  pressedKey: string | null;
  onPressKey: (key: string) => void;
  actions: BiosActions;
  status: BiosStatusLine | null;
  confirmBios: ConfirmBiosFn;
}> = ({
  biosStatus,
  biosLevel,
  readFailed,
  detail,
  platform,
  busyFolder,
  pressedKey,
  onPressKey,
  actions,
  status,
  confirmBios,
}) => {
  const [tabKey, setTabKey] = useState<string | null>(null);
  const files = biosStatus.files ?? [];
  const tabs = biosStatus.emulator_tabs;
  const activeTab = selectedTab(tabs, tabKey);
  const shownRows = tabRows(files, tabs, activeTab);
  const libraryRows = files.filter(isUnclaimedLibraryRow);

  const platformNameOrNull = detail.platformName || null;
  const platformDisplayName = detail.platformName || detail.platformSlug || "this platform";
  const headline = `${biosSummary(biosStatus, files, biosLevel, platformNameOrNull).sentence}${biosHeldRatio(biosStatus)}`;
  const storeOnly = storeOnlySentence(
    biosStatus.store_only,
    biosStatus.active_core_label ?? detail.activeCoreLabel,
    platformDisplayName,
  );
  const shownLabel = activeTab ? tabLabel(activeTab) : null;
  const shownEmulator = shownLabel ?? biosStatus.active_core_label ?? null;
  const serverOffline = biosStatus.server_offline ?? false;
  const shownFetchable = new Set(shownRows.filter(isFetchable).map((f) => f.file_name));
  const legendEntries = biosLegendEntries([...shownRows, ...libraryRows]);

  return (
    <div>
      {biosAnswerStale(true, readFailed) && (
        <div data-testid="bios-stale" style={{ ...MUTED_TEXT_STYLE, marginBottom: "6px" }}>
          Could not re-read the BIOS state, so what is below may be out of date. Open the game page again to retry.
        </div>
      )}
      <div style={INLINE_ROW_STYLE}>
        <span style={{ ...DOT_STYLE, backgroundColor: biosColorForLevel(biosLevel) }} />
        <span>{headline}</span>
      </div>
      {storeOnly !== null && (
        <div data-testid="bios-store-only" style={{ ...MUTED_TEXT_STYLE, marginTop: "4px" }}>
          {storeOnly}
        </div>
      )}
      <DesktopBiosGroupSection biosStatus={biosStatus} />
      <DesktopBiosTabPills
        tabs={tabs}
        activeTab={activeTab}
        onSelect={(chosen) => setTabKey(chosen.launching ? null : chosen.emulator)}
      />
      {shownRows.length > 0 && (
        <div style={{ marginTop: "8px" }}>
          <div data-testid="bios-table-header" style={TABLE_HEADER_STYLE}>
            <span>File</span>
            <span>On disk</span>
            <span>Contents</span>
            <span />
          </div>
          {shownRows.map((file) => {
            const key = `table:${file.file_name}`;
            return (
              <DesktopBiosFileRow
                key={file.file_name}
                file={file}
                emulator={shownEmulator}
                placeLines={biosPlaceLines(
                  file,
                  shownEmulator,
                  actions.attemptFor(platform.folder, file.local_path, downloadName(file)),
                )}
                actionButton={
                  <DesktopBiosRowActionButton
                    file={file}
                    rowFetchable={shownFetchable.has(file.file_name)}
                    serverOffline={serverOffline}
                    platform={platform}
                    backupRoot={biosStatus.backup_root}
                    busyFolder={busyFolder}
                    pressedKey={pressedKey}
                    buttonKey={key}
                    onPressKey={onPressKey}
                    actions={actions}
                    confirmBios={confirmBios}
                  />
                }
              />
            );
          })}
        </div>
      )}
      <DesktopBiosBulkSection
        biosStatus={biosStatus}
        activeTab={activeTab}
        shownRows={shownRows}
        platform={platform}
        platformDisplayName={platformDisplayName}
        busyFolder={busyFolder}
        pressedKey={pressedKey}
        onPressKey={onPressKey}
        actions={actions}
        status={status}
        confirmBios={confirmBios}
      />
      <DesktopBiosUnclaimedSection
        libraryRows={libraryRows}
        activeCoreLabel={biosStatus.active_core_label ?? null}
        serverOffline={serverOffline}
        platform={platform}
        backupRoot={biosStatus.backup_root}
        busyFolder={busyFolder}
        pressedKey={pressedKey}
        onPressKey={onPressKey}
        actions={actions}
        confirmBios={confirmBios}
      />
      {legendEntries.length > 0 && (
        <div
          data-testid="bios-legend"
          style={{ display: "flex", flexWrap: "wrap", columnGap: "20px", rowGap: "4px", marginTop: "12px" }}
        >
          {legendEntries.map((entry) => (
            <span key={entry.text} style={MUTED_TEXT_STYLE}>
              <span style={{ color: entry.color }}>{entry.glyph}</span> {entry.text}
            </span>
          ))}
        </div>
      )}
    </div>
  );
};

function failedBiosMessage(detail: GameDetailState): string {
  if (detail.platformSlug && !detail.platformFolder) {
    return "Could not read the BIOS state because this platform's folder in RomM is not recorded yet. Run a library sync in Tender to record it.";
  }
  return "Could not read the BIOS state. Open the game page again to retry.";
}

export const EmulationSettings: FC<EmulationSettingsProps> = ({ appId, detail }) => {
  const cardRef = useRef<HTMLDivElement>(null);
  const isStranded = useStrandedAnswer() !== null;
  const leaseOwner = `desktop-emulation-card:${appId}`;
  usePruneLeaseOwner(leaseOwner);

  const dialogHost = useDialogHost();
  const confirmBios = useMemo(() => desktopBiosConfirmDialog(dialogHost.ask), [dialogHost.ask]);

  const { biosStatus, biosLevel, readFailed, rereadBios, announceOwnBios, announceOwnCore } = useDesktopBiosReadState(
    appId,
    detail.romId,
    detail.platformSlug,
    detail.platformFolder,
    isStranded,
  );

  const [busyFolder, setBusyFolder] = useState<string | null>(null);
  const [pressedKey, setPressedKey] = useState<string | null>(null);
  const [status, setStatus] = useState<BiosStatusLine | null>(null);

  const handleSetBusy = useCallback((folder: string | null) => {
    setBusyFolder(folder);
    if (folder === null) setPressedKey(null);
  }, []);

  const platform = useMemo<PlatformKey>(
    () => ({ slug: detail.platformSlug, folder: detail.platformFolder ?? "" }),
    [detail.platformSlug, detail.platformFolder],
  );

  const handleActionReread = useCallback(async () => {
    if (detail.romId === null) return;
    await Promise.all([rereadBios(detail.romId), refreshBiosStatus(appId)]);
  }, [appId, detail.romId, rereadBios]);

  const biosActions = useBiosActions({
    setBusy: handleSetBusy,
    setStatus,
    reread: handleActionReread,
    announce: announceOwnBios,
    romId: detail.romId,
  });

  useEffect(() => {
    if (isStranded) return;
    const onTabSwitch = (e: Event) => {
      if ((e as CustomEvent<{ tab?: string } | null>).detail?.tab === "bios") {
        cardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    };
    globalThis.addEventListener("romm_tab_switch", onTabSwitch);
    return () => globalThis.removeEventListener("romm_tab_switch", onTabSwitch);
  }, [isStranded]);

  if (detail.romId === null || isStranded) return null;

  const held = biosStatus !== null;
  const biosState = biosReadState(held, readFailed);
  let biosBody: ReactNode;
  if (held) {
    biosBody = (
      <DesktopBiosAnsweredContent
        biosStatus={biosStatus}
        biosLevel={biosLevel}
        readFailed={readFailed}
        detail={detail}
        platform={platform}
        busyFolder={busyFolder}
        pressedKey={pressedKey}
        onPressKey={setPressedKey}
        actions={biosActions}
        status={status}
        confirmBios={confirmBios}
      />
    );
  } else if (biosState === "failed") {
    biosBody = (
      <div data-testid="bios-read-failed" style={MUTED_TEXT_STYLE}>
        {failedBiosMessage(detail)}
      </div>
    );
  } else {
    biosBody = (
      <div data-testid="bios-checking" style={INLINE_ROW_STYLE}>
        <SvgThrobber style={INLINE_SPINNER_STYLE} />
        <span style={MUTED_TEXT_STYLE}>Checking…</span>
      </div>
    );
  }

  return (
    <div ref={cardRef} data-testid="desktop-emulation-card" style={CARD_STYLE}>
      <h3 style={HEADING_STYLE}>Emulation</h3>
      <DesktopEmulatorSection
        appId={appId}
        detail={detail}
        isStranded={isStranded}
        leaseOwner={leaseOwner}
        rereadBios={rereadBios}
        announceOwnCore={announceOwnCore}
      />
      <div style={SECTION_DIVIDER_STYLE}>
        <div style={SUBHEADING_ROW_STYLE}>
          <span style={SUBHEADING_STYLE}>BIOS / FIRMWARE</span>
        </div>
        {biosBody}
      </div>
      {dialogHost.element}
    </div>
  );
};
