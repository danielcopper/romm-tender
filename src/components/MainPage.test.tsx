// CATCH-REJECTION ASSERTION RULE (applies to all orchestration shell tests):
// Every catch block with a setX(...) / logError side effect MUST have its
// side effect asserted in the test (rendered status string, surfaced toast,
// captured logError call). Asserting only that the rejecting call was
// invoked is vacuous — the rejection happens after the call returns, so
// the test would pass with or without the .catch. Truly-/* ignore */
// catches (no observable side effect) are exempt; for those, assert the
// absence of state change.
//
// MainPage catch sites (asserted below):
//   - mount: refreshMigrationState().catch → logError("Failed to refresh
//     migration state: ...") — asserted via vi.spyOn(backend, "logError").
//   - mount: getSyncStatus().catch → logError("Failed to query sync status") —
//     asserted, together with the quiet page it falls back to.
//   - handleCancel try/catch → showTransientStatus("Failed to cancel sync"),
//     surfaced under the still-running progress rows (the status field is not
//     gated on the run being idle).
//   - fixRetroarchInputDriver inline `.catch(() => {})` (inside ConfirmModal
//     onOK) — truly-ignored; warning state remains (no clear).
//
// MUTATION CHECKS (by inspection — auto-mode classifier likely blocks on
// React state internals + listener cleanup, so confidence is recorded here):
//   1. Removing the useMigrationStatus() call from MainPage would break the
//      "subscribes on mount and unsubscribes on unmount" test —
//      migrationListeners.length would stay at 0 after mount. (The real hook's
//      own teardown is NOT covered here: this file module-mocks
//      ../utils/migrationStore, so neither it nor the real onMigrationChange
//      ever runs. Its unsubscribe is pinned in src/utils/migrationStore.test.ts.)
//   2. Removing the paused poll's `clearInterval` teardown would break the
//      "tears the paused poll down on unmount" test — the stats read would go
//      on firing after the panel is gone.
//   3. Removing the showTransientStatus("Failed to cancel sync") call from
//      handleCancel's catch would break the "cancelSync rejection" test — the
//      Field label would render as the empty string instead of the failure
//      message.

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, fireEvent, act } from "@testing-library/react";
import { createElement, useSyncExternalStore, type ReactElement } from "react";
import { MainPage, ConnectionIndicator } from "./MainPage";
import * as backend from "../api/backend";
import { useVersionError } from "./VersionErrorCard";
import {
  resetSyncProgressStoreForTests,
  setSyncProgress,
  updateSyncProgress,
  onSyncProgressChange,
  getSyncProgress,
  FETCH_SHARE,
  COVERS_SHARE,
  APPLY_SHARE,
} from "../utils/syncProgress";
import { beginEtaRun, resetEta } from "../utils/syncEta";
import * as syncEta from "../utils/syncEta";
import { setDownloads } from "../utils/downloadStore";
import { resetConnectionProbeForTests } from "../utils/connectionProbe";
import { resetSyncStatsStoreForTests } from "../utils/syncStatsStore";
import { setLegacyInstallState } from "../utils/legacyInstallStore";
import { LEGACY_INSTALL_TITLE } from "./LegacyInstallBanner";
import { resetPendingPreviewStoreForTests, adoptPreview, clearPendingPreview } from "../utils/pendingPreviewStore";
import { showModal } from "@decky/ui";
import * as syncManager from "../utils/syncManager";
import * as connectionState from "../utils/connectionState";
import { firstBodyStop, pageEntryStop, placeEntryFocus } from "../utils/entryFocus";
import type {
  MigrationStatus,
  SaveSortMigrationStatus,
  SyncStats,
  SyncStatusAnswer,
  SyncPreview,
  SessionBudgetStatus,
  DownloadItem,
  PluginSettings,
  SyncProgress,
} from "../types";

// -----------------------------------------------------------------------------
// Module mocks
// -----------------------------------------------------------------------------

vi.mock("./VersionErrorCard", () => ({
  useVersionError: vi.fn(() => null),
  VersionErrorCard: (props: { message: string; compact?: boolean }) =>
    createElement("div", { "data-testid": "version-error-card" }, props.message),
}));

vi.mock("./MigrationBlockedPage", () => ({
  MigrationBlockedPage: (_props: { migration: MigrationStatus }) =>
    createElement("div", { "data-testid": "migration-blocked-page" }),
}));

// migrationStore — listener-array mock so tests drive subscribe/notify
// deterministically. resetAllMocks wipes impls; re-stubbed in beforeEach.
const migrationListeners: Array<() => void> = [];
let currentMigrationState: MigrationStatus = { pending: false };
// useMigrationStatus routes through the mocked seams rather than being stubbed
// with a constant, so the listener-array assertions still measure the panel's
// real subscribe/unsubscribe. subscribe/snapshot are built once — a fresh
// subscribe reference per render makes React re-subscribe on every render.
vi.mock("../utils/migrationStore", () => {
  const subscribe = (cb: () => void) => mod.onMigrationChange(cb);
  const snapshot = () => mod.getMigrationState();
  const mod = {
    getMigrationState: vi.fn(() => currentMigrationState),
    setMigrationStatus: vi.fn((s: MigrationStatus) => {
      currentMigrationState = s;
      migrationListeners.forEach((fn) => fn());
    }),
    onMigrationChange: vi.fn((cb: () => void) => {
      migrationListeners.push(cb);
      return () => {
        const i = migrationListeners.indexOf(cb);
        if (i >= 0) migrationListeners.splice(i, 1);
      };
    }),
    useMigrationStatus: () => useSyncExternalStore(subscribe, snapshot),
  };
  return mod;
});
import * as migrationStore from "../utils/migrationStore";

// saveSortMigrationStore — same listener-array pattern.
const saveSortListeners: Array<() => void> = [];
let currentSaveSortState: SaveSortMigrationStatus = { pending: false };
vi.mock("../utils/saveSortMigrationStore", () => {
  const subscribe = (cb: () => void) => mod.onSaveSortMigrationChange(cb);
  const snapshot = () => mod.getSaveSortMigrationState();
  const mod = {
    getSaveSortMigrationState: vi.fn(() => currentSaveSortState),
    setSaveSortMigrationStatus: vi.fn((s: SaveSortMigrationStatus) => {
      currentSaveSortState = s;
      saveSortListeners.forEach((fn) => fn());
    }),
    onSaveSortMigrationChange: vi.fn((cb: () => void) => {
      saveSortListeners.push(cb);
      return () => {
        const i = saveSortListeners.indexOf(cb);
        if (i >= 0) saveSortListeners.splice(i, 1);
      };
    }),
    useSaveSortMigrationState: () => useSyncExternalStore(subscribe, snapshot),
  };
  return mod;
});
import * as saveSortMigrationStore from "../utils/saveSortMigrationStore";

vi.mock("../utils/syncManager", () => ({
  requestSyncCancel: vi.fn(),
  reconcileStaleShortcuts: vi.fn().mockResolvedValue(undefined),
  isCancelRequested: vi.fn().mockReturnValue(false),
  resetSyncCancel: vi.fn(),
}));

// Local @decky/ui re-mock — global stub lacks ProgressBar (used to render sync
// + download progress). Mirror the rest with thin pass-throughs + a vi.fn
// showModal so we can capture ConfirmModal calls.
vi.mock("@decky/ui", async () => {
  type AnyProps = Record<string, unknown> & { children?: unknown };
  const { createElement: ce } = await import("react");
  const passthrough = (tag: string) => (p: AnyProps) => ce(tag, {}, p.children as never);
  return {
    PanelSection: (p: AnyProps & { title?: unknown }) =>
      ce(
        "section",
        { "data-testid": "panel-section", "data-title": typeof p.title === "string" ? p.title : undefined },
        typeof p.title === "string" ? ce("h2", { "data-testid": "panel-title" }, p.title) : null,
        p.children as never,
      ),
    PanelSectionRow: passthrough("div"),
    // Focusable wrappers around read-only rows (info fields, banners, progress) —
    // pass children through a plain div so the wrapped content stays queryable.
    Focusable: passthrough("div"),
    // Mirrors the shared stub in src/test-setup.ts: the description rides in a
    // SIBLING span, never inside the button, so it stays assertable (a dropped
    // prop would make every assertion of its ABSENCE vacuously true) without
    // leaking into the button's own textContent, which is how tests here identify
    // buttons by label.
    ButtonItem: ({
      children,
      onClick,
      disabled,
      description,
    }: AnyProps & { onClick?: () => void; disabled?: boolean; description?: unknown }) =>
      ce(
        "div",
        null,
        ce("button", { onClick, disabled }, children as never),
        description == null ? null : ce("span", { "data-testid": "button-desc" }, description as never),
      ),
    // `onActivate` is what makes a focusable Field a stop that ACTS — the Last
    // sync row. Steam fires it from A and from a click, so the stub surfaces it
    // as a click handler, plus a marker attribute so its ABSENCE is assertable
    // (a dropped prop would make that assertion vacuous).
    // `focusable` renders `tabindex="0"`, which is what makes a row carrying no
    // control of its own a stop at all — the three status rows are exactly that,
    // and the rule that places entry focus reads the DOM for those stops.
    Field: (p: AnyProps & { label?: unknown; description?: unknown; onActivate?: () => void; focusable?: boolean }) =>
      ce(
        "div",
        {
          "data-testid": "field",
          "data-activate": p.onActivate ? "true" : undefined,
          onClick: p.onActivate,
          tabIndex: p.focusable ? 0 : undefined,
        },
        ce("span", { "data-testid": "field-label" }, p.label as never),
        ce("span", { "data-testid": "field-desc" }, p.description as never),
        p.children as never,
      ),
    ToggleField: (
      p: AnyProps & {
        checked?: boolean;
        onChange?: (v: boolean) => void;
        label?: unknown;
      },
    ) =>
      ce(
        "div",
        { "data-testid": "toggle" },
        ce("input", {
          type: "checkbox",
          "data-testid": "toggle-input",
          checked: p.checked ?? false,
          onChange: (e: { target: { checked: boolean } }) => p.onChange?.(e.target.checked),
        }),
        typeof p.label === "string" ? p.label : null,
      ),
    Spinner: () => ce("div", { "data-testid": "spinner" }),
    DialogButton: ({ children, onClick, disabled }: AnyProps & { onClick?: () => void; disabled?: boolean }) =>
      ce("button", { "data-testid": "dialog-button", onClick, disabled }, children as never),
    ConfirmModal: (
      p: AnyProps & {
        strTitle?: string;
        strDescription?: string;
        strOKButtonText?: string;
        strCancelButtonText?: string;
        onOK?: () => void;
        onCancel?: () => void;
      },
    ) => ce("div", { "data-testid": "confirm-modal" }, p.children as never),
    ProgressBar: (p: AnyProps & { nProgress?: number; indeterminate?: boolean }) =>
      ce(
        "div",
        { "data-testid": "progress" },
        ce("span", { "data-testid": "progress-progress" }, String(p.nProgress)),
        ce("span", { "data-testid": "progress-indeterminate" }, String(p.indeterminate)),
      ),
    showModal: vi.fn(),
  };
});

// -----------------------------------------------------------------------------
// Helpers
// -----------------------------------------------------------------------------

const flushAsync = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

/** A promise plus the handle to settle it, so a test can hold a backend read
 *  open across an event and decide when — and in which order — it answers. */
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

function defaultSettings(): PluginSettings {
  return {
    romm_url: "https://romm.local",
    has_token: true,
    steam_input_mode: "default",
    sgdb_api_key_masked: "",
    log_level: "warn",
    romm_allow_insecure_ssl: false,
  };
}

function defaultStats(): SyncStats {
  return {
    last_sync: null,
    platforms: 0,
    collections: 0,
    roms: 0,
    total_shortcuts: 0,
  };
}

function buttonByExactText(container: HTMLElement, text: string): HTMLButtonElement | null {
  const btn = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === text);
  return (btn as HTMLButtonElement | undefined) ?? null;
}

function lastConfirmModalProps<T = Record<string, unknown>>(): T | null {
  const calls = vi.mocked(showModal).mock.calls;
  if (calls.length === 0) return null;
  const el = calls[calls.length - 1]?.[0] as ReactElement<T> | undefined;
  return el?.props ?? null;
}

/** What the conditional slot says, or `null` where there is no slot at all. */
function slotLabel(container: HTMLElement): string | null {
  return container.querySelector('[data-testid="sync-slot-label"]')?.textContent ?? null;
}

/** The number beside it — the step counter of a run, or a preview's counts. */
function slotValue(container: HTMLElement): string | null {
  return container.querySelector('[data-testid="sync-slot-value"]')?.textContent ?? null;
}

function fieldLabels(container: HTMLElement): string[] {
  return Array.from(container.querySelectorAll('[data-testid="field-label"]')).map((n) => n.textContent);
}

// -----------------------------------------------------------------------------
// Tests
// -----------------------------------------------------------------------------

