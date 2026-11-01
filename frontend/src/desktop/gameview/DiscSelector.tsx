/**
 * The disc and version selector beside the desktop Play button. What it draws
 * and does: `docs/architecture/desktop-dom-architecture.md`.
 */

import { useState, useEffect, useRef, useCallback, type FC, type ReactNode } from "react";
import { addEventListener, removeEventListener } from "../../api/host";
import { FaCompactDisc, FaChevronDown, FaLayerGroup } from "react-icons/fa";
import { getCachedGameDetail, logError, logWarn } from "../../api/backend";
import type { DiscSelection, VersionList, VersionInfo } from "../../api/backend";
import { detach } from "../../utils/detach";
import { useOutsideClick } from "../../utils/useOutsideClick";
import { mountPruneLeaseOwner, releasePruneLeasesByOwner } from "../../utils/pruneLease";
import { executeVersionSwitch, fetchVersionCovers, isSelectable, loadVersionList } from "../../utils/versionSwitch";
import {
  type DiscOptionData,
  type DiscDisplayState,
  computeDiscDisplayState,
  fetchDiscSelection,
  executeDiscSelection,
} from "../../utils/discSelection";
import { isTerminalStage } from "../../utils/syncProgress";
import { DEFAULT_BADGE, DOWNLOADED_BADGE, NOT_SYNCED_BADGE, versionRowHint } from "../../utils/versionPickerWording";
import { DISC_GREY, DISC_ACCENT, DiscStack, DiscWithNumber, buildDiscOptions } from "../../shared/DiscGlyphs";
import { AvailabilityHint, Badge } from "../../shared/VersionMarks";
import type { DownloadCompleteEvent, DownloadFailedEvent, SyncProgress } from "../../types";
import type { RommDataChangedDetail, RommRomUninstalledDetail } from "../../types/events";
import type { AskDialog } from "./dialogs/askDialog";
import { desktopUnsyncedSavesDialog } from "./dialogs/desktopDialogs";

interface DiscSelectorProps {
  appId: number;
  ask: AskDialog;
  /** Whether the bound version's file is missing, which leaves its discs out. */
  fileMissing: boolean;
}

const LOG_TAG = "DesktopDiscSelector";

const MENU_ITEM_STYLE = {
  width: "100%",
  padding: "8px 16px",
  textAlign: "left",
  border: "none",
  fontSize: "13px",
  display: "flex",
  alignItems: "center",
  gap: "10px",
  whiteSpace: "nowrap",
} as const;

function selectorTriggerFace(discDisplayState: DiscDisplayState | null, offDefault: boolean): ReactNode {
  if (!discDisplayState) {
    return <FaLayerGroup size={20} color={offDefault ? DISC_ACCENT : DISC_GREY} />;
  }
  if (discDisplayState.showPlaylistFace) {
    return <DiscStack size={20} color={DISC_GREY} />;
  }
  return (
    <DiscWithNumber
      size={20}
      color={discDisplayState.isPinned ? DISC_ACCENT : DISC_GREY}
      num={discDisplayState.activeNum}
    />
  );
}

function selectorButtonTitle(discDisplayState: DiscDisplayState | null, hasVersions: boolean): string {
  if (!discDisplayState) return "Select Version";
  return hasVersions ? "Select Disc or Version" : "Select Disc";
}

function versionRowCover(base64: string | undefined): ReactNode {
  if (base64) {
    return (
      <img
        alt=""
        src={`data:image/png;base64,${base64}`}
        style={{ width: "24px", height: "24px", borderRadius: "2px", objectFit: "cover", flexShrink: 0 }}
      />
    );
  }
  return <FaCompactDisc size={18} color={DISC_GREY} style={{ flexShrink: 0 }} />;
}

