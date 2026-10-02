/**
 * Exercises index.tsx's `download_complete` and `migration_relaunch_options`
 * listeners through the backend-event harness. The plugin factory registers
 * the listeners on the in-memory bus; tests dispatch events via emitHostEvent
 * and assert the launch-options confirm-poll fires for the payload's appId.
 *
 * The heavyweight registration side effects (game-detail patch, launch
 * interceptor, metadata patches, session manager) are mocked to no-ops so the
 * factory can run in happy-dom without touching Steam internals. steamShortcuts
 * is mocked so the confirm-poll is observable; logError is mocked so the
 * post-catch side effect (the surfaced error message) is observable.
 */

import "@testing-library/jest-dom/vitest";
import { describe, it, expect, afterEach, beforeEach, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { toaster, type Plugin } from "./api/host";
import { emitHostEvent, hostEventListenerCount } from "./test-utils/host-event-bus";
import {
  getSettingsResetNotice,
  getUpdateNotice,
  getUpdateOutcome,
  acknowledgeUpdateToast,
  acknowledgeUpdateAttemptToast,
  acknowledgeUpdateAvailableToast,
  getStoppedUpdateAttempt as readStoppedUpdateAttempt,
  getUpdateAttemptToast,
  getUpdateInstallState,
  getAllPlaytime,
  getAppIdRomIdMap,
  getInstalledRelaunchOptions,
  invalidateCachedGameDetail,
  getMetadataCachePage,
  releasePruneConflictLease,
  renewPruneConflictLease,
  waitForPruneRelease,
  type UpdateOutcome,
} from "./api/backend";
import { registerGameDetailPatch } from "./bigpicture/patches/gameDetailPatch";
import { registerLaunchInterceptor } from "./utils/launchInterceptor";
import { getSettingsResetState, setSettingsResetState } from "./utils/settingsResetStore";
import { getUpdateNoticeState, resetUpdateNoticeStoreForTests } from "./utils/updateNoticeStore";
import { getUpdateOutcomeState, resetUpdateOutcomeStoreForTests } from "./utils/updateOutcomeStore";
import {
  getUpdateInstallAttempt,
  resetUpdateInstallStoreForTests,
  setUpdateInstallAttempt,
} from "./utils/updateInstallStore";
import { resetFailedUpdateToastsForTests } from "./utils/failedUpdateToast";
import { getStoppedUpdateAttempt, resetStoppedUpdateStoreForTests } from "./utils/stoppedUpdateStore";
import { getDownloadState, setDownloads } from "./utils/downloadStore";
import { getSyncProgress, setSyncProgress } from "./utils/syncProgress";
import { estimateApplySeconds } from "./utils/syncEstimate";
import { resetEta, weightedCoarseFraction } from "./utils/syncEta";
import { recordSyncCreated, resetSyncDelta, getSyncDelta } from "./utils/syncDeltaStore";
import { resetSyncCancel } from "./utils/syncManager";
import { beginPrunePreview, beginPruneRun, getPruneState, resetPruneState } from "./utils/pruneStore";
import type { StartupReport } from "./boot/steamModules";
import type {
  DownloadCompleteEvent,
  DownloadProgressEvent,
  SyncPlanData,
  SyncProgress,
  SyncStaleData,
  RomMetadata,
} from "./types";

// The start-up check gates everything the factory does, and under this suite it
// would answer NO for almost every search: the global `@decky/ui` stub in
// `test-setup.ts` hands back `undefined` for the class maps, the tabbed page, the
// scroll panel and the module finder, exactly as a Steam that had moved them
// would. Left unmocked, every test below would be handed the fallback page.
//
// So the answer is supplied here, and the one test that wants the other answer
// sets it. It is a stand-in for the CHECK, never for the searches — what the
// check itself reads is pinned in `boot/steamModules.test.ts`, where nothing is
// stubbed away.
const everythingResolved = (): StartupReport => ({
  everySearchAnswered: true,
  panelMayMount: true,
  missing: [],
  missingPackageNames: [],
  checked: 33,
});
let startupAnswer: StartupReport = everythingResolved();
vi.mock("./boot/steamModules", async () => {
  const actual = await vi.importActual<typeof import("./boot/steamModules")>("./boot/steamModules");
  return { ...actual, checkSteamModules: () => startupAnswer };
});

// index.tsx's last act is to hand the factory to the Quick Access installer,
// which is how the panel reaches the screen in Steam. Under this suite that
// would run the factory once at IMPORT — before the mocks below have a value to
// hand it — and would then patch renderers the stubbed `@decky/ui` cannot find.
// What a render pass does to a tab array is `qam/quickAccessEntry.test.ts`'s;
// the install itself reaches Steam and is device-verified only. What this
// file exercises is the factory, which it calls itself.
vi.mock("./qam/installEntry", () => ({
  installQuickAccessEntry: vi.fn(() => ({ patched: true })),
}));

vi.mock("./bigpicture/patches/gameDetailPatch", () => ({
  registerGameDetailPatch: vi.fn(),
}));
vi.mock("./utils/rommAppIds", () => ({
  registerRomMAppId: vi.fn(),
  unregisterRomMAppId: vi.fn(),
}));
vi.mock("./utils/metadataPatches", () => ({
  registerMetadataPatches: vi.fn(),
  applyAllPlaytime: vi.fn().mockResolvedValue(undefined),
  applyAllMetadata: vi.fn().mockResolvedValue(undefined),
}));
vi.mock("./utils/launchInterceptor", () => ({
  registerLaunchInterceptor: vi.fn(),
}));
vi.mock("./utils/sessionManager", () => ({
  initSessionManager: vi.fn().mockResolvedValue(undefined),
}));

const handlePruneAction = vi.fn().mockResolvedValue(undefined);
vi.mock("./utils/pruneActions", () => ({
  handlePruneAction: (...args: unknown[]) => handlePruneAction(...args),
  cancelPruneActions: vi.fn(),
}));
const publishCommittedVersionSwitch = vi.fn().mockResolvedValue(undefined);
vi.mock("./utils/versionSwitchApplication", () => ({
  publishCommittedVersionSwitch: (...args: unknown[]) => publishCommittedVersionSwitch(...args),
}));
vi.mock("./utils/syncManager", () => ({
  initUnitSyncManager: vi.fn(() => () => {}),
  resetSyncCancel: vi.fn(),
}));

// Observe the collection create/update + stale-cleanup calls fired by
// onSyncComplete. getHostname resolves a fixed hostname so the machine-scoped
// `RomM: <platform> (steamdeck)` suffix is deterministic.
const createOrUpdateCollections = vi.fn().mockResolvedValue(undefined);
const createOrUpdateRomMCollections = vi.fn().mockResolvedValue(undefined);
const clearPlatformCollection = vi.fn().mockResolvedValue(undefined);
vi.mock("./utils/collections", () => ({
  createOrUpdateCollections: (...args: unknown[]) => createOrUpdateCollections(...args),
  createOrUpdateRomMCollections: (...args: unknown[]) => createOrUpdateRomMCollections(...args),
  clearPlatformCollection: (...args: unknown[]) => clearPlatformCollection(...args),
  getHostname: vi.fn().mockResolvedValue("steamdeck"),
}));

// Observe the launch-options confirm-poll.
const setLaunchOptionsConfirmed = vi.fn().mockResolvedValue(true);
const removeShortcut = vi.fn();
vi.mock("./utils/steamShortcuts", () => ({
  removeShortcut: (...args: unknown[]) => removeShortcut(...args),
  setLaunchOptionsConfirmed: (...args: unknown[]) => setLaunchOptionsConfirmed(...args),
}));

// Steam's globals the update announcement waits on are not stubbed here, so
// the wait answers at once; what it waits for is tested beside the store.
vi.mock("./utils/steamReadyForToasts", async () => {
  const actual = await vi.importActual<typeof import("./utils/steamReadyForToasts")>("./utils/steamReadyForToasts");
  return { ...actual, waitUntilSteamCanShowToasts: vi.fn().mockResolvedValue({ inTime: true, unmet: [] }) };
});

// Observe the surfaced error message (post-catch side effect).
const logError = vi.fn();
vi.mock("./api/backend", async () => {
  const actual = await vi.importActual<typeof import("./api/backend")>("./api/backend");
  return {
    ...actual,
    invalidateCachedGameDetail: vi.fn(),
    logError: (...args: unknown[]) => logError(...args),
    logInfo: vi.fn(),
  };
});

// Main stands in for whichever page the router has mounted. The two flags carry
// the two things a page can say to the router's focus effect: `ownsEntryFocus`
// is the marker of a page that places its own, and `declaresEntryStop` wraps the
// SECOND button in the declaration a page makes when its first stop is not where
// it wants to open. React drops an attribute whose value is undefined, so each
// renders only in its own case.
let mainPageOwnsEntryFocus = false;
let mainPageDeclaresEntryStop = false;
vi.mock("./bigpicture/MainPage", () => ({
  MainPage: ({ onNavigate }: { onNavigate: (page: string) => void }) =>
    createElement(
      "div",
      { "data-romm-owns-entry-focus": mainPageOwnsEntryFocus ? "" : undefined },
      createElement("button", null, "first button"),
      createElement(
        "div",
        { "data-romm-entry-stop": mainPageDeclaresEntryStop ? "" : undefined },
        createElement("button", { onClick: () => onNavigate("downloads") }, "go to downloads"),
      ),
    ),
}));
vi.mock("./bigpicture/DownloadQueue", () => ({
  DownloadQueue: () => createElement("div", null, "downloads page"),
}));
const relocateShortcutsToLauncher = vi.fn().mockResolvedValue({ status: "relocated" });
vi.mock("./utils/launcherRelocation", () => ({
  relocateShortcutsToLauncher: () => relocateShortcutsToLauncher(),
}));

import { applyAllPlaytime, registerMetadataPatches, applyAllMetadata } from "./utils/metadataPatches";
import { getLauncherState, setLauncherRelocated } from "./utils/launcherStore";
import {
  notificationsUnavailable,
  resetNotificationsHealthForTests,
  setNotificationsUnavailable,
} from "./utils/notificationsHealth";
import { registerRomMAppId, unregisterRomMAppId } from "./utils/rommAppIds";
import { installQuickAccessEntry } from "./qam/installEntry";
import "./index";

// Importing `./index` runs its last act, which hands the factory to the Quick
// Access installer mocked above — so the factory is taken from that call, the
// one argument index.tsx really passes. Calling it registers the listeners and
// returns the plugin descriptor with the panel itself.
const pluginFactory: () => Plugin = vi.mocked(installQuickAccessEntry).mock.calls[0]![0];

function flush(): Promise<void> {
  return new Promise((r) => setTimeout(r, 0));
}

/** The install's read where nothing is offered and no attempt was made. */
const NOTHING_INSTALLING = {
  offered: false,
  version: null,
  wait_reasons: [],
  paused_downloads: 0,
  attempt: null,
  try_again: false,
};

beforeEach(() => {
  // The metadata cache is paged at init; default to a single empty page so
  // loadAppIdsAndMetadata terminates and reaches initDone in every test. Cases
  // that assert init behaviour rely on this resolving (the raw endpoint stub
  // resolves undefined, which would throw on `page.total`).
  vi.mocked(getMetadataCachePage).mockResolvedValue({ items: {}, total: 0 });
  // The sync-progress store is a real module — reset it so an etaSeconds set by
  // one test's sync_plan doesn't leak into the next.
  setSyncProgress({ running: false, stage: "", current: 0, total: 0, message: "" });
  resetPruneState();
  handlePruneAction.mockClear();
  publishCommittedVersionSwitch.mockClear();
  vi.mocked(waitForPruneRelease).mockReset().mockResolvedValue({
    success: true,
    message: "Cleanup claim is released.",
  });
  vi.mocked(releasePruneConflictLease).mockReset().mockResolvedValue({ success: true, message: "released" });
  vi.mocked(invalidateCachedGameDetail).mockClear();
  vi.mocked(getUpdateInstallState).mockResolvedValue(NOTHING_INSTALLING);
  // The global afterEach's vi.unstubAllGlobals wipes the Steam ambient globals
  // after the file's first test; several sync_complete paths read SteamClient /
  // appStore, so default them to no-ops here.
  vi.stubGlobal("SteamClient", { Apps: {} });
  vi.stubGlobal("appStore", { GetAppOverviewByAppID: () => null, allApps: [] });
});

describe("index.tsx — what the factory does when a Steam search found nothing", () => {
  const failing: StartupReport = {
    everySearchAnswered: false,
    panelMayMount: false,
    missing: ["Focusable", "PanelSection"],
    missingPackageNames: ["Focusable", "PanelSection"],
    checked: 33,
  };
  // The refusal is logged on purpose, and the suite fails a test that emits an
  // unexpected `console.error` — so the spy both permits it and makes the line
  // an assertion rather than noise nobody reads.
  let consoleError: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    startupAnswer = failing;
    consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleError.mockRestore();
    startupAnswer = everythingResolved();
  });

  it("mounts the fallback page instead of the panel, and names what is missing", () => {
    const plugin = pluginFactory();
    render(createElement("div", null, plugin.content));

    expect(screen.getByText(/can't start right now/i)).toBeInTheDocument();
    expect(screen.getByText(/Focusable, PanelSection/)).toBeInTheDocument();
  });

  it("puts the same names in the log, where a user with no panel can still reach them", () => {
    pluginFactory();
    expect(consoleError).toHaveBeenCalledWith(expect.stringContaining("Missing: Focusable, PanelSection"));
  });

  it("registers nothing at all — not the patches, not the listeners, not the relocation", () => {
    // A half-working panel acts on what it cannot see. Nothing below the check
    // is written to run without the components it was written against, so the
    // refusal has to be total rather than a degraded panel: the pages whose
    // lookups happened to resolve would still reach the ones that did not.
    vi.mocked(registerGameDetailPatch).mockClear();
    vi.mocked(registerLaunchInterceptor).mockClear();
    relocateShortcutsToLauncher.mockClear();

    pluginFactory();

    expect(registerGameDetailPatch).not.toHaveBeenCalled();
    expect(registerLaunchInterceptor).not.toHaveBeenCalled();
    expect(relocateShortcutsToLauncher).not.toHaveBeenCalled();
    expect(hostEventListenerCount("sync_progress")).toBe(0);
    expect(hostEventListenerCount("download_complete")).toBe(0);
  });
});

describe("index.tsx — what the factory does when only a decoration was not found", () => {
  const cosmeticOnly: StartupReport = {
    everySearchAnswered: false,
    panelMayMount: true,
    missing: ["ControllerGlyph"],
    missingPackageNames: [],
    checked: 33,
  };
  let consoleWarn: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    startupAnswer = cosmeticOnly;
    consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    consoleWarn.mockRestore();
    startupAnswer = everythingResolved();
  });

  it("mounts the panel and registers everything, with no failure page", () => {
    // The whole point: a glyph the one place that draws it already renders
    // without must not cost the user their interface.
    vi.mocked(registerGameDetailPatch).mockClear();
    const plugin = pluginFactory();
    render(createElement("div", null, plugin.content));

    expect(screen.queryByText(/can't start right now/i)).not.toBeInTheDocument();
    expect(registerGameDetailPatch).toHaveBeenCalled();
  });

  it("reports it in the log, which is the only place it is reported at all", () => {
    pluginFactory();
    expect(consoleWarn).toHaveBeenCalledWith(expect.stringContaining("Missing: ControllerGlyph"));
    expect(consoleWarn).toHaveBeenCalledWith(expect.stringContaining("a newer Tender is the repair"));
  });
});

describe("index.tsx — what the factory records about the toasts", () => {
  // The notice on Main is rendered off a module store rather than a probe, so
  // the factory is the one place the start-up check's answer reaches it. Both
  // directions, because a flag that was never written reads as "all well" and
  // the notice would simply never appear.
  afterEach(() => {
    startupAnswer = everythingResolved();
    resetNotificationsHealthForTests();
  });

  it("records that the notice is owed when a lookup a toast is raised through missed", () => {
    const consoleWarn = vi.spyOn(console, "warn").mockImplementation(() => {});
    startupAnswer = {
      everySearchAnswered: false,
      panelMayMount: true,
      missing: ["ToastRenderer"],
      missingPackageNames: [],
      checked: 33,
    };
    pluginFactory();
    expect(notificationsUnavailable()).toBe(true);
    expect(consoleWarn).toHaveBeenCalledWith(expect.stringContaining("Tender's notifications are off"));
    consoleWarn.mockRestore();
  });

  it("records nothing owed when every search answered", () => {
    setNotificationsUnavailable(true);
    pluginFactory();
    expect(notificationsUnavailable()).toBe(false);
  });
});

describe("index.tsx — launcher relocation at panel load", () => {
  beforeEach(() => {
    setLauncherRelocated(false);
    relocateShortcutsToLauncher.mockReset().mockResolvedValue({ status: "relocated" });
  });

  it("points the shortcuts at the launcher without the panel being opened", async () => {
    pluginFactory();
    await act(flush);

    expect(relocateShortcutsToLauncher).toHaveBeenCalledTimes(1);
    expect(getLauncherState().relocated).toBe(true);
  });

  it("leaves the relocation unestablished when the backend blocked the rewrite", async () => {
    relocateShortcutsToLauncher.mockResolvedValue({ status: "blocked" });

    pluginFactory();
    await act(flush);

    expect(getLauncherState().relocated).toBe(false);
  });

  it("leaves the relocation unestablished when the pass throws", async () => {
    relocateShortcutsToLauncher.mockRejectedValue(new Error("shortcut store exploded"));

    pluginFactory();
    await act(flush);

    expect(getLauncherState().relocated).toBe(false);
    expect(logError).toHaveBeenCalledWith(expect.stringContaining("shortcut store exploded"));
  });
});

describe("index.tsx — persistent prune listeners", () => {
  it("handles tokenized Steam actions in the panel's entry module", async () => {
    pluginFactory();
    beginPrunePreview("preview-1");
    const action = {
      run_id: "run-1",
      preview_id: "preview-1",
      action_token: "token-1",
      action: "remove_shortcut" as const,
      app_id: 9001,
    };
    expect(hostEventListenerCount("prune_action_required")).toBe(1);

    await act(async () => {
      emitHostEvent("prune_action_required", action);
      await Promise.resolve();
    });

    expect(handlePruneAction).toHaveBeenCalledWith(action);
  });

  it("stores progress and completion, invalidates affected details, and emits a refresh", async () => {
    pluginFactory();
    beginPrunePreview("preview-1");
    const changed = vi.fn();
    globalThis.addEventListener("romm_data_changed", changed);

    act(() => {
      emitHostEvent("prune_progress", {
        run_id: "run-1",
        preview_id: "preview-1",
        current: 1,
        total: 2,
        stage: "checking",
        rom_ids: [7],
        name: "Removed Game",
      });
      emitHostEvent("prune_complete", {
        success: true,
        partial: false,
        run_id: "run-1",
        preview_id: "preview-1",
        removed_rom_ids: [7],
        affected_app_ids: [9001],
        removed_app_ids: [9001],
        results: [{ group_id: "group-1", rom_ids: [7], status: "removed", message: "Removed." }],
      });
    });

    expect(getPruneState().progress).toBeNull();
    expect(getPruneState().complete?.removed_rom_ids).toEqual([7]);
    expect(invalidateCachedGameDetail).toHaveBeenCalledWith(9001);
    expect(unregisterRomMAppId).toHaveBeenCalledWith(9001);
    expect(changed).toHaveBeenCalledTimes(1);
    expect(toaster.toast).toHaveBeenCalledWith({ title: "Tender", body: "Removed 1 local entry." });

    globalThis.removeEventListener("romm_data_changed", changed);
  });

  it("a foreign or duplicate terminal frame has no root side effects", () => {
    pluginFactory();
    const changed = vi.fn();
    globalThis.addEventListener("romm_data_changed", changed);
    vi.mocked(unregisterRomMAppId).mockClear();
    beginPrunePreview("preview-current");
    beginPruneRun("current", "preview-current");
    const frame = {
      success: true,
      partial: false,
      run_id: "old",
      preview_id: "preview-old",
      chunk_index: 0,
      final: true,
      removed_rom_ids: [7],
      affected_app_ids: [9001],
      removed_app_ids: [9001],
      results: [{ group_id: "group-1", rom_ids: [7], status: "removed" as const, message: "Removed." }],
    };

    act(() => {
      emitHostEvent("prune_complete", frame);
      emitHostEvent("prune_complete", frame);
    });

    expect(getPruneState().runId).toBe("current");
    expect(invalidateCachedGameDetail).not.toHaveBeenCalled();
    expect(unregisterRomMAppId).not.toHaveBeenCalled();
    expect(changed).not.toHaveBeenCalled();
    globalThis.removeEventListener("romm_data_changed", changed);
  });

  it("surfaces a zero-row committed partial instead of reporting that nothing changed", () => {
    pluginFactory();
    beginPrunePreview("preview-partial");

    act(() => {
      emitHostEvent("prune_complete", {
        success: false,
        partial: true,
        run_id: "run-partial",
        preview_id: "preview-partial",
        removed_count: 0,
        problem_count: 1,
        removed_rom_ids: [],
        affected_app_ids: [9001],
        removed_app_ids: [9001],
        results: [
          {
            group_id: "group-1",
            rom_ids: [7],
            status: "partial",
            committed_action: "remove_shortcut",
            message: "Steam removed the shortcut, but local cleanup was retained.",
          },
        ],
      });
    });

    expect(toaster.toast).toHaveBeenCalledWith({
      title: "Tender",
      body: "Shortcut removal committed; local cleanup incomplete.",
      subtext: "Steam removed the shortcut, but local cleanup was retained.",
    });
  });

  it("hands back a continuation lease the terminal frame gave it nothing to do with", async () => {
    pluginFactory();
    beginPrunePreview("preview-nothing");

    await act(async () => {
      emitHostEvent("prune_complete", {
        success: true,
        partial: false,
        run_id: "run-nothing",
        preview_id: "preview-nothing",
        // The lease is attached by the backend emit path; this run committed no
        // repoint, so publishPruneSwitches is never called for it.
        publication_required: true,
        prune_lease_token: "orphan-lease",
        removed_rom_ids: [7],
        affected_app_ids: [],
        results: [{ group_id: "group-1", rom_ids: [7], status: "removed", message: "Removed." }],
      });
      await Promise.resolve();
    });

    // Without this the lease refuses the next cleanup's start for its full
    // 300s TTL.
    await waitFor(() => expect(releasePruneConflictLease).toHaveBeenCalledWith("orphan-lease"));
  });

  it("publishes a known committed partial repoint after terminal completion", async () => {
    pluginFactory();
    beginPrunePreview("preview-repoint");
    let release: ((value: { success: true; message: string }) => void) | undefined;
    vi.mocked(waitForPruneRelease).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve;
        }),
    );

    act(() => {
      emitHostEvent("prune_complete", {
        success: false,
        partial: true,
        run_id: "run-repoint-partial",
        preview_id: "preview-repoint",
        publication_required: true,
        prune_lease_token: "publication-lease",
        removed_rom_ids: [],
        affected_app_ids: [9001],
        results: [
          {
            group_id: "group-1",
            rom_ids: [7, 8],
            status: "partial",
            committed_action: "repoint_shortcut",
            app_id: 9001,
            target_rom_id: 8,
            message: "The shortcut changed; source data was retained.",
          },
        ],
      });
    });
    await flush();

    expect(publishCommittedVersionSwitch).not.toHaveBeenCalled();
    expect(releasePruneConflictLease).not.toHaveBeenCalledWith("publication-lease");
    release?.({ success: true, message: "released" });
    await flush();
    expect(publishCommittedVersionSwitch).toHaveBeenCalledWith(9001, 8, undefined, expect.any(AbortSignal));
    expect(releasePruneConflictLease).toHaveBeenCalledWith("publication-lease");
  });

  it("does not publish an ambiguous repoint outcome", async () => {
    pluginFactory();
    beginPrunePreview("preview-ambiguous");

    act(() => {
      emitHostEvent("prune_complete", {
        success: false,
        partial: true,
        run_id: "run-repoint-ambiguous",
        preview_id: "preview-ambiguous",
        removed_rom_ids: [],
        affected_app_ids: [9001],
        results: [
          {
            group_id: "group-1",
            rom_ids: [7, 8],
            status: "partial",
            committed_action: "repoint_shortcut",
            action_ambiguous: true,
            app_id: 9001,
            target_rom_id: 8,
            message: "The repoint outcome is unknown.",
          },
        ],
      });
    });
    await flush();

    expect(publishCommittedVersionSwitch).not.toHaveBeenCalled();
    expect(toaster.toast).toHaveBeenCalledWith({
      title: "Tender",
      body: "Shortcut repoint outcome is uncertain; source data was retained.",
      subtext: "The repoint outcome is unknown.",
    });
  });

  it("fails closed when a committed repoint terminal frame has no publication lease", async () => {
    pluginFactory();
    beginPrunePreview("preview-missing-publication-lease");

    act(() => {
      emitHostEvent("prune_complete", {
        success: true,
        partial: false,
        run_id: "run-missing-publication-lease",
        preview_id: "preview-missing-publication-lease",
        publication_required: true,
        removed_rom_ids: [7],
        affected_app_ids: [9001],
        results: [
          {
            group_id: "group-1",
            rom_ids: [7, 8],
            status: "repointed",
            committed_action: "repoint_shortcut",
            app_id: 9001,
            target_rom_id: 8,
            message: "Repointed.",
          },
        ],
      });
    });
    await flush();

    expect(waitForPruneRelease).not.toHaveBeenCalledWith("run-missing-publication-lease");
    expect(publishCommittedVersionSwitch).not.toHaveBeenCalled();
    expect(logError).toHaveBeenCalledWith(
      "Cleanup publication was skipped because its continuation lease was missing.",
    );
  });
});