describe("MainPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    migrationListeners.length = 0;
    saveSortListeners.length = 0;
    currentMigrationState = { pending: false };
    currentSaveSortState = { pending: false };
    setDownloads([]);
    // Clear the module-level live-ETA estimator so a prior test's run never bleeds
    // into the next (its state persists across renders like the other stores).
    resetEta();
    // The connection probe outlives the panel by design (#1730), so its verdict
    // survives unmount and would carry a prior test's answer into the next.
    resetConnectionProbeForTests();
    // Same for the sync stats and session budget: both the stored answers and
    // any read still open outlive the render that issued them.
    resetSyncStatsStoreForTests();
    // The pending preview outlives the panel by design — that is the whole point
    // of the store — so a card one test leaves standing would render over the
    // next test's idle page.
    resetPendingPreviewStoreForTests();
    // The whole sync-progress store, not just an idle frame: these cases reuse
    // one run id, and a run this store has seen END can never be put back in
    // flight.
    resetSyncProgressStoreForTests();

    // Re-stub useVersionError (resetAllMocks wiped it).
    vi.mocked(useVersionError).mockReturnValue(null);

    // Re-stub isCancelRequested (resetAllMocks wiped the module-mock return
    // value). Defaults to false so the normal preview flow runs; the
    // RC-CANCEL-PREVIEW test flips it true (#1202).
    vi.mocked(syncManager.isCancelRequested).mockReturnValue(false);

    // Re-stub migrationStore impls.
    vi.mocked(migrationStore.getMigrationState).mockImplementation(() => currentMigrationState);
    vi.mocked(migrationStore.setMigrationStatus).mockImplementation((s: MigrationStatus) => {
      currentMigrationState = s;
      migrationListeners.forEach((fn) => fn());
    });
    vi.mocked(migrationStore.onMigrationChange).mockImplementation((cb: () => void) => {
      migrationListeners.push(cb);
      return () => {
        const i = migrationListeners.indexOf(cb);
        if (i >= 0) migrationListeners.splice(i, 1);
      };
    });

    // Re-stub saveSortMigrationStore impls.
    vi.mocked(saveSortMigrationStore.getSaveSortMigrationState).mockImplementation(() => currentSaveSortState);
    vi.mocked(saveSortMigrationStore.setSaveSortMigrationStatus).mockImplementation((s: SaveSortMigrationStatus) => {
      currentSaveSortState = s;
      saveSortListeners.forEach((fn) => fn());
    });
    vi.mocked(saveSortMigrationStore.onSaveSortMigrationChange).mockImplementation((cb: () => void) => {
      saveSortListeners.push(cb);
      return () => {
        const i = saveSortListeners.indexOf(cb);
        if (i >= 0) saveSortListeners.splice(i, 1);
      };
    });

    // Default backend mocks — tests override per case.
    vi.mocked(backend.refreshMigrationState).mockResolvedValue({
      retrodeck: { pending: false },
      save_sort: { pending: false },
    });
    vi.mocked(backend.getSyncStats).mockResolvedValue(defaultStats());
    vi.mocked(backend.getSessionBudgetStatus).mockResolvedValue({
      success: true,
      rss_kb: null,
      warn_kb: 1_800_000,
      ceiling_kb: 2_200_000,
      cliff_kb: 2_450_000,
      memory_delta_kb: null,
      resume_ready: null,
      run_done_items: null,
      run_total_items: null,
    });
    vi.mocked(backend.testConnection).mockResolvedValue({
      success: true,
      message: "",
    });
    vi.mocked(backend.getSettings).mockResolvedValue(defaultSettings());
    vi.mocked(backend.startSync).mockResolvedValue({
      success: true,
      message: "",
    });
    vi.mocked(backend.syncPreview).mockResolvedValue({
      success: true,
      summary: {
        new_count: 0,
        changed_count: 0,
        unchanged_count: 0,
        remove_count: 0,
        disabled_platform_remove_count: 0,
      },
      new_names: [],
      changed_names: [],
      preview_id: "p1",
    });
    vi.mocked(backend.syncApplyDelta).mockResolvedValue({
      success: true,
      message: "",
    });
    vi.mocked(backend.syncCancelPreview).mockResolvedValue({
      success: true,
      message: "",
    });
    // Nothing staged is the default; the restore tests opt into a preview.
    vi.mocked(backend.getPendingPreview).mockResolvedValue({ success: true, preview: null });
    vi.mocked(backend.getSyncStatus).mockResolvedValue({
      running: false,
      stage: "",
      current: 0,
      total: 0,
      message: "",
    });
    vi.mocked(backend.getRetroDeckStatus).mockResolvedValue({
      status: "ok",
      config_path: "/cfg/retrodeck.json",
      resolved_home: "/home/deck/retrodeck",
    });
    vi.mocked(backend.cancelSync).mockResolvedValue({
      success: true,
      message: "Cancelled",
    });
    vi.mocked(backend.clearSyncCache).mockResolvedValue({
      success: true,
      message: "Cleared",
    });
    vi.mocked(backend.fixRetroarchInputDriver).mockResolvedValue({
      success: true,
      message: "Fixed",
    });

    // Reset version error spy + connectionState side-channel.
    connectionState.setVersionError(null);
  });

  // ===========================================================================
  // A. Top-level render gating
  // ===========================================================================
  describe("top-level render gating", () => {
    it("renders only VersionErrorCard when useVersionError returns a message", async () => {
      vi.mocked(useVersionError).mockReturnValue("server too old");
      const { queryByTestId } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(queryByTestId("version-error-card")).not.toBeNull();
      expect(queryByTestId("migration-blocked-page")).toBeNull();
      expect(queryByTestId("panel-section")).toBeNull();
    });

    it("renders only MigrationBlockedPage when migration.pending=true", async () => {
      currentMigrationState = { pending: true };
      vi.mocked(backend.refreshMigrationState).mockResolvedValue({
        retrodeck: { pending: true },
        save_sort: { pending: false },
      });
      const { queryByTestId } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(queryByTestId("migration-blocked-page")).not.toBeNull();
      expect(queryByTestId("version-error-card")).toBeNull();
    });

    it("renders the panel without any section headings, blocks divided by rules", async () => {
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // No headings anywhere — the thin block separator is the only boundary,
      // between the status block and the menu.
      expect(container.querySelectorAll('[data-testid="panel-title"]')).toHaveLength(0);
      expect(container.querySelectorAll('[data-testid="block-separator"]')).toHaveLength(1);
    });

    // happy-dom has no gamepad and no nav tree, so what these two pin is the
    // CHOICE of element. That the reader sees the ring on it, and what Steam
    // scrolls to bring it into view, is the device round's to settle.
    it("opens on the menu's Sync entry rather than the status row the body starts with", async () => {
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      // The router's rule, run over exactly what it is handed: the plugin's own
      // content, with Decky's panel title and back arrow outside it.
      expect(placeEntryFocus(container, pageEntryStop)).toBe(true);

      const focused = document.activeElement as HTMLElement | null;
      expect(focused?.tagName).toBe("BUTTON");
      expect(focused?.textContent).toBe("Sync");
      // Non-vacuous, and the whole reason the entry is declared rather than
      // found: the body's own first stop is a status row that acts on nothing,
      // so opening there spends the reader's first press on a move.
      expect(firstBodyStop(container)?.querySelector('[data-testid="field-label"]')?.textContent).toBe("Connection");
    });

    it("opens there with a notice's own button on screen, which no button-first rule could", async () => {
      // Main's first BUTTON depends on which condition is showing, so a
      // button-first rule would open the panel on the save-sort notice today
      // and somewhere else tomorrow.
      currentSaveSortState = { pending: true, saves_count: 3 };
      vi.mocked(backend.refreshMigrationState).mockResolvedValue({
        retrodeck: { pending: false },
        save_sort: { pending: true, saves_count: 3 },
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      const noticeButton = buttonByExactText(container, "Go to Settings");
      expect(noticeButton).not.toBeNull();
      expect(container.querySelector("button")).toBe(noticeButton);
      expect(pageEntryStop(container)?.textContent).toBe("Sync");
    });
  });

  // ===========================================================================
  // B. Mount useEffect — initial fetches
  // ===========================================================================
  describe("mount useEffect", () => {
    it("calls refreshMigrationState and pushes the result into both stores", async () => {
      const retrodeck: MigrationStatus = { pending: false, roms_count: 1 };
      const saveSort: SaveSortMigrationStatus = { pending: false };
      vi.mocked(backend.refreshMigrationState).mockResolvedValue({
        retrodeck,
        save_sort: saveSort,
      });
      render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(vi.mocked(migrationStore.setMigrationStatus)).toHaveBeenCalledWith(retrodeck);
      expect(vi.mocked(saveSortMigrationStore.setSaveSortMigrationStatus)).toHaveBeenCalledWith(saveSort);
    });

    it("logs the failure when refreshMigrationState rejects", async () => {
      vi.mocked(backend.refreshMigrationState).mockRejectedValue(new Error("boom"));
      const logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to refresh migration state"));
      logSpy.mockRestore();
    });

    it("populates stats from getSyncStats", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        roms: 42,
        platforms: 3,
        collections: 2,
        last_sync: new Date(Date.now() - 30_000).toISOString(),
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // Pin the joined one-line library form (order, "·" separators, plural
      // forms). The stat counts bound shortcuts (sibling groups), so the label
      // says games, not ROMs (#1298 audit).
      expect(container.textContent).toContain("42 games · 3 platforms · 2 collections");
    });

    it("testConnection success sets connected=true and clears versionError", async () => {
      vi.mocked(backend.testConnection).mockResolvedValue({
        success: true,
        message: "",
      });
      const setVerSpy = vi.spyOn(connectionState, "setVersionError");
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(container.textContent).toContain("Connected");
      expect(setVerSpy).toHaveBeenCalledWith(null);
      setVerSpy.mockRestore();
    });

    it("testConnection reason='version_error' surfaces r.message via setVersionError", async () => {
      vi.mocked(backend.testConnection).mockResolvedValue({
        success: false,
        message: "server out of date",
        reason: "version_error",
      });
      const setVerSpy = vi.spyOn(connectionState, "setVersionError");
      render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(setVerSpy).toHaveBeenCalledWith("server out of date");
      setVerSpy.mockRestore();
    });

    it("testConnection success=false (no version_error) sets connected=false and clears versionError", async () => {
      vi.mocked(backend.testConnection).mockResolvedValue({
        success: false,
        message: "auth failed",
      });
      const setVerSpy = vi.spyOn(connectionState, "setVersionError");
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(container.textContent).toContain("Not connected");
      expect(setVerSpy).toHaveBeenCalledWith(null);
      setVerSpy.mockRestore();
    });

    it("getSettings retroarch_input_check renders the warning section", async () => {
      vi.mocked(backend.getSettings).mockResolvedValue({
        ...defaultSettings(),
        retroarch_input_check: { warning: true, current: "sdl2" },
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(container.textContent).toContain("RetroArch: input_driver issue");
    });

    it("getSettings without retroarch_input_check does NOT render the warning", async () => {
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(container.textContent).not.toContain("RetroArch: input_driver");
    });

    it("recovers in-flight sync state from getSyncStatus() on mount", async () => {
      // Backend is authoritative: the mount query returns a live run, so the
      // in-flight UI is shown even though the event-fed store was idle.
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "fetching",
        message: "Fetching library...",
        step: 1,
        totalSteps: 5,
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // In-flight: the conditional slot and Cancel Sync are both there, and the
      // slot carries the recovered run's step counter.
      expect(buttonByExactText(container, "Cancel Sync")).not.toBeNull();
      expect(slotValue(container)).toBe("1 of 5");
    });

    it("logs the failure when getSyncStatus rejects on mount", async () => {
      vi.mocked(backend.getSyncStatus).mockRejectedValue(new Error("offline"));
      const logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to query sync status"));
      // Falls back to the quiet page — no slot, and nothing to cancel.
      expect(slotLabel(container)).toBeNull();
      expect(buttonByExactText(container, "Cancel Sync")).toBeNull();
      logSpy.mockRestore();
    });

    it("logs the failure when getSyncStats rejects on mount", async () => {
      vi.mocked(backend.getSyncStats).mockRejectedValue(new Error("boom"));
      const logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to load sync stats"));
      logSpy.mockRestore();
    });

    it("keeps the connection row in 'Checking…' after a single testConnection rejection (retrying, not failed) (#1045)", async () => {
      // A lone rejection is treated as transient — the probe retries rather than
      // declaring the backend dead. Before any backoff elapses (only microtasks
      // flushed) the row must still read "Checking…", never the failure state.
      vi.mocked(backend.testConnection).mockRejectedValue(new Error("net"));
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(container.textContent).toContain("Checking...");
      expect(container.textContent).not.toContain("Backend error");
    });

    it("logs the failure when getSettings rejects on mount", async () => {
      vi.mocked(backend.getSettings).mockRejectedValue(new Error("io"));
      const logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to load settings"));
      logSpy.mockRestore();
    });
  });

  // ===========================================================================
  // C. Store subscribers — subscribe + cleanup + re-render on notify
  // ===========================================================================
  describe("store subscribers", () => {
    it("subscribes to onMigrationChange on mount, unsubscribes on unmount", async () => {
      const { unmount } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(migrationListeners.length).toBe(1);
      unmount();
      expect(migrationListeners.length).toBe(0);
    });

    it("subscribes to onSaveSortMigrationChange on mount, unsubscribes on unmount", async () => {
      const { unmount } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(saveSortListeners.length).toBe(1);
      unmount();
      expect(saveSortListeners.length).toBe(0);
    });

    it("re-renders MigrationBlockedPage when migration store flips to pending", async () => {
      const { queryByTestId } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // Initially: normal panel
      expect(queryByTestId("migration-blocked-page")).toBeNull();

      await act(async () => {
        vi.mocked(migrationStore.setMigrationStatus)({ pending: true });
      });

      expect(queryByTestId("migration-blocked-page")).not.toBeNull();
    });

    it("re-renders the save-sort migration banner when saveSort store flips to pending", async () => {
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(container.textContent).not.toContain("RetroArch save sorting changed");

      await act(async () => {
        vi.mocked(saveSortMigrationStore.setSaveSortMigrationStatus)({
          pending: true,
          saves_count: 7,
        });
      });

      expect(container.textContent).toContain("RetroArch save sorting changed");
      expect(container.textContent).toContain("7 save file(s) to migrate");
    });
  });

  // ===========================================================================
  // D. ConnectionIndicator — 4 states (covered via top-level rendering)
  // ===========================================================================
  describe("ConnectionIndicator", () => {
    it("connected=null (testConnection never resolves) renders 'Checking...' + Spinner", async () => {
      vi.mocked(backend.testConnection).mockImplementation(
        () =>
          new Promise(() => {
            /* never */
          }),
      );
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(container.textContent).toContain("Checking...");
      expect(container.querySelector('[data-testid="spinner"]')).not.toBeNull();
    });

    it("connected=true renders 'Connected'", async () => {
      vi.mocked(backend.testConnection).mockResolvedValue({ success: true, message: "" });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(container.textContent).toContain("Connected");
      expect(container.textContent).not.toContain("Not connected");
    });

    it("connected=false renders 'Not connected'", async () => {
      vi.mocked(backend.testConnection).mockResolvedValue({ success: false, message: "" });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(container.textContent).toContain("Not connected");
    });

    // A failed probe carries the backend's {reason, message}; the row shows a
    // specific label instead of a bare "Not connected". version_error is not
    // exercised here — a version failure short-circuits the whole panel to the
    // VersionErrorCard (covered separately); the label mapping is covered by the
    // direct-render cases below.
    it.each([
      ["auth_failed", "401 Unauthorized", "Sign-in rejected"],
      ["server_unreachable", "timed out", "Server unreachable"],
      ["config_error", "No server URL configured", "No server URL"],
      ["config_error", "Not signed in — sign in to RomM first", "Not signed in"],
      ["config_error", "some other config issue", "Not connected"],
    ] as const)("a failed probe with reason=%s renders %s", async (reason, message, label) => {
      vi.mocked(backend.testConnection).mockResolvedValue({ success: false, reason, message });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(container.textContent).toContain(label);
    });
  });

  // ===========================================================================
  // D1. ConnectionIndicator — failure label mapping (direct render)
  // ===========================================================================
  describe("ConnectionIndicator failure labels", () => {
    it.each([
      ["auth_failed", "", "Sign-in rejected"],
      ["server_unreachable", "", "Server unreachable"],
      ["version_error", "server too old", "Unsupported RomM version"],
      ["config_error", "No server URL configured", "No server URL"],
      ["config_error", "Not signed in — sign in to RomM first", "Not signed in"],
      ["config_error", "unclassified config problem", "Not connected"],
      ["unknown", "", "Not connected"],
    ] as const)("reason=%s / message=%s → %s", (reason, message, label) => {
      const { container } = render(<ConnectionIndicator connected={false} failure={{ reason, message }} />);
      expect(container.textContent).toContain(label);
    });

    it("falls back to 'Not connected' when no failure detail is present", () => {
      const { container } = render(<ConnectionIndicator connected={false} failure={null} />);
      expect(container.textContent).toContain("Not connected");
    });

    it("still renders the unchanged Connected / Checking… / Backend error states", () => {
      const connected = render(<ConnectionIndicator connected={true} />);
      expect(connected.container.textContent).toContain("Connected");
      const checking = render(<ConnectionIndicator connected={null} />);
      expect(checking.container.textContent).toContain("Checking...");
      const failed = render(<ConnectionIndicator connected="backend_failed" />);
      expect(failed.container.textContent).toContain("Backend error");
    });
  });

  // ===========================================================================
  // D2. Backend bootstrap failure — the retry-exhausted failure state (#1045)
  // ===========================================================================
  describe("backend bootstrap failure (#1045)", () => {
    it("shows 'Backend error' only when the backend itself is dead (testConnection AND getSettings both fail)", async () => {
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
      try {
        // Bootstrap aborted: the whole RPC bridge is dead, so BOTH the
        // test_connection probes AND the get_settings liveness ping fail.
        vi.mocked(backend.testConnection).mockRejectedValue(new Error("backend down"));
        vi.mocked(backend.getSettings).mockRejectedValue(new Error("backend down"));
        const errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
        const { container } = render(<MainPage onNavigate={vi.fn()} />);
        // Drive the full retry schedule (2+5+10+15+20s of backoff) + the ping.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(60_000);
        });
        // The row lands on the explicit failure — not an eternal "Checking…"
        // spinner (the #1045 bug), and not the false "Not connected".
        expect(container.textContent).toContain("Backend error");
        expect(container.textContent).toContain("Plugin backend failed to start — check Decky logs.");
        expect(container.textContent).not.toContain("Checking...");
        expect(container.textContent).not.toContain("Not connected");
        // Non-vacuous catch coverage: the dead-backend branch logs the liveness
        // ping failure to console.error (logError itself would hang here).
        expect(errSpy).toHaveBeenCalledWith(
          expect.stringContaining("backend RPC bridge unreachable"),
          expect.anything(),
        );
        errSpy.mockRestore();
      } finally {
        vi.useRealTimers();
      }
    });

    it("shows 'Not connected' (NOT 'Backend error') when the backend is alive but the server is unreachable-by-timeout", async () => {
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
      try {
        // Healthy backend, hanging RomM server: test_connection never answers
        // within any per-attempt deadline (the backend heartbeat outlives it),
        // but the get_settings liveness ping resolves — the backend IS alive.
        vi.mocked(backend.testConnection).mockImplementation(
          () =>
            new Promise(() => {
              /* never resolves — server round-trip hangs past every deadline */
            }),
        );
        vi.mocked(backend.getSettings).mockResolvedValue(defaultSettings());
        const { container } = render(<MainPage onNavigate={vi.fn()} />);
        // Drive every 5s per-attempt timeout + backoff to exhaustion, then the ping.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(90_000);
        });
        // Truthful state for an unreachable server — the backend didn't fail.
        expect(container.textContent).toContain("Not connected");
        expect(container.textContent).not.toContain("Backend error");
        expect(container.textContent).not.toContain("Checking...");
      } finally {
        vi.useRealTimers();
      }
    });

    it("recovers to 'Connected' when a retry succeeds after a slow start (no false backend-failure)", async () => {
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
      try {
        // The backend is merely slow: the first two probes reject, the third
        // resolves. The row must recover, never showing the failure state.
        vi.mocked(backend.testConnection)
          .mockRejectedValueOnce(new Error("starting"))
          .mockRejectedValueOnce(new Error("starting"))
          .mockResolvedValue({ success: true, message: "" });
        const { container } = render(<MainPage onNavigate={vi.fn()} />);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(10_000);
        });
        expect(container.textContent).toContain("Connected");
        expect(container.textContent).not.toContain("Backend error");
      } finally {
        vi.useRealTimers();
      }
    });
  });

  // ===========================================================================
  // E. Module helpers — exercised via rendered output
  // ===========================================================================
  describe("formatBytes (via active download bytes caption)", () => {
    async function renderWithActiveDownload(bytes: number, total: number): Promise<HTMLElement> {
      const item: DownloadItem = {
        rom_id: 1,
        rom_name: "Test ROM",
        platform_name: "Test Platform",
        file_name: "test.bin",
        status: "downloading",
        progress: bytes,
        bytes_downloaded: bytes,
        total_bytes: total,
        resumable: false,
      };
      setDownloads([item]);
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      // The store is seeded before render, so the subscription has it from the
      // first pass — only the mount useEffect's microtasks need flushing.
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      return container;
    }

    beforeEach(() => {
      vi.useFakeTimers({
        toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout"],
      });
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("renders bytes < 1024 as '<n> B'", async () => {
      const c = await renderWithActiveDownload(512, 1024);
      const bytes = c.querySelector('[data-testid="dl-bytes"]');
      expect(bytes?.textContent).toContain("512 B");
      expect(bytes?.textContent).toContain("1.0 KB");
    });

    it("renders bytes in MB range with 1 decimal", async () => {
      const c = await renderWithActiveDownload(2 * 1024 * 1024, 4 * 1024 * 1024);
      const bytes = c.querySelector('[data-testid="dl-bytes"]');
      expect(bytes?.textContent).toContain("2.0 MB");
      expect(bytes?.textContent).toContain("4.0 MB");
    });

    it("renders bytes in GB range with 2 decimals", async () => {
      const c = await renderWithActiveDownload(Math.round(1.5 * 1024 * 1024 * 1024), 2 * 1024 * 1024 * 1024);
      const bytes = c.querySelector('[data-testid="dl-bytes"]');
      expect(bytes?.textContent).toContain("1.50 GB");
      expect(bytes?.textContent).toContain("2.00 GB");
    });

    it("renders only the bytes_downloaded value when total_bytes is 0", async () => {
      const c = await renderWithActiveDownload(700, 0);
      const bytes = c.querySelector('[data-testid="dl-bytes"]');
      expect(bytes?.textContent).toBe("700 B");
    });
  });

  describe("Last sync field", () => {
    function lastSyncText(container: HTMLElement): string | null {
      const labels = Array.from(container.querySelectorAll('[data-testid="field-label"]'));
      const idx = labels.findIndex((n) => n.textContent === "Last sync");
      if (idx < 0) return null;
      // Field's children contains the <span> for the value text.
      const field = labels[idx]?.parentElement;
      return field?.textContent ?? null;
    }

    it("renders 'Never' when stats.last_sync is null", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        roms: 0,
        last_sync: null,
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(lastSyncText(container)).toContain("Never");
    });

    it("falls back to the raw value when last_sync cannot be parsed", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        last_sync: "not-a-timestamp",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // Neither `new Date` nor `Date.parse` throws on an unparseable string —
      // they yield an Invalid Date and NaN. A formatter that does the arithmetic
      // anyway reaches its last branch with NaN and renders it, so the second
      // assertion is the one that pins the bug rather than the fallback shape.
      expect(lastSyncText(container)).toContain("not-a-timestamp");
      expect(lastSyncText(container)).not.toContain("NaN");
    });

    it("renders 'Just now' for a sync within the last minute", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        last_sync: new Date(Date.now() - 5_000).toISOString(),
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(lastSyncText(container)).toContain("Just now");
    });

    it("renders 'Xm ago' for a sync less than 60m ago", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        last_sync: new Date(Date.now() - 5 * 60_000).toISOString(),
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(lastSyncText(container)).toContain("5m ago");
    });

    it("renders 'Xh ago' for a sync less than 24h ago", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        last_sync: new Date(Date.now() - 3 * 60 * 60_000).toISOString(),
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(lastSyncText(container)).toContain("3h ago");
    });

    it("renders 'Xd ago' for a sync more than 24h ago", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        last_sync: new Date(Date.now() - 4 * 24 * 60 * 60_000).toISOString(),
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(lastSyncText(container)).toContain("4d ago");
    });

    it("states the outcome and the age when only last_attempt is set (never completed) (#1318)", async () => {
      // A cancelled/crashed run with no completed run ever — must NOT read "Never".
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        last_sync: null,
        last_attempt: { finished_at: new Date(Date.now() - 10 * 60_000).toISOString(), status: "cancelled" },
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(lastSyncText(container)).toContain("cancelled 10m ago");
      expect(lastSyncText(container)).not.toContain("Never");
    });

    it("states an interrupted run the same way (crash-resume)", async () => {
      // A crash-resumed run reports the "interrupted" terminal status the backend
      // now also emits — the status string is rendered verbatim.
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        last_sync: null,
        last_attempt: { finished_at: new Date(Date.now() - 3 * 60 * 60_000).toISOString(), status: "interrupted" },
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(lastSyncText(container)).toContain("interrupted 3h ago");
      expect(lastSyncText(container)).not.toContain("Never");
    });

    it("states an errored run, which the Sync page will still refuse to resume", async () => {
      // Reporting a run and offering to continue it are different questions:
      // `syncResumeState` answers the second one and refuses this status.
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        last_sync: null,
        last_attempt: { finished_at: new Date(Date.now() - 45 * 60_000).toISOString(), status: "errored" },
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(lastSyncText(container)).toContain("errored 45m ago");
    });

    it("falls back to the raw attempt timestamp rather than rendering NaN", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        last_sync: null,
        last_attempt: { finished_at: "not-a-timestamp", status: "cancelled" },
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(lastSyncText(container)).toContain("cancelled not-a-timestamp");
      expect(lastSyncText(container)).not.toContain("NaN");
    });

    it("renders both the last_sync time and a subtle attempt line when both exist", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        last_sync: new Date(Date.now() - 5 * 60_000).toISOString(),
        last_attempt: { finished_at: new Date(Date.now() - 2 * 60_000).toISOString(), status: "cancelled" },
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // Primary line: the completed run's relative time. Second line: the newer
      // attempt that did not complete, stated as its outcome and its age.
      expect(lastSyncText(container)).toContain("5m ago");
      expect(lastSyncText(container)).toContain("cancelled 2m ago");
    });

    it("renders 'Never' when neither last_sync nor last_attempt is present", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        last_sync: null,
        last_attempt: null,
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(lastSyncText(container)).toContain("Never");
    });
  });

  describe("paused-run notice (#1383, #1814)", () => {
    // The card with Restart Steam now and the resume lives on the Sync page —
    // its home. What Main keeps is the notice naming the condition and the jump,
    // plus the two reads that make the notice go away again.
    function pausedStats(status: string | undefined, finishedAt = "2026-07-11T17:48:00"): SyncStats {
      return {
        ...defaultStats(),
        roms: 42,
        resumable_games: 30,
        last_attempt: status ? { finished_at: finishedAt, status: status as "paused" } : null,
      };
    }

    function budget(rssKb: number | null): SessionBudgetStatus {
      return {
        success: true,
        rss_kb: rssKb,
        warn_kb: 1_800_000,
        ceiling_kb: 2_200_000,
        cliff_kb: 2_450_000,
        memory_delta_kb: null,
        resume_ready: null,
        run_done_items: null,
        run_total_items: null,
      };
    }

    const notice = (c: HTMLElement) => c.querySelector('[data-testid="sync-paused-notice"]');

    it("names the condition and offers the jump, without the card that acts on it", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(pausedStats("paused"));
      vi.mocked(backend.getSessionBudgetStatus).mockResolvedValue(budget(2_299_000));
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(notice(container)?.textContent).toContain("Sync paused");
      expect(buttonByExactText(container, "Open Sync")).not.toBeNull();
      // The action exists only at its home: neither the card nor its button.
      expect(container.querySelector('[data-testid="budget-paused-banner"]')).toBeNull();
      expect(buttonByExactText(container, "Restart Steam now")).toBeNull();
    });

    it("shows nothing when the last run did not pause", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(pausedStats(undefined));
      vi.mocked(backend.getSessionBudgetStatus).mockResolvedValue(budget(1_900_000));
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      // A high heap after a completed run is the Sync page's business now — Main
      // carries only the paused condition, which is the one with a home.
      expect(notice(container)).toBeNull();
      expect(container.querySelector('[data-testid="budget-high-heap-banner"]')).toBeNull();
    });

    it("Open Sync goes to the page that holds the restart and the resume", async () => {
      const onNavigate = vi.fn();
      vi.mocked(backend.getSyncStats).mockResolvedValue(pausedStats("paused"));
      vi.mocked(backend.getSessionBudgetStatus).mockResolvedValue(budget(2_299_000));
      const { container } = render(<MainPage onNavigate={onNavigate} />);
      await flushAsync();

      fireEvent.click(buttonByExactText(container, "Open Sync")!);
      expect(onNavigate).toHaveBeenCalledWith("sync");
    });

    it("flips the last-attempt line + drops the notice when a newer terminal supersedes it (#39)", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(pausedStats("paused", "2026-07-11T14:41:00"));
      vi.mocked(backend.getSessionBudgetStatus).mockResolvedValue(budget(500_000));
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(notice(container)).not.toBeNull();
      expect(container.textContent).toContain("paused ");

      // The resume the user pressed is under way — the terminal frame below ends
      // THIS run, which is what provokes the stats re-read.
      await act(async () => {
        setSyncProgress({ running: true, stage: "applying", message: "Working" });
      });
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        roms: 42,
        last_attempt: { finished_at: "2026-07-11T14:45:00", status: "cancelled" },
      });
      await act(async () => {
        setSyncProgress({ running: false, stage: "cancelled", message: "Sync cancelled" });
      });
      await flushAsync();

      expect(notice(container)).toBeNull();
      expect(container.textContent).toContain("cancelled ");
      expect(container.textContent).not.toContain("paused ");
    });

    it("recovers via the paused-poll stats backstop if the terminal refetch was missed (#39)", async () => {
      // Belt-and-braces on top of the backend emit-last fix: even if no terminal
      // event reached this mount, the paused poll re-reads the stats and flips
      // once the newer terminal appears in the data.
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
      try {
        vi.mocked(backend.getSyncStats).mockResolvedValue(pausedStats("paused", "2026-07-11T14:41:00"));
        vi.mocked(backend.getSessionBudgetStatus).mockResolvedValue(budget(500_000));
        const { container } = render(<MainPage onNavigate={vi.fn()} />);
        await flushAsync();
        expect(notice(container)).not.toBeNull();

        vi.mocked(backend.getSyncStats).mockResolvedValue({
          ...defaultStats(),
          roms: 42,
          last_attempt: { finished_at: "2026-07-11T14:45:00", status: "cancelled" },
        });
        await act(async () => {
          await vi.advanceTimersByTimeAsync(10_000); // one paused-poll tick
        });

        expect(notice(container)).toBeNull();
        expect(container.textContent).toContain("cancelled ");
      } finally {
        vi.useRealTimers();
      }
    });

    it("does not poll the stats while a run is going — the run's own end re-reads them", async () => {
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
      try {
        vi.mocked(backend.getSyncStats).mockResolvedValue(pausedStats("paused"));
        render(<MainPage onNavigate={vi.fn()} />);
        await flushAsync();
        await act(async () => {
          setSyncProgress({ running: true, stage: "applying", message: "Working", runId: "r1" });
        });
        const before = vi.mocked(backend.getSyncStats).mock.calls.length;

        await act(async () => {
          await vi.advanceTimersByTimeAsync(60_000);
        });
        expect(vi.mocked(backend.getSyncStats).mock.calls).toHaveLength(before);
      } finally {
        vi.useRealTimers();
      }
    });

    it("tears the paused poll down on unmount", async () => {
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
      try {
        vi.mocked(backend.getSyncStats).mockResolvedValue(pausedStats("paused"));
        const { unmount } = render(<MainPage onNavigate={vi.fn()} />);
        await flushAsync();
        const before = vi.mocked(backend.getSyncStats).mock.calls.length;

        unmount();
        await act(async () => {
          await vi.advanceTimersByTimeAsync(30_000);
        });
        expect(vi.mocked(backend.getSyncStats).mock.calls).toHaveLength(before);
      } finally {
        vi.useRealTimers();
      }
    });
  });

  describe("the slot's coarse bar", () => {
    it("interpolates within the running unit, under the run's step counter", async () => {
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        step: 2,
        totalSteps: 5,
        current: 3,
        total: 10,
        message: "N64: 3/10",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // The slot states the coarse step counter and nothing finer — no stage
      // caption, no fine-detail line, no estimate; those are the Sync page's.
      expect(slotValue(container)).toBe("2 of 5");
      expect(container.querySelector('[data-testid="sync-fine"]')).toBeNull();
      expect(container.querySelector('[data-testid="estimate-time"]')).toBeNull();
      // Interpolated: floor (step-1)=1 plus the apply sub-slice fill — fetch and
      // covers already filled their shares, so applying starts at (F+C) and adds
      // A*(3/10) — over 5 steps (#1407).
      const within = FETCH_SHARE + COVERS_SHARE + APPLY_SHARE * (3 / 10);
      const nProgress = Number(container.querySelector('[data-testid="progress-progress"]')?.textContent);
      expect(nProgress).toBeCloseTo(((1 + within) / 5) * 100, 5);
      expect(container.querySelector('[data-testid="progress-indeterminate"]')?.textContent).toBe("false");
    });

    it("main bar interpolates a large unit's within-unit fraction (2091 items at 2/8)", async () => {
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        step: 2,
        totalSteps: 8,
        current: 450,
        total: 2091,
        message: "PSX: 450/2091",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // (step-1 + apply sub-slice fill) / totalSteps * 100, where applying fills
      // (F+C) + A*(450/2091) of the unit's slice (#1407).
      const within = FETCH_SHARE + COVERS_SHARE + APPLY_SHARE * (450 / 2091);
      const nProgress = Number(container.querySelector('[data-testid="progress-progress"]')?.textContent);
      expect(nProgress).toBeCloseTo(((1 + within) / 8) * 100, 5);
    });

    it("main bar weights units by the plan's item weights when a plan is measured (#1382)", async () => {
      // Plan weights [10, 2091, 5] — the huge PSX unit owns most of the bar, not
      // an equal 1/3 slice. The run state comes from the real syncEta module,
      // exactly as the sync_plan listener seeds it.
      beginEtaRun("run-1", [10, 2091, 5], 2106);
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        step: 2,
        totalSteps: 3,
        current: 450,
        total: 2091,
        message: "PSX: 450/2091",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // Weighted: unit 1's full weight (10) plus PSX's within-unit fill of its
      // 2091 weight. Applying fills (F+C) + A*(450/2091) of the unit (#1407), so
      // the running unit contributes within*2091 of the 2106 total.
      const within = FETCH_SHARE + COVERS_SHARE + APPLY_SHARE * (450 / 2091);
      const nProgress = Number(container.querySelector('[data-testid="progress-progress"]')?.textContent);
      expect(nProgress).toBeCloseTo(((10 + within * 2091) / 2106) * 100, 5);
    });

    it("a LEADING predicted-skip unit claims its equal index share (#1506)", async () => {
      // Unit 1 was zero-weighted as a predicted wholesale skip. #1382 gave such
      // a unit no bar width at all, on the premise that a skip is
      // instantaneous, so costing it nothing was free. #1506 is the evidence
      // that the premise is false for a LEADING skip: an empty apply delta
      // still refreshes covers and occupies real wall-clock time, so a
      // zero-width leading unit pinned the whole bar to empty while it worked.
      // It therefore claims an ordinary equal 1/totalUnits slice as a floor.
      beginEtaRun("run-1", [0, 100], 100);
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        step: 2,
        totalSteps: 2,
        current: 50,
        total: 100,
        message: "GBA: 50/100",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // The leading skip floors the bar at its 1/2 slice; unit 2's weighted
      // fill — applying at (F+C) + A*(50/100) of its 100 weight over the 100
      // total (#1407) — fills the band above that floor rather than stalling on
      // it, so the bar keeps moving through the unit that does the real work.
      const within = FETCH_SHARE + COVERS_SHARE + APPLY_SHARE * (50 / 100);
      const floor = 1 / 2;
      const nProgress = Number(container.querySelector('[data-testid="progress-progress"]')?.textContent);
      expect(nProgress).toBeCloseTo((floor + (1 - floor) * within) * 100, 5);
    });

    it("a mid-plan zero-weight unit still occupies no bar width (#1382)", async () => {
      // The #1506 floor covers only the plan's LEADING run of zero-weight
      // units. Unit 2 here follows real work, so the weighting's distribution
      // intent is untouched: the bar rests on unit 1's completed share and the
      // skipped unit adds nothing, whatever its within-unit fill reads.
      beginEtaRun("run-1", [100, 0, 100], 200);
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        step: 2,
        totalSteps: 3,
        current: 50,
        total: 100,
        message: "GBA: 50/100",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // Unit 1's 100 of the 200 total, and nothing from the zero-weight unit 2.
      const nProgress = Number(container.querySelector('[data-testid="progress-progress"]')?.textContent);
      expect(nProgress).toBeCloseTo(50, 5);
    });

    it("falls back to index weighting when the plan's unit count mismatches the run (stale plan)", async () => {
      // A leftover plan from another run (2 units) cannot apportion an 8-unit
      // run — the bar falls back to the equal-slice interpolation.
      beginEtaRun("run-other", [5, 5], 10);
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        step: 2,
        totalSteps: 8,
        current: 450,
        total: 2091,
        message: "PSX: 450/2091",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // Index fallback: (step-1 + apply sub-slice fill) / totalSteps (#1407).
      const within = FETCH_SHARE + COVERS_SHARE + APPLY_SHARE * (450 / 2091);
      const nProgress = Number(container.querySelector('[data-testid="progress-progress"]')?.textContent);
      expect(nProgress).toBeCloseTo(((1 + within) / 8) * 100, 5);
    });

    it("main bar rests at the unit floor during fetch when no sub-stage is present (old backend)", async () => {
      // A backend that predates #1407 sends fetch frames with no subStage: they
      // carry current/total (page counters) to drive the fine line, but with no
      // sub-slice to fill the coarse bar rests at (step-1)/totalSteps — the
      // pre-#1407 behaviour, never a backwards jump at the fetch→apply boundary.
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "fetching",
        step: 2,
        totalSteps: 8,
        current: 30,
        total: 62,
        message: "Fetching GBA (page 30/62)",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // Floor only: (2-1)/8 * 100 = 12.5. The page counter (30/62) does not lift it.
      expect(container.querySelector('[data-testid="progress-progress"]')?.textContent).toBe("12.5");
    });

    it("fetch sub-stage fills within the fetch sub-slice (#1407)", async () => {
      // A fetch-phase frame (subStage "fetch") lifts the bar within the fetch
      // share only — page 30/62 → FETCH_SHARE * (30/62) above the unit floor.
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "fetching",
        subStage: "fetch",
        step: 2,
        totalSteps: 8,
        current: 30,
        total: 62,
        message: "Fetching GBA (page 30/62)",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      const within = FETCH_SHARE * (30 / 62);
      const nProgress = Number(container.querySelector('[data-testid="progress-progress"]')?.textContent);
      expect(nProgress).toBeCloseTo(((1 + within) / 8) * 100, 5);
    });

    it("covers sub-stage continues above the fetch share (#1407)", async () => {
      // A cover-phase frame (subStage "covers") starts where fetch ended
      // (FETCH_SHARE) and fills the covers share by its own current/total.
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "fetching",
        subStage: "covers",
        step: 2,
        totalSteps: 8,
        current: 500,
        total: 2000,
        message: "Preparing covers for GBA (500/2000)",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      const within = FETCH_SHARE + COVERS_SHARE * (500 / 2000);
      expect(within).toBeGreaterThan(FETCH_SHARE);
      const nProgress = Number(container.querySelector('[data-testid="progress-progress"]')?.textContent);
      expect(nProgress).toBeCloseTo(((1 + within) / 8) * 100, 5);
    });

    it("advances monotonically across a fetch → covers → apply frame sequence (#1407)", async () => {
      // Drive the running unit (step 2/8) through its three phases and assert the
      // coarse bar never decreases at any frame — the core #1407 guarantee. The
      // mount seed is a running fetch anchor so the in-flight bar renders.
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "fetching",
        step: 2,
        totalSteps: 8,
        current: 0,
        total: 0,
        message: "Fetching GBA",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      const barValue = () => Number(container.querySelector('[data-testid="progress-progress"]')?.textContent);

      const frames: SyncProgress[] = [];
      // Fetch anchor (no sub-stage) then paginated fetch frames.
      frames.push({ running: true, stage: "fetching", step: 2, totalSteps: 8, current: 0, total: 0 });
      for (let page = 1; page <= 10; page++) {
        frames.push({
          running: true,
          stage: "fetching",
          subStage: "fetch",
          step: 2,
          totalSteps: 8,
          current: page,
          total: 10,
        });
      }
      // Cover download frames.
      for (let c = 1; c <= 100; c++) {
        frames.push({
          running: true,
          stage: "fetching",
          subStage: "covers",
          step: 2,
          totalSteps: 8,
          current: c,
          total: 100,
        });
      }
      // Frontend apply frames.
      for (let a = 1; a <= 50; a++) {
        frames.push({ running: true, stage: "applying", step: 2, totalSteps: 8, current: a, total: 50 });
      }

      let previous = -1;
      for (const frame of frames) {
        act(() => setSyncProgress(frame));
        const value = barValue();
        expect(value).toBeGreaterThanOrEqual(previous);
        previous = value;
      }
      // The unit ends the apply phase at its full slice ceiling: floor + 1 slice.
      expect(previous).toBeCloseTo((2 / 8) * 100, 5);
    });

    it("main bar reads 100% during finalizing (step == totalSteps, all units done)", async () => {
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "finalizing",
        step: 8,
        totalSteps: 8,
        message: "Finalizing…",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // Terminal-ish stage keeps the full step count → 8/8 * 100 = 100 (a naive
      // (step-1) base would drop it to 87.5, jumping backwards from the last
      // unit's apply which reached 100%).
      expect(container.querySelector('[data-testid="progress-progress"]')?.textContent).toBe("100");
    });

    it("main bar goes indeterminate when totalSteps is 0", async () => {
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "fetching",
        step: 0,
        totalSteps: 0,
        message: "Fetching platforms...",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(container.querySelector('[data-testid="progress-indeterminate"]')?.textContent).toBe("true");
    });
  });

  // The merge itself stays on Main — it is the mount's own seed from
  // get_sync_status — but the fine line and the estimate it preserves are the
  // Sync page's to render, so what it preserved is read off the store.
  describe("QAM remount mid-run preserves fine progress + ETA in the store", () => {
    it("merges the store's fine fields + etaSeconds over the backend's coarse running snapshot", async () => {
      // Module store holds the in-flight run's FINE state — what a live QAM had
      // (frontend per-item updates + the sync_plan-derived ETA) before it was
      // torn down and remounted.
      setSyncProgress({
        running: true,
        stage: "applying",
        current: 1200,
        total: 3084,
        message: "PSX: 1200/3084",
        step: 2,
        totalSteps: 8,
        runId: "run-live",
        etaSeconds: 480,
      });
      // Backend snapshot for the SAME run is coarse: current/total 0, no ETA.
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        current: 0,
        total: 0,
        message: "PSX (2/8)",
        step: 2,
        totalSteps: 8,
        runId: "run-live",
      });

      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      // Coarse step counter is what Main itself shows, either way.
      expect(slotValue(container)).toBe("2 of 8");
      // The fine fields survive the remount — the backend's snapshot carried a
      // total of 0, so a blind replace would have flattened them.
      expect(getSyncProgress().message).toBe("PSX: 1200/3084");
      expect(getSyncProgress().total).toBe(3084);
      // etaSeconds is frontend-only and never in the backend snapshot; a blind
      // replace would drop it.
      expect(getSyncProgress().etaSeconds).toBe(480);
    });

    it("keeps the store's applying stage when the backend's snapshot is still the fetch anchor", async () => {
      // The backend never emits an "applying" frame (its last emit is the fetch
      // anchor), so a remount mid-apply must not let the stale "fetching" stage
      // drop the coarse-bar interpolation or flip the label.
      setSyncProgress({
        running: true,
        stage: "applying",
        current: 1200,
        total: 3084,
        message: "PSX: 1200/3084",
        step: 2,
        totalSteps: 8,
        runId: "run-live",
      });
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "fetching",
        current: 0,
        total: 0,
        message: "Fetching PSX",
        step: 2,
        totalSteps: 8,
        runId: "run-live",
      });

      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(getSyncProgress().stage).toBe("applying");
      // Interpolation stays live in the apply sub-slice: (1 + (F+C) + A*1200/3084)
      // / 8, not the 12.5% unit floor (#1407).
      const within = FETCH_SHARE + COVERS_SHARE + APPLY_SHARE * (1200 / 3084);
      const nProgress = Number(container.querySelector('[data-testid="progress-progress"]')?.textContent);
      expect(nProgress).toBeCloseTo(((1 + within) / 8) * 100, 5);
    });

    it("replaces (drops stale fine fields + ETA) when the backend reports a different run", async () => {
      setSyncProgress({
        running: true,
        stage: "applying",
        current: 1200,
        total: 3084,
        message: "PSX: 1200/3084",
        step: 2,
        totalSteps: 8,
        runId: "run-old",
        etaSeconds: 480,
      });
      // A different in-flight run — the old run's fine fields + ETA must NOT
      // bleed through into the fresh run's UI.
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "fetching",
        current: 0,
        total: 0,
        message: "Fetching library...",
        step: 0,
        totalSteps: 0,
        runId: "run-new",
      });

      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      // The fresh run's frame stands whole: stale ETA and stale fine fields
      // from the prior run are both gone (replace branch, not merge).
      expect(getSyncProgress().message).toBe("Fetching library...");
      expect(getSyncProgress().etaSeconds).toBeUndefined();
      expect(getSyncProgress().total).toBe(0);
      // No unit count yet, so the slot's bar is the indeterminate one.
      expect(container.querySelector('[data-testid="progress-indeterminate"]')?.textContent).toBe("true");
    });

    it("does not replay a stored terminal frame as a fresh completion (#1019)", async () => {
      // The last run's terminal frame is still in the store when the panel comes
      // back — the state every QAM close leaves behind. Nothing is in flight, so
      // this mount ends no run: it has nothing to tear down and nothing to
      // announce, however long ago that run finished.
      setSyncProgress({
        running: false,
        stage: "done",
        current: 0,
        total: 0,
        message: "Preview ready",
      });
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: false,
        stage: "done",
        current: 0,
        total: 0,
        message: "Preview ready",
      });

      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      // No completion line, and specifically not the affirmative green one a
      // just-finished run gets.
      expect(fieldLabels(container)).not.toContain("Preview ready");
      // The change-driven re-read is provoked by a run ENDING; only the mount's
      // own read may have gone out.
      expect(vi.mocked(backend.getSyncStats)).toHaveBeenCalledTimes(1);
      // The quiet page, not a run: a stored terminal frame arms no Cancel and
      // opens no slot.
      expect(slotLabel(container)).toBeNull();
      expect(buttonByExactText(container, "Cancel Sync")).toBeNull();
    });

    it("takes the completion wording from the run's own terminal frame, not the sync_complete merge", async () => {
      // The backend ends a run with two signals in a fixed order, and both reach
      // the store: sync_complete, which index.tsx merges as {running, stage} and
      // so carries the PREVIOUS frame's message, then the run's own terminal frame
      // with the summary. The panel must end up showing the second.
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        current: 118,
        total: 120,
        message: "PSX: 118/120",
        step: 3,
        totalSteps: 3,
        runId: "run-9",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      const statsReadsBefore = vi.mocked(backend.getSyncStats).mock.calls.length;

      // The run's last pre-terminal frame.
      await act(async () => {
        setSyncProgress({
          running: true,
          stage: "finalizing",
          current: 120,
          total: 120,
          message: "Finalizing…",
          step: 3,
          totalSteps: 3,
          runId: "run-9",
        });
        await Promise.resolve();
      });

      // 1) sync_complete, merged — inherits "Finalizing…".
      await act(async () => {
        updateSyncProgress({ running: false, stage: "done" });
        await Promise.resolve();
      });
      // 2) The run's authoritative terminal frame.
      await act(async () => {
        setSyncProgress({
          running: false,
          stage: "done",
          current: 120,
          total: 120,
          message: "Sync complete: 120 games from 3 platforms",
          step: 3,
          totalSteps: 3,
          runId: "run-9",
        });
        await Promise.resolve();
      });

      expect(fieldLabels(container)).toContain("Sync complete: 120 games from 3 platforms");
      expect(fieldLabels(container)).not.toContain("Finalizing…");
      // Still ONE run ending: the second frame corrects the wording, it does not
      // re-run the change-driven re-reads.
      expect(vi.mocked(backend.getSyncStats).mock.calls.length - statsReadsBefore).toBe(1);
      expect(slotLabel(container)).toBeNull();
    });
  });

  // The estimate itself is the Sync page's readout now, and its derivation is
  // pinned where it lives (src/utils/syncRunView.test.ts). What is measured here
  // is the subscription Main installs: a mounted instance keeps re-rendering,
  // and what it feeds the estimator with.
  describe("the store subscription Main holds", () => {
    it("a throwing earlier listener cannot starve the mounted instance's re-render (freeze contract)", async () => {
      // On-device an instance mounted before run start froze on the optimistic
      // "Applying" frame and stopped re-rendering for the rest of the run — a
      // subscriber throw aborting the store's notify loop before the re-render.
      // Register a THROWING listener BEFORE mounting so it sits earlier in the
      // store's listener array than MainPage's own subscriber: with the store's
      // per-listener try/catch reverted this earlier throw aborts notify() and
      // MainPage never re-renders, so the assertions below genuinely pin the
      // hardening (not just a happy-path re-render smoke test).
      const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});
      const unsubThrower = onSyncProgressChange(() => {
        throw new Error("earlier listener boom");
      });
      try {
        const { container } = render(<MainPage onNavigate={vi.fn()} />);
        await flushAsync();

        // The optimistic "Applying" frame an apply writes — the frame the frozen
        // instance was stuck on. Written straight into the store, because the
        // apply itself is the Sync page's press now and this test is about the
        // store's notify loop, not about who pressed what.
        await act(async () => {
          setSyncProgress({ running: true, stage: "applying", message: "Applying changes...", etaSeconds: 1000 });
        });
        expect(slotLabel(container)).not.toBeNull();

        // sync_plan listener shape — a partial update carrying only the ETA seed.
        await act(async () => {
          updateSyncProgress({ etaSeconds: 1000 });
        });

        // Per-item applying frames (syncManager processUnitShortcuts shape). Each
        // must drive a fresh render despite the earlier listener throwing on every
        // notify — the frozen instance stopped advancing here.
        await act(async () => {
          updateSyncProgress({
            running: true,
            stage: "applying",
            current: 5,
            total: 200,
            message: "PSX: 5/200",
            step: 2,
            totalSteps: 8,
          });
        });
        expect(slotValue(container)).toBe("2 of 8");

        await act(async () => {
          updateSyncProgress({ current: 6, total: 200, message: "PSX: 6/200", step: 3, totalSteps: 8 });
        });
        // The mounted instance kept re-rendering — the counter advanced.
        expect(slotValue(container)).toBe("3 of 8");
        // Non-vacuous: the earlier listener really did throw on notify (isolated
        // by the store to console.error), so the re-renders above prove isolation.
        expect(consoleSpy).toHaveBeenCalledWith("[RomM] sync-progress listener threw:", expect.any(Error));
      } finally {
        unsubThrower();
        consoleSpy.mockRestore();
      }
    });

    it("logs and keeps advancing the local mirror when the subscriber's derived work throws", async () => {
      // The subscriber's outer try/catch: the local mirror (setSyncProgress) is
      // updated FIRST and unconditionally, then the derived work runs guarded. If
      // the derived work throws, the catch must log AND the mirror must still
      // advance on every later frame — the re-render chain must not break. Inject
      // the throw by making observeApplyProgress() (called in the non-terminal
      // branch) throw on each applying frame.
      const etaSpy = vi.spyOn(syncEta, "observeApplyProgress").mockImplementation(() => {
        throw new Error("derived boom");
      });
      const logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      try {
        // Recover an in-flight applying run on mount so syncing=true (the
        // in-flight body renders) and the subscriber fires with an applying frame.
        vi.mocked(backend.getSyncStatus).mockResolvedValue({
          running: true,
          stage: "applying",
          step: 2,
          totalSteps: 8,
          current: 5,
          total: 200,
          message: "PSX: 5/200",
          runId: "run-throw",
        });
        const { container } = render(<MainPage onNavigate={vi.fn()} />);
        await flushAsync();
        // Post-catch state: the mirror advanced to the first frame despite the
        // throw, and the catch surfaced the subscriber-failure log.
        expect(slotValue(container)).toBe("2 of 8");
        expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("sync-progress subscriber failed"));

        // Subsequent frames keep advancing the local mirror — the throw on each
        // frame never breaks the re-render chain.
        await act(async () => {
          updateSyncProgress({
            running: true,
            stage: "applying",
            step: 3,
            totalSteps: 8,
            current: 6,
            total: 200,
            message: "PSX: 6/200",
          });
        });
        expect(slotValue(container)).toBe("3 of 8");

        await act(async () => {
          updateSyncProgress({
            running: true,
            stage: "applying",
            step: 4,
            totalSteps: 8,
            current: 7,
            total: 200,
            message: "PSX: 7/200",
          });
        });
        expect(slotValue(container)).toBe("4 of 8");
      } finally {
        etaSpy.mockRestore();
        logSpy.mockRestore();
      }
    });

    it("does NOT feed the live-rate estimator on a cover-refresh applying frame (#1456)", async () => {
      const etaSpy = vi.spyOn(syncEta, "observeApplyProgress");
      try {
        // Recover an in-flight applying run so the subscriber fires on applying frames.
        vi.mocked(backend.getSyncStatus).mockResolvedValue({
          running: true,
          stage: "applying",
          step: 2,
          totalSteps: 8,
          current: 5,
          total: 200,
          message: "PSX: 5/200",
          runId: "run-cover-eta",
        });
        render(<MainPage onNavigate={vi.fn()} />);
        await flushAsync();
        etaSpy.mockClear();

        // A normal shortcut-item applying frame DOES feed the estimator.
        await act(async () => {
          updateSyncProgress({ running: true, stage: "applying", step: 2, totalSteps: 8, current: 6, total: 200 });
        });
        expect(etaSpy).toHaveBeenCalledTimes(1);
        etaSpy.mockClear();

        // A cover-refresh applying frame carries a cover counter, not item
        // progress (current is unchanged) — the estimator must NOT be fed, or the
        // cover phase would distort the rate (#1456).
        await act(async () => {
          updateSyncProgress({
            running: true,
            stage: "applying",
            step: 2,
            totalSteps: 8,
            message: "PSX: covers 37/140",
            coverRefresh: true,
          });
        });
        expect(etaSpy).not.toHaveBeenCalled();
      } finally {
        etaSpy.mockRestore();
      }
    });
  });

  describe("the conditional slot (#1814)", () => {
    function previewSummary(overrides: Partial<SyncPreview["summary"]> = {}): SyncPreview {
      return {
        success: true,
        summary: {
          new_count: 13,
          changed_count: 4,
          unchanged_count: 0,
          remove_count: 0,
          disabled_platform_remove_count: 0,
          ...overrides,
        },
        new_names: [],
        changed_names: [],
        preview_id: "p-slot",
      };
    }

    it("is absent while the Sync page has nothing to report, and so is Cancel", async () => {
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(slotLabel(container)).toBeNull();
      expect(buttonByExactText(container, "Cancel Sync")).toBeNull();
      // The three status rows are all that is left of the block.
      expect(fieldLabels(container)).toContain("Connection");
      expect(fieldLabels(container)).toContain("Last sync");
    });

    it("states a preview run coarsely — a short label, the counter and the bar", async () => {
      setSyncProgress({
        running: true,
        stage: "fetching",
        step: 3,
        totalSteps: 16,
        message: "x",
        runId: "run-p",
        runKind: "preview",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      // The kind is the backend's word, carried on the frame — the stage would
      // say the same thing for an apply run's fetch phase.
      expect(slotLabel(container)).toBe("Checking for changes");
      expect(slotValue(container)).toBe("3 of 16");
      expect(container.querySelector('[data-testid="progress"]')).not.toBeNull();
    });

    it("states an apply run as Syncing, on the same fetching stage a preview uses", async () => {
      // Same stage, same counters, same shape — only the kind differs, which is
      // the whole reason it is on the wire.
      setSyncProgress({
        running: true,
        stage: "fetching",
        step: 3,
        totalSteps: 16,
        message: "x",
        runId: "run-a",
        runKind: "apply",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(slotLabel(container)).toBe("Syncing");
    });

    it("claims neither where no kind was established", async () => {
      // Unreachable while both halves ship together, and deliberately not
      // guessed: an answer nothing established is never rendered as one of the
      // two real ones.
      setSyncProgress({ running: true, stage: "fetching", step: 1, totalSteps: 4, message: "x", runId: "run-b" });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(slotLabel(container)).toBe("Sync in progress");
      expect(slotValue(container)).toBe("1 of 4");
    });

    it("takes the kind from the backend's snapshot when the QAM reloaded mid-run", async () => {
      // The case no inference can reach: the store starts empty after a reload,
      // so the mount's get_sync_status answer is the only thing that can say
      // what the run in flight is doing.
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "fetching",
        step: 2,
        totalSteps: 9,
        message: "Fetching N64",
        runId: "run-live",
        runKind: "apply",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(slotLabel(container)).toBe("Syncing");
      expect(slotValue(container)).toBe("2 of 9");
    });

    it("lets the backend's kind overlay an optimistic start that carried none", async () => {
      // The start window: a frame written before the backend claimed the run is
      // merged with the snapshot, and the kind is the backend's to state.
      setSyncProgress({ running: true, stage: "fetching", message: "Fetching library..." });
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "fetching",
        step: 1,
        totalSteps: 3,
        message: "Fetching N64",
        runId: "run-live",
        runKind: "preview",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(slotLabel(container)).toBe("Checking for changes");
    });

    it("states a pending preview's counts, with no bar — nothing is moving", async () => {
      adoptPreview(previewSummary({ remove_count: 2 }));
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(slotLabel(container)).toBe("Changes ready");
      expect(slotValue(container)).toBe("13 new · 4 updated · 2 removed");
      expect(container.querySelector('[data-testid="progress"]')).toBeNull();
      expect(buttonByExactText(container, "Cancel Sync")).toBeNull();
    });

    it("names cover work rather than showing a row of zeros", async () => {
      adoptPreview(previewSummary({ new_count: 0, changed_count: 0, cover_refresh_count: 7 }));
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(slotValue(container)).toBe("cover work only");
      expect(container.textContent).not.toContain("0 new");
    });

    it("names a collection change, the one the reader will see in Steam", async () => {
      adoptPreview(
        previewSummary({
          new_count: 0,
          changed_count: 0,
          collection_diff: { has_changes: true, added: ["Favourites"], removed: [] },
        }),
      );
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(slotValue(container)).toBe("collection changes");
    });

    it("names the collection over the covers where a preview holds both", async () => {
      // The reader sees a collection appear in Steam; a refreshed cover only
      // replaces a tile they already have.
      adoptPreview(
        previewSummary({
          new_count: 0,
          changed_count: 0,
          cover_refresh_count: 7,
          platform_collection_diff: { has_changes: true, added_count: 1, removed_count: 0 },
        }),
      );
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(slotValue(container)).toBe("collection changes");
    });

    it("does not call a re-stamp cover work — it invites a review instead", async () => {
      // What is left once the two nameable cases are taken: a platform re-stamp
      // has something for Apply to do and nothing a reader would recognise to
      // name, and it is not cover work — the page says which it is.
      adoptPreview(previewSummary({ new_count: 0, changed_count: 0, restamp_platform_count: 2 }));
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(slotValue(container)).toBe("ready to review");
    });

    it("opens the Sync page when pressed, on both of its occasions", async () => {
      const onNavigate = vi.fn();
      adoptPreview(previewSummary());
      const { container } = render(<MainPage onNavigate={onNavigate} />);
      await flushAsync();

      const row = container.querySelector('[data-testid="sync-slot-label"]')!.closest('[data-testid="field"]');
      // The marker is what makes it a focus stop that ACTS rather than a place
      // the reader can land and do nothing.
      expect(row?.getAttribute("data-activate")).toBe("true");
      await act(async () => {
        fireEvent.click(row!);
        await Promise.resolve();
      });
      expect(onNavigate).toHaveBeenCalledWith("sync");
      // The press is navigation and nothing else: the preview it names is still
      // standing, on both sides.
      expect(vi.mocked(backend.syncCancelPreview)).not.toHaveBeenCalled();
      expect(slotLabel(container)).toBe("Changes ready");
    });

    it("is the run while one is going, even with a preview held for later", async () => {
      adoptPreview(previewSummary());
      setSyncProgress({ running: true, stage: "applying", step: 1, totalSteps: 2, message: "x", runId: "run-live" });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(slotLabel(container)).not.toBe("Changes ready");
      expect(buttonByExactText(container, "Cancel Sync")).not.toBeNull();
    });

    it("gives a cancelled or interrupted run no slot of its own — Last sync states it", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        roms: 42,
        resumable_games: 30,
        last_attempt: { finished_at: new Date(Date.now() - 10 * 60_000).toISOString(), status: "cancelled" },
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(slotLabel(container)).toBeNull();
      expect(container.textContent).toContain("cancelled 10m ago");
    });
  });

  describe("what moved off Main to the Sync page (#1814)", () => {
    it("holds neither Skip Preview nor Force Full Sync any more", async () => {
      // Both had a recorded run as their condition, so the state that used to
      // show them is exactly what this renders.
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        ...defaultStats(),
        roms: 42,
        last_sync: "2026-07-11T17:48:00",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(buttonByExactText(container, "Force Full Sync")).toBeNull();
      expect(container.textContent).not.toContain("Skip Preview");
      expect(container.querySelector('[data-testid="toggle-input"]')).toBeNull();
    });

    it("shows no preview card — the change table is the Sync page's", async () => {
      adoptPreview({
        success: true,
        summary: {
          new_count: 2,
          changed_count: 0,
          unchanged_count: 0,
          remove_count: 0,
          disabled_platform_remove_count: 0,
        },
        new_names: [],
        changed_names: [],
        preview_id: "p-no-card",
        pause_likely: true,
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(container.querySelector('[data-testid="sync-changes"]')).toBeNull();
      expect(container.querySelector('[data-testid="sync-estimate"]')).toBeNull();
      expect(container.querySelector('[data-testid="preview-expiry"]')).toBeNull();
      expect(container.querySelector('[data-testid="budget-advisory"]')).toBeNull();
      expect(buttonByExactText(container, "Apply Sync")).toBeNull();
    });
  });

  describe("pending preview restored on mount", () => {
    // The card, its countdown and its Apply moved to the Sync page with the
    // preview (src/components/SyncPage.test.tsx). What Main reads the store for
    // is its SLOT: a preview still worth reviewing is worth a row that says so.
    function previewWithChanges(id: string, expiresAt?: number): SyncPreview {
      return {
        success: true,
        summary: {
          new_count: 2,
          changed_count: 0,
          unchanged_count: 0,
          remove_count: 0,
          disabled_platform_remove_count: 0,
        },
        new_names: ["a", "b"],
        changed_names: [],
        preview_id: id,
        ...(expiresAt === undefined ? {} : { expires_at: expiresAt }),
      };
    }

    it("reads the held preview back and offers a review of it", async () => {
      vi.mocked(backend.getPendingPreview).mockResolvedValue({
        success: true,
        preview: previewWithChanges("still-held"),
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(slotLabel(container)).toBe("Changes ready");
      expect(slotValue(container)).toBe("2 new");
    });

    it("nothing pending leaves the quiet page untouched", async () => {
      vi.mocked(backend.getPendingPreview).mockResolvedValue({ success: true, preview: null });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(slotLabel(container)).toBeNull();
    });

    it("logs the failure when getPendingPreview rejects, and the idle page stands", async () => {
      const logSpy = vi.spyOn(backend, "logError");
      vi.mocked(backend.getPendingPreview).mockRejectedValue(new Error("boom"));
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to query pending preview"));
      expect(slotLabel(container)).toBeNull();
    });

    it("an expired preview counts as none, and the timer that says so fires at the deadline", async () => {
      // `Date` is faked alongside the timer: the deadline is absolute wall-clock
      // seconds, so advancing the timer without advancing the clock would fire
      // the callback into a world where the preview is still good.
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
      try {
        const expiresAt = Math.floor(Date.now() / 1000) + 120;
        vi.mocked(backend.getPendingPreview).mockResolvedValue({
          success: true,
          preview: previewWithChanges("about-to-expire", expiresAt),
        });
        const { container } = render(<MainPage onNavigate={vi.fn()} />);
        await act(async () => {
          await Promise.resolve();
          await Promise.resolve();
          await Promise.resolve();
        });
        expect(slotLabel(container)).toBe("Changes ready");

        await act(async () => {
          await vi.advanceTimersByTimeAsync(120_000);
        });

        // The preview is NOT discarded — only Main's row about it goes.
        expect(slotLabel(container)).toBeNull();
        expect(vi.mocked(backend.syncCancelPreview)).not.toHaveBeenCalled();
      } finally {
        vi.useRealTimers();
      }
    });

    it("a preview the backend sent no deadline for never stops being reviewable", async () => {
      // An older backend sends no `expires_at`. There is nothing to count down
      // to, so the offer stands however long the panel is left open — the same
      // behaviour the card had before deadlines existed.
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
      try {
        vi.mocked(backend.getPendingPreview).mockResolvedValue({
          success: true,
          preview: previewWithChanges("no-deadline"),
        });
        const { container } = render(<MainPage onNavigate={vi.fn()} />);
        await act(async () => {
          await Promise.resolve();
          await Promise.resolve();
          await Promise.resolve();
        });
        expect(slotLabel(container)).toBe("Changes ready");

        await act(async () => {
          await vi.advanceTimersByTimeAsync(60 * 60_000);
        });

        expect(slotLabel(container)).toBe("Changes ready");
      } finally {
        vi.useRealTimers();
      }
    });

    it("loses to a preview the user answered on the Sync page while the read was in flight", async () => {
      // The ordering rule the store owns, seen from Main: a read issued before
      // the answer cannot put the offer back after it.
      const read = deferred<{ success: boolean; preview: SyncPreview | null }>();
      vi.mocked(backend.getPendingPreview).mockReturnValue(read.promise);
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      act(() => clearPendingPreview());
      await act(async () => {
        read.resolve({ success: true, preview: previewWithChanges("stale-read") });
        await Promise.resolve();
      });

      expect(slotLabel(container)).toBeNull();
    });
  });

  describe("handleCancel", () => {
    it("clicking 'Cancel Sync' requests cancel, cancels the active run, and disarms to 'Cancelling…' (#1202)", async () => {
      // Pre-arm an in-flight sync via the backend-authoritative mount query. The
      // run id rides on the sync_progress store (runId), which handleCancel
      // reads to scope the cancel — no separate frontend run-id mirror (#1202).
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        message: "Working",
        runId: "run-x",
      });
      vi.mocked(backend.cancelSync).mockResolvedValue({
        success: true,
        message: "cancelled-msg",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      const cancel = buttonByExactText(container, "Cancel Sync");
      expect(cancel).not.toBeNull();
      await act(async () => {
        fireEvent.click(cancel!);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(vi.mocked(syncManager.requestSyncCancel)).toHaveBeenCalled();
      // Scoped to the active run id sourced from the sync_progress store (#1202).
      expect(vi.mocked(backend.cancelSync)).toHaveBeenCalledWith("run-x");
      // RC-B: disarmed into a disabled "Cancelling…" — NOT re-armed to idle.
      const cancelling = buttonByExactText(container, "Cancelling…");
      expect(cancelling).not.toBeNull();
      expect(cancelling!.disabled).toBe(true);
      expect(buttonByExactText(container, "Sync Library")).toBeNull();
    });

    it("cancel in the pre-progress window (no run id yet) cancels unconditionally (#1202)", async () => {
      // The "Fetching library…" window: the backend hasn't stamped a run id into
      // sync_progress yet, so the store's runId is empty. handleCancel must send
      // "" → the backend's unconditional cancel path, NOT a stale id.
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "fetching",
        message: "Fetching library...",
        // no runId — pre-progress window
      });
      vi.mocked(backend.cancelSync).mockResolvedValue({
        success: true,
        message: "cancelled-msg",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      const cancel = buttonByExactText(container, "Cancel Sync");
      expect(cancel).not.toBeNull();
      await act(async () => {
        fireEvent.click(cancel!);
        await Promise.resolve();
        await Promise.resolve();
      });
      // Non-vacuous: "" proves the empty/absent run id maps to the unconditional
      // cancel. A regression that fabricated a stale id would send non-empty.
      expect(vi.mocked(backend.cancelSync)).toHaveBeenCalledWith("");
    });

    it("stays disarmed ('Cancelling…') during the drain — no status flash — then re-arms on the terminal CANCELLED stage (#1202 RC-B)", async () => {
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        message: "Working",
        runId: "run-x",
      });
      vi.mocked(backend.cancelSync).mockResolvedValue({
        success: true,
        message: "cancelled-msg",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Cancel Sync")!);
        await Promise.resolve();
        await Promise.resolve();
      });
      // Drain: disarmed to "Cancelling…", the run's slot still standing, and the
      // cancelSync result message NOT surfaced — no instant-finish flash.
      expect(buttonByExactText(container, "Cancelling…")).not.toBeNull();
      expect(slotLabel(container)).not.toBeNull();
      expect(fieldLabels(container)).not.toContain("cancelled-msg");

      // Terminal CANCELLED sync_progress lands via the module store → re-arm.
      await act(async () => {
        setSyncProgress({ running: false, stage: "cancelled", message: "Sync cancelled" });
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(slotLabel(container)).toBeNull();
      expect(buttonByExactText(container, "Cancelling…")).toBeNull();
      // The terminal stage's message is surfaced (not the cancelSync result's).
      expect(fieldLabels(container)).toContain("Sync cancelled");
    });

    it("the terminal cancel message stays past 8s and auto-clears after 15s", async () => {
      vi.useFakeTimers({
        toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout"],
      });
      try {
        vi.mocked(backend.getSyncStatus).mockResolvedValue({
          running: true,
          stage: "applying",
          message: "Working",
          runId: "run-x",
        });
        vi.mocked(backend.cancelSync).mockResolvedValue({
          success: true,
          message: "cancelled-msg",
        });
        const { container } = render(<MainPage onNavigate={vi.fn()} />);
        await act(async () => {
          await Promise.resolve();
          await Promise.resolve();
        });
        await act(async () => {
          fireEvent.click(buttonByExactText(container, "Cancel Sync")!);
          await Promise.resolve();
          await Promise.resolve();
        });
        // Terminal stage surfaces the message + arms the 15s auto-clear.
        await act(async () => {
          setSyncProgress({ running: false, stage: "cancelled", message: "Sync cancelled" });
          await Promise.resolve();
        });
        expect(fieldLabels(container)).toContain("Sync cancelled");
        // Past the OLD 8s threshold: still visible (the "stays longer" change).
        await act(async () => {
          await vi.advanceTimersByTimeAsync(8000);
        });
        expect(fieldLabels(container)).toContain("Sync cancelled");
        // Crossing 15s total: now cleared.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(7001);
        });
        expect(fieldLabels(container)).not.toContain("Sync cancelled");
      } finally {
        vi.useRealTimers();
      }
    });

    it("cancelSync rejection: re-arms and surfaces 'Failed to cancel sync' (no terminal will arrive)", async () => {
      // The cancel call itself fails — no backend terminal stage will follow, so
      // handleCancel re-arms (out of "Cancelling…") and surfaces the failure so
      // the user can retry.
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        message: "Working",
        runId: "run-x",
      });
      vi.mocked(backend.cancelSync).mockRejectedValue(new Error("net"));
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Cancel Sync")!);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(vi.mocked(backend.cancelSync)).toHaveBeenCalled();
      // The failure message reaches the user under the still-running progress
      // rows, and the button is re-armed for the retry — out of "Cancelling…" but
      // still a Cancel, because the run it would stop never learned of the cancel.
      expect(fieldLabels(container)).toContain("Failed to cancel sync");
      expect(buttonByExactText(container, "Cancel Sync")).not.toBeNull();
      expect(buttonByExactText(container, "Cancelling…")).toBeNull();
    });

    it("a cancel whose call failed leaves the run in flight for every reader of the store (#1019)", async () => {
      // DangerZone and RemovedGamesCleanup read `running` straight from the
      // module store, so whatever the panel concludes here they conclude too.
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        message: "Working",
        runId: "run-x",
      });
      vi.mocked(backend.cancelSync).mockRejectedValue(new Error("net"));
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Cancel Sync")!);
        await Promise.resolve();
        await Promise.resolve();
      });

      // The cancel never reached the backend, so the run it would have stopped is
      // still going — the store keeps saying so...
      expect(getSyncProgress().running).toBe(true);
      // ...and the panel says the same thing, rather than offering a Sync button
      // that can only earn a sync_in_progress reject.
      expect(buttonByExactText(container, "Sync Library")).toBeNull();
      expect(buttonByExactText(container, "Cancel Sync")).not.toBeNull();
    });
  });

  // ===========================================================================
  // J. handleClearCache — Force Full Sync flow
  // ===========================================================================
  describe("handleFixInputDriver (via ConfirmModal onOK)", () => {
    async function renderWithWarning(): Promise<HTMLElement> {
      vi.mocked(backend.getSettings).mockResolvedValue({
        ...defaultSettings(),
        retroarch_input_check: { warning: true, current: "udev" },
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      return container;
    }

    it("clicking the Fix button opens the ConfirmModal via showModal", async () => {
      const container = await renderWithWarning();
      const fixBtn = Array.from(container.querySelectorAll('[data-testid="dialog-button"]')).find(
        (b) => b.textContent === "Fix",
      ) as HTMLButtonElement | undefined;
      expect(fixBtn).not.toBeUndefined();
      fireEvent.click(fixBtn!);
      expect(vi.mocked(showModal)).toHaveBeenCalledTimes(1);
      const props = lastConfirmModalProps<{
        strTitle?: string;
        strOKButtonText?: string;
      }>();
      expect(props?.strTitle).toBe("Fix RetroArch input_driver?");
      expect(props?.strOKButtonText).toBe("Apply Fix");
    });

    it("onOK success=true clears the retroarchWarning section", async () => {
      vi.mocked(backend.fixRetroarchInputDriver).mockResolvedValue({
        success: true,
        message: "Done",
      });
      const container = await renderWithWarning();
      const fixBtn = Array.from(container.querySelectorAll('[data-testid="dialog-button"]')).find(
        (b) => b.textContent === "Fix",
      ) as HTMLButtonElement | undefined;
      fireEvent.click(fixBtn!);
      const props = lastConfirmModalProps<{ onOK?: () => void | Promise<void> }>();
      await act(async () => {
        await props?.onOK?.();
      });
      expect(container.textContent).not.toContain("RetroArch: input_driver");
    });

    it("onOK success=false leaves the warning in place", async () => {
      vi.mocked(backend.fixRetroarchInputDriver).mockResolvedValue({
        success: false,
        message: "Could not write",
      });
      const container = await renderWithWarning();
      const fixBtn = Array.from(container.querySelectorAll('[data-testid="dialog-button"]')).find(
        (b) => b.textContent === "Fix",
      ) as HTMLButtonElement | undefined;
      fireEvent.click(fixBtn!);
      const props = lastConfirmModalProps<{ onOK?: () => void | Promise<void> }>();
      await act(async () => {
        await props?.onOK?.();
      });
      // Warning stays
      expect(container.textContent).toContain("RetroArch: input_driver");
    });

    it("onOK rejection is silently swallowed (warning stays, no crash)", async () => {
      vi.mocked(backend.fixRetroarchInputDriver).mockRejectedValue(new Error("perm"));
      const container = await renderWithWarning();
      const fixBtn = Array.from(container.querySelectorAll('[data-testid="dialog-button"]')).find(
        (b) => b.textContent === "Fix",
      ) as HTMLButtonElement | undefined;
      fireEvent.click(fixBtn!);
      const props = lastConfirmModalProps<{ onOK?: () => void | Promise<void> }>();
      await act(async () => {
        await props?.onOK?.();
      });
      // Truly-ignored catch — warning unchanged.
      expect(container.textContent).toContain("RetroArch: input_driver");
    });
  });

  // ===========================================================================
  // L. Navigation buttons
  // ===========================================================================
  describe("navigation", () => {
    it.each([
      ["Sync", "sync"],
      ["Library", "library"],
      ["Settings", "settings"],
      ["Data Management", "data"],
    ] as const)("the %s entry is the way to the %s page", async (label, page) => {
      const onNavigate = vi.fn();
      const { container } = render(<MainPage onNavigate={onNavigate} />);
      await flushAsync();
      fireEvent.click(buttonByExactText(container, label)!);
      expect(onNavigate).toHaveBeenCalledWith(page);
    });

    it("the Last sync row states and does nothing — the menu is the way to the page", async () => {
      // The status rows say what is; the menu navigates. They stay focusable so
      // a reader can walk the block and Steam can scroll it into view, but a
      // press on one is not a second door.
      const onNavigate = vi.fn();
      vi.mocked(backend.getSyncStats).mockResolvedValue({ ...defaultStats(), last_sync: "2026-07-11T17:48:00" });
      const { container } = render(<MainPage onNavigate={onNavigate} />);
      await flushAsync();

      const row = Array.from(container.querySelectorAll('[data-testid="field"]')).find(
        (f) => f.querySelector('[data-testid="field-label"]')?.textContent === "Last sync",
      );
      expect(row).not.toBeUndefined();
      expect(row?.getAttribute("data-activate")).toBeNull();
      fireEvent.click(row!);
      expect(onNavigate).not.toHaveBeenCalled();
    });

    it("offers no System entry — its core and BIOS controls live in Library", async () => {
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(buttonByExactText(container, "System")).toBeNull();
    });

    it("clicking 'Go to Settings' (save-sort migration banner) invokes onNavigate('settings')", async () => {
      currentSaveSortState = { pending: true, saves_count: 3 };
      // refreshMigrationState runs on mount and writes save_sort back to the
      // store — also return pending:true so the banner stays visible.
      vi.mocked(backend.refreshMigrationState).mockResolvedValue({
        retrodeck: { pending: false },
        save_sort: { pending: true, saves_count: 3 },
      });
      const onNavigate = vi.fn();
      const { container } = render(<MainPage onNavigate={onNavigate} />);
      await flushAsync();
      fireEvent.click(buttonByExactText(container, "Go to Settings")!);
      expect(onNavigate).toHaveBeenCalledWith("settings");
    });

    it("clicking 'View All' (Downloads section) invokes onNavigate('downloads')", async () => {
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout"] });
      try {
        setDownloads([
          {
            rom_id: 1,
            rom_name: "X",
            platform_name: "Y",
            file_name: "x.bin",
            status: "downloading",
            progress: 0,
            bytes_downloaded: 0,
            total_bytes: 1024,
            resumable: false,
          },
        ]);
        const onNavigate = vi.fn();
        const { container } = render(<MainPage onNavigate={onNavigate} />);
        await act(async () => {
          await Promise.resolve();
          await Promise.resolve();
        });
        fireEvent.click(buttonByExactText(container, "View All")!);
        expect(onNavigate).toHaveBeenCalledWith("downloads");
      } finally {
        vi.useRealTimers();
      }
    });
  });

  // ===========================================================================
  // M. Downloads section render
  // ===========================================================================
  describe("downloads section", () => {
    // The downloads section reads the store through useDownloads(), so seeding
    // the store before render is enough. Fake timers stay for MainPage's other
    // timed effects.
    beforeEach(() => {
      vi.useFakeTimers({
        toFake: ["setInterval", "clearInterval", "setTimeout", "clearTimeout"],
      });
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    async function renderSection(): Promise<HTMLElement> {
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      return container;
    }

    it("hidden when no downloads in the store", async () => {
      const container = await renderSection();
      // The downloads block is heading-less; its "View All" button is the
      // presence anchor.
      expect(buttonByExactText(container, "View All")).toBeNull();
    });

    it("rendered when at least one active download", async () => {
      setDownloads([
        {
          rom_id: 1,
          rom_name: "Active",
          platform_name: "Genesis",
          file_name: "a.bin",
          status: "downloading",
          progress: 50,
          bytes_downloaded: 512,
          total_bytes: 1024,
          resumable: false,
        },
      ]);
      const container = await renderSection();
      expect(buttonByExactText(container, "View All")).not.toBeNull();
      // The rom name lands in the full-width caption (not clipped in a Field
      // label column) — see the #751 ProgressBarWithInfo fix.
      expect(container.querySelector('[data-testid="dl-caption"]')?.textContent).toBe("Active");
    });

    it("shows '+N more downloading' when more than 2 active downloads", async () => {
      setDownloads([
        {
          rom_id: 1,
          rom_name: "A",
          platform_name: "X",
          file_name: "a",
          status: "downloading",
          progress: 0,
          bytes_downloaded: 0,
          total_bytes: 1024,
          resumable: false,
        },
        {
          rom_id: 2,
          rom_name: "B",
          platform_name: "X",
          file_name: "b",
          status: "downloading",
          progress: 0,
          bytes_downloaded: 0,
          total_bytes: 1024,
          resumable: false,
        },
        {
          rom_id: 3,
          rom_name: "C",
          platform_name: "X",
          file_name: "c",
          status: "downloading",
          progress: 0,
          bytes_downloaded: 0,
          total_bytes: 1024,
          resumable: false,
        },
      ]);
      const container = await renderSection();
      expect(container.textContent).toContain("+1 more downloading");
    });

    it("shows 'N completed' count for finished items", async () => {
      setDownloads([
        {
          rom_id: 1,
          rom_name: "A",
          platform_name: "X",
          file_name: "a",
          status: "completed",
          progress: 100,
          bytes_downloaded: 100,
          total_bytes: 100,
          resumable: false,
        },
        {
          rom_id: 2,
          rom_name: "B",
          platform_name: "X",
          file_name: "b",
          status: "failed",
          progress: 0,
          bytes_downloaded: 0,
          total_bytes: 100,
          resumable: false,
        },
      ]);
      const container = await renderSection();
      // Self-describing label — the heading-less downloads block gives the row
      // no context of its own.
      expect(container.textContent).toContain("2 downloads completed");
      // The block ends in a rule so it doesn't run into the menu buttons.
      expect(container.querySelectorAll('[data-testid="block-separator"]')).toHaveLength(2);
    });

    it("active item with total_bytes > 0 renders nProgress = (bytes/total)*100, indeterminate=false", async () => {
      setDownloads([
        {
          rom_id: 1,
          rom_name: "P",
          platform_name: "G",
          file_name: "p",
          status: "downloading",
          progress: 25,
          bytes_downloaded: 256,
          total_bytes: 1024,
          resumable: false,
        },
      ]);
      const container = await renderSection();
      const progress = container.querySelector('[data-testid="progress-progress"]');
      expect(progress?.textContent).toBe("25");
      const indet = container.querySelector('[data-testid="progress-indeterminate"]');
      expect(indet?.textContent).toBe("false");
      expect(container.querySelector('[data-testid="dl-caption"]')?.textContent).toBe("P");
      expect(container.querySelector('[data-testid="dl-bytes"]')?.textContent).toBe("256 B / 1.0 KB");
    });

    it("active item with total_bytes === 0 renders indeterminate=true", async () => {
      setDownloads([
        {
          rom_id: 1,
          rom_name: "P",
          platform_name: "G",
          file_name: "p",
          status: "downloading",
          progress: 0,
          bytes_downloaded: 0,
          total_bytes: 0,
          resumable: false,
        },
      ]);
      const container = await renderSection();
      const indet = container.querySelector('[data-testid="progress-indeterminate"]');
      expect(indet?.textContent).toBe("true");
      expect(container.querySelector('[data-testid="dl-caption"]')?.textContent).toBe("P");
      expect(container.querySelector('[data-testid="dl-bytes"]')?.textContent).toBe("0 B");
    });
  });

  // ===========================================================================
  // N. Subscription cleanup
  // ===========================================================================
  describe("subscription cleanup", () => {
    it("onSyncProgressChange subscription is removed on unmount", async () => {
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        message: "Working",
      });
      const { container, unmount } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // In-flight UI is up — the subscription is live.
      expect(buttonByExactText(container, "Cancel Sync")).not.toBeNull();
      unmount();
      // After unmount, a store update must not throw (listener was removed)
      // and must not resurrect the unmounted tree.
      act(() => {
        setSyncProgress({ running: false, stage: "done", message: "Sync complete" });
      });
      expect(buttonByExactText(container, "Sync Library")).toBeNull();
    });
  });

  // ===========================================================================
  // N2. Backend-authoritative progress — store subscription drives the UI
  // ===========================================================================
  describe("store-driven sync UI (#751)", () => {
    it("terminal stage tears down the in-flight UI and surfaces the final message", async () => {
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        step: 1,
        totalSteps: 2,
        message: "Working",
      });
      const statsAfter: SyncStats = {
        ...defaultStats(),
        roms: 7,
        last_sync: new Date().toISOString(),
      };
      vi.mocked(backend.getSyncStats).mockResolvedValue(statsAfter);
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // In-flight initially.
      expect(buttonByExactText(container, "Cancel Sync")).not.toBeNull();

      // A terminal sync_progress lands via the module store.
      await act(async () => {
        setSyncProgress({ running: false, stage: "done", message: "Sync complete: 7 games" });
        await Promise.resolve();
        await Promise.resolve();
      });

      // Torn down: the slot and Cancel both gone, final message surfaced, stats
      // refreshed.
      expect(slotLabel(container)).toBeNull();
      expect(buttonByExactText(container, "Cancel Sync")).toBeNull();
      expect(fieldLabels(container)).toContain("Sync complete: 7 games");
      expect(vi.mocked(backend.getSyncStats)).toHaveBeenCalledTimes(2);
    });

    // The terminal stage rewrites last_sync, last_attempt and the counts, so the
    // re-read below is issued BECAUSE the facts changed, and it may not join a
    // read issued while the run was still going. Open the panel in a run's last
    // seconds and the mount read is exactly such a read. Nothing re-reads
    // afterwards for a completed run — the paused poll never arms — so a joined
    // pre-run answer would be the last one the panel ever shows.
    it("re-reads the stats itself at the terminal stage rather than joining the read still open", async () => {
      const mountRead = deferred<SyncStats>();
      const terminalRead = deferred<SyncStats>();
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        message: "Working",
      });
      vi.mocked(backend.getSyncStats).mockReturnValueOnce(mountRead.promise).mockReturnValueOnce(terminalRead.promise);

      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // Still open: the mount read has not answered, so the library line is blank.
      expect(container.textContent).not.toContain("games");

      await act(async () => {
        setSyncProgress({ running: false, stage: "done", message: "Sync complete" });
        await Promise.resolve();
      });
      // Two reads, not one — a joined read would answer for the run that just ended.
      expect(vi.mocked(backend.getSyncStats)).toHaveBeenCalledTimes(2);

      terminalRead.resolve({ ...defaultStats(), roms: 7, platforms: 1, last_sync: new Date().toISOString() });
      await flushAsync();
      // The overtaken pre-run answer lands last and must change nothing.
      mountRead.resolve({ ...defaultStats(), roms: 3, platforms: 1 });
      await flushAsync();

      expect(container.textContent).toContain("7 games");
      expect(container.textContent).not.toContain("3 games");
    });

    it("a status read still open when a run starts does not retract it (#751)", async () => {
      // The mount's get_sync_status was issued before the Sync page's press, so
      // it cannot answer whether the run that press started is in flight — and
      // its "nothing running" must not be allowed to collapse the slot. The
      // press is the other page's now; what reaches Main is the store write it
      // makes, which is exactly what this drives.
      const status = deferred<SyncProgress>();
      vi.mocked(backend.getSyncStatus).mockReturnValue(status.promise);

      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      await act(async () => {
        setSyncProgress({ running: true, stage: "fetching", message: "Fetching library...", runKind: "preview" });
        await Promise.resolve();
      });
      expect(buttonByExactText(container, "Cancel Sync")).not.toBeNull();

      // The pre-start snapshot lands late.
      await act(async () => {
        status.resolve({ running: false, stage: "", current: 0, total: 0, message: "" });
        await Promise.resolve();
      });

      // The store still carries the run that started — so the panel, and every
      // other reader of it, still shows one in flight.
      expect(getSyncProgress().running).toBe(true);
      expect(slotLabel(container)).toBe("Checking for changes");
      expect(buttonByExactText(container, "Cancel Sync")).not.toBeNull();
    });
  });

  // ===========================================================================
  // N3. Sync-complete status styling — green + smaller + longer visibility
  // ===========================================================================
  describe("sync-complete status styling", () => {
    const syncStatus = (c: HTMLElement) => c.querySelector('[data-testid="sync-status"]') as HTMLElement | null;

    // Mount into a live run so a terminal sync_progress has an in-flight UI to
    // tear down, then land the terminal frame through the module store exactly
    // as a backend event would.
    async function mountThenTerminal(stage: "done" | "cancelled" | "error", message: string): Promise<HTMLElement> {
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        step: 1,
        totalSteps: 1,
        message: "Working",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      await act(async () => {
        setSyncProgress({ running: false, stage, message });
        await Promise.resolve();
        await Promise.resolve();
      });
      return container;
    }

    it("renders a clean sync finish smaller and green", async () => {
      const c = await mountThenTerminal("done", "Sync complete: 7 games");
      const el = syncStatus(c);
      expect(el?.textContent).toBe("Sync complete: 7 games");
      // Non-vacuous: both the affirmative green AND the small caption size sit on
      // the element — dropping the success tone (or the size) fails this.
      expect(el?.style.color).toBe("#59bf40");
      expect(el?.style.fontSize).toBe("12px");
    });

    it("renders a cancelled finish smaller but NOT green", async () => {
      const c = await mountThenTerminal("cancelled", "Sync cancelled");
      const el = syncStatus(c);
      expect(el?.textContent).toBe("Sync cancelled");
      // Neutral tone: no colour override, so the panel's default text colour wins.
      expect(el?.style.color).toBe("");
      expect(el?.style.fontSize).toBe("12px");
    });

    it("renders an errored finish smaller but NOT green", async () => {
      const c = await mountThenTerminal("error", "Sync failed: server unreachable");
      const el = syncStatus(c);
      expect(el?.textContent).toBe("Sync failed: server unreachable");
      expect(el?.style.color).toBe("");
      expect(el?.style.fontSize).toBe("12px");
    });

    it("keeps the finish status visible past 8s and clears it only after 15s", async () => {
      vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
      try {
        vi.mocked(backend.getSyncStatus).mockResolvedValue({
          running: true,
          stage: "applying",
          step: 1,
          totalSteps: 1,
          message: "Working",
        });
        const { container } = render(<MainPage onNavigate={vi.fn()} />);
        await flushAsync();
        await act(async () => {
          setSyncProgress({ running: false, stage: "done", message: "Sync complete: 7 games" });
          await Promise.resolve();
          await Promise.resolve();
        });
        expect(syncStatus(container)?.textContent).toBe("Sync complete: 7 games");

        // Past the OLD 8s auto-clear: still on screen — this guards the "stays
        // visible a bit longer" ask (the pre-change 8000ms would have cleared it).
        await act(async () => {
          await vi.advanceTimersByTimeAsync(9000);
        });
        expect(syncStatus(container)).not.toBeNull();

        // Crossing 15s total: the auto-clear fires and the line disappears.
        await act(async () => {
          await vi.advanceTimersByTimeAsync(6001);
        });
        expect(syncStatus(container)).toBeNull();
      } finally {
        vi.useRealTimers();
      }
    });
  });

  // ===========================================================================
  // O. Skip Preview toggle
  // ===========================================================================
  describe("RetroDECK config-health banner", () => {
    it("shows the unreadable banner when status is 'unreadable'", async () => {
      vi.mocked(backend.getRetroDeckStatus).mockResolvedValue({
        status: "unreadable",
        config_path: "/cfg/retrodeck.json",
        resolved_home: "/home/deck/retrodeck",
      });
      const { findByText } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(await findByText("RetroDECK configuration unreadable")).toBeInTheDocument();
      expect(await findByText(/syncs and downloads may target the wrong location/)).toBeInTheDocument();
      // Probed config path is surfaced.
      expect(await findByText(/\/cfg\/retrodeck\.json/)).toBeInTheDocument();
    });

    it("shows the root-missing banner when status is 'root_missing'", async () => {
      vi.mocked(backend.getRetroDeckStatus).mockResolvedValue({
        status: "root_missing",
        config_path: "/cfg/retrodeck.json",
        resolved_home: "/run/media/sdcard/retrodeck",
      });
      const { findByText } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(await findByText("RetroDECK library not found")).toBeInTheDocument();
      expect(await findByText(/make sure the card is inserted/)).toBeInTheDocument();
      // Resolved home is surfaced.
      expect(await findByText(/\/run\/media\/sdcard\/retrodeck/)).toBeInTheDocument();
    });

    it("renders no banner when status is 'ok'", async () => {
      vi.mocked(backend.getRetroDeckStatus).mockResolvedValue({
        status: "ok",
        config_path: "/cfg/retrodeck.json",
        resolved_home: "/home/deck/retrodeck",
      });
      const { queryByText } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(queryByText("RetroDECK configuration unreadable")).toBeNull();
      expect(queryByText("RetroDECK library not found")).toBeNull();
    });

    it("renders no banner when status is 'absent' (fresh-install case)", async () => {
      vi.mocked(backend.getRetroDeckStatus).mockResolvedValue({
        status: "absent",
        config_path: "/cfg/retrodeck.json",
        resolved_home: "/home/deck/retrodeck",
      });
      const { queryByText } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(queryByText("RetroDECK configuration unreadable")).toBeNull();
      expect(queryByText("RetroDECK library not found")).toBeNull();
    });

    it("leaves the banner cleared when getRetroDeckStatus rejects", async () => {
      vi.mocked(backend.getRetroDeckStatus).mockRejectedValue(new Error("boom"));
      const logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      const { queryByText } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // No banner, and the rejection is logged (non-vacuous .catch assertion).
      expect(queryByText("RetroDECK configuration unreadable")).toBeNull();
      expect(queryByText("RetroDECK library not found")).toBeNull();
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to query RetroDECK status"));
    });
  });

  // ===========================================================================
  // The preview survives leaving the main page
  // ===========================================================================
  describe("the preview survives leaving the main page", () => {
    /** What the Sync page's press writes into the module store the moment a run
     *  is started: running, and carrying no run id — the backend has not stamped
     *  one yet, and will not until a reconcile and a round trip later. Main is
     *  showing a run it did not start, which is every run now. */
    function optimisticStart(): SyncProgress {
      return {
        running: true,
        stage: "fetching",
        current: 0,
        total: 0,
        message: "Fetching library...",
        runKind: "preview",
      };
    }

    /** The frame a previous preview run left behind. It used to be what
     *  `get_sync_status` answered with until the NEXT run overwrote it, so a
     *  panel remounting during the start window read it as the state of the run
     *  it was actually watching. `inFlight: false` is truthful here and is the
     *  point: during the start window the backend genuinely has no run. */
    function lingeringDoneSnapshot(): SyncStatusAnswer {
      return {
        running: false,
        stage: "done",
        current: 0,
        total: 0,
        message: "Preview ready",
        runId: "run-previous",
        inFlight: false,
      };
    }

    /** The stats that used to put "Resume Sync" on Main — an incomplete attempt,
     *  bound shortcuts, and surviving progress — so an assertion that nothing
     *  offers to start a run is made in the state that offered one loudest. */
    function statsWithEveryStartControl(): SyncStats {
      return {
        last_sync: null,
        platforms: 1,
        collections: 0,
        roms: 12,
        total_shortcuts: 12,
        last_attempt: { finished_at: "2026-06-01T17:48:00", status: "cancelled" },
        resumable_games: 12,
      };
    }

    function previewWithChanges(id: string): SyncPreview {
      return {
        success: true,
        summary: {
          new_count: 2,
          changed_count: 0,
          unchanged_count: 0,
          remove_count: 0,
          disabled_platform_remove_count: 0,
        },
        new_names: ["a", "b"],
        changed_names: [],
        preview_id: id,
      };
    }

    /** Every button Main renders. Read whole rather than queried by label, so an
     *  assertion that no control starts a run is carried by the set itself and
     *  cannot pass on a selector that could never match anything. */
    function buttonLabels(container: HTMLElement): string[] {
      return Array.from(container.querySelectorAll("button")).map((b) => b.textContent);
    }

    it("offers no way to start a run, in the state that used to offer one loudest", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(statsWithEveryStartControl());
      adoptPreview(previewWithChanges("held"));
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      // The menu, and nothing else — the preview is a row that states, not a
      // button that acts.
      expect(buttonLabels(container)).toEqual(["Sync", "Library", "Settings", "Data Management"]);
    });

    it("issues neither a preview nor a run, whatever on it is pressed", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(statsWithEveryStartControl());
      adoptPreview(previewWithChanges("held"));
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      await act(async () => {
        Array.from(container.querySelectorAll("button")).forEach((b) => fireEvent.click(b));
        container.querySelectorAll('[data-testid="field"]').forEach((f) => fireEvent.click(f));
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(vi.mocked(backend.syncPreview)).not.toHaveBeenCalled();
      expect(vi.mocked(backend.startSync)).not.toHaveBeenCalled();
      expect(vi.mocked(backend.syncApplyDelta)).not.toHaveBeenCalled();
      expect(vi.mocked(backend.syncCancelPreview)).not.toHaveBeenCalled();
    });

    it("a remount mid-start keeps the in-progress view", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(statsWithEveryStartControl());
      setSyncProgress(optimisticStart());
      vi.mocked(backend.getSyncStatus).mockResolvedValue(lingeringDoneSnapshot());

      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      // First paint is right — the seed reads the module store.
      expect(slotLabel(container)).not.toBeNull();
      await flushAsync();
      // And the lingering snapshot did not retract it.
      expect(slotLabel(container)).not.toBeNull();
      expect(getSyncProgress().running).toBe(true);
    });

    it("a remount mid-start does not announce the previous run's terminal frame", async () => {
      setSyncProgress(optimisticStart());
      vi.mocked(backend.getSyncStatus).mockResolvedValue(lingeringDoneSnapshot());
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(container.querySelector('[data-testid="sync-status"]')).toBeNull();
    });

    it("recovers when a run's terminal frame was lost and the backend says nothing is running", async () => {
      // The wedge the run-id rule alone would create. The store holds a live
      // frame for a run that has really ended — its terminal frame never
      // arrived — and the reset means the backend's own frame no longer names
      // that run either. `inFlight: false` is the only thing left that can say
      // so, and it has to be enough: "Cancel Sync" for an unknown run is answered
      // "No sync in progress" with no terminal to follow, and DangerZone gates
      // four more actions on the same flag — so a panel that cannot leave this
      // state is stuck until a plugin reload.
      vi.mocked(backend.getSyncStats).mockResolvedValue(statsWithEveryStartControl());
      setSyncProgress({
        running: true,
        stage: "applying",
        current: 40,
        total: 100,
        message: "GBA: 40/100",
        runId: "run-lost",
      });
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: false,
        stage: "",
        current: 0,
        total: 0,
        message: "",
        runId: "",
        inFlight: false,
      });

      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      expect(slotLabel(container)).not.toBeNull();
      await flushAsync();

      expect(slotLabel(container)).toBeNull();
      expect(buttonByExactText(container, "Cancel Sync")).toBeNull();
      expect(getSyncProgress().running).toBe(false);
    });

    it("Cancel Sync cancels the run, even while the store is holding a preview", async () => {
      // A run in flight takes the slot but does not drop the preview, so the
      // store can hold one while the run is showing. The only button beside the
      // slot is Cancel Sync, and it must cancel the RUN — discarding the preview
      // instead would leave the run going with nothing left to stop it.
      adoptPreview(previewWithChanges("held-under-a-run"));
      setSyncProgress({ running: true, stage: "applying", message: "GBA: 1/2", runId: "run-live" });
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "applying",
        current: 1,
        total: 2,
        message: "GBA: 1/2",
        runId: "run-live",
        inFlight: true,
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(slotLabel(container)).not.toBe("Changes ready");

      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Cancel Sync")!);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(vi.mocked(backend.cancelSync)).toHaveBeenCalledWith("run-live");
      expect(vi.mocked(backend.syncCancelPreview)).not.toHaveBeenCalled();
    });

    it("asks the backend when a preview run ends having staged something this panel never saw", async () => {
      // The answer to `sync_preview` was lost with the instance that asked for it
      // (a QAM close, a reload, a navigation to the Sync page). The terminal
      // frame is the cue to go and ask.
      vi.mocked(backend.getPendingPreview).mockResolvedValue({ success: true, preview: null });
      setSyncProgress({ running: true, stage: "fetching", message: "Fetching library...", runId: "run-live" });
      vi.mocked(backend.getSyncStatus).mockResolvedValue({
        running: true,
        stage: "fetching",
        current: 0,
        total: 0,
        message: "Fetching Game Boy...",
        runId: "run-live",
      });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();

      vi.mocked(backend.getPendingPreview).mockResolvedValue({
        success: true,
        preview: previewWithChanges("preview-staged-while-away"),
      });
      await act(async () => {
        setSyncProgress({
          running: false,
          stage: "done",
          current: 0,
          total: 0,
          message: "Preview ready",
          runId: "run-live",
        });
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(slotLabel(container)).toBe("Changes ready");
    });

    it("does not re-ask when the store already holds the run's preview", async () => {
      setSyncProgress({ running: true, stage: "fetching", message: "Fetching library...", runId: "run-live" });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      vi.mocked(backend.getPendingPreview).mockClear();

      await act(async () => {
        adoptPreview(previewWithChanges("already-here"));
        setSyncProgress({
          running: false,
          stage: "done",
          current: 0,
          total: 0,
          message: "Preview ready",
          runId: "run-live",
        });
        await Promise.resolve();
      });

      expect(vi.mocked(backend.getPendingPreview)).not.toHaveBeenCalled();
      expect(slotLabel(container)).toBe("Changes ready");
    });

    // -------------------------------------------------------------------------
    // The slot, from the store's side: a preview the panel never adopted itself
    // still fills it, and a run in flight is what fills it instead.
    // -------------------------------------------------------------------------
    describe("the slot is filled from the store, not from this instance", () => {
      it("by a run the store knows about before the backend confirms it", async () => {
        // The mount lands in the start window, where the backend has not yet
        // reported the run the store already knows about — the device case that
        // put the idle buttons over a live preview.
        vi.mocked(backend.getSyncStats).mockResolvedValue(statsWithEveryStartControl());
        setSyncProgress(optimisticStart());
        vi.mocked(backend.getSyncStatus).mockResolvedValue(lingeringDoneSnapshot());

        const { container } = render(<MainPage onNavigate={vi.fn()} />);
        expect(slotLabel(container)).toBe("Checking for changes");
        await flushAsync();
        expect(slotLabel(container)).toBe("Checking for changes");
        expect(buttonByExactText(container, "Cancel Sync")).not.toBeNull();
      });

      it("by a run the backend confirms, on a fresh mount", async () => {
        vi.mocked(backend.getSyncStats).mockResolvedValue(statsWithEveryStartControl());
        setSyncProgress({
          running: true,
          stage: "fetching",
          message: "Fetching library...",
          runId: "run-live",
          runKind: "preview",
        });
        vi.mocked(backend.getSyncStatus).mockResolvedValue({
          running: true,
          stage: "fetching",
          current: 0,
          total: 0,
          message: "Fetching Game Boy...",
          runId: "run-live",
          runKind: "preview",
        });

        const { container } = render(<MainPage onNavigate={vi.fn()} />);
        await flushAsync();
        expect(slotLabel(container)).toBe("Checking for changes");
        expect(buttonByExactText(container, "Cancel Sync")).not.toBeNull();
      });

      it("by a preview the backend hands back on mount", async () => {
        vi.mocked(backend.getSyncStats).mockResolvedValue(statsWithEveryStartControl());
        vi.mocked(backend.getPendingPreview).mockResolvedValue({
          success: true,
          preview: previewWithChanges("held"),
        });

        const { container } = render(<MainPage onNavigate={vi.fn()} />);
        await flushAsync();

        expect(slotLabel(container)).toBe("Changes ready");
        expect(slotValue(container)).toBe("2 new");
      });

      it("by a preview the panel adopted before this mount, at first paint", async () => {
        // Nothing to fetch and nothing in flight: the slot is filled at first
        // paint because the store, not the instance, is holding the preview.
        vi.mocked(backend.getSyncStats).mockResolvedValue(statsWithEveryStartControl());
        adoptPreview(previewWithChanges("held-across-mounts"));
        vi.mocked(backend.getPendingPreview).mockResolvedValue({ success: true, preview: null });

        const { container } = render(<MainPage onNavigate={vi.fn()} />);
        expect(slotLabel(container)).toBe("Changes ready");
        await flushAsync();
        expect(slotLabel(container)).toBe("Changes ready");
      });
    });
  });

  describe("MainPage legacy-install notice", () => {
    // Main renders <LegacyInstallNotice/>, which reads both stores itself — this
    // is the Main site of the three, driven through the panel's own stats read
    // so the "a beat later" case below is the real timing and not a staged one.
    // The other two sites are the full-page states, and this file cannot see
    // them: both are replaced by testid stubs above. VersionErrorCard.test.tsx
    // and MigrationBlockedPage.test.tsx carry those.
    const STRANDED_SENTENCE = "Your library and settings are still in that older install too";

    beforeEach(() => {
      setLegacyInstallState({ pending: true, legacyDataPresent: true, dismissed: false });
    });

    afterEach(() => {
      // The store outlives the panel and this hook runs before RTL's cleanup,
      // so the notify reaches a still-mounted subscriber — act, or React
      // reports the update as unwrapped and the suite's console guard fails it.
      act(() => {
        setLegacyInstallState({ pending: false, legacyDataPresent: false, dismissed: false });
      });
    });

    it("adds the stranded-data sentence when the older install has data and this one holds no ROMs", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({ ...defaultStats(), roms: 0 });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(container.textContent).toContain(LEGACY_INSTALL_TITLE);
      expect(container.textContent).toContain(STRANDED_SENTENCE);
    });

    it("drops the stranded-data sentence once this install holds ROMs of its own", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({ ...defaultStats(), roms: 42 });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      // The launcher warning is what stops the irreversible removal — it survives
      // whatever the library reads.
      expect(container.textContent).toContain(LEGACY_INSTALL_TITLE);
      expect(container.textContent).not.toContain(STRANDED_SENTENCE);
    });

    it("withholds the stranded-data sentence until the stats have landed", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({ ...defaultStats(), roms: 0 });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);

      // Asserted at first paint: stats are still null, which is not knowledge
      // that this install is empty. The launcher warning is already up.
      expect(container.textContent).toContain(LEGACY_INSTALL_TITLE);
      expect(container.textContent).not.toContain(STRANDED_SENTENCE);

      // Drain the mount reads inside act, then the sentence is there — the
      // withholding is a beat, not a permanent silence.
      await flushAsync();
      expect(container.textContent).toContain(STRANDED_SENTENCE);
    });

    it("shows no card at all when no legacy install stands beside this one", async () => {
      setLegacyInstallState({ pending: false, legacyDataPresent: false, dismissed: false });
      vi.mocked(backend.getSyncStats).mockResolvedValue({ ...defaultStats(), roms: 0 });
      const { container } = render(<MainPage onNavigate={vi.fn()} />);
      await flushAsync();
      expect(container.textContent).not.toContain(LEGACY_INSTALL_TITLE);
    });
  });
});