export const DiscSelector: FC<DiscSelectorProps> = ({ appId, ask, fileMissing }) => {
  const discLeaseOwner = `desktop-disc-selector:${appId}`;
  const versionLeaseOwner = `desktop-version-picker:${appId}`;

  const [selection, setSelection] = useState<DiscSelection | null>(null);
  // The pinned disc, as read and as a pick echoed it; `null` follows the default.
  const [selected, setSelected] = useState<DiscOptionData>(null);
  const romIdRef = useRef<number | null>(null);

  const [versionList, setVersionList] = useState<VersionList | null>(null);
  // Set while a switch is under way; `executeVersionSwitch` says when it clears.
  const [switching, setSwitching] = useState(false);
  const [covers, setCovers] = useState<Record<number, string>>({});
  const coversRequested = useRef<Set<number>>(new Set());
  const memberIdsRef = useRef<Set<number>>(new Set());
  const listRequestIdRef = useRef(0);
  // Reset when a switch starts here, so only a broadcast that arrives during it
  // counts.
  const switchBroadcastSeenRef = useRef(false);
  const loadVersionListRef = useRef<{
    appId: number;
    load: (source?: "normal" | "vanished_refusal") => Promise<void>;
  } | null>(null);

  const [showMenu, setShowMenu] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const closeMenu = useCallback(() => setShowMenu(false), []);
  useOutsideClick(menuRef, closeMenu, showMenu);

  // The bound version's discs, asked again when a download of it completes and
  // when a switch binds another version.
  useEffect(() => {
    mountPruneLeaseOwner(discLeaseOwner);
    let cancelled = false;

    const fetchSelection = async (rid: number): Promise<void> => {
      const result = await fetchDiscSelection(rid, LOG_TAG);
      // A switch while it was asked binds another ROM, whose discs these are not.
      if (cancelled || !result || rid !== romIdRef.current) return;
      setSelection(result);
      setSelected(result.selected ?? null);
    };

    // Only the newest detail read binds: an older one landing late names the
    // version a switch has since left.
    let initSeq = 0;
    const init = async (): Promise<void> => {
      const seq = ++initSeq;
      try {
        const cached = await getCachedGameDetail(appId);
        if (cancelled || seq !== initSeq || !cached.found || cached.rom_id == null) return;
        romIdRef.current = cached.rom_id;
        if (!cached.installed) return;
        await fetchSelection(cached.rom_id);
      } catch (e) {
        logError(`${LOG_TAG}: init error: ${e}`);
      }
    };
    detach(init());

    const completeListener = addEventListener<DownloadCompleteEvent>("download_complete", (evt) => {
      if (evt.rom_id === romIdRef.current) detach(fetchSelection(evt.rom_id));
    });
    const onUninstall = (e: Event) => {
      if ((e as CustomEvent<RommRomUninstalledDetail>).detail.rom_id !== romIdRef.current) return;
      setSelection(null);
      setSelected(null);
    };
    globalThis.addEventListener("romm_rom_uninstalled", onUninstall);
    const onSwitched = (e: Event) => {
      const detail = (e as CustomEvent<RommDataChangedDetail>).detail;
      if (detail.type !== "version_switched" || detail.app_id !== appId) return;
      // The old version's discs are not the new one's, also where the read below fails.
      romIdRef.current = null;
      setSelection(null);
      setSelected(null);
      detach(init());
    };
    globalThis.addEventListener("romm_data_changed", onSwitched);

    return () => {
      cancelled = true;
      detach(releasePruneLeasesByOwner(discLeaseOwner));
      removeEventListener("download_complete", completeListener);
      globalThis.removeEventListener("romm_rom_uninstalled", onUninstall);
      globalThis.removeEventListener("romm_data_changed", onSwitched);
    };
  }, [appId, discLeaseOwner]);

  // The group's versions, asked again when Big Picture's VersionPicker asks: a
  // switch, a prune, a member's download completing or failing, a member's
  // uninstall, and a sync run's end. A failed download counts because the
  // sibling supersede removes the old install when the download STARTS.
  useEffect(() => {
    mountPruneLeaseOwner(versionLeaseOwner);
    let cancelled = false;
    switchBroadcastSeenRef.current = false;
    const load = async (source: "normal" | "vanished_refusal" = "normal"): Promise<void> => {
      const requestId = ++listRequestIdRef.current;
      const isCurrent = (): boolean => !cancelled && requestId === listRequestIdRef.current;
      try {
        const result = await loadVersionList(appId, isCurrent);
        if (!result || !isCurrent()) return;
        memberIdsRef.current = new Set((result.versions ?? []).map((v) => v.rom_id));
        setVersionList(result);
      } catch (e) {
        if (!isCurrent()) return;
        if (source === "vanished_refusal") {
          logWarn(`${LOG_TAG}: version-vanished list refresh failed: ${e}`);
        } else {
          logError(`${LOG_TAG}: getVersionList failed: ${e}`);
        }
      } finally {
        // Only a current read issued after this game's version_switched
        // broadcast clears the in-flight guard: only such a read is known to
        // hold the list the switch left behind.
        if (switchBroadcastSeenRef.current && isCurrent()) setSwitching(false);
      }
    };
    loadVersionListRef.current = { appId, load };
    detach(load());

    const onDataChanged = (e: Event) => {
      const detail = (e as CustomEvent<RommDataChangedDetail>).detail;
      const switched = detail.type === "version_switched" && detail.app_id === appId;
      const pruned =
        detail.type === "rom_pruned" &&
        (detail.app_ids.includes(appId) || detail.rom_ids.some((romId) => memberIdsRef.current.has(romId)));
      if (switched) switchBroadcastSeenRef.current = true;
      if (switched || pruned) detach(load());
    };
    globalThis.addEventListener("romm_data_changed", onDataChanged);
    const onInstallChanged = (romId: number) => {
      if (memberIdsRef.current.has(romId)) detach(load());
    };
    const dlComplete = addEventListener<DownloadCompleteEvent>("download_complete", (evt) =>
      onInstallChanged(evt.rom_id),
    );
    const dlFailed = addEventListener<DownloadFailedEvent>("download_failed", (evt) => onInstallChanged(evt.rom_id));
    const onUninstalled = (e: Event) => onInstallChanged((e as CustomEvent<RommRomUninstalledDetail>).detail.rom_id);
    globalThis.addEventListener("romm_rom_uninstalled", onUninstalled);
    const syncEnded = addEventListener<SyncProgress>("sync_progress", (frame) => {
      if (!frame.running && isTerminalStage(frame.stage)) detach(load());
    });

    return () => {
      cancelled = true;
      // A switch under way is torn down with this game's lease owner.
      setSwitching(false);
      detach(releasePruneLeasesByOwner(versionLeaseOwner));
      if (loadVersionListRef.current?.load === load) loadVersionListRef.current = null;
      globalThis.removeEventListener("romm_data_changed", onDataChanged);
      removeEventListener("download_complete", dlComplete);
      removeEventListener("download_failed", dlFailed);
      globalThis.removeEventListener("romm_rom_uninstalled", onUninstalled);
      removeEventListener("sync_progress", syncEnded);
    };
  }, [appId, versionLeaseOwner]);

  useEffect(() => {
    return fetchVersionCovers(versionList?.versions, coversRequested.current, (romId, base64) => {
      setCovers((prev) => ({ ...prev, [romId]: base64 }));
    });
  }, [versionList]);

  const refreshAfterVanishedRefusal = (): Promise<void> => {
    const loader = loadVersionListRef.current;
    if (loader?.appId !== appId) return Promise.resolve();
    return loader.load("vanished_refusal");
  };

  const handleDiscChange = async (data: DiscOptionData): Promise<void> => {
    setShowMenu(false);
    const rid = romIdRef.current;
    if (rid == null) return;
    await executeDiscSelection({
      appId,
      romId: rid,
      data,
      leaseOwner: discLeaseOwner,
      onSelected: setSelected,
      logTag: LOG_TAG,
    });
  };

  const handleSwitch = async (target: VersionInfo): Promise<void> => {
    setShowMenu(false);
    switchBroadcastSeenRef.current = false;
    await executeVersionSwitch({
      appId,
      target,
      leaseOwner: versionLeaseOwner,
      askUnsyncedSaves: desktopUnsyncedSavesDialog(ask),
      onCoverResolved: (romId, cover) => setCovers((prev) => ({ ...prev, [romId]: cover })),
      onVanishedRefusal: () => detach(refreshAfterVanishedRefusal()),
      setSwitching,
      logTag: LOG_TAG,
    });
  };

  const discDisplayState = fileMissing ? null : computeDiscDisplayState(selection, selected);
  const versions = versionList?.multi_version ? (versionList.versions ?? []) : [];
  const hasVersions = versions.length > 0;
  if (!discDisplayState && !hasVersions) return null;

  const active = versions.find((v) => v.active);
  // As Big Picture's VersionPicker: "not the default" only where RomM holds one.
  const offDefault = versions.some((v) => v.is_default) && !(active?.is_default ?? false);

  const face = selectorTriggerFace(discDisplayState, offDefault);
  const buttonTitle = selectorButtonTitle(discDisplayState, hasVersions);

  return (
    <div style={{ position: "relative" }} ref={menuRef}>
      <button
        type="button"
        data-testid="disc-btn"
        title={buttonTitle}
        aria-label={buttonTitle}
        aria-haspopup="menu"
        aria-expanded={showMenu}
        disabled={switching}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          gap: "7px",
          height: "48px",
          padding: "0 12px",
          marginLeft: "8px",
          borderRadius: "2px",
          border: "1px solid rgba(255, 255, 255, 0.1)",
          background: "rgba(255, 255, 255, 0.06)",
          color: "#ffffff",
          cursor: switching ? "default" : "pointer",
          opacity: switching ? 0.55 : 1,
          boxShadow: "0 1px 4px rgba(0, 0, 0, 0.4)",
          lineHeight: "normal",
          boxSizing: "border-box",
        }}
        onClick={() => setShowMenu((prev) => !prev)}
      >
        {face}
        <FaChevronDown size={10} color="#cfd3d8" />
      </button>

      {showMenu && (
        <div
          data-testid="disc-menu"
          role="menu"
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: "8px",
            minWidth: "180px",
            maxHeight: "360px",
            overflowY: "auto",
            background: "#1e2837",
            border: "1px solid #3c4856",
            borderRadius: "2px",
            boxShadow: "0 8px 16px rgba(0, 0, 0, 0.5)",
            zIndex: 1000,
            padding: "4px 0",
          }}
        >
          {discDisplayState &&
            selection &&
            buildDiscOptions(selection).map((o) => {
              const isActive = o.data === discDisplayState.effectiveSelected;
              return (
                <button
                  key={String(o.data)}
                  type="button"
                  role="menuitem"
                  style={{
                    ...MENU_ITEM_STYLE,
                    background: isActive ? "rgba(89, 182, 255, 0.1)" : "transparent",
                    color: isActive ? DISC_ACCENT : "#c7d5e0",
                    cursor: "pointer",
                  }}
                  onClick={() => detach(handleDiscChange(o.data))}
                >
                  {o.icon}
                  <span style={{ flex: 1 }}>{o.text}</span>
                  {isActive ? <span style={{ marginLeft: "6px", fontWeight: 700 }}>✓</span> : null}
                </button>
              );
            })}

          {discDisplayState && hasVersions && (
            <hr
              data-testid="disc-version-separator"
              style={{ height: "1px", background: "#67707b", border: "none", margin: "4px 0" }}
            />
          )}

          {versions.map((v) => {
            const selectable = isSelectable(v);
            const hint = versionRowHint(v);
            return (
              <button
                key={v.rom_id}
                type="button"
                role="menuitem"
                disabled={!selectable}
                style={{
                  ...MENU_ITEM_STYLE,
                  background: v.active ? "rgba(89, 182, 255, 0.1)" : "transparent",
                  color: v.active ? DISC_ACCENT : "#c7d5e0",
                  cursor: selectable ? "pointer" : "default",
                  opacity: selectable ? 1 : 0.55,
                }}
                onClick={() => detach(handleSwitch(v))}
              >
                {versionRowCover(covers[v.rom_id])}
                <span style={{ fontWeight: v.active ? 600 : 400 }}>{v.label || v.name || String(v.rom_id)}</span>
                {v.is_default ? <Badge text={DEFAULT_BADGE} tone="accent" /> : null}
                {v.installed ? <Badge text={DOWNLOADED_BADGE} tone="good" /> : null}
                {v.switchable && !v.synced ? <Badge text={NOT_SYNCED_BADGE} tone="muted" /> : null}
                {hint === null ? null : <AvailabilityHint text={hint} />}
                {v.active ? <span style={{ marginLeft: "auto", fontWeight: 700 }}>✓</span> : null}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
};