describe("index.tsx — download_complete launch-options sync", () => {
  beforeEach(() => {
    setLaunchOptionsConfirmed.mockClear();
    setLaunchOptionsConfirmed.mockResolvedValue(true);
    logError.mockClear();
  });

  it("confirm-sets launch options for the payload appId on download_complete", async () => {
    pluginFactory();

    const event: DownloadCompleteEvent = {
      rom_id: 42,
      rom_name: "Test ROM",
      platform_name: "PSX",
      file_path: "/games/test.bin",
      app_id: 5000,
      launch_options: 'flatpak run net.retrodeck.retrodeck "/games/test.bin"',
    };
    act(() => {
      emitHostEvent<DownloadCompleteEvent>("download_complete", event);
    });
    await flush();

    expect(setLaunchOptionsConfirmed).toHaveBeenCalledWith(
      5000,
      'flatpak run net.retrodeck.retrodeck "/games/test.bin"',
    );
  });

  it("no-ops gracefully when the downloaded rom has no bound appId (null)", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<DownloadCompleteEvent>("download_complete", {
        rom_id: 999,
        rom_name: "Unsynced",
        platform_name: "PSX",
        file_path: "/games/u.bin",
        app_id: null,
        launch_options: 'flatpak run net.retrodeck.retrodeck "/games/u.bin"',
      });
    });
    await flush();

    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
  });

  it("surfaces a logError when setLaunchOptionsConfirmed rejects", async () => {
    setLaunchOptionsConfirmed.mockRejectedValue(new Error("set failed"));
    pluginFactory();

    act(() => {
      emitHostEvent<DownloadCompleteEvent>("download_complete", {
        rom_id: 42,
        rom_name: "Test ROM",
        platform_name: "PSX",
        file_path: "/games/test.bin",
        app_id: 5000,
        launch_options: 'flatpak run net.retrodeck.retrodeck "/games/test.bin"',
      });
    });
    await flush();

    expect(logError).toHaveBeenCalledWith(
      expect.stringContaining("download_complete: failed to set launch options for rom 42"),
    );
  });
});

