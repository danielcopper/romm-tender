/**
 * VersionPicker — the game-detail "Version" control for a sibling group (#1297).
 *
 * Rendered as a compact icon trigger in the play-button section, immediately to
 * the right of DiscSelector (its structural twin, #865): a single DialogButton
 * that opens an anchored `showContextMenu` list of every version in the group.
 * Each row is
 * marked — the active version (✓ + tint), the default (the version the
 * resolution chain + Preferred-region setting would pick), downloaded versions,
 * and versions that exist on the server but aren't synced locally yet. Per-row
 * covers load lazily from the per-ROM cover cache (cache-first fetchCoverBase64,
 * #1346), so each version shows its own art rather than the group's shared grid
 * cover; a not-yet-synced sibling downloads its cover once.
 *
 * A version RomM no longer serves stays listed as retained context, dimmed and
 * unswitchable. When local data for it exists, that row carries a trash
 * affordance and activating it opens the removed-game cleanup confirmation
 * scoped to that ROM — the row is the menu's focusable unit, so the action has
 * to live on it rather than in a nested button or a row of its own.
 *
 * Selecting a version while the game is not downloaded rebinds the group's Steam
 * shortcut to it (appId-safe: the name/appId stay sticky) so the Download button
 * fetches exactly that version. Switching a *downloaded* game rebinds it too and
 * confirm-writes the target's launch command onto the shortcut (#1298); if the
 * currently-bound install has unsynced saves the backend soft-blocks and the
 * picker offers the sync-or-strand confirm. A single-version group renders
 * nothing (the null-gate pattern).
 */

import { useState, useEffect, useRef, FC, ReactNode } from "react";
import { addEventListener, removeEventListener } from "../api/host";
import { showToast } from "../utils/toast";
import { Menu, MenuItem, showContextMenu, DialogButton } from "@decky/ui";
import { FaChevronDown, FaCompactDisc, FaLayerGroup, FaTrash } from "react-icons/fa";
import { logError, logWarn } from "../api/backend";
import type { VersionList, VersionInfo } from "../api/backend";
import { showUnsyncedSavesModal } from "./UnsyncedSavesSwitchModal";
import { getEventTarget } from "../utils/events";
import { NO_FILE_ON_ROMM_LABEL } from "../utils/noFileOnRommWording";
import { detach } from "../utils/detach";
import { mountPruneLeaseOwner, releasePruneLeasesByOwner } from "../utils/pruneLease";
import { loadVersionList, fetchVersionCovers, executeVersionSwitch, isSelectable } from "../utils/versionSwitch";
import type { RommDataChangedDetail, RommRomUninstalledDetail } from "../types/events";
import type { DownloadCompleteEvent, DownloadFailedEvent } from "../types";
import { openRemovedGamesCleanupModal } from "./RemovedGamesCleanup";

interface VersionPickerProps {
  appId: number;
}

// Steam accent blue for the active version, neutral grey otherwise — the same
// palette DiscSelector uses so the two game-detail pickers read as one system.
const ACTIVE_ACCENT = "#59b6ff";
const NEUTRAL_GREY = "#dcdedf";

const BADGE_COLORS: Record<"accent" | "muted" | "good", { bg: string; fg: string }> = {
  accent: { bg: "rgba(89, 182, 255, 0.18)", fg: ACTIVE_ACCENT },
  good: { bg: "rgba(91, 163, 43, 0.22)", fg: "#7ac74f" },
  muted: { bg: "rgba(255, 255, 255, 0.10)", fg: "rgba(255, 255, 255, 0.55)" },
};

/** The label a row (or a singleton binding) carries once RomM 404s its exact id. */
const VANISHED_HINT = "No longer available on RomM";

/** Accessible name of the trash affordance that opens the cleanup confirmation. */
const REMOVE_LOCAL_DATA_LABEL = "Remove local data";