describe("index.tsx — download_progress cancelled eviction (#149 downloads-round)", () => {
  it("drops the entry from the store when a cancelled frame arrives", async () => {
    pluginFactory();
    setDownloads([
      {
        rom_id: 42,
        rom_name: "Paused",
        platform_name: "N64",
        file_name: "game.z64",
        status: "paused",
        progress: 0.5,
        bytes_downloaded: 500,
        total_bytes: 1000,
        resumable: true,
      },
    ]);

    act(() => {
      emitHostEvent<DownloadProgressEvent>("download_progress", {
        rom_id: 42,
        rom_name: "Paused",
        platform_name: "N64",
        file_name: "game.z64",
        status: "cancelled",
        progress: 0.5,
        bytes_downloaded: 500,
        total_bytes: 1000,
        resumable: true,
      });
    });

    // Explicit discard → no residue in the store (which MainPage's count + the
    // DownloadQueue view both read).
    expect(getDownloadState().some((d) => d.rom_id === 42)).toBe(false);
  });

  it("updates in place (does not drop) for a non-cancelled frame", async () => {
    pluginFactory();
    setDownloads([]);

    act(() => {
      emitHostEvent<DownloadProgressEvent>("download_progress", {
        rom_id: 7,
        rom_name: "Live",
        platform_name: "N64",
        file_name: "g.z64",
        status: "downloading",
        progress: 0.2,
        bytes_downloaded: 200,
        total_bytes: 1000,
        resumable: false,
      });
    });

    expect(getDownloadState().find((d) => d.rom_id === 7)?.status).toBe("downloading");
  });
});

describe("index.tsx — sync_stale listener", () => {
  beforeEach(() => {
    removeShortcut.mockClear();
    logError.mockClear();
  });

  it("removes each stale shortcut by the payload app_id (no rom_id→app_id re-resolve)", async () => {
    // No getExistingRomMShortcuts is even imported — proving the orphan race is
    // gone: removal happens via the payload app_id the backend captured before
    // unbinding, so an empty backend map can't strand the shortcut.
    pluginFactory();

    act(() => {
      emitHostEvent<SyncStaleData>("sync_stale", {
        remove: [
          { rom_id: 99, app_id: 9900 },
          { rom_id: 77, app_id: 7700 },
        ],
      });
    });
    await flush();

    expect(removeShortcut).toHaveBeenCalledWith(9900);
    expect(removeShortcut).toHaveBeenCalledWith(7700);
    expect(removeShortcut).toHaveBeenCalledTimes(2);
  });

  it("ignores an empty remove array", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncStaleData>("sync_stale", { remove: [] });
    });
    await flush();

    expect(removeShortcut).not.toHaveBeenCalled();
  });

  it("chunk-paces a large stale removal (25 back-to-back, 50ms breather) and records the delta up front (#977)", async () => {
    pluginFactory();
    await flush();
    removeShortcut.mockClear();
    resetSyncDelta();

    // 26 stale shortcuts = one full 25-item chunk + a remainder, so exactly one
    // 50ms breather must fall between the two chunks.
    const remove = Array.from({ length: 26 }, (_, i) => ({ rom_id: i + 1, app_id: 1000 + i }));

    vi.useFakeTimers();
    try {
      act(() => {
        emitHostEvent<SyncStaleData>("sync_stale", { remove });
      });
      await act(async () => {
        for (let i = 0; i < 40; i++) await Promise.resolve();
      });
      // First 25-item chunk removed back-to-back; the 26th is gated behind the breather.
      expect(removeShortcut).toHaveBeenCalledTimes(25);
      // The removed-delta for ALL 26 is recorded up front, so a sync_complete that
      // interleaves during the paced breather reads the true count, not a partial one.
      expect(getSyncDelta().removed).toBe(26);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      expect(removeShortcut).toHaveBeenCalledTimes(26);
    } finally {
      vi.useRealTimers();
    }
  });

  it("holds its own event lease through a paced tail when sync_complete never arrives", async () => {
    pluginFactory();
    await flush();
    vi.mocked(releasePruneConflictLease).mockClear();
    const remove = Array.from({ length: 26 }, (_, i) => ({ rom_id: i + 1, app_id: 2000 + i }));

    vi.useFakeTimers();
    try {
      act(() => {
        emitHostEvent<SyncStaleData>("sync_stale", { remove, prune_lease_token: "standalone-stale-lease" });
      });
      await act(async () => {
        for (let i = 0; i < 40; i++) await Promise.resolve();
      });
      expect(removeShortcut).toHaveBeenCalledTimes(25);
      expect(releasePruneConflictLease).not.toHaveBeenCalledWith("standalone-stale-lease");

      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      expect(removeShortcut).toHaveBeenCalledTimes(26);
      await vi.waitFor(() => expect(releasePruneConflictLease).toHaveBeenCalledWith("standalone-stale-lease"));
    } finally {
      vi.useRealTimers();
    }
  });

  it("catches a rejecting stale tail so it never wedges the later sync_complete continuation", async () => {
    pluginFactory();
    await flush();
    vi.mocked(releasePruneConflictLease).mockClear();
    vi.mocked(renewPruneConflictLease).mockResolvedValue({ success: true, message: "renewed" });
    removeShortcut.mockClear();
    logError.mockClear();
    createOrUpdateCollections.mockClear();
    // A Steam removal that outlasts the continuation's five-minute bound: the
    // stored promise REJECTS at the bound, and the removal itself settles only
    // afterwards.
    let finishRemoval!: () => void;
    removeShortcut.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          finishRemoval = resolve;
        }),
    );

    vi.useFakeTimers();
    try {
      act(() => {
        emitHostEvent<SyncStaleData>("sync_stale", {
          remove: [{ rom_id: 1, app_id: 3000 }],
          prune_lease_token: "rejecting-stale-lease",
        });
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(300_001);
      });

      // Post-catch state: the failure is surfaced where the tail is STORED, so the
      // stored promise is settled (nothing waits on an unhandled rejection), and
      // the token is released once the removal it was held for settles.
      expect(logError).toHaveBeenCalledWith(expect.stringContaining("stale shortcut removal failed"));
      expect(removeShortcut).toHaveBeenCalledTimes(1);
      finishRemoval();
      await act(async () => {
        for (let i = 0; i < 10; i++) await Promise.resolve();
      });
      expect(releasePruneConflictLease).toHaveBeenCalledWith("rejecting-stale-lease");
    } finally {
      vi.useRealTimers();
    }

    // The completion continuation awaits that same tail and still runs its
    // sibling reconciles to the end instead of being aborted by it.
    act(() => {
      emitHostEvent<SyncCompleteAfterStaleFailure>("sync_complete", {
        platform_app_ids: { gba: [3000] },
        total_games: 1,
        prune_lease_token: "completion-after-failed-tail",
      });
    });
    await flush();

    expect(createOrUpdateCollections).toHaveBeenCalled();
    await vi.waitFor(() => expect(releasePruneConflictLease).toHaveBeenCalledWith("completion-after-failed-tail"));
  });
});

type SyncCompleteAfterStaleFailure = {
  platform_app_ids: Record<string, number[]>;
  total_games: number;
  prune_lease_token?: string;
};

describe("index.tsx — migration_relaunch_options listener", () => {
  beforeEach(() => {
    setLaunchOptionsConfirmed.mockClear();
    setLaunchOptionsConfirmed.mockResolvedValue(true);
    logError.mockClear();
  });

  it("confirm-sets launch options for each migrated item", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<{ items: { app_id: number; launch_options: string }[] }>("migration_relaunch_options", {
        items: [
          { app_id: 100, launch_options: 'flatpak run net.retrodeck.retrodeck "/new/a.bin"' },
          { app_id: 200, launch_options: 'flatpak run net.retrodeck.retrodeck "/new/b.bin"' },
        ],
      });
    });
    await flush();

    expect(setLaunchOptionsConfirmed).toHaveBeenCalledWith(100, 'flatpak run net.retrodeck.retrodeck "/new/a.bin"');
    expect(setLaunchOptionsConfirmed).toHaveBeenCalledWith(200, 'flatpak run net.retrodeck.retrodeck "/new/b.bin"');
  });

  it("ignores an empty items array", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<{ items: { app_id: number; launch_options: string }[] }>("migration_relaunch_options", {
        items: [],
      });
    });
    await flush();

    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
  });

  it("surfaces a logError when setLaunchOptionsConfirmed rejects for an item", async () => {
    setLaunchOptionsConfirmed.mockRejectedValue(new Error("set failed"));
    pluginFactory();

    act(() => {
      emitHostEvent<{ items: { app_id: number; launch_options: string }[] }>("migration_relaunch_options", {
        items: [{ app_id: 100, launch_options: 'flatpak run net.retrodeck.retrodeck "/new/a.bin"' }],
      });
    });
    await flush();

    expect(logError).toHaveBeenCalledWith(
      expect.stringContaining("migration_relaunch_options: failed to set launch options for appId 100"),
    );
  });
});

describe("index.tsx — startup launch-options reconcile (#1043)", () => {
  const relaunchOptions = (items: { app_id: number; launch_options: string }[]) => ({
    success: true as const,
    items,
    prune_lease_token: items.length > 0 ? "installed-lease" : null,
  });

  beforeEach(() => {
    setLaunchOptionsConfirmed.mockClear();
    setLaunchOptionsConfirmed.mockResolvedValue(true);
    logError.mockClear();
    // Make the init detach reach initDone=true so the reconcile fires.
    vi.mocked(getAllPlaytime).mockResolvedValue({ playtime: {} });
    vi.mocked(getAppIdRomIdMap).mockResolvedValue({});
    // Settle the sibling reset-notice detach so its .catch doesn't muddy logError.
    vi.mocked(getSettingsResetNotice).mockResolvedValue({ pending: false, backed_up_to: null });
    vi.mocked(getInstalledRelaunchOptions).mockReset();
  });

  it("confirm-sets launch options for each reconciled item after init", async () => {
    vi.mocked(getInstalledRelaunchOptions).mockResolvedValue(
      relaunchOptions([
        { app_id: 100, launch_options: 'flatpak run net.retrodeck.retrodeck "/roms/a.bin"' },
        { app_id: 200, launch_options: 'flatpak run net.retrodeck.retrodeck "/roms/b.bin"' },
      ]),
    );
    pluginFactory();
    await flush();

    expect(setLaunchOptionsConfirmed).toHaveBeenCalledWith(100, 'flatpak run net.retrodeck.retrodeck "/roms/a.bin"');
    expect(setLaunchOptionsConfirmed).toHaveBeenCalledWith(200, 'flatpak run net.retrodeck.retrodeck "/roms/b.bin"');
    expect(logError).not.toHaveBeenCalledWith(expect.stringContaining("startup_reconcile"));
  });

  it("never confirm-sets when there is nothing installed to reconcile", async () => {
    vi.mocked(getInstalledRelaunchOptions).mockResolvedValue(relaunchOptions([]));
    pluginFactory();
    await flush();

    expect(getInstalledRelaunchOptions).toHaveBeenCalled();
    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
  });

  it("surfaces a startup_reconcile-prefixed logError when a confirm returns false", async () => {
    setLaunchOptionsConfirmed.mockResolvedValue(false);
    vi.mocked(getInstalledRelaunchOptions).mockResolvedValue(
      relaunchOptions([{ app_id: 100, launch_options: 'flatpak run net.retrodeck.retrodeck "/roms/a.bin"' }]),
    );
    pluginFactory();
    await flush();

    expect(setLaunchOptionsConfirmed).toHaveBeenCalledWith(100, 'flatpak run net.retrodeck.retrodeck "/roms/a.bin"');
    expect(logError).toHaveBeenCalledWith("startup_reconcile: failed to confirm launch options for appId 100");
  });

  it("surfaces a startup_reconcile-prefixed logError when the pull endpoint rejects", async () => {
    vi.mocked(getInstalledRelaunchOptions).mockRejectedValue(new Error("pull failed"));
    pluginFactory();
    await flush();

    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
    expect(logError).toHaveBeenCalledWith(
      expect.stringContaining("startup_reconcile: failed to reconcile launch options"),
    );
  });
});