/**
 * The cleanup affordance, shown on a vanished row and on a vanished singleton
 * binding. Icon-only: it sits at the right edge of a row that already says why
 * the version is unusable, so a text label would only repeat that hint.
 *
 * Colour comes from the injected stylesheet, never a `color` prop: Steam
 * repaints a focused destructive MenuItem red, and an inline colour would
 * survive that and leave a red icon on a red row. `onMenuRow` opts into the
 * focused-state flip, which must not reach the singleton button (its focus
 * background stays dark).
 */
const RemoveLocalDataIcon: FC<{ onMenuRow?: boolean; style?: React.CSSProperties }> = ({ onMenuRow, style }) => (
  <FaTrash
    size={14}
    role="img"
    aria-label={REMOVE_LOCAL_DATA_LABEL}
    className={onMenuRow ? "romm-vanished-trash romm-vanished-trash-row" : "romm-vanished-trash"}
    style={style}
  />
);

/** The italic inline hint that explains why a version can't be selected. */
const AvailabilityHint: FC<{ text: string }> = ({ text }) => (
  <span style={{ marginLeft: "8px", fontSize: "11px", fontStyle: "italic", color: NEUTRAL_GREY }}>{text}</span>
);

/** A small pill badge (Default / Downloaded / not synced) shown after a row's label. */
const Badge: FC<{ text: string; tone: "accent" | "muted" | "good" }> = ({ text, tone }) => {
  const { bg, fg } = BADGE_COLORS[tone];
  return (
    <span
      style={{
        marginLeft: "8px",
        padding: "1px 7px",
        borderRadius: "10px",
        fontSize: "11px",
        fontWeight: 600,
        backgroundColor: bg,
        color: fg,
      }}
    >
      {text}
    </span>
  );
};