describe("index.tsx — sync_complete launch-options reconcile (#1151)", () => {
  type SyncCompletePayload = {
    platform_app_ids: Record<string, number[]>;
    romm_collection_app_ids?: Record<string, number[]>;
    total_games: number;
    cancelled?: boolean;
    prune_lease_token?: string;
  };

  const relaunchOptions = (items: { app_id: number; launch_options: string }[]) => ({
    success: true as const,
    items,
    prune_lease_token: items.length > 0 ? "installed-lease" : null,
  });

  beforeEach(() => {
    setLaunchOptionsConfirmed.mockClear();
    setLaunchOptionsConfirmed.mockResolvedValue(true);
    logError.mockClear();
    vi.mocked(getAllPlaytime).mockResolvedValue({ playtime: {} });
    vi.mocked(getAppIdRomIdMap).mockResolvedValue({});
    vi.mocked(getSettingsResetNotice).mockResolvedValue({ pending: false, backed_up_to: null });
    // The startup reconcile fires on factory init; default it to an empty set
    // so each test isolates the sync_complete-triggered reconcile below.
    vi.mocked(getInstalledRelaunchOptions).mockReset();
    vi.mocked(getInstalledRelaunchOptions).mockResolvedValue(relaunchOptions([]));
  });

  it("re-confirms launch options for every installed+bound ROM after a sync", async () => {
    pluginFactory();
    await flush(); // settle the startup reconcile (empty set)
    setLaunchOptionsConfirmed.mockClear();
    vi.mocked(getInstalledRelaunchOptions).mockClear();
    vi.mocked(getInstalledRelaunchOptions).mockResolvedValue(
      relaunchOptions([{ app_id: 100, launch_options: 'flatpak run net.retrodeck.retrodeck "/roms/a.bin"' }]),
    );

    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", {
        platform_app_ids: {},
        total_games: 0,
        cancelled: false,
      });
    });
    await flush();

    expect(getInstalledRelaunchOptions).toHaveBeenCalled();
    expect(setLaunchOptionsConfirmed).toHaveBeenCalledWith(100, 'flatpak run net.retrodeck.retrodeck "/roms/a.bin"');
    expect(logError).not.toHaveBeenCalledWith(expect.stringContaining("sync_reconcile"));
  });

  it("reconciles even when the sync was cancelled", async () => {
    pluginFactory();
    await flush();
    setLaunchOptionsConfirmed.mockClear();
    vi.mocked(getInstalledRelaunchOptions).mockClear();
    vi.mocked(getInstalledRelaunchOptions).mockResolvedValue(
      relaunchOptions([{ app_id: 200, launch_options: 'flatpak run net.retrodeck.retrodeck "/roms/b.bin"' }]),
    );

    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", {
        platform_app_ids: {},
        total_games: 0,
        cancelled: true,
      });
    });
    await flush();

    expect(setLaunchOptionsConfirmed).toHaveBeenCalledWith(200, 'flatpak run net.retrodeck.retrodeck "/roms/b.bin"');
  });

  it("surfaces a sync_reconcile-prefixed logError when the pull endpoint rejects", async () => {
    pluginFactory();
    await flush();
    setLaunchOptionsConfirmed.mockClear();
    logError.mockClear();
    vi.mocked(getInstalledRelaunchOptions).mockReset();
    vi.mocked(getInstalledRelaunchOptions).mockRejectedValue(new Error("pull failed"));

    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", {
        platform_app_ids: {},
        total_games: 0,
        cancelled: false,
      });
    });
    await flush();

    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
    expect(logError).toHaveBeenCalledWith(
      expect.stringContaining("sync_reconcile: failed to reconcile launch options"),
    );
  });

  it("holds the sync event lease until collection and sibling Steam continuations settle", async () => {
    let finishCollections: (() => void) | undefined;
    createOrUpdateCollections.mockImplementationOnce(
      () =>
        new Promise<void>((resolve) => {
          finishCollections = resolve;
        }),
    );
    pluginFactory();
    await flush();
    vi.mocked(releasePruneConflictLease).mockClear();

    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", {
        platform_app_ids: { SNES: [100] },
        total_games: 1,
        prune_lease_token: "sync-complete-lease",
      });
    });
    await vi.waitFor(() =>
      expect(createOrUpdateCollections).toHaveBeenCalledWith({ SNES: [100] }, undefined, expect.any(AbortSignal)),
    );
    expect(releasePruneConflictLease).not.toHaveBeenCalledWith("sync-complete-lease");

    finishCollections?.();
    await vi.waitFor(() => expect(releasePruneConflictLease).toHaveBeenCalledWith("sync-complete-lease"));
  });

  it("holds the sync event lease until the paced sync_stale tail settles", async () => {
    pluginFactory();
    await flush();
    vi.mocked(releasePruneConflictLease).mockClear();
    removeShortcut.mockClear();
    const remove = Array.from({ length: 26 }, (_, index) => ({ rom_id: index + 1, app_id: 1000 + index }));

    vi.useFakeTimers();
    try {
      act(() => {
        emitHostEvent<SyncStaleData>("sync_stale", { remove, prune_lease_token: "stale-event-lease" });
      });
      await act(async () => {
        for (let index = 0; index < 40; index++) await Promise.resolve();
      });
      expect(removeShortcut).toHaveBeenCalledTimes(25);
      expect(releasePruneConflictLease).not.toHaveBeenCalledWith("stale-event-lease");

      act(() => {
        emitHostEvent<SyncCompletePayload>("sync_complete", {
          platform_app_ids: {},
          total_games: 0,
          prune_lease_token: "stale-tail-lease",
        });
      });
      await act(async () => {
        for (let index = 0; index < 20; index++) await Promise.resolve();
      });
      expect(releasePruneConflictLease).not.toHaveBeenCalledWith("stale-tail-lease");

      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      expect(removeShortcut).toHaveBeenCalledTimes(26);
      await vi.waitFor(() => expect(releasePruneConflictLease).toHaveBeenCalledWith("stale-tail-lease"));
      expect(releasePruneConflictLease).toHaveBeenCalledWith("stale-event-lease");
      expect(
        vi.mocked(releasePruneConflictLease).mock.calls.filter(([token]) => token === "stale-event-lease"),
      ).toHaveLength(1);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("index.tsx — sync_complete registers RomM appIds (#1205)", () => {
  type SyncCompletePayload = {
    platform_app_ids: Record<string, number[]>;
    romm_collection_app_ids?: Record<string, number[]>;
    total_games: number;
    cancelled?: boolean;
  };

  function emitSyncComplete(payload: SyncCompletePayload): void {
    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", payload);
    });
  }

  beforeEach(() => {
    vi.mocked(registerRomMAppId).mockClear();
    logError.mockClear();
    vi.mocked(getAllPlaytime).mockResolvedValue({ playtime: {} });
    vi.mocked(getAppIdRomIdMap).mockResolvedValue({});
    vi.mocked(getSettingsResetNotice).mockResolvedValue({ pending: false, backed_up_to: null });
    vi.mocked(getInstalledRelaunchOptions).mockReset();
    vi.mocked(getInstalledRelaunchOptions).mockResolvedValue({
      success: true,
      items: [],
      prune_lease_token: null,
    });
    // Empty collectionStore so the detached stale-cleanup is a no-op here.
    vi.stubGlobal("collectionStore", { userCollections: [] });
  });

  it("registers every platform and RomM-collection appId from the payload", async () => {
    pluginFactory();
    await flush(); // settle startup detaches (they call registerRomMAppId with the empty appIdMap)
    vi.mocked(registerRomMAppId).mockClear();

    emitSyncComplete({
      platform_app_ids: { "Nintendo 64": [100, 101], PSX: [200] },
      romm_collection_app_ids: { "[Faves]": [300], "[RPGs]": [200, 400] },
      total_games: 5,
    });
    await flush();

    // Every appId across BOTH maps is registered (200 spans a platform and a
    // collection — idempotent, still fine).
    for (const appId of [100, 101, 200, 300, 400]) {
      expect(registerRomMAppId).toHaveBeenCalledWith(appId);
    }
  });

  it("registers RomM-collection appIds even when platform_app_ids is empty (collection-only sync)", async () => {
    // The #1205 core repro: a collection-only sync never populates
    // platform_app_ids, so its new shortcuts land only in romm_collection_app_ids.
    // The old platform-only loop left them unregistered until a Steam restart.
    pluginFactory();
    await flush();
    vi.mocked(registerRomMAppId).mockClear();

    emitSyncComplete({
      platform_app_ids: {},
      romm_collection_app_ids: { "[Faves]": [777, 888] },
      total_games: 2,
    });
    await flush();

    expect(registerRomMAppId).toHaveBeenCalledWith(777);
    expect(registerRomMAppId).toHaveBeenCalledWith(888);
  });
});

describe("index.tsx — corrupt-settings reset notice", () => {
  beforeEach(() => {
    vi.mocked(toaster.toast).mockClear();
    logError.mockClear();
    vi.mocked(getSettingsResetNotice).mockReset();
    // Reset the module store so a prior test's pending state doesn't leak.
    setSettingsResetState({ pending: false, backedUpTo: null });
  });

  it("populates the store and fires NO toast when the boot notice reports a reset", async () => {
    vi.mocked(getSettingsResetNotice).mockResolvedValue({
      pending: true,
      backed_up_to: "settings.json.corrupt-1781697600",
    });
    pluginFactory();
    await flush();

    // Persistent banner store is populated — surfaced by the QAM banner +
    // game-detail card, not a toast.
    expect(getSettingsResetState()).toEqual({
      pending: true,
      backedUpTo: "settings.json.corrupt-1781697600",
    });
    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("leaves the store not-pending and fires no toast when the boot notice reports no reset", async () => {
    vi.mocked(getSettingsResetNotice).mockResolvedValue({ pending: false, backed_up_to: null });
    pluginFactory();
    await flush();

    expect(getSettingsResetState()).toEqual({ pending: false, backedUpTo: null });
    expect(toaster.toast).not.toHaveBeenCalled();
  });

  it("surfaces a logError when the reset-notice check rejects", async () => {
    vi.mocked(getSettingsResetNotice).mockRejectedValue(new Error("boom"));
    pluginFactory();
    await flush();

    expect(logError).toHaveBeenCalledWith(expect.stringContaining("Failed to check settings reset notice"));
    expect(toaster.toast).not.toHaveBeenCalled();
  });
});

describe("index.tsx — the release check at panel load", () => {
  beforeEach(() => {
    logError.mockClear();
    vi.mocked(getUpdateNotice).mockReset();
    resetUpdateNoticeStoreForTests();
  });

  it("asks once and fills the store the card and the section read", async () => {
    vi.mocked(getUpdateNotice).mockResolvedValue({
      available: true,
      newer: true,
      latest_version: "0.34.0",
      current_version: "0.33.0",
      enabled: true,
      installed_program: true,
      toast_owed: false,
      seen: false,
    });
    pluginFactory();
    await flush();

    expect(getUpdateNotice).toHaveBeenCalledTimes(1);
    expect(getUpdateNoticeState().available).toBe(true);
  });

  it("does not hold the panel up while GitHub is slow to answer", () => {
    vi.mocked(getUpdateNotice).mockReturnValue(new Promise(() => {}));
    const plugin = pluginFactory();

    expect(plugin.content).toBeDefined();
    expect(getUpdateNoticeState().available).toBe(false);
  });

  it("logs a check that rejected and shows no card", async () => {
    vi.mocked(getUpdateNotice).mockRejectedValue(new Error("boom"));
    pluginFactory();
    await flush();

    expect(logError).toHaveBeenCalledWith(expect.stringContaining("Failed to check for a newer release"));
    expect(getUpdateNoticeState().available).toBe(false);
  });
});

describe("index.tsx — what the backend pushes about updates", () => {
  beforeEach(() => {
    resetUpdateNoticeStoreForTests();
    setUpdateInstallAttempt(null);
    resetStoppedUpdateStoreForTests();
  });

  it("takes a notice from the backend's own check into the store the card and the section read", () => {
    pluginFactory();

    act(() =>
      emitHostEvent("update_notice", {
        available: true,
        newer: true,
        latest_version: "0.35.0",
        current_version: "0.33.0",
        enabled: true,
        installed_program: true,
        toast_owed: false,
      }),
    );

    expect(getUpdateNoticeState()).toMatchObject({ available: true, latestVersion: "0.35.0" });
  });

  it("takes an install frame into the store Settings › Updates reads", () => {
    pluginFactory();
    const frame = {
      version: "0.35.0",
      step: "verifying",
      bytes_done: 100,
      bytes_total: 100,
      failure: null,
    };

    act(() => emitHostEvent("update_install_progress", frame));

    expect(getUpdateInstallAttempt()).toEqual(frame);
  });

  it("raises the toast the backend owes once an install frame turned failed, and acknowledges it", async () => {
    vi.stubGlobal("App", { GetServicesInitialized: () => true });
    vi.stubGlobal("securitystore", { IsLockScreenActive: () => false });
    vi.stubGlobal("SteamUIStore", { WindowStore: { GamepadUIMainWindowInstance: null } });
    vi.mocked(toaster.toast).mockClear();
    resetFailedUpdateToastsForTests();
    pluginFactory();
    await flush();
    vi.mocked(getUpdateAttemptToast).mockResolvedValue({
      attempt: 2,
      version: "0.35.0",
      failure: "checksum_mismatch",
    });

    act(() =>
      emitHostEvent("update_install_progress", {
        version: "0.35.0",
        step: "failed",
        bytes_done: 0,
        bytes_total: null,
        failure: "checksum_mismatch",
      }),
    );

    await vi.waitFor(() =>
      expect(toaster.toast).toHaveBeenCalledWith({
        title: "Tender",
        body: "Update to 0.35.0 failed. The download did not match its checksum.",
      }),
    );
    await vi.waitFor(() => expect(acknowledgeUpdateAttemptToast).toHaveBeenCalledWith(2));
    vi.mocked(getUpdateAttemptToast).mockReset();
  });

  it("asks for no toast over a frame that did not turn failed", async () => {
    pluginFactory();
    await flush();
    vi.mocked(getUpdateAttemptToast).mockClear();

    act(() =>
      emitHostEvent("update_install_progress", {
        version: "0.35.0",
        step: "verifying",
        bytes_done: 1,
        bytes_total: 1,
        failure: null,
      }),
    );
    await flush();

    expect(getUpdateAttemptToast).not.toHaveBeenCalled();
  });

  it("takes a stopped attempt judged after panel load into the store the card on Main reads", () => {
    pluginFactory();

    act(() =>
      emitHostEvent("update_attempt_stopped", {
        attempted_version: "0.35.0",
        from_version: "0.33.0",
        started_at: "2026-09-29T10:00:00Z",
        toast_owed: false,
      }),
    );

    expect(getStoppedUpdateAttempt()).toEqual({ attemptedVersion: "0.35.0", fromVersion: "0.33.0" });
  });

  it("takes a refusal by the pre-install check into the store the card on Main reads, with its card up", () => {
    pluginFactory();
    const record = {
      attempted_version: "0.35.0",
      restored_version: "0.33.0",
      rolled_back_at: "2026-09-29T10:02:00Z",
      kind: "check",
    };

    act(() => emitHostEvent("update_failure_recorded", record));

    expect(getUpdateOutcomeState().failure).toEqual({
      attemptedVersion: "0.35.0",
      restoredVersion: "0.33.0",
      rolledBackAt: "2026-09-29T10:02:00Z",
      kind: "check",
    });
    expect(getUpdateOutcomeState().failureDismissed).toBe(false);
    expect(hostEventListenerCount("update_failure_recorded")).toBe(1);
  });
});

describe("index.tsx — what the last update did, at panel load", () => {
  beforeEach(() => {
    logError.mockClear();
    vi.mocked(toaster.toast).mockClear();
    vi.mocked(getUpdateOutcome).mockReset();
    vi.mocked(acknowledgeUpdateToast).mockReset().mockResolvedValue({ success: true });
    resetUpdateOutcomeStoreForTests();
  });

  it("raises the toast for an attempt that failed while no panel was loaded", async () => {
    vi.stubGlobal("App", { GetServicesInitialized: () => true });
    vi.stubGlobal("securitystore", { IsLockScreenActive: () => false });
    vi.stubGlobal("SteamUIStore", { WindowStore: { GamepadUIMainWindowInstance: null } });
    resetFailedUpdateToastsForTests();
    vi.mocked(getUpdateAttemptToast).mockResolvedValue({ attempt: 1, version: "1.3.0", failure: "download_failed" });
    pluginFactory();

    await vi.waitFor(() =>
      expect(toaster.toast).toHaveBeenCalledWith({
        title: "Tender",
        body: "Update to 1.3.0 failed. The download failed.",
      }),
    );
    expect(acknowledgeUpdateAttemptToast).toHaveBeenCalledWith(1);
    vi.mocked(getUpdateAttemptToast).mockReset();
  });

  it("announces an update that went through in one toast and acknowledges it", async () => {
    vi.mocked(getUpdateOutcome).mockResolvedValue({
      announce_version: "1.3.0",
      announce_direction: "updated",
      toast_owed: true,
      failure: null,
      failure_dismissed: false,
      failure_toast_owed: false,
    });
    pluginFactory();
    await flush();

    expect(getUpdateOutcome).toHaveBeenCalledTimes(1);
    expect(toaster.toast).toHaveBeenCalledWith({ title: "Tender", body: "Tender updated to 1.3.0" });
    expect(vi.mocked(toaster.toast).mock.calls.filter(([t]) => /updated to/.test(String(t.body)))).toHaveLength(1);
    expect(acknowledgeUpdateToast).toHaveBeenCalledTimes(1);
  });

  it("fills the store the rolled-back notice reads", async () => {
    vi.mocked(getUpdateOutcome).mockResolvedValue({
      announce_version: null,
      announce_direction: null,
      toast_owed: false,
      failure: {
        attempted_version: "1.3.0",
        restored_version: "1.2.3",
        rolled_back_at: "2026-09-25T10:15:00Z",
        kind: "rollback",
      },
      failure_dismissed: false,
      failure_toast_owed: false,
    });
    pluginFactory();
    await flush();

    expect(getUpdateOutcomeState().failure?.attemptedVersion).toBe("1.3.0");
    expect(acknowledgeUpdateToast).not.toHaveBeenCalled();
  });

  it("logs a read that rejected and announces nothing", async () => {
    vi.mocked(getUpdateOutcome).mockRejectedValue(new Error("boom"));
    pluginFactory();
    await flush();

    expect(logError).toHaveBeenCalledWith(expect.stringContaining("Failed to read what the last update did"));
    expect(vi.mocked(toaster.toast).mock.calls.filter(([t]) => /updated to/.test(String(t.body)))).toHaveLength(0);
  });
});

describe("index.tsx — the toast that a newer release is out, at panel load", () => {
  const OWED = {
    available: true,
    newer: true,
    latest_version: "1.4.0",
    current_version: "1.3.0",
    enabled: true,
    installed_program: true,
    toast_owed: true,
    seen: false,
  };
  const NOTHING_MOVED: UpdateOutcome = {
    announce_version: null,
    announce_direction: null,
    toast_owed: false,
    failure: null,
    failure_dismissed: false,
    failure_toast_owed: false,
  };
  const availableToasts = () =>
    vi.mocked(toaster.toast).mock.calls.filter(([t]) => /is available/.test(String(t.body)));

  beforeEach(() => {
    vi.stubGlobal("App", { GetServicesInitialized: () => true });
    vi.stubGlobal("securitystore", { IsLockScreenActive: () => false });
    vi.stubGlobal("SteamUIStore", { WindowStore: { GamepadUIMainWindowInstance: null } });
    resetFailedUpdateToastsForTests();
    resetUpdateNoticeStoreForTests();
    resetUpdateOutcomeStoreForTests();
    resetStoppedUpdateStoreForTests();
    resetUpdateInstallStoreForTests();
    vi.mocked(toaster.toast).mockClear();
    vi.mocked(getUpdateNotice).mockReset().mockResolvedValue(OWED);
    vi.mocked(getUpdateOutcome).mockReset().mockResolvedValue(NOTHING_MOVED);
    vi.mocked(readStoppedUpdateAttempt).mockReset().mockResolvedValue(null);
    vi.mocked(acknowledgeUpdateAvailableToast).mockReset().mockResolvedValue({ success: true });
  });

  afterEach(() => {
    vi.mocked(getUpdateNotice).mockReset();
    vi.mocked(getUpdateOutcome).mockReset();
    vi.mocked(readStoppedUpdateAttempt).mockReset();
  });

  it("raises it once the reads answered, and acknowledges it", async () => {
    pluginFactory();

    await vi.waitFor(() => expect(acknowledgeUpdateAvailableToast).toHaveBeenCalledWith("1.4.0"));
    expect(availableToasts()).toEqual([
      [{ title: "Tender", body: "Tender 1.4.0 is available. Settings › Updates to install it." }],
    ]);
  });

  it("raises none where the last read to answer names a failed update to that release", async () => {
    let answerOutcome!: () => void;
    vi.mocked(getUpdateOutcome).mockReturnValue(
      new Promise((resolve) => {
        answerOutcome = () =>
          resolve({
            announce_version: null,
            announce_direction: null,
            toast_owed: false,
            failure: {
              attempted_version: "1.4.0",
              restored_version: "1.3.0",
              rolled_back_at: "2026-09-25T10:15:00Z",
              kind: "rollback",
            },
            failure_dismissed: true,
            failure_toast_owed: false,
          });
      }),
    );
    pluginFactory();
    await flush();
    await flush();

    answerOutcome();
    await flush();
    await flush();

    expect(availableToasts()).toHaveLength(0);
    expect(acknowledgeUpdateAvailableToast).not.toHaveBeenCalled();
  });

  it("raises none, and acknowledges none, where what the last update did could not be read", async () => {
    vi.mocked(getUpdateOutcome).mockRejectedValue(new Error("socket closed"));
    pluginFactory();
    await vi.waitFor(() =>
      expect(logError).toHaveBeenCalledWith("Failed to read what the last update did: Error: socket closed"),
    );
    await flush();
    await flush();

    expect(availableToasts()).toHaveLength(0);
    expect(acknowledgeUpdateAvailableToast).not.toHaveBeenCalled();
  });

  it("raises none while the install's read at load finds an attempt under way", async () => {
    vi.mocked(getUpdateInstallState).mockResolvedValue({
      ...NOTHING_INSTALLING,
      attempt: { version: "1.4.0", step: "downloading", bytes_done: 10, bytes_total: 100, failure: null },
    });
    pluginFactory();
    await vi.waitFor(() => expect(getUpdateInstallAttempt()?.step).toBe("downloading"));
    await flush();
    await flush();

    expect(availableToasts()).toHaveLength(0);
    expect(acknowledgeUpdateAvailableToast).not.toHaveBeenCalled();
  });
});

describe("index.tsx — sync_complete stale-collection cleanup (#1040)", () => {
  // A SNES platform collection and a [Faves] RomM smart-collection, both
  // machine-scoped to "steamdeck" (the getHostname mock). Delete is a vi.fn so
  // the smart-collection delete is observable; the platform collection is
  // removed via the mocked clearPlatformCollection, so only its presence in
  // userCollections matters for the stale filter.
  function seedCollections(): {
    snes: { Delete: ReturnType<typeof vi.fn> };
    faves: { Delete: ReturnType<typeof vi.fn> };
  } {
    const snes = { id: "snes-id", displayName: "RomM: Super Nintendo (steamdeck)", Delete: vi.fn() };
    const faves = { id: "faves-id", displayName: "RomM: [Faves] (steamdeck)", Delete: vi.fn() };
    vi.stubGlobal("collectionStore", { userCollections: [snes, faves] });
    return { snes, faves };
  }

  type SyncCompletePayload = {
    platform_app_ids: Record<string, number[]>;
    romm_collection_app_ids?: Record<string, number[]>;
    total_games: number;
    cancelled?: boolean;
  };

  function emitSyncComplete(payload: SyncCompletePayload): void {
    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", payload);
    });
  }

  beforeEach(() => {
    createOrUpdateCollections.mockClear();
    createOrUpdateRomMCollections.mockClear();
    clearPlatformCollection.mockClear();
    vi.mocked(applyAllPlaytime).mockClear();
    vi.mocked(applyAllPlaytime).mockResolvedValue(undefined);
    vi.mocked(toaster.toast).mockClear();
    logError.mockClear();
    // Give the playtime re-apply detach a well-shaped payload so it reaches
    // applyAllPlaytime instead of throwing on a destructure of undefined.
    vi.mocked(getAllPlaytime).mockResolvedValue({ playtime: {} });
    vi.mocked(getAppIdRomIdMap).mockResolvedValue({});
  });

  it("runs the stale cleanup on a completed (non-cancelled) sync", async () => {
    const { faves } = seedCollections();
    pluginFactory();

    // Only "Nintendo 64" is active — SNES and [Faves] are stale and removed.
    emitSyncComplete({ platform_app_ids: { "Nintendo 64": [1] }, total_games: 1 });
    await flush();

    expect(clearPlatformCollection).toHaveBeenCalledWith("Super Nintendo", expect.any(AbortSignal));
    expect(faves.Delete).toHaveBeenCalledTimes(1);
  });

  it("keeps a case-variant ACTIVE RomM collection (does not delete it) (#1569)", async () => {
    const { snes, faves } = seedCollections();
    pluginFactory();

    // The live collection is "[Faves]"; the active map keys it as "faves" (the
    // reporter's folded-first-seen casing). Case-insensitive identity → it is
    // ACTIVE and must survive. SNES has no active platform → still stale.
    emitSyncComplete({
      platform_app_ids: { "Nintendo 64": [1] },
      romm_collection_app_ids: { faves: [1] },
      total_games: 1,
    });
    await flush();

    expect(faves.Delete).not.toHaveBeenCalled();
    // Non-vacuous: the stale SNES platform IS still cleaned, so cleanup ran.
    expect(clearPlatformCollection).toHaveBeenCalledWith("Super Nintendo", expect.any(AbortSignal));
    expect(snes.Delete).not.toHaveBeenCalled(); // platform delete routes via clearPlatformCollection
  });

  it("keeps a case-variant ACTIVE platform collection (does not clear it) (#1569)", async () => {
    const { faves } = seedCollections();
    pluginFactory();

    // Live "RomM: Super Nintendo (steamdeck)"; active map keys it "super nintendo".
    // Case-insensitive → ACTIVE, must not be cleared. [Faves] has no active RomM
    // entry → stale and removed (non-vacuous: cleanup ran).
    emitSyncComplete({
      platform_app_ids: { "super nintendo": [1] },
      total_games: 1,
    });
    await flush();

    expect(clearPlatformCollection).not.toHaveBeenCalled();
    expect(faves.Delete).toHaveBeenCalledTimes(1);
  });

  it("sweeps a stale RomM collection whose prefix is a case variant of ours (#2131)", async () => {
    const shouted = { id: "shouted-id", displayName: "ROMM: [Faves] (steamdeck)", Delete: vi.fn() };
    vi.stubGlobal("collectionStore", { userCollections: [shouted] });
    pluginFactory();

    emitSyncComplete({ platform_app_ids: { "Nintendo 64": [1] }, total_games: 1 });
    await flush();

    expect(shouted.Delete).toHaveBeenCalledTimes(1);
  });

  it("sweeps a stale RomM collection whose host suffix is a case variant of ours (#2131)", async () => {
    const shouted = { id: "shouted-id", displayName: "RomM: [Faves] (STEAMDECK)", Delete: vi.fn() };
    vi.stubGlobal("collectionStore", { userCollections: [shouted] });
    pluginFactory();

    emitSyncComplete({ platform_app_ids: { "Nintendo 64": [1] }, total_games: 1 });
    await flush();

    expect(shouted.Delete).toHaveBeenCalledTimes(1);
  });

  it("spares a case variant of our prefix that carries another host's suffix (#2131)", async () => {
    const theirs = { id: "theirs-id", displayName: "ROMM: [Faves] (othermachine)", Delete: vi.fn() };
    const ours = { id: "ours-id", displayName: "ROMM: [Gone] (STEAMDECK)", Delete: vi.fn() };
    vi.stubGlobal("collectionStore", { userCollections: [theirs, ours] });
    pluginFactory();

    emitSyncComplete({ platform_app_ids: { "Nintendo 64": [1] }, total_games: 1 });
    await flush();

    expect(theirs.Delete).not.toHaveBeenCalled();
    // Non-vacuous: the cleanup ran and swept this host's case variant.
    expect(ours.Delete).toHaveBeenCalledTimes(1);
  });

  it("clears a stale platform collection whose prefix and host suffix are case variants of ours (#2131)", async () => {
    const shouted = { id: "shouted-id", displayName: "ROMM: Super Nintendo (STEAMDECK)", Delete: vi.fn() };
    vi.stubGlobal("collectionStore", { userCollections: [shouted] });
    pluginFactory();

    emitSyncComplete({ platform_app_ids: { "Nintendo 64": [1] }, total_games: 1 });
    await flush();

    expect(clearPlatformCollection).toHaveBeenCalledWith("Super Nintendo", expect.any(AbortSignal));
  });

  it("keeps an active RomM collection whose name differs from the active key only by case folding (#2131)", async () => {
    const strasse = { id: "strasse-id", displayName: "RomM: [STRASSE] (steamdeck)", Delete: vi.fn() };
    const gone = { id: "gone-id", displayName: "RomM: [Gone] (steamdeck)", Delete: vi.fn() };
    vi.stubGlobal("collectionStore", { userCollections: [strasse, gone] });
    pluginFactory();

    emitSyncComplete({
      platform_app_ids: { "Nintendo 64": [1] },
      romm_collection_app_ids: { Straße: [1] },
      total_games: 1,
    });
    await flush();

    expect(strasse.Delete).not.toHaveBeenCalled();
    // Non-vacuous: the cleanup ran and still sweeps a collection with no key.
    expect(gone.Delete).toHaveBeenCalledTimes(1);
  });

  it("keeps an active platform collection whose name differs from the active key only by case folding (#2131)", async () => {
    const strasse = { id: "strasse-id", displayName: "RomM: STRASSE (steamdeck)", Delete: vi.fn() };
    const gone = { id: "gone-id", displayName: "RomM: Super Nintendo (steamdeck)", Delete: vi.fn() };
    vi.stubGlobal("collectionStore", { userCollections: [strasse, gone] });
    pluginFactory();

    emitSyncComplete({ platform_app_ids: { Straße: [1] }, total_games: 1 });
    await flush();

    expect(clearPlatformCollection).not.toHaveBeenCalledWith("STRASSE", expect.any(AbortSignal));
    // Non-vacuous: the cleanup ran and still clears a platform with no key.
    expect(clearPlatformCollection).toHaveBeenCalledWith("Super Nintendo", expect.any(AbortSignal));
    expect(clearPlatformCollection).toHaveBeenCalledTimes(1);
  });

  it("removes a RomM collection whose key lost its label and keeps the bare-named one", async () => {
    // Steam still holds a standard collection under a "(Standard)"-labelled
    // name, and the active set keys it by its bare name "Kids".
    const oldName = { id: "old-id", displayName: "RomM: [Kids (Standard)] (steamdeck)", Delete: vi.fn() };
    const newName = { id: "new-id", displayName: "RomM: [Kids] (steamdeck)", Delete: vi.fn() };
    vi.stubGlobal("collectionStore", { userCollections: [oldName, newName] });
    pluginFactory();

    emitSyncComplete({
      platform_app_ids: {},
      romm_collection_app_ids: { Kids: [1] },
      total_games: 1,
    });
    await flush();

    expect(createOrUpdateRomMCollections).toHaveBeenCalledWith({ Kids: [1] }, undefined, expect.any(AbortSignal));
    expect(oldName.Delete).toHaveBeenCalledTimes(1);
    expect(newName.Delete).not.toHaveBeenCalled();
  });

  it("skips the stale cleanup on a cancelled sync with a partial map (regression)", async () => {
    const { snes, faves } = seedCollections();
    pluginFactory();

    // Cancel reached only "Nintendo 64"; SNES + [Faves] must SURVIVE.
    emitSyncComplete({ platform_app_ids: { "Nintendo 64": [1] }, total_games: 1, cancelled: true });
    await flush();

    expect(clearPlatformCollection).not.toHaveBeenCalled();
    expect(snes.Delete).not.toHaveBeenCalled();
    expect(faves.Delete).not.toHaveBeenCalled();
  });

  it("skips the stale cleanup on an early cancel with an empty map (full-wipe case)", async () => {
    const { snes, faves } = seedCollections();
    pluginFactory();

    // Cancel fired before unit 1 — the map is empty. Treating it as the active
    // set would wipe EVERY RomM collection; nothing must be deleted.
    emitSyncComplete({ platform_app_ids: {}, total_games: 0, cancelled: true });
    await flush();

    expect(clearPlatformCollection).not.toHaveBeenCalled();
    expect(snes.Delete).not.toHaveBeenCalled();
    expect(faves.Delete).not.toHaveBeenCalled();
  });

  it("still fires the cancelled toast and re-applies playtime on a cancelled sync", async () => {
    seedCollections();
    pluginFactory();
    // The factory's own init runs one initial playtime apply; clear it so the
    // assertion counts only the apply triggered by sync_complete.
    await flush();
    vi.mocked(applyAllPlaytime).mockClear();
    vi.mocked(toaster.toast).mockClear();

    emitSyncComplete({ platform_app_ids: { "Nintendo 64": [1] }, total_games: 1, cancelled: true });
    await flush();

    expect(toaster.toast).toHaveBeenCalledWith(expect.objectContaining({ body: expect.stringContaining("cancelled") }));
    expect(applyAllPlaytime).toHaveBeenCalledTimes(1);
  });

  it("still creates/updates the reached platforms' collections on a cancelled sync", async () => {
    seedCollections();
    pluginFactory();

    emitSyncComplete({ platform_app_ids: { "Nintendo 64": [1] }, total_games: 1, cancelled: true });
    await flush();

    // The additive create/update path is NOT gated on cancel — the platforms
    // that DID complete still get their collections.
    expect(createOrUpdateCollections).toHaveBeenCalledWith({ "Nintendo 64": [1] }, undefined, expect.any(AbortSignal));
  });
});

describe("index.tsx — sync_complete re-applies overview metadata (#1207)", () => {
  type SyncCompletePayload = {
    platform_app_ids: Record<string, number[]>;
    romm_collection_app_ids?: Record<string, number[]>;
    total_games: number;
    cancelled?: boolean;
  };

  function meta(summary: string): RomMetadata {
    return {
      summary,
      genres: [],
      companies: [],
      first_release_date: null,
      average_rating: null,
      game_modes: [],
      player_count: "",
      cached_at: 0,
    };
  }

  beforeEach(() => {
    vi.mocked(registerMetadataPatches).mockClear();
    vi.mocked(applyAllMetadata).mockClear();
    vi.mocked(applyAllMetadata).mockResolvedValue(undefined);
    vi.mocked(applyAllPlaytime).mockResolvedValue(undefined);
    vi.mocked(getAllPlaytime).mockResolvedValue({ playtime: {} });
    // Init's own metadata fetch resolves empty; each test sets distinct fresh
    // data AFTER init so the sync_complete re-fetch is provably re-fetched.
    vi.mocked(getMetadataCachePage).mockResolvedValue({ items: {}, total: 0 });
    vi.mocked(getAppIdRomIdMap).mockResolvedValue({});
  });

  it("re-fetches the paged cache + map and re-applies on a normal completion", async () => {
    pluginFactory();
    await flush(); // init done — registerMetadataPatches called once with the empty init cache
    vi.mocked(registerMetadataPatches).mockClear();
    vi.mocked(applyAllMetadata).mockClear();

    // The re-fetch after sync must see FRESH data, not the init-time empty cache.
    vi.mocked(getMetadataCachePage).mockResolvedValue({ items: { "100": meta("Fresh") }, total: 1 });
    vi.mocked(getAppIdRomIdMap).mockResolvedValue({ "100": 55 });

    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", { platform_app_ids: {}, total_games: 1 });
    });
    await flush();

    // registerMetadataPatches received the RE-FETCHED cache + map (distinct page content).
    expect(registerMetadataPatches).toHaveBeenCalledTimes(1);
    const [cacheArg, mapArg] = vi.mocked(registerMetadataPatches).mock.calls[0]!;
    expect((cacheArg as Record<string, RomMetadata>)["100"]!.summary).toBe("Fresh");
    expect(mapArg).toEqual({ "100": 55 });
    // …and the readiness-gated overview pass re-ran.
    expect(applyAllMetadata).toHaveBeenCalledTimes(1);
  });

  it("re-applies overview metadata on a CANCELLED sync too (partial units are still fresh)", async () => {
    pluginFactory();
    await flush();
    vi.mocked(registerMetadataPatches).mockClear();
    vi.mocked(applyAllMetadata).mockClear();
    vi.mocked(getMetadataCachePage).mockResolvedValue({ items: { "7": meta("Partial") }, total: 1 });
    vi.mocked(getAppIdRomIdMap).mockResolvedValue({ "7": 9 });

    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", { platform_app_ids: {}, total_games: 3, cancelled: true });
    });
    await flush();

    expect(registerMetadataPatches).toHaveBeenCalledTimes(1);
    expect(applyAllMetadata).toHaveBeenCalledTimes(1);
  });

  it("logs and leaves the other blocks intact when the metadata re-fetch fails", async () => {
    pluginFactory();
    await flush();
    vi.mocked(applyAllMetadata).mockClear();
    vi.mocked(applyAllPlaytime).mockClear();
    logError.mockClear();
    // The paged re-fetch throws; the detached block's own catch logs it.
    vi.mocked(getMetadataCachePage).mockRejectedValue(new Error("boom"));

    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", { platform_app_ids: {}, total_games: 1 });
    });
    await flush();

    expect(logError).toHaveBeenCalledWith(expect.stringContaining("Failed to re-apply metadata after sync"));
    expect(applyAllMetadata).not.toHaveBeenCalled();
    // Non-vacuous: the playtime re-apply is a separate detached block and still ran.
    expect(applyAllPlaytime).toHaveBeenCalled();
  });
});

describe("index.tsx — sync_complete toast shows the true delta (#744)", () => {
  // total_games is intentionally MISLEADING in these payloads (the bug): the
  // toast must ignore it and report the real created/removed delta tracked by
  // syncDeltaStore. created is seeded via recordSyncCreated (the mocked
  // syncManager would do this on the create path); removed flows through the
  // real sync_stale listener.
  type SyncCompletePayload = {
    platform_app_ids: Record<string, number[]>;
    romm_collection_app_ids?: Record<string, number[]>;
    total_games: number;
    cancelled?: boolean;
    interrupted?: boolean;
    interrupt_reason?: string;
    restart_recommended?: boolean;
  };

  function lastToastBody(): string | undefined {
    const calls = vi.mocked(toaster.toast).mock.calls;
    if (calls.length === 0) return undefined;
    const last = calls[calls.length - 1]![0] as { body?: string };
    return last.body;
  }

  beforeEach(() => {
    vi.mocked(toaster.toast).mockClear();
    logError.mockClear();
    vi.mocked(applyAllPlaytime).mockResolvedValue(undefined);
    vi.mocked(getAllPlaytime).mockResolvedValue({ playtime: {} });
    vi.mocked(getAppIdRomIdMap).mockResolvedValue({});
    // No RomM collections so the stale-cleanup detach is a no-op for these tests.
    vi.stubGlobal("collectionStore", { userCollections: [] });
    resetSyncDelta();
  });

  it("sync_plan resets the per-run cancel flag (#1198)", async () => {
    pluginFactory();
    vi.mocked(resetSyncCancel).mockClear();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", { run_id: "run-xyz", units: [], total_units: 1, total_roms: 1 });
    });

    // The listener clears the per-run cancel flag once per run, before any unit
    // — reliable even on a skip-only run where no per-unit handler fires. Run
    // identity for a Cancel click now comes from the sync_progress store (#1202).
    expect(vi.mocked(resetSyncCancel)).toHaveBeenCalled();
  });

  it("drives terminal teardown from sync_complete even if no stage:done frame follows", async () => {
    // The on-device hang: the apply left the store on the optimistic "applying"
    // frame, sync_complete arrived, but the separate backend stage:"done"
    // sync_progress frame never did — so the QAM stayed stuck on "Applying".
    // sync_complete alone must flip the store to a terminal stage.
    pluginFactory();
    setSyncProgress({ running: true, stage: "applying", message: "Applying changes..." });

    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", { platform_app_ids: {}, total_games: 42 });
    });
    await flush();

    expect(getSyncProgress().running).toBe(false);
    expect(getSyncProgress().stage).toBe("done");
  });

  it("flips the store to a cancelled stage when sync_complete is cancelled", async () => {
    pluginFactory();
    setSyncProgress({ running: true, stage: "applying", message: "Applying changes..." });

    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", { platform_app_ids: {}, total_games: 5, cancelled: true });
    });
    await flush();

    expect(getSyncProgress().running).toBe(false);
    expect(getSyncProgress().stage).toBe("cancelled");
  });

  it("reports 'X added, Y removed' when both are non-zero (ignores total_games)", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", { run_id: "run-1", units: [], total_units: 2, total_roms: 2 });
    });
    // Two distinct shortcuts created this run (what the syncManager create path records).
    recordSyncCreated(100);
    recordSyncCreated(200);
    // One shortcut removed via the real sync_stale listener.
    act(() => {
      emitHostEvent<SyncStaleData>("sync_stale", { remove: [{ rom_id: 7, app_id: 700 }] });
    });

    // total_games=53 is the misleading total — the toast must NOT use it.
    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", { platform_app_ids: {}, total_games: 53 });
    });
    await flush();

    expect(lastToastBody()).toBe("Sync complete — 2 added, 1 removed.");
  });

  it("omits the zero part — only removals → 'Sync complete — N removed.'", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", { run_id: "run-1", units: [], total_units: 1, total_roms: 0 });
    });
    act(() => {
      emitHostEvent<SyncStaleData>("sync_stale", {
        remove: [
          { rom_id: 7, app_id: 700 },
          { rom_id: 8, app_id: 800 },
        ],
      });
    });
    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", { platform_app_ids: {}, total_games: 53 });
    });
    await flush();

    expect(lastToastBody()).toBe("Sync complete — 2 removed.");
  });

  it("reports 'Library up to date.' when nothing changed (the #744 repro)", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", { run_id: "run-1", units: [], total_units: 1, total_roms: 53 });
    });
    // No creates, no removes — but total_games=53 (the old toast wrongly said
    // "53 games added"). The fixed toast must say the library is up to date.
    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", { platform_app_ids: {}, total_games: 53 });
    });
    await flush();

    expect(lastToastBody()).toBe("Library up to date.");
  });

  it("dedups a shortcut created in two units (platform + collection) — counted once", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", { run_id: "run-1", units: [], total_units: 2, total_roms: 1 });
    });
    // Same appId surfaces in its platform unit and a collection unit; the Set
    // in the store collapses it to one "added".
    recordSyncCreated(100);
    recordSyncCreated(100);
    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", { platform_app_ids: {}, total_games: 1 });
    });
    await flush();

    expect(lastToastBody()).toBe("Sync complete — 1 added.");
  });

  it("on cancel with partial work → 'Sync cancelled — … so far.'", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", { run_id: "run-1", units: [], total_units: 3, total_roms: 10 });
    });
    recordSyncCreated(100);
    recordSyncCreated(200);
    recordSyncCreated(300);
    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", {
        platform_app_ids: {},
        total_games: 53,
        cancelled: true,
      });
    });
    await flush();

    expect(lastToastBody()).toBe("Sync cancelled — 3 added so far.");
  });

  it("on cancel before any work → 'Sync cancelled.' (no delta)", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", { run_id: "run-1", units: [], total_units: 3, total_roms: 10 });
    });
    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", {
        platform_app_ids: {},
        total_games: 53,
        cancelled: true,
      });
    });
    await flush();

    expect(lastToastBody()).toBe("Sync cancelled.");
  });

  it("on a heartbeat-timeout interrupt with partial work → 'Sync interrupted — … so far.' (#1384)", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", { run_id: "run-1", units: [], total_units: 3, total_roms: 10 });
    });
    recordSyncCreated(100);
    recordSyncCreated(200);
    recordSyncCreated(300);
    // An interrupted run rides the cancelled finalize — the backend sets BOTH
    // flags. The additive `interrupted` must win the wording: the run died
    // externally (frontend crash/reload), the user never pressed Cancel.
    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", {
        platform_app_ids: {},
        total_games: 53,
        cancelled: true,
        interrupted: true,
      });
    });
    await flush();

    expect(lastToastBody()).toBe("Sync interrupted — 3 added so far.");
  });

  it("on a heartbeat-timeout interrupt before any work → 'Sync interrupted.' (no delta, #1384)", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", { run_id: "run-1", units: [], total_units: 3, total_roms: 10 });
    });
    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", {
        platform_app_ids: {},
        total_games: 53,
        cancelled: true,
        interrupted: true,
      });
    });
    await flush();

    expect(lastToastBody()).toBe("Sync interrupted.");
  });

  it("on a session-budget pause → shows the pause guidance verbatim with the delta (#1383)", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", { run_id: "run-1", units: [], total_units: 3, total_roms: 10 });
    });
    recordSyncCreated(100);
    recordSyncCreated(200);
    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", {
        platform_app_ids: {},
        total_games: 53,
        cancelled: true,
        interrupt_reason:
          "Sync paused: Steam's memory is nearly full. Restart Steam when convenient, then sync again to continue.",
      });
    });
    await flush();

    // The distinct reason wins over the generic "Sync cancelled — …" wording, and
    // the reason's trailing period is stripped so the parenthetical reads cleanly.
    expect(lastToastBody()).toBe(
      "Sync paused: Steam's memory is nearly full. Restart Steam when convenient, then sync again to continue (2 added so far).",
    );
    // The pause toast gets a longer duration so the guidance isn't truncated away
    // before it is read (#1383).
    const toastCalls = vi.mocked(toaster.toast).mock.calls;
    const lastToast = toastCalls[toastCalls.length - 1]![0] as { duration?: number };
    expect(lastToast.duration).toBe(15000);
  });

  it("a non-pause completion toast carries no custom duration (default lifetime)", async () => {
    pluginFactory();
    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", { run_id: "run-1", units: [], total_units: 1, total_roms: 1 });
    });
    recordSyncCreated(100);
    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", { platform_app_ids: {}, total_games: 1 });
    });
    await flush();
    const toastCalls = vi.mocked(toaster.toast).mock.calls;
    const lastToast = toastCalls[toastCalls.length - 1]![0] as { duration?: number };
    expect(lastToast.duration).toBeUndefined();
  });

  it("on a session-budget pause with no delta → shows just the reason (#1383)", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", { run_id: "run-1", units: [], total_units: 3, total_roms: 10 });
    });
    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", {
        platform_app_ids: {},
        total_games: 53,
        cancelled: true,
        interrupt_reason:
          "Sync paused: Steam's memory is nearly full. Restart Steam when convenient, then sync again to continue.",
      });
    });
    await flush();

    expect(lastToastBody()).toBe(
      "Sync paused: Steam's memory is nearly full. Restart Steam when convenient, then sync again to continue.",
    );
  });

  it("on a clean run with restart_recommended → appends the restart nudge (#1383)", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", { run_id: "run-1", units: [], total_units: 1, total_roms: 1 });
    });
    recordSyncCreated(100);
    act(() => {
      emitHostEvent<SyncCompletePayload>("sync_complete", {
        platform_app_ids: {},
        total_games: 1,
        restart_recommended: true,
      });
    });
    await flush();

    expect(lastToastBody()).toBe("Sync complete — 1 added. Steam restart recommended before further large operations.");
  });
});