export const VersionPicker: FC<VersionPickerProps> = ({ appId }) => {
  const leaseOwner = `version-picker:${appId}`;
  const [versionList, setVersionList] = useState<VersionList | null>(null);
  // In-flight switch guard (#1345 round-2 / E): a switch rebinds the shortcut and
  // then relies on the version_switched re-fetch to refresh the (now stale) list.
  // While a switch is running the trigger is disabled + shows a throbber and the
  // menu can't open, so a rapid second click can't act against the stale list
  // (the swallowed switch-back bug) or interleave two switches' confirm polls.
  const [switching, setSwitching] = useState(false);
  // rom_id -> cover base64 for every version, filled lazily once the list loads.
  const [covers, setCovers] = useState<Record<number, string>>({});
  const coversRequested = useRef<Set<number>>(new Set());
  // The group's member rom_ids from the last loaded list — lets the install-change
  // listeners below ignore events for other games without a fetch.
  const memberIdsRef = useRef<Set<number>>(new Set());
  const listRequestIdRef = useRef(0);
  const loadVersionListRef = useRef<{
    appId: number;
    load: (source?: "normal" | "vanished_refusal") => Promise<void>;
  } | null>(null);

  // Initial load + refresh on a version switch (this or another surface). The
  // effect owns the loader lifetime for this appId. All request sources share one
  // generation so only the latest completion can publish list/reachability state.
  useEffect(() => {
    mountPruneLeaseOwner(leaseOwner);
    let cancelled = false;
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
          logWarn(`VersionPicker: version-vanished list refresh failed: ${e}`);
        } else {
          logError(`VersionPicker: getVersionList failed: ${e}`);
        }
      } finally {
        // The post-switch version_switched reload landing is the "switch fully
        // settled" signal — clear the in-flight guard here so the trigger
        // re-enables against a FRESH list, never a stale one (#1345 round-2 / E).
        // In the finally (not just on success) so a failed reload can't leave the
        // guard stuck; on the initial mount load switching is already false (no-op).
        if (source === "normal" && isCurrent()) setSwitching(false);
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
      if (switched || pruned) detach(load());
    };
    globalThis.addEventListener("romm_data_changed", onDataChanged);

    // A download or an uninstall changes a group member's Downloaded badge
    // WITHOUT a version switch — reload so the menu never shows a superseded
    // install state. download_failed matters too: the sibling supersede removes
    // the old install when the download STARTS, so a failed download has still
    // changed the on-disk picture.
    const onInstallChanged = (romId: number) => {
      if (memberIdsRef.current.has(romId)) detach(load());
    };
    const dlComplete = addEventListener<DownloadCompleteEvent>("download_complete", (evt) =>
      onInstallChanged(evt.rom_id),
    );
    const dlFailed = addEventListener<DownloadFailedEvent>("download_failed", (evt) => onInstallChanged(evt.rom_id));
    const onUninstalled = (e: Event) => onInstallChanged((e as CustomEvent<RommRomUninstalledDetail>).detail.rom_id);
    globalThis.addEventListener("romm_rom_uninstalled", onUninstalled);

    return () => {
      cancelled = true;
      detach(releasePruneLeasesByOwner(leaseOwner));
      if (loadVersionListRef.current?.load === load) loadVersionListRef.current = null;
      globalThis.removeEventListener("romm_data_changed", onDataChanged);
      removeEventListener("download_complete", dlComplete);
      removeEventListener("download_failed", dlFailed);
      globalThis.removeEventListener("romm_rom_uninstalled", onUninstalled);
    };
  }, [appId, leaseOwner]);

  // Lazily fetch a cover for every version once the list is known, via the
  // cache-first fetchCoverBase64 (#1346).
  useEffect(() => {
    return fetchVersionCovers(versionList?.versions, coversRequested.current, (romId, base64) => {
      setCovers((prev) => ({ ...prev, [romId]: base64 }));
    });
  }, [versionList]);

  // Apply a successful switch: confirm-write the target's launch command onto the
  // Steam shortcut (blank for an uninstalled target — intended, so the shortcut
  // never keeps the old version's command), then invalidate the cache and
  // broadcast the switch so sibling surfaces re-read the new binding.
  //
  // The backend rebind is ALREADY committed by the time we get here, so the
  // cache-invalidate + broadcast must always run — even if the launch-command
  // write fails or throws. A missed confirm only leaves the shortcut on a stale
  // command; it self-heals at the next startup/sync reconcile, so we warn and
  // nudge the user rather than reporting the whole switch as failed.
  const refreshAfterVanishedRefusal = (): Promise<void> => {
    const loader = loadVersionListRef.current;
    if (loader?.appId !== appId) return Promise.resolve();
    return loader.load("vanished_refusal");
  };

  const handleSwitch = async (target: VersionInfo): Promise<void> => {
    await executeVersionSwitch({
      appId,
      target,
      leaseOwner,
      askUnsyncedSaves: showUnsyncedSavesModal,
      onCoverResolved: (romId, cover) => setCovers((prev) => ({ ...prev, [romId]: cover })),
      onVanishedRefusal: () => detach(refreshAfterVanishedRefusal()),
      setSwitching,
      logTag: "VersionPicker",
    });
  };

  const openCleanup = (romId: number): void => {
    detach(
      openRemovedGamesCleanupModal(romId)
        .then((opened) => {
          if (!opened) showToast("This local entry already changed.");
        })
        .catch((error) => {
          logError(`VersionPicker: cleanup preview failed for rom ${romId}: ${error}`);
          showToast("Could not prepare local cleanup.", { subtext: `${error}` });
        }),
    );
  };

  if (!versionList?.multi_version) {
    const bound = versionList?.bound_version;
    if (!versionList?.bound_vanished || !bound?.synced) return null;
    // A single-member group has nothing to pick between, so it renders no menu —
    // but its vanished binding still has to SAY why the game is unusable, next to
    // the inline cleanup that is the only action left for it.
    return (
      <span style={{ display: "inline-flex", alignItems: "center" }}>
        <DialogButton
          className="romm-disc-btn"
          onClick={() => openCleanup(bound.rom_id)}
          aria-label={REMOVE_LOCAL_DATA_LABEL}
          title={REMOVE_LOCAL_DATA_LABEL}
        >
          <RemoveLocalDataIcon />
        </DialogButton>
        <AvailabilityHint text={VANISHED_HINT} />
      </span>
    );
  }
  if (!versionList.versions || versionList.versions.length === 0) return null;

  const versions = versionList.versions;
  const active = versions.find((v) => v.active);
  // Accent the trigger when the bound version isn't the group's natural default
  // (mirrors DiscSelector's "pinned ≠ default" accent) — an instant "this game is
  // on a non-default version" read; neutral when it is the default.
  const activeIsDefault = active?.is_default ?? false;

  const rowCover = (v: VersionInfo): ReactNode => {
    const base64 = covers[v.rom_id];
    if (base64) {
      return (
        <img
          alt=""
          src={`data:image/png;base64,${base64}`}
          style={{ width: "28px", height: "28px", borderRadius: "3px", objectFit: "cover", flexShrink: 0 }}
        />
      );
    }
    return <FaCompactDisc size={20} color={NEUTRAL_GREY} style={{ flexShrink: 0 }} />;
  };

  const rowAvailabilityHint = (v: VersionInfo): ReactNode => {
    if (v.vanished) return <AvailabilityHint text={VANISHED_HINT} />;
    if (v.no_file) return <AvailabilityHint text={NO_FILE_ON_ROMM_LABEL} />;
    if (!v.switchable) return <AvailabilityHint text="conflicting metadata match in RomM" />;
    return null;
  };

  const openMenu = (e: MouseEvent): void => {
    // Blocked while a switch is in flight — the list is stale until the reload
    // lands, so opening it now would let a click act against the wrong versions.
    if (switching) return;
    showContextMenu(
      <Menu label="Version">
        {versions.map((v) => {
          // A synced vanished row has exactly one thing left to offer, so the row
          // IS that offer: it carries the trash affordance and activates the
          // cleanup confirmation. The action has to sit on the row itself — a
          // MenuItem is the menu's focusable unit, so a nested button would be
          // unreachable by gamepad and a row of its own belongs to no version
          // visually. Switching stays impossible either way: handleSwitch
          // refuses a vanished target.
          const removable = v.vanished && v.synced;
          return (
            <MenuItem
              key={v.rom_id}
              disabled={!removable && !isSelectable(v)}
              {...(removable ? { tone: "destructive" as const } : {})}
              onClick={() => (removable ? openCleanup(v.rom_id) : detach(handleSwitch(v)))}
            >
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "10px",
                  width: "100%",
                  color: v.active ? ACTIVE_ACCENT : undefined,
                  // Unavailable/conflicting rows stay visible as retained context,
                  // but are dimmed until RomM offers a usable target.
                  opacity: isSelectable(v) ? undefined : 0.55,
                }}
              >
                {rowCover(v)}
                <span>{v.label || v.name || String(v.rom_id)}</span>
                {v.is_default ? <Badge text="Default" tone="accent" /> : null}
                {v.installed ? <Badge text="Downloaded" tone="good" /> : null}
                {v.switchable && !v.synced ? <Badge text="not synced" tone="muted" /> : null}
                {rowAvailabilityHint(v)}
                {v.active ? <span style={{ marginLeft: "6px", fontWeight: 700 }}>✓</span> : null}
                {removable ? <RemoveLocalDataIcon onMenuRow style={{ marginLeft: "auto", flexShrink: 0 }} /> : null}
              </span>
            </MenuItem>
          );
        })}
      </Menu>,
      getEventTarget(e),
    );
  };

  // A compact icon-only trigger (twin of DiscSelector) — a single DialogButton so
  // it is one natively-focusable, gamepad-reachable element inside the play-section
  // Focusable row. The verbose per-version detail lives in the anchored menu.
  return (
    <DialogButton
      className="romm-disc-btn"
      onClick={openMenu}
      disabled={switching}
      aria-label="Version"
      title="Version"
      style={switching ? { opacity: 0.55 } : {}}
    >
      <FaLayerGroup size={20} color={activeIsDefault ? NEUTRAL_GREY : ACTIVE_ACCENT} />
      {switching ? (
        <span className="romm-throbber" style={{ width: "14px", height: "14px" }} />
      ) : (
        <FaChevronDown size={10} color="#cfd3d8" />
      )}
    </DialogButton>
  );
};