describe("index.tsx — an apply run that ends at stage error is toasted", () => {
  const UNREACHABLE = "Server unreachable — check your URL and ensure RomM is running";

  function toastBodies(): (string | undefined)[] {
    return vi.mocked(toaster.toast).mock.calls.map((call) => (call[0] as { body?: string }).body);
  }

  function errorFrame(overrides: Partial<SyncProgress> = {}): SyncProgress {
    return {
      running: false,
      stage: "error",
      current: 0,
      total: 0,
      message: UNREACHABLE,
      runId: "run-err",
      runKind: "apply",
      ...overrides,
    };
  }

  beforeEach(() => {
    vi.mocked(toaster.toast).mockClear();
  });

  it("names the failure when the run's work queue could not be built", () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncProgress>("sync_progress", errorFrame());
    });

    expect(toastBodies()).toEqual([`Sync failed — ${UNREACHABLE}`]);
    expect(getSyncProgress().stage).toBe("error");
  });

  it("does not say it twice when the frame already does", () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncProgress>("sync_progress", errorFrame({ message: `Sync failed — ${UNREACHABLE}` }));
    });

    expect(toastBodies()).toEqual([`Sync failed — ${UNREACHABLE}`]);
  });

  it("still says the sync failed when the frame carries no message", () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncProgress>("sync_progress", errorFrame({ message: "" }));
    });

    expect(toastBodies()).toEqual(["Sync failed."]);
  });

  it("toasts a run once, however often its error frame arrives", () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncProgress>("sync_progress", errorFrame());
      emitHostEvent<SyncProgress>("sync_progress", errorFrame());
    });

    expect(toastBodies()).toHaveLength(1);
  });

  it("toasts the next run's failure too", () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncProgress>("sync_progress", errorFrame({ runId: "run-1" }));
      emitHostEvent<SyncProgress>("sync_progress", errorFrame({ runId: "run-2", message: "Authentication failed" }));
    });

    expect(toastBodies()).toEqual([`Sync failed — ${UNREACHABLE}`, "Sync failed — Authentication failed"]);
  });

  it("leaves a preview's failure to the Sync page", () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncProgress>("sync_progress", errorFrame({ runKind: "preview" }));
    });

    expect(toastBodies()).toEqual([]);
  });

  it("raises nothing for a frame that does not stop a run at stage error", () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncProgress>("sync_progress", errorFrame({ stage: "done", message: "Sync complete" }));
      emitHostEvent<SyncProgress>("sync_progress", errorFrame({ stage: "cancelled", message: "Sync cancelled" }));
      emitHostEvent<SyncProgress>("sync_progress", errorFrame({ running: true }));
    });

    expect(toastBodies()).toEqual([]);
  });
});

describe("index.tsx — sync_plan seeds the applying-phase ETA (always-on estimate)", () => {
  it("writes the composition-priced seed (unbound rows as creates) into the sync progress store", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", {
        run_id: "run-eta",
        units: [{ type: "platform", id: 1, name: "N64", slug: "n64", rom_count: 120, bound_count: 0 }],
        total_units: 3,
        total_roms: 120,
      });
    });

    // Nothing bound yet, so every planned item is a create — the fresh-import
    // shape, priced exactly as the preview would price it.
    expect(getSyncProgress().etaSeconds).toBeCloseTo(estimateApplySeconds(120, 0));
  });

  it("prices already-bound rows as cheap updates, not as fresh creates (#1511)", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", {
        run_id: "run-eta",
        units: [
          // A fully-mirrored platform re-syncing: every row already carries a
          // shortcut, so the run is all updates. Pricing it as creates is the
          // over-read #1511 was opened for; the seed must price it as updates.
          {
            type: "platform",
            id: 1,
            name: "N64",
            slug: "n64",
            rom_count: 1000,
            collapsed_count: 1000,
            bound_count: 1000,
          },
        ],
        total_units: 1,
        total_roms: 1000,
        total_estimated_items: 1000,
      });
    });

    expect(getSyncProgress().etaSeconds).toBeCloseTo(estimateApplySeconds(0, 1000));
    resetEta();
  });

  it("prices a Force Full Sync's sibling duplicates as nothing, not as phantom creates (#1517)", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", {
        run_id: "run-eta",
        units: [
          // A Force Full Sync clears the completion stamps, so collapsed_count is
          // absent and the unit weighs its pre-collapse rom_count — sibling
          // duplicates included. Only new_shortcut_count knows those duplicates
          // are not new shortcuts; the bound-row subtraction would price 400 of
          // them as creates, each with a cover download it never performs.
          {
            type: "platform",
            id: 1,
            name: "N64",
            slug: "n64",
            rom_count: 1000,
            bound_count: 600,
            new_shortcut_count: 0,
          },
        ],
        total_units: 1,
        total_roms: 1000,
      });
    });

    expect(getSyncProgress().etaSeconds).toBeCloseTo(estimateApplySeconds(0, 600));
    resetEta();
  });

  it("preserves etaSeconds across a subsequent backend sync_progress frame", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", {
        run_id: "run-eta",
        units: [{ type: "platform", id: 1, name: "N64", slug: "n64", rom_count: 200, bound_count: 0 }],
        total_units: 1,
        total_roms: 200,
      });
    });
    // A backend frame carries no etaSeconds — the listener must not wipe it.
    act(() => {
      emitHostEvent<SyncProgress>("sync_progress", {
        running: true,
        stage: "applying",
        step: 1,
        totalSteps: 1,
        message: "N64: 1/200",
      });
    });

    expect(getSyncProgress().etaSeconds).toBeCloseTo(estimateApplySeconds(200, 0));
    expect(getSyncProgress().stage).toBe("applying");
  });

  it("does NOT clobber an etaSeconds already seeded by the preview path (handleApply)", async () => {
    pluginFactory();

    // handleApply full-replaces the store with a tighter delta-based etaSeconds
    // before sync_plan arrives; the listener must leave that seed intact. Both
    // click paths full-replace at click time, so a present etaSeconds is always
    // this run's preview seed, never a stale prior-run value.
    const previewSeed = 321;
    setSyncProgress({ running: true, stage: "applying", message: "Applying changes...", etaSeconds: previewSeed });
    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", { run_id: "run-eta", units: [], total_units: 3, total_roms: 5400 });
    });

    // The crude estimateApplySeconds(5400, 0) bound must NOT overwrite the preview seed.
    expect(getSyncProgress().etaSeconds).toBe(previewSeed);
  });

  it("still seeds the total_roms bound when no preview seed is present (skip-preview path)", async () => {
    pluginFactory();

    // Skip-preview never sets an etaSeconds — the store has none at sync_plan
    // time, so the listener still supplies the upper bound. Regression guard for
    // the etaSeconds-undefined gate.
    expect(getSyncProgress().etaSeconds).toBeUndefined();
    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", {
        run_id: "run-eta",
        units: [{ type: "platform", id: 1, name: "N64", slug: "n64", rom_count: 80, bound_count: 0 }],
        total_units: 2,
        total_roms: 80,
      });
    });

    expect(getSyncProgress().etaSeconds).toBeCloseTo(estimateApplySeconds(80, 0));
  });

  it("excludes predicted-skip units from the seed (#1382 skip-aware)", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", {
        run_id: "run-eta",
        units: [
          {
            type: "platform",
            id: 1,
            name: "N64",
            slug: "n64",
            rom_count: 115,
            collapsed_count: 115,
            bound_count: 115,
            predicted_skip: true,
          },
          { type: "platform", id: 2, name: "GBA", slug: "gba", rom_count: 5, bound_count: 0 },
        ],
        total_units: 3,
        total_roms: 120,
        total_estimated_items: 5,
      });
    });

    // An incremental re-sync prices only the predicted work, not the library.
    expect(getSyncProgress().etaSeconds).toBeCloseTo(estimateApplySeconds(5, 0));
    resetEta();
  });

  it("seeds the live estimator with skip-aware unit weights (predicted_skip → 0, collapsed over raw)", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", {
        run_id: "run-eta",
        units: [
          // Predicted skip: weight 0 even though counts are known.
          {
            type: "platform",
            id: 1,
            name: "N64",
            slug: "n64",
            rom_count: 100,
            predicted_skip: true,
            collapsed_count: 60,
          },
          // Known collapsed count wins over the raw rom_count.
          {
            type: "platform",
            id: 2,
            name: "GBA",
            slug: "gba",
            rom_count: 100,
            predicted_skip: false,
            collapsed_count: 40,
          },
          // Never synced: raw rom_count fallback.
          { type: "platform", id: 3, name: "SNES", slug: "snes", rom_count: 30, predicted_skip: false },
        ],
        total_units: 3,
        total_roms: 230,
        total_estimated_items: 70,
      });
    });

    // Observable through the weighted coarse fraction over the seeded weights
    // [0, 40, 30]: units 1+2 done (0 + 40) plus half of SNES (15) → 55/70 of
    // the weight. The leading predicted-skip unit weighs 0, so it claims an
    // equal 1/3 index slice as its floor and the weighted share fills the band
    // above it (#1506): 1/3 + (2/3)·55/70.
    expect(weightedCoarseFraction(2, 0.5, 3)).toBeCloseTo(1 / 3 + (2 / 3) * (55 / 70), 10);
    resetEta();
  });

  it("falls back to raw weights and total_roms when the estimate fields are absent (old backend)", async () => {
    pluginFactory();

    act(() => {
      emitHostEvent<SyncPlanData>("sync_plan", {
        run_id: "run-eta",
        units: [
          { type: "platform", id: 1, name: "N64", slug: "n64", rom_count: 60 },
          { type: "platform", id: 2, name: "GBA", slug: "gba", rom_count: 20 },
        ],
        total_units: 2,
        total_roms: 80,
      });
    });

    expect(getSyncProgress().etaSeconds).toBeCloseTo(estimateApplySeconds(80, 0));
    // Raw rom_count weights: unit 1 done (60) plus half of unit 2 (10) → 70/80.
    expect(weightedCoarseFraction(1, 0.5, 2)).toBeCloseTo(70 / 80, 10);
    resetEta();
  });
});

describe("index.tsx — where entry focus lands on a page swap", () => {
  beforeEach(() => {
    mainPageOwnsEntryFocus = false;
    mainPageDeclaresEntryStop = false;
  });

  it("focuses the mounted page's first button", async () => {
    vi.useFakeTimers();
    try {
      const plugin = pluginFactory();
      render(plugin.content);

      // Steam's gamepad nav keeps a focus pointer across page swaps and would
      // otherwise resolve it onto whatever sits at the old page's position.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      const btn = screen.getByRole("button", { name: "first button" });

      expect(btn).toHaveFocus();
      expect(btn).toHaveClass("gpfocus");
    } finally {
      vi.useRealTimers();
    }
  });

  it("opens a page inside the area it declared rather than at its first stop", async () => {
    vi.useFakeTimers();
    try {
      mainPageDeclaresEntryStop = true;
      const plugin = pluginFactory();
      render(plugin.content);

      // The router still does the placing — the page only says where. Main is
      // the one page that says anything: its status rows act on nothing, so
      // opening on the first of them spends the reader's first press on a move.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      const declared = screen.getByRole("button", { name: "go to downloads" });

      expect(declared).toHaveFocus();
      expect(declared).toHaveClass("gpfocus");
      expect(screen.getByRole("button", { name: "first button" })).not.toHaveFocus();
    } finally {
      vi.useRealTimers();
    }
  });

  it("takes B back one page, and binds nothing on Main", async () => {
    // The escape route is never removed: on a sub-page B returns to Main, and on
    // Main nothing is bound, so the press travels on to whatever holds the panel.
    // Steam already prints "B ZURÜCK" — this makes it true rather than
    // misleading.
    const plugin = pluginFactory();
    const { container } = render(plugin.content);

    // Fired on the page's own content and allowed to bubble, so the binding has
    // to sit on an ANCESTOR of that content to answer it. Steam dispatches a
    // gamepad button along the focus path — its own tabbed page relies on
    // exactly that, binding onCancelButton on the container that wraps the tab's
    // content rather than on the content itself — so a binding on a sibling of
    // the page would never see the press with focus inside it. The target is the
    // page's own element rather than whatever element happens to come last in
    // the container: a binding rendered on an empty node after the page would
    // BE that last node, and the press would land on the binding itself.
    const pressB = () => {
      fireEvent(
        screen.getByText("downloads page"),
        new CustomEvent("decky-button-down", { detail: { button: 2 }, bubbles: true }),
      );
    };

    // On Main the router wraps nothing, so there is no Focusable to answer B.
    expect(container.querySelector('[data-testid="focusable"]')).toBeNull();

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "go to downloads" }));
      await Promise.resolve();
    });
    expect(screen.getByText("downloads page")).toBeInTheDocument();

    await act(async () => {
      pressB();
      await Promise.resolve();
    });
    expect(screen.getByRole("button", { name: "first button" })).toBeInTheDocument();
    expect(screen.queryByText("downloads page")).toBeNull();
  });

  it("leaves focus alone for a page that places its own", async () => {
    vi.useFakeTimers();
    try {
      mainPageOwnsEntryFocus = true;
      const plugin = pluginFactory();
      render(plugin.content);

      // A wide page's first button is its Back row, which sits above the tabs
      // and so outside Steam's tabbed page — landing there would hide the L1/R1
      // glyphs the page needs to be switchable at all.
      await act(async () => {
        await vi.advanceTimersByTimeAsync(50);
      });
      const btn = screen.getByRole("button", { name: "first button" });

      expect(btn).not.toHaveFocus();
      expect(btn).not.toHaveClass("gpfocus");
    } finally {
      vi.useRealTimers();
    }
  });
});
