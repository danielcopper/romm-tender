// The Data Management page, driven through the page itself: what the six
// inventory rows say, and what each detail pane does. `DataManagementPage`,
// `DataDetail` and `useDataPage` only exist together — the page renders what
// the hook holds and the panes act through it — so they are exercised as one
// here rather than against a hand-built state object, which would pin the seam
// instead of the behaviour.
//
// Focus selects, so a row is selected by firing focusin on it, which is what
// Steam's navigation does; the @decky/ui stub in `frontend/src/test-setup.ts`
// forwards `onFocus` on a Focusable for exactly that reason.
//
// CATCH-REJECTION ASSERTION RULE: every catch with a state side effect is
// asserted through what the user then sees — the surfaced status line, the
// logError spy — never merely by the rejecting call having been made.

import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, fireEvent, act, waitFor, within } from "@testing-library/react";
import type { RenderResult } from "@testing-library/react";
import { DataManagementPage } from "./DataManagementPage";
import * as backend from "../api/backend";
import {
  removeShortcut,
  setLaunchOptionsConfirmed,
  getAllNonSteamShortcutAppIds,
  getLiveRomMShortcutAppIds,
  scanShortcutOwnership,
} from "../utils/steamShortcuts";
import { clearAllRomMCollections } from "../utils/collections";
import { setSyncProgress } from "../utils/syncProgress";
import { beginPruneRun, resetPruneState, setPruneComplete, setPruneProgress } from "../utils/pruneStore";
import { stubCollectionStore, stubAppStore } from "../test-utils/steamStubs";
import type { DataInventory, SyncStats } from "../types";

vi.mock("../utils/scrollHelpers", () => ({
  scrollToTop: vi.fn(),
  scrollElementToTop: vi.fn(),
  scrollFocusedToCenter: vi.fn(),
}));
// setLaunchOptionsConfirmed is exercised through the real batchConfirmLaunchOptions
// (launchOptionsReconcile stays unmocked) — mock the leaf so the bulk-uninstall
// launch-options reset is asserted without touching SteamClient.
vi.mock("../utils/steamShortcuts", () => ({
  removeShortcut: vi.fn(),
  setLaunchOptionsConfirmed: vi.fn(),
  getAllNonSteamShortcutAppIds: vi.fn(),
  getLiveRomMShortcutAppIds: vi.fn(),
  scanShortcutOwnership: vi.fn(),
}));
vi.mock("../utils/collections", () => ({
  clearAllRomMCollections: vi.fn(),
  clearPlatformCollection: vi.fn(),
}));

/** Drain the mount-time effect chain: four parallel loads settle on the microtask queue. */
const flushAsync = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

function stats(overrides: Partial<SyncStats> = {}): SyncStats {
  return {
    last_sync: null,
    platforms: 1,
    collections: 0,
    roms: 0,
    total_shortcuts: 0,
    ...overrides,
  } as SyncStats;
}

function inventory(overrides: Partial<DataInventory> = {}): DataInventory {
  return {
    installed_roms: 0,
    installed_bytes: 0,
    recovery_bundles: 0,
    recovery_bytes: 0,
    recovery_root: "/home/deck/romm-tender-recovery",
    recovery_bundle_list: [],
    ...overrides,
  };
}

/** Render the page and settle its mount reads. */
async function renderPage(): Promise<RenderResult> {
  const result = render(<DataManagementPage onBack={vi.fn()} />);
  await flushAsync();
  return result;
}

/** Move focus onto a row, which is what selects it and mounts its pane. */
function selectRow(view: RenderResult, id: string): void {
  fireEvent.focusIn(view.getByTestId(`data-row-${id}`));
}

/** Render the page with one row already selected. */
async function pageOn(id: string): Promise<RenderResult> {
  const view = await renderPage();
  selectRow(view, id);
  return view;
}

/** A pane's button, found by what it currently says — its label IS its state. */
function button(view: RenderResult, text: string | RegExp): HTMLButtonElement {
  return view.getByText(text).closest("button") as HTMLButtonElement;
}

/** The recovery pane's bundle rows, in the order they are drawn. The Focusable
 *  stub stamps its own test id, so a row is found as a stop inside the table. */
function bundleRows(view: RenderResult): HTMLElement[] {
  return within(view.getByTestId("bundle-table")).getAllByTestId("focusable");
}

/** Press a button and let its awaited work settle. */
async function press(el: Element): Promise<void> {
  await act(async () => {
    fireEvent.click(el);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("DataManagementPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    resetPruneState();
    setSyncProgress({ running: false, stage: "", current: 0, total: 0, message: "", runId: "" });
    vi.mocked(backend.getWhitelistSettings).mockResolvedValue({ disabled_defaults: [], custom_names: [] });
    vi.mocked(backend.updateWhitelistSettings).mockResolvedValue({ success: true });
    vi.mocked(backend.getSyncStats).mockResolvedValue(stats());
    vi.mocked(backend.getDataInventory).mockResolvedValue(inventory());
    vi.mocked(backend.removeAllShortcuts).mockResolvedValue({
      success: true,
      message: "",
      app_ids: [],
      rom_ids: [],
    });
    vi.mocked(backend.reportRemovalResults).mockResolvedValue({ success: true, message: "" });
    vi.mocked(backend.uninstallAllRoms).mockResolvedValue({
      success: true,
      removed_count: 0,
      errors: [],
      app_ids: [],
    });
    vi.mocked(backend.cleanupOrphanedGridImages).mockResolvedValue({ success: true, candidate_count: 0 });
    vi.mocked(setLaunchOptionsConfirmed).mockResolvedValue(true);
    vi.mocked(getAllNonSteamShortcutAppIds).mockReturnValue([]);
    // Default: the sweep identifies every entry and finds none of them ours,
    // so the union removal is a no-op and every non-Steam entry is foreign
    // unless a test says otherwise.
    vi.mocked(getLiveRomMShortcutAppIds).mockResolvedValue([]);
    vi.mocked(scanShortcutOwnership).mockResolvedValue({ owned: [], unresolved: [] });
    vi.mocked(clearAllRomMCollections).mockResolvedValue(undefined);
    stubCollectionStore([]);
    stubAppStore({});
    // test-setup's vi.stubGlobal calls run once at module-load; afterEach's
    // vi.unstubAllGlobals() strips them. Re-stub SteamClient.Apps.RemoveShortcut
    // here so the non-Steam removal can fire without ReferenceError. The mock
    // drops the app from the collection store, mirroring Steam's real behavior,
    // so the post-removal settle-poll sees the store shrink and re-counts
    // immediately instead of waiting out its timeout on a real timer.
    vi.stubGlobal("SteamClient", {
      Apps: {
        RemoveShortcut: vi.fn((appId: number) => {
          if (typeof collectionStore !== "undefined") {
            collectionStore.deckDesktopApps?.apps.delete(appId);
          }
        }),
      },
    });
  });

  describe("the inventory list", () => {
    it("lists the six populations in order, and no group headings", async () => {
      const view = await renderPage();

      const labels = ["shortcuts", "rom-files", "grid-images", "non-steam", "removed-games", "recovery-bundles"].map(
        (id) => view.getByTestId(`data-row-${id}`).textContent,
      );

      expect(labels[0]).toContain("Tender's shortcuts");
      expect(labels[1]).toContain("Installed ROMs");
      expect(labels[2]).toContain("Grid images");
      expect(labels[3]).toContain("Other non-Steam games");
      expect(labels[4]).toContain("Gone from RomM");
      expect(labels[5]).toContain("Recovery bundles");
      // The list carries populations, never a category name for a group of them.
      expect(view.container.textContent).not.toContain("Bulk actions");
    });

    it("carries every row that needs no round trip with its count on arrival", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 812 }));
      vi.mocked(backend.getDataInventory).mockResolvedValue(inventory({ installed_roms: 37, recovery_bundles: 2 }));
      stubCollectionStore([1, 2, 3]);
      stubAppStore({ 1: { strDisplayName: "A" }, 2: { strDisplayName: "B" }, 3: { strDisplayName: "C" } });

      const view = await renderPage();

      expect(view.getByTestId("data-row-shortcuts").textContent).toContain("812");
      expect(view.getByTestId("data-row-rom-files").textContent).toContain("37");
      expect(view.getByTestId("data-row-non-steam").textContent).toContain("3");
      expect(view.getByTestId("data-row-recovery-bundles").textContent).toContain("2");
    });

    it("reads `scan` on the two rows whose count costs a round trip", async () => {
      const view = await renderPage();

      expect(view.getByTestId("data-row-grid-images").textContent).toContain("scan");
      expect(view.getByTestId("data-row-removed-games").textContent).toContain("scan");
    });

    it("every row is a focus stop, so a reader can walk and scroll the list", async () => {
      const view = await renderPage();

      // `selectOnActivate` puts the activate handler on the row wrapper, which
      // is what makes it a stop rather than a container passing focus to
      // children it has not got. The stub surfaces that as data-activate.
      for (const id of ["shortcuts", "rom-files", "grid-images", "non-steam", "removed-games", "recovery-bundles"]) {
        const row = view.getByTestId(`data-row-${id}`);
        expect(row.closest("[data-activate]")).toBeTruthy();
      }
    });

    it("opens on the first row's pane", async () => {
      const view = await renderPage();

      expect(view.container.textContent).toContain("The Steam entries this plugin created");
    });
  });

  describe("a figure the page reads is reading, failed or answered — never one for the other", () => {
    it("shows a spinner, not a dash, in every row whose read is still in flight", async () => {
      vi.mocked(backend.getSyncStats).mockReturnValue(new Promise(() => {}));
      vi.mocked(backend.getDataInventory).mockReturnValue(new Promise(() => {}));
      vi.mocked(scanShortcutOwnership).mockReturnValue(new Promise(() => {}));
      const view = await renderPage();

      for (const id of ["shortcuts", "rom-files", "non-steam", "recovery-bundles"]) {
        const row = view.getByTestId(`data-row-${id}`);
        expect(within(row).queryByTestId("spinner")).not.toBeNull();
        expect(row.textContent).not.toContain("—");
      }
    });

    it("says Reading… in a pane whose read is in flight", async () => {
      vi.mocked(backend.getDataInventory).mockReturnValue(new Promise(() => {}));
      const view = await pageOn("rom-files");

      expect(view.container.textContent).toContain("Reading…");
      expect(view.container.textContent).not.toContain("could not be read");
    });

    it("ends a rejected inventory read in failed, not in Reading…", async () => {
      const logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      vi.mocked(backend.getDataInventory).mockRejectedValue(new Error("backend_exception"));
      const view = await pageOn("rom-files");

      expect(within(view.getByTestId("data-row-rom-files")).queryByTestId("spinner")).toBeNull();
      expect(view.getByTestId("data-row-rom-files").textContent).toContain("—");
      expect(view.getByTestId("data-row-recovery-bundles").textContent).toContain("—");
      expect(view.container.textContent).toContain(
        "The installed-ROM figures could not be read — open the page again to retry.",
      );
      expect(view.container.textContent).not.toContain("Reading…");
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to read the data inventory"));
      logSpy.mockRestore();

      selectRow(view, "recovery-bundles");
      expect(view.container.textContent).toContain(
        "The list of recovery bundles could not be read — open the page again to retry.",
      );
      // A failed read says nothing about what the folder holds.
      expect(view.container.textContent).not.toContain("Nothing has been sealed");
    });

    it("reads a resolved failure shape as failed, never as an inventory", async () => {
      vi.mocked(backend.getDataInventory).mockResolvedValue({
        success: false,
        reason: "backend_exception",
        message: "boom",
      } as unknown as DataInventory);
      const view = await pageOn("rom-files");

      expect(view.getByTestId("data-row-rom-files").textContent).toContain("—");
      expect(view.getByTestId("data-row-rom-files").textContent).not.toContain("undefined");
      expect(view.container.textContent).toContain("The installed-ROM figures could not be read");
    });

    it("ends a rejected shortcut count in failed, not in Reading…", async () => {
      vi.mocked(backend.getSyncStats).mockRejectedValue(new Error("offline"));
      const view = await renderPage();

      expect(view.getByTestId("data-row-shortcuts").textContent).toContain("—");
      expect(view.container.textContent).toContain(
        "The shortcut count could not be read — open the page again to retry.",
      );
    });

    it("reads a resolved failure shape for the shortcut count as failed", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({
        success: false,
        reason: "backend_exception",
        message: "boom",
      } as unknown as SyncStats);
      const view = await renderPage();

      expect(view.getByTestId("data-row-shortcuts").textContent).toContain("—");
      expect(view.container.textContent).toContain("The shortcut count could not be read");
    });

    it("says Reading… on the non-Steam pane while the ownership scan runs, never the failure", async () => {
      vi.mocked(scanShortcutOwnership).mockReturnValue(new Promise(() => {}));
      stubCollectionStore([1]);
      stubAppStore({ 1: { strDisplayName: "Firefox" } });
      const view = await pageOn("non-steam");

      expect(view.container.textContent).toContain("Reading…");
      expect(view.container.textContent).not.toContain("could not be read");
      expect(view.container.textContent).not.toContain("Could not be read");
      // Nothing is offered while ownership is unanswered.
      expect(view.queryByText(/Remove .*non-Steam game/)).toBeNull();
    });

    it("ends a failed re-read after an uninstall in failed, not in the stale figure", async () => {
      vi.mocked(backend.getDataInventory)
        .mockResolvedValueOnce(inventory({ installed_roms: 2, installed_bytes: 2048 }))
        .mockRejectedValue(new Error("offline"));
      vi.mocked(backend.uninstallAllRoms).mockResolvedValue({
        success: true,
        removed_count: 2,
        errors: [],
        app_ids: [],
      });
      const view = await pageOn("rom-files");

      fireEvent.click(button(view, "Uninstall all ROM files"));
      await press(button(view, "Delete every downloaded ROM file?"));

      await waitFor(() => expect(view.getByTestId("data-row-rom-files").textContent).toContain("—"));
      expect(view.container.textContent).not.toContain("2 installed");
    });
  });

  describe("a scan never rides on the selection", () => {
    it("selecting Grid images fires no backend scan", async () => {
      const view = await pageOn("grid-images");

      expect(vi.mocked(backend.cleanupOrphanedGridImages)).not.toHaveBeenCalled();
      expect(view.getByTestId("data-row-grid-images").textContent).toContain("scan");
    });

    it("walking the whole list fires no scan on any row", async () => {
      const view = await renderPage();

      for (const id of ["shortcuts", "rom-files", "grid-images", "non-steam", "removed-games", "recovery-bundles"]) {
        selectRow(view, id);
      }
      await flushAsync();

      expect(vi.mocked(backend.cleanupOrphanedGridImages)).not.toHaveBeenCalled();
      expect(vi.mocked(backend.getPrunePreview)).not.toHaveBeenCalled();
    });

    it("only a press scans, and the row then carries the answer", async () => {
      vi.mocked(backend.cleanupOrphanedGridImages).mockResolvedValue({ success: true, candidate_count: 4 });
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));

      expect(vi.mocked(backend.cleanupOrphanedGridImages)).toHaveBeenCalledWith([], true);
      expect(view.getByTestId("data-row-grid-images").textContent).toContain("4");
    });
  });

  describe("Recovery bundles", () => {
    it("states how many are sealed and what they take", async () => {
      vi.mocked(backend.getDataInventory).mockResolvedValue(
        inventory({ recovery_bundles: 3, recovery_bytes: 2_147_483_648 }),
      );
      const view = await pageOn("recovery-bundles");

      expect(view.container.textContent).toContain("3 bundles");
      expect(view.container.textContent).toContain("2.00 GB");
    });

    it("offers no action at all — this page never deletes a bundle", async () => {
      vi.mocked(backend.getDataInventory).mockResolvedValue(inventory({ recovery_bundles: 3, recovery_bytes: 1024 }));
      const view = await pageOn("recovery-bundles");

      // The pane is the detail region; nothing in it is pressable.
      const pane = view.getByText(/Before the cleanup deletes/).closest("div")!.parentElement!;
      expect(within(pane).queryAllByRole("button")).toHaveLength(0);
      expect(view.container.textContent).not.toContain("Delete");
      expect(view.container.textContent).not.toContain("Remove");
    });

    it("scopes an empty answer to the folder it counted, not to the device", async () => {
      const view = await pageOn("recovery-bundles");

      // A device carrying bundles an older version sealed elsewhere has more
      // than this row can see, so the pane may not claim the device is empty.
      expect(view.container.textContent).toContain("Nothing has been sealed under that folder");
      expect(view.container.textContent).toContain("not counted here");
      expect(view.container.textContent).not.toContain("Nothing has been sealed on this device");
    });

    it("names the root the backend counted under rather than spelling one itself", async () => {
      // The folder is derived from the package name, which this side cannot
      // see — and a literal would be wrong under a non-default home too.
      vi.mocked(backend.getDataInventory).mockResolvedValue(
        inventory({ recovery_bundles: 1, recovery_bytes: 10, recovery_root: "/var/home/deck/tender-recovery" }),
      );
      const view = await pageOn("recovery-bundles");

      expect(view.container.textContent).toContain("/var/home/deck/tender-recovery");
      expect(view.container.textContent).not.toContain("~/romm-tender-recovery");
      expect(view.container.textContent).not.toContain("beside your home directory");
    });

    it("lists each bundle newest first, with the ones naming no day last", async () => {
      vi.mocked(backend.getDataInventory).mockResolvedValue(
        inventory({
          recovery_bundles: 4,
          recovery_bytes: 3_000,
          recovery_bundle_list: [
            { name: "hand-renamed backup", day: null, bytes: 1_000 },
            { name: "Shenmue", day: "2026-09-20", bytes: 2_048 },
            { name: "Crazy-Taxi", day: "2026-10-02", bytes: null },
            { name: "Shenmue-II", day: "2026-09-20", bytes: 500 },
          ],
        }),
      );
      const view = await pageOn("recovery-bundles");

      expect(view.getByTestId("bundle-header").textContent).toBe("GameSealedSize");
      const rows = bundleRows(view).map((row) => row.textContent);
      expect(rows).toEqual([
        "Crazy-Taxi2026-10-02—",
        "Shenmue2026-09-202.0 KB",
        "Shenmue-II2026-09-20500 B",
        "hand-renamed backup—1000 B",
      ]);
      // The totals and the explanation stay above the list.
      expect(view.container.textContent).toContain("4 bundles");
      expect(view.container.textContent).toContain("Each carries a README");
    });

    it("makes every bundle row a focus stop, and offers nothing on any", async () => {
      vi.mocked(backend.getDataInventory).mockResolvedValue(
        inventory({
          recovery_bundles: 2,
          recovery_bytes: 20,
          recovery_bundle_list: [
            { name: "Shenmue", day: "2026-09-20", bytes: 10 },
            { name: "Shenmue", day: "2026-09-20", bytes: 10 },
          ],
        }),
      );
      const view = await pageOn("recovery-bundles");

      const rows = bundleRows(view);
      expect(rows).toHaveLength(2);
      for (const row of rows) {
        expect(row.getAttribute("data-activate")).toBe("true");
        expect(within(row).queryAllByRole("button")).toHaveLength(0);
      }
    });

    it("draws no table where nothing has been sealed", async () => {
      const view = await pageOn("recovery-bundles");

      expect(view.queryByTestId("bundle-table")).toBeNull();
    });

    it("says nothing about where they live until the read has landed", async () => {
      vi.mocked(backend.getDataInventory).mockReturnValue(new Promise(() => {}));
      const view = await pageOn("recovery-bundles");

      expect(view.container.textContent).toContain("seals a snapshot of it.");
      expect(view.container.textContent).not.toContain("undefined");
    });
  });

  describe("the installed ROMs pane", () => {
    it("states the count and marks the size as an approximation", async () => {
      vi.mocked(backend.getDataInventory).mockResolvedValue(
        inventory({ installed_roms: 12, installed_bytes: 5_368_709_120 }),
      );
      const view = await pageOn("rom-files");

      // The figure names its own unit: two installed versions of one game are
      // two installs, so "games" would be a miscount and "files" another.
      expect(view.container.textContent).toContain("12 installed");
      expect(view.container.textContent).not.toContain("12 games");
      // And nothing around it supplies a unit the figure has not got: a reader
      // reading top to bottom must not complete "12 installed" as "12 game
      // files" off the sentence above it.
      expect(view.container.textContent).toContain("one per install");
      expect(view.container.textContent).not.toContain("game files");
      expect(view.container.textContent).not.toContain("these games");
      expect(view.container.textContent).toContain("≈ 5.00 GB");
      // The `≈` is not decoration: the figure is the server's, not a disk walk.
      expect(view.container.textContent).toContain("what your RomM server reported");
    });

    it("first press arms the confirm; the second uninstalls and re-reads the figures", async () => {
      vi.mocked(backend.getDataInventory)
        .mockResolvedValueOnce(inventory({ installed_roms: 2, installed_bytes: 2048 }))
        .mockResolvedValue(inventory({ installed_roms: 0, installed_bytes: 0 }));
      vi.mocked(backend.uninstallAllRoms).mockResolvedValue({
        success: true,
        removed_count: 2,
        errors: [],
        app_ids: [],
      });
      const view = await pageOn("rom-files");

      fireEvent.click(button(view, "Uninstall all ROM files"));
      expect(view.container.textContent).toContain("Delete every downloaded ROM file?");
      expect(vi.mocked(backend.uninstallAllRoms)).not.toHaveBeenCalled();

      await press(button(view, "Delete every downloaded ROM file?"));

      expect(vi.mocked(backend.uninstallAllRoms)).toHaveBeenCalledTimes(1);
      await waitFor(() => expect(view.getByTestId("data-row-rom-files").textContent).toContain("0"));
    });

    it("resets each kept shortcut's launch command to the uninstalled placeholder", async () => {
      vi.mocked(backend.getDataInventory).mockResolvedValue(inventory({ installed_roms: 2 }));
      vi.mocked(backend.uninstallAllRoms).mockResolvedValue({
        success: true,
        removed_count: 2,
        errors: [],
        app_ids: [11, 22],
      });
      const view = await pageOn("rom-files");

      fireEvent.click(button(view, "Uninstall all ROM files"));
      await press(button(view, "Delete every downloaded ROM file?"));

      await waitFor(() => {
        expect(vi.mocked(setLaunchOptionsConfirmed)).toHaveBeenCalledWith(11, "");
        expect(vi.mocked(setLaunchOptionsConfirmed)).toHaveBeenCalledWith(22, "");
      });
    });

    it("resets no launch options when no kept shortcut is bound", async () => {
      vi.mocked(backend.getDataInventory).mockResolvedValue(inventory({ installed_roms: 1 }));
      const view = await pageOn("rom-files");

      fireEvent.click(button(view, "Uninstall all ROM files"));
      await press(button(view, "Delete every downloaded ROM file?"));

      expect(vi.mocked(setLaunchOptionsConfirmed)).not.toHaveBeenCalled();
    });

    it("surfaces the failure and resets nothing when the call rejects", async () => {
      vi.mocked(backend.getDataInventory).mockResolvedValue(inventory({ installed_roms: 1 }));
      vi.mocked(backend.uninstallAllRoms).mockRejectedValue(new Error("offline"));
      const view = await pageOn("rom-files");

      fireEvent.click(button(view, "Uninstall all ROM files"));
      await press(button(view, "Delete every downloaded ROM file?"));

      expect(view.getByTestId("status-rom-files").textContent).toBe("Failed to uninstall ROMs");
      expect(vi.mocked(setLaunchOptionsConfirmed)).not.toHaveBeenCalled();
    });

    it("surfaces a gate refusal and removes nothing", async () => {
      vi.mocked(backend.getDataInventory).mockResolvedValue(inventory({ installed_roms: 1 }));
      vi.mocked(backend.uninstallAllRoms).mockResolvedValue({
        success: false,
        reason: "sync_active",
        message: "A sync is running",
      });
      const view = await pageOn("rom-files");

      fireEvent.click(button(view, "Uninstall all ROM files"));
      await press(button(view, "Delete every downloaded ROM file?"));

      expect(view.getByTestId("status-rom-files").textContent).toBe("A sync is running");
      expect(vi.mocked(setLaunchOptionsConfirmed)).not.toHaveBeenCalled();
    });

    it("reports the error count on a partial failure", async () => {
      vi.mocked(backend.getDataInventory).mockResolvedValue(inventory({ installed_roms: 3 }));
      vi.mocked(backend.uninstallAllRoms).mockResolvedValue({
        success: false,
        removed_count: 2,
        errors: [{ rom_id: "9", error: "permission denied" }],
        app_ids: [],
      });
      const view = await pageOn("rom-files");

      fireEvent.click(button(view, "Uninstall all ROM files"));
      await press(button(view, "Delete every downloaded ROM file?"));

      expect(view.getByTestId("status-rom-files").textContent).toContain("(1 errors)");
    });
  });

  describe("the Tender's shortcuts pane", () => {
    it("first press arms the confirm; the second removes every shortcut", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 2 }));
      vi.mocked(backend.removeAllShortcuts).mockResolvedValue({
        success: true,
        message: "Removed 5",
        app_ids: [10, 20],
        rom_ids: [1, 2],
        prune_lease_token: "all-removal-lease",
      });
      const view = await pageOn("shortcuts");

      fireEvent.click(button(view, "Remove all shortcuts"));
      expect(view.container.textContent).toContain("Remove every RomM shortcut?");
      expect(vi.mocked(backend.removeAllShortcuts)).not.toHaveBeenCalled();

      await press(button(view, "Remove every RomM shortcut?"));

      expect(vi.mocked(backend.removeAllShortcuts)).toHaveBeenCalledTimes(1);
      expect(vi.mocked(removeShortcut)).toHaveBeenCalledWith(10);
      expect(vi.mocked(removeShortcut)).toHaveBeenCalledWith(20);
      expect(vi.mocked(backend.reportRemovalResults)).toHaveBeenCalledWith([1, 2], "all-removal-lease");
      expect(vi.mocked(clearAllRomMCollections)).toHaveBeenCalled();
      expect(vi.mocked(clearAllRomMCollections).mock.invocationCallOrder[0]).toBeLessThan(
        vi.mocked(backend.reportRemovalResults).mock.invocationCallOrder[0]!,
      );
      expect(view.getByTestId("status-shortcuts").textContent).toBe("Removed 5");
    });

    it("drops the row's count to zero once they are gone", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 7 }));
      vi.mocked(backend.removeAllShortcuts).mockResolvedValue({
        success: true,
        message: "Removed 7",
        app_ids: [1],
        rom_ids: [1],
      });
      const view = await pageOn("shortcuts");

      fireEvent.click(button(view, "Remove all shortcuts"));
      await press(button(view, "Remove every RomM shortcut?"));

      await waitFor(() => expect(view.getByTestId("data-row-shortcuts").textContent).toContain("0"));
    });

    it("skips reportRemovalResults when rom_ids is empty", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 1 }));
      const view = await pageOn("shortcuts");

      fireEvent.click(button(view, "Remove all shortcuts"));
      await press(button(view, "Remove every RomM shortcut?"));

      expect(vi.mocked(backend.reportRemovalResults)).not.toHaveBeenCalled();
    });

    it("removes the UNION of backend app_ids and live-scanned orphans, deduped", async () => {
      // The backend binding map returns [1, 2]; the live exe-ownership scan
      // also finds 3 — an orphan a crashed sync left in Steam with no DB
      // binding. All three go; 2 is in both lists and is removed once.
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 2 }));
      vi.mocked(backend.removeAllShortcuts).mockResolvedValue({
        success: true,
        message: "Removed all",
        app_ids: [1, 2],
        rom_ids: [50, 51],
      });
      vi.mocked(getLiveRomMShortcutAppIds).mockResolvedValue([2, 3]);
      const logSpy = vi.spyOn(backend, "logInfo").mockImplementation(() => {});
      const view = await pageOn("shortcuts");

      fireEvent.click(button(view, "Remove all shortcuts"));
      await press(button(view, "Remove every RomM shortcut?"));

      await waitFor(() => expect(vi.mocked(removeShortcut)).toHaveBeenCalledWith(3));
      expect(vi.mocked(removeShortcut)).toHaveBeenCalledWith(1);
      expect(vi.mocked(removeShortcut)).toHaveBeenCalledWith(2);
      expect(vi.mocked(removeShortcut)).toHaveBeenCalledTimes(3);
      // rom_ids stay the backend set exactly — an orphan has no DB row.
      expect(vi.mocked(backend.reportRemovalResults)).toHaveBeenCalledWith([50, 51], null);
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("1 live-scanned RomM shortcut"));
      logSpy.mockRestore();
    });

    it("falls back to the backend list and warns when the live scan is unavailable", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 2 }));
      vi.mocked(backend.removeAllShortcuts).mockResolvedValue({
        success: true,
        message: "Removed all",
        app_ids: [1, 2],
        rom_ids: [50],
      });
      vi.mocked(getLiveRomMShortcutAppIds).mockResolvedValue(null);
      const warnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
      const view = await pageOn("shortcuts");

      fireEvent.click(button(view, "Remove all shortcuts"));
      await press(button(view, "Remove every RomM shortcut?"));

      await waitFor(() => expect(view.getByTestId("status-shortcuts").textContent).toBe("Removed all"));
      expect(vi.mocked(removeShortcut)).toHaveBeenCalledTimes(2);
      expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining("Live RomM shortcut scan unavailable"));
      warnSpy.mockRestore();
    });

    it("surfaces a gate refusal and removes nothing", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 3 }));
      vi.mocked(backend.removeAllShortcuts).mockResolvedValue({
        success: false,
        reason: "sync_active",
        message: "A sync is running",
      });
      const view = await pageOn("shortcuts");

      fireEvent.click(button(view, "Remove all shortcuts"));
      await press(button(view, "Remove every RomM shortcut?"));

      expect(view.getByTestId("status-shortcuts").textContent).toBe("A sync is running");
      expect(vi.mocked(removeShortcut)).not.toHaveBeenCalled();
    });

    it("surfaces the failure when the call rejects", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 3 }));
      vi.mocked(backend.removeAllShortcuts).mockRejectedValue(new Error("offline"));
      const view = await pageOn("shortcuts");

      fireEvent.click(button(view, "Remove all shortcuts"));
      await press(button(view, "Remove every RomM shortcut?"));

      expect(view.getByTestId("status-shortcuts").textContent).toBe("Failed to remove shortcuts");
    });
  });

  describe("the union sweep stays reachable with no bindings left", () => {
    it("offers the removal even where the bound count is zero", async () => {
      // The sweep behind this button exists for exactly this state: the
      // bindings are gone and orphans of ours stand in Steam. Disabling on the
      // bound count kills the button precisely when it is needed.
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 0 }));
      const view = await pageOn("shortcuts");

      expect(view.getByTestId("data-row-shortcuts").textContent).toContain("0");
      expect(button(view, "Remove all shortcuts")).toHaveProperty("disabled", false);
    });

    it("removes the live-scanned orphans when the backend knows of none", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 0 }));
      vi.mocked(backend.removeAllShortcuts).mockResolvedValue({
        success: true,
        message: "Removed all",
        app_ids: [],
        rom_ids: [],
      });
      vi.mocked(getLiveRomMShortcutAppIds).mockResolvedValue([77, 88]);
      const view = await pageOn("shortcuts");

      fireEvent.click(button(view, "Remove all shortcuts"));
      await press(button(view, "Remove every RomM shortcut?"));

      await waitFor(() => expect(vi.mocked(removeShortcut)).toHaveBeenCalledWith(77));
      expect(vi.mocked(removeShortcut)).toHaveBeenCalledWith(88);
    });
  });

  describe("Other non-Steam games is disjoint from Tender's shortcuts", () => {
    it("counts only the entries this plugin did not create", async () => {
      stubCollectionStore([1, 2, 3]);
      stubAppStore({
        1: { strDisplayName: "Some RomM Game" },
        2: { strDisplayName: "Another RomM Game" },
        3: { strDisplayName: "Moonlight" },
      });
      vi.mocked(scanShortcutOwnership).mockResolvedValue({ owned: [1, 2], unresolved: [] });
      const view = await pageOn("non-steam");

      expect(view.getByTestId("data-row-non-steam").textContent).toContain("1");
      expect(view.container.textContent).toContain("1 entry");
    });

    it("removes only the foreign entries, never one of ours", async () => {
      stubCollectionStore([1, 2]);
      stubAppStore({ 1: { strDisplayName: "Some RomM Game" }, 2: { strDisplayName: "Handmade Shortcut" } });
      vi.mocked(scanShortcutOwnership).mockResolvedValue({ owned: [1], unresolved: [] });
      const view = await pageOn("non-steam");

      fireEvent.click(button(view, /Remove \d+ /));
      await press(button(view, /Remove \d+ /));

      expect(vi.mocked(removeShortcut)).toHaveBeenCalledWith(2);
      expect(vi.mocked(removeShortcut)).not.toHaveBeenCalledWith(1);
      expect(vi.mocked(removeShortcut)).toHaveBeenCalledTimes(1);
    });

    it("lists only the foreign entries in the whitelist", async () => {
      stubCollectionStore([1, 2]);
      stubAppStore({ 1: { strDisplayName: "Some RomM Game" }, 2: { strDisplayName: "Handmade Shortcut" } });
      vi.mocked(scanShortcutOwnership).mockResolvedValue({ owned: [1], unresolved: [] });
      const view = await pageOn("non-steam");

      fireEvent.click(view.getByText("Configure whitelist (0 protected)"));

      expect(view.getAllByTestId("toggle").map((row) => row.textContent)).toEqual(["Handmade Shortcut"]);
    });

    it("refuses the removal and says why when ownership cannot be established", async () => {
      stubCollectionStore([1, 2]);
      stubAppStore({ 1: { strDisplayName: "A" }, 2: { strDisplayName: "B" } });
      vi.mocked(scanShortcutOwnership).mockResolvedValue(null);
      const view = await pageOn("non-steam");

      expect(view.getByTestId("data-row-non-steam").textContent).toContain("—");
      expect(view.container.textContent).toContain("could not be read");
      expect(view.queryByText(/Remove \d+ non-Steam game/)).toBeNull();
      expect(vi.mocked(removeShortcut)).not.toHaveBeenCalled();
    });

    it("refuses the removal when the scan rejects rather than guessing", async () => {
      stubCollectionStore([1]);
      stubAppStore({ 1: { strDisplayName: "A" } });
      vi.mocked(scanShortcutOwnership).mockRejectedValue(new Error("offline"));
      const warnSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
      const view = await pageOn("non-steam");

      expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining("Live RomM shortcut scan failed"));
      expect(view.queryByText(/Remove \d+ non-Steam game/)).toBeNull();
      warnSpy.mockRestore();
    });
  });

  describe("an entry Steam did not answer for is never offered for removal", () => {
    it("keeps an unidentified entry out of the count and out of the set", async () => {
      // `getAppDetails` resolves null after its timeout, which on a large
      // library is ordinary — reading that as "not ours" would offer one of
      // ours for removal, which is the whole-store refusal one entry wide.
      stubCollectionStore([1, 2, 3]);
      stubAppStore({
        1: { strDisplayName: "A RomM Game" },
        2: { strDisplayName: "Timed Out" },
        3: { strDisplayName: "Moonlight" },
      });
      vi.mocked(scanShortcutOwnership).mockResolvedValue({ owned: [1], unresolved: [2] });
      const view = await pageOn("non-steam");

      expect(view.getByTestId("data-row-non-steam").textContent).toContain("1");
      expect(view.container.textContent).toContain("1 entry could not be identified");
      // Actionable, not merely true: the reader can close the arithmetic and
      // knows what to do about it.
      expect(view.container.textContent).toContain("not in the count above");
      expect(view.container.textContent).toContain("open the page again to retry");
    });

    it("removes the proven-foreign entries and leaves the unidentified one", async () => {
      stubCollectionStore([1, 2, 3]);
      stubAppStore({
        1: { strDisplayName: "A RomM Game" },
        2: { strDisplayName: "Timed Out" },
        3: { strDisplayName: "Handmade Shortcut" },
      });
      vi.mocked(scanShortcutOwnership).mockResolvedValue({ owned: [1], unresolved: [2] });
      const view = await pageOn("non-steam");

      fireEvent.click(button(view, /Remove \d+ /));
      await press(button(view, /Remove \d+ /));

      expect(vi.mocked(removeShortcut)).toHaveBeenCalledWith(3);
      expect(vi.mocked(removeShortcut)).not.toHaveBeenCalledWith(2);
      expect(vi.mocked(removeShortcut)).not.toHaveBeenCalledWith(1);
      expect(vi.mocked(removeShortcut)).toHaveBeenCalledTimes(1);
    });

    it("says nothing about unidentified entries when the sweep answered for all of them", async () => {
      stubCollectionStore([1, 2]);
      stubAppStore({ 1: { strDisplayName: "Moonlight" }, 2: { strDisplayName: "Chiaki" } });
      vi.mocked(scanShortcutOwnership).mockResolvedValue({ owned: [], unresolved: [] });
      const view = await pageOn("non-steam");

      expect(view.container.textContent).toContain("2 entries");
      expect(view.container.textContent).not.toContain("could not be identified");
    });

    it("counts only the unidentified entries Steam still lists", async () => {
      // A shortcut a removal has since taken out is no longer in the
      // enumeration, so it is not something the pane is leaving alone.
      stubCollectionStore([1]);
      stubAppStore({ 1: { strDisplayName: "Moonlight" } });
      vi.mocked(scanShortcutOwnership).mockResolvedValue({ owned: [], unresolved: [99] });
      const view = await pageOn("non-steam");

      expect(view.container.textContent).not.toContain("could not be identified");
      expect(view.container.textContent).toContain("1 entry");
    });
  });

  describe("a scan row, once pressed, reads like every other figure", () => {
    it("shows a spinner in the Grid images row and a dead Scanning… button while its scan runs", async () => {
      vi.mocked(backend.cleanupOrphanedGridImages).mockReturnValue(new Promise(() => {}));
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));

      const row = view.getByTestId("data-row-grid-images");
      expect(within(row).queryByTestId("spinner")).not.toBeNull();
      expect(row.textContent).not.toContain("scan");
      expect(row.textContent).not.toContain("—");
      expect(button(view, "Scanning…").disabled).toBe(true);
    });

    it("shows a dash and the retry line when the Grid images scan rejects", async () => {
      vi.mocked(backend.cleanupOrphanedGridImages).mockRejectedValue(new Error("offline"));
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));

      expect(view.getByTestId("data-row-grid-images").textContent).toContain("—");
      expect(view.container.textContent).toContain("The scan failed — press Scan for orphaned images to try again.");
      // The button is the retry, and it is live again.
      expect(button(view, "Scan for orphaned images").disabled).toBe(false);
    });

    it("counts a Grid images scan that cannot read Steam's shortcut list as failed", async () => {
      vi.mocked(getAllNonSteamShortcutAppIds).mockReturnValue(null);
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));

      expect(view.getByTestId("data-row-grid-images").textContent).toContain("—");
    });

    it("shows a spinner in the Gone from RomM row while its scan runs", async () => {
      vi.mocked(backend.getPrunePreview).mockReturnValue(new Promise(() => {}));
      const view = await pageOn("removed-games");

      await press(button(view, "Clean Up Removed RomM Games"));

      const row = view.getByTestId("data-row-removed-games");
      expect(within(row).queryByTestId("spinner")).not.toBeNull();
      expect(row.textContent).not.toContain("scan");
      expect(button(view, "Scanning…").disabled).toBe(true);
    });

    it("shows a dash and the retry line when the Gone from RomM scan rejects", async () => {
      vi.mocked(backend.getPrunePreview).mockRejectedValue(new Error("offline"));
      const view = await pageOn("removed-games");

      await press(button(view, "Clean Up Removed RomM Games"));

      expect(view.getByTestId("data-row-removed-games").textContent).toContain("—");
      expect(view.container.textContent).toContain("The scan failed — press Clean Up Removed RomM Games to try again.");
    });
  });

  describe("Gone from RomM carries what its scan found", () => {
    it("reads `scan` until pressed, then keeps the number for the visit", async () => {
      vi.mocked(backend.getPrunePreview).mockResolvedValue({
        success: true,
        total: 4,
        preview_id: "preview-1",
        items: [],
      } as never);
      const view = await pageOn("removed-games");
      expect(view.getByTestId("data-row-removed-games").textContent).toContain("scan");

      await press(button(view, "Clean Up Removed RomM Games"));

      expect(view.getByTestId("data-row-removed-games").textContent).toContain("4");
      // The number survives a walk to another row and back.
      selectRow(view, "shortcuts");
      selectRow(view, "removed-games");
      expect(view.getByTestId("data-row-removed-games").textContent).toContain("4");
    });

    it("carries a zero answer rather than staying unscanned", async () => {
      vi.mocked(backend.getPrunePreview).mockResolvedValue({ success: true, total: 0, items: [] } as never);
      const view = await pageOn("removed-games");

      await press(button(view, "Clean Up Removed RomM Games"));

      expect(view.getByTestId("data-row-removed-games").textContent).toContain("0");
    });

    it("drops back to unscanned once a cleanup run completes", async () => {
      // Scan, review, run is the common path, and the number the scan found is
      // what the run has just changed — so the row asks to be scanned again
      // rather than reporting a figure the run made wrong.
      vi.mocked(backend.getPrunePreview).mockResolvedValue({
        success: true,
        total: 3,
        preview_id: "preview-1",
        items: [],
      } as never);
      const view = await pageOn("removed-games");
      await press(button(view, "Clean Up Removed RomM Games"));
      expect(view.getByTestId("data-row-removed-games").textContent).toContain("3");

      await act(async () => {
        beginPruneRun("run-1", "preview-1");
        setPruneComplete({
          preview_id: "preview-1",
          run_id: "run-1",
          success: true,
          removed_rom_ids: [1],
          results: [],
          message: "",
        } as never);
        await Promise.resolve();
      });

      expect(view.getByTestId("data-row-removed-games").textContent).toContain("scan");
    });

    it("puts Grid images back to unscanned once a cleanup run completes", async () => {
      // A run without recovery removes shortcuts and leaves their grid images,
      // which the last grid scan never counted.
      vi.mocked(backend.getPrunePreview).mockResolvedValue({
        success: true,
        total: 3,
        preview_id: "preview-1",
        items: [],
      } as never);
      vi.mocked(backend.cleanupOrphanedGridImages).mockResolvedValue({ success: true, candidate_count: 2 });
      const view = await pageOn("removed-games");
      await press(button(view, "Clean Up Removed RomM Games"));
      selectRow(view, "grid-images");
      await press(button(view, "Scan for orphaned images"));
      expect(view.getByTestId("data-row-grid-images").textContent).toContain("2");

      await act(async () => {
        beginPruneRun("run-1", "preview-1");
        setPruneComplete({
          preview_id: "preview-1",
          run_id: "run-1",
          success: true,
          removed_rom_ids: [1],
          results: [],
          message: "",
        } as never);
        await Promise.resolve();
      });

      expect(view.getByTestId("data-row-grid-images").textContent).toContain("scan");
    });

    it("keeps the number while a run is only in progress", async () => {
      vi.mocked(backend.getPrunePreview).mockResolvedValue({
        success: true,
        total: 3,
        preview_id: "preview-1",
        items: [],
      } as never);
      const view = await pageOn("removed-games");
      await press(button(view, "Clean Up Removed RomM Games"));

      await act(async () => {
        beginPruneRun("run-1", "preview-1");
        setPruneProgress({
          preview_id: "preview-1",
          run_id: "run-1",
          stage: "checking",
          current: 1,
          total: 3,
          name: "A Game",
        } as never);
        await Promise.resolve();
      });

      expect(view.getByTestId("data-row-removed-games").textContent).toContain("3");
    });

    it("shows a dash and the retry line when the scan answers a failure", async () => {
      vi.mocked(backend.getPrunePreview).mockResolvedValue({ success: false, message: "offline" } as never);
      const view = await pageOn("removed-games");

      await press(button(view, "Clean Up Removed RomM Games"));

      expect(view.getByTestId("data-row-removed-games").textContent).toContain("—");
      expect(view.getByTestId("data-row-removed-games").textContent).not.toContain("scan");
      expect(view.container.textContent).toContain("The scan failed — press Clean Up Removed RomM Games to try again.");
    });
  });

  describe("the Grid images pane", () => {
    it("reports zero candidates without offering a removal", async () => {
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));

      expect(view.getByTestId("status-grid-images").textContent).toBe("No orphaned grid images found");
      expect(view.queryByText(/Remove \d+ orphaned image/)).toBeNull();
    });

    it("removes the orphans on the confirmed second press", async () => {
      vi.mocked(backend.cleanupOrphanedGridImages)
        .mockResolvedValueOnce({ success: true, candidate_count: 2 })
        .mockResolvedValue({ success: true, candidate_count: 2, removed_count: 2 });
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));
      fireEvent.click(button(view, /Remove \d+ (orphaned )?image/));
      expect(view.container.textContent).toContain("Remove 2 images?");
      await press(button(view, /Remove \d+ (orphaned )?image/));

      expect(vi.mocked(backend.cleanupOrphanedGridImages)).toHaveBeenLastCalledWith([], false);
      await waitFor(() => expect(view.getByTestId("status-grid-images").textContent).toBe("Removed 2 orphaned images"));
      // The removal took every candidate it found, so nothing orphaned at that
      // moment is left and the row can say so.
      expect(view.getByTestId("data-row-grid-images").textContent).toContain("0");
      expect(view.getByTestId("data-row-grid-images").textContent).not.toContain("scan");
      expect(view.container.textContent).toContain("0 orphaned images found");
    });

    it("reads 0 when the removal took all of its own candidates, even more than the scan counted", async () => {
      vi.mocked(backend.cleanupOrphanedGridImages)
        .mockResolvedValueOnce({ success: true, candidate_count: 2 })
        .mockResolvedValue({ success: true, candidate_count: 3, removed_count: 3 });
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));
      fireEvent.click(button(view, /Remove \d+ (orphaned )?image/));
      await press(button(view, /Remove \d+ (orphaned )?image/));

      await waitFor(() => expect(view.getByTestId("status-grid-images").textContent).toBe("Removed 3 orphaned images"));
      expect(view.getByTestId("data-row-grid-images").textContent).not.toContain("scan");
      expect(view.container.textContent).toContain("0 orphaned images found");
    });

    it("asks to be scanned again when some of the removal's own candidates could not be deleted", async () => {
      vi.mocked(backend.cleanupOrphanedGridImages)
        .mockResolvedValueOnce({ success: true, candidate_count: 3 })
        .mockResolvedValue({ success: true, candidate_count: 3, removed_count: 1 });
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));
      fireEvent.click(button(view, /Remove \d+ (orphaned )?image/));
      await press(button(view, /Remove \d+ (orphaned )?image/));

      await waitFor(() =>
        expect(view.getByTestId("status-grid-images").textContent).toBe(
          "Removed 1 of 3 orphaned images — 2 could not be deleted. Scan again to count what remains.",
        ),
      );
      expect(view.getByTestId("data-row-grid-images").textContent).toContain("scan");
      expect(view.container.textContent).toContain("Not scanned yet");
      expect(button(view, "Scan for orphaned images")).toBeTruthy();
      expect(view.queryByText(/Remove \d+ orphaned image/)).toBeNull();
    });

    it("asks to be scanned again when a shortfall happens to match the scan's count", async () => {
      // One image orphaned after the scan and one failed unlink: two removed of
      // three, as many as the scan counted, with one still on disk.
      vi.mocked(backend.cleanupOrphanedGridImages)
        .mockResolvedValueOnce({ success: true, candidate_count: 2 })
        .mockResolvedValue({ success: true, candidate_count: 3, removed_count: 2 });
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));
      fireEvent.click(button(view, /Remove \d+ (orphaned )?image/));
      await press(button(view, /Remove \d+ (orphaned )?image/));

      await waitFor(() =>
        expect(view.getByTestId("status-grid-images").textContent).toBe(
          "Removed 2 of 3 orphaned images — 1 could not be deleted. Scan again to count what remains.",
        ),
      );
      expect(view.getByTestId("data-row-grid-images").textContent).toContain("scan");
    });

    it.each([
      ["no candidate count", { success: true, removed_count: 2 }],
      ["no removed count", { success: true, candidate_count: 2 }],
    ])("asks to be scanned again when the removal's answer carries %s", async (_label, answer) => {
      vi.mocked(backend.cleanupOrphanedGridImages)
        .mockResolvedValueOnce({ success: true, candidate_count: 2 })
        .mockResolvedValue(answer);
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));
      fireEvent.click(button(view, /Remove \d+ (orphaned )?image/));
      await press(button(view, /Remove \d+ (orphaned )?image/));

      await waitFor(() =>
        expect(view.getByTestId("status-grid-images").textContent).toBe(
          "The removal did not say whether every image went. Scan again to count what remains.",
        ),
      );
      expect(view.getByTestId("data-row-grid-images").textContent).toContain("scan");
    });

    it("cannot start a second removal while its own is running", async () => {
      let release: (() => void) | undefined;
      vi.mocked(backend.cleanupOrphanedGridImages)
        .mockResolvedValueOnce({ success: true, candidate_count: 2 })
        .mockReturnValue(
          new Promise((resolve) => {
            release = () => resolve({ success: true, candidate_count: 2, removed_count: 2 });
          }),
        );
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));
      fireEvent.click(button(view, /Remove \d+ (orphaned )?image/));
      await press(button(view, /Remove \d+ (orphaned )?image/));

      expect(view.container.textContent).toContain("Removing orphaned images");
      const remove = button(view, /Remove \d+ (orphaned )?image/);
      expect(remove).toHaveProperty("disabled", true);
      fireEvent.click(remove);
      await press(remove);
      // The scan and the one removal — a second confirm reached nothing.
      expect(vi.mocked(backend.cleanupOrphanedGridImages)).toHaveBeenCalledTimes(2);

      await act(async () => {
        release!();
        await Promise.resolve();
        await Promise.resolve();
      });
      await waitFor(() => expect(view.getByTestId("status-grid-images").textContent).toBe("Removed 2 orphaned images"));
    });

    it("aborts without calling the backend when the live-shortcut scan returns null", async () => {
      vi.mocked(getAllNonSteamShortcutAppIds).mockReturnValue(null);
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));

      expect(vi.mocked(backend.cleanupOrphanedGridImages)).not.toHaveBeenCalled();
      // A scan press: the cause is worded for a scan, not for a removal.
      expect(view.getByTestId("status-grid-images").textContent).toBe(
        "Steam's shortcut list could not be read, so nothing could be checked.",
      );
    });

    it("surfaces a gate refusal on the scan", async () => {
      vi.mocked(backend.cleanupOrphanedGridImages).mockResolvedValue({
        success: false,
        reason: "sync_active",
        message: "A sync is running",
      });
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));

      expect(view.getByTestId("status-grid-images").textContent).toBe("A sync is running");
      // A refused scan is a failed one: the row shows a dash and the button is
      // the retry.
      expect(view.getByTestId("data-row-grid-images").textContent).toContain("—");
      expect(view.container.textContent).toContain("The scan failed — press Scan for orphaned images to try again.");
    });

    it("surfaces the incomplete_scan refusal on the removal", async () => {
      vi.mocked(backend.cleanupOrphanedGridImages)
        .mockResolvedValueOnce({ success: true, candidate_count: 1 })
        .mockResolvedValue({ success: false, reason: "incomplete_scan", message: "A bound shortcut was missing" });
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));
      fireEvent.click(button(view, /Remove \d+ (orphaned )?image/));
      await press(button(view, /Remove \d+ (orphaned )?image/));

      expect(view.getByTestId("status-grid-images").textContent).toBe("A bound shortcut was missing");
      // A refusal removed nothing, so the scan's count still stands and is
      // still offered.
      expect(view.getByTestId("data-row-grid-images").textContent).toContain("1");
      expect(view.getByTestId("data-row-grid-images").textContent).not.toContain("scan");
      expect(button(view, /Remove 1 orphaned image/)).toBeTruthy();
    });

    it("says a rejected scan failed once, in the retry line, with no second status line, and logs why", async () => {
      vi.mocked(backend.cleanupOrphanedGridImages).mockRejectedValue(new Error("boom"));
      const logSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));

      expect(view.container.textContent).toContain("The scan failed — press Scan for orphaned images to try again.");
      expect(view.queryByTestId("status-grid-images")).toBeNull();
      // The screen names no cause, so the log is the only place a lost
      // connection or a timeout behind that line is recorded.
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Orphaned grid image scan failed: Error: boom"));
      logSpy.mockRestore();
    });

    it("asks to be scanned again when the removal rejects, says the outcome is unknown, and logs why", async () => {
      vi.mocked(backend.cleanupOrphanedGridImages)
        .mockResolvedValueOnce({ success: true, candidate_count: 1 })
        .mockRejectedValue(new Error("boom"));
      const logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      const view = await pageOn("grid-images");

      await press(button(view, "Scan for orphaned images"));
      fireEvent.click(button(view, /Remove \d+ (orphaned )?image/));
      await press(button(view, /Remove \d+ (orphaned )?image/));

      expect(view.getByTestId("status-grid-images").textContent).toBe(
        "Whether the images were removed could not be established. Scan again to count what remains.",
      );
      expect(view.getByTestId("data-row-grid-images").textContent).toContain("scan");
      expect(view.queryByText(/Remove \d+ orphaned image/)).toBeNull();
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Orphaned grid image removal failed: Error: boom"));
      logSpy.mockRestore();
    });
  });

  describe("a shortcut removal un-asks the grid count", () => {
    it("reads scan on Grid images after every shortcut was removed", async () => {
      vi.mocked(backend.cleanupOrphanedGridImages).mockResolvedValue({ success: true, candidate_count: 2 });
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 1 }));
      vi.mocked(backend.removeAllShortcuts).mockResolvedValue({
        success: true,
        message: "Removed 1",
        app_ids: [10],
        rom_ids: [1],
      });
      const view = await pageOn("grid-images");
      await press(button(view, "Scan for orphaned images"));
      expect(view.getByTestId("data-row-grid-images").textContent).toContain("2");

      selectRow(view, "shortcuts");
      fireEvent.click(button(view, "Remove all shortcuts"));
      await press(button(view, "Remove every RomM shortcut?"));
      await waitFor(() => expect(view.getByTestId("status-shortcuts").textContent).toBe("Removed 1"));

      expect(view.getByTestId("data-row-grid-images").textContent).toContain("scan");
    });

    it("reads scan on Grid images when the shortcut removal fails part-way through", async () => {
      // The bound shortcuts are already gone from Steam when the live sweep
      // that follows them rejects, so the run ends in a failure after it has
      // removed shortcuts. A per-shortcut failure cannot stand in here: the
      // paced removal logs it and carries on rather than rejecting.
      vi.mocked(backend.cleanupOrphanedGridImages).mockResolvedValue({ success: true, candidate_count: 2 });
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 2 }));
      vi.mocked(backend.removeAllShortcuts).mockResolvedValue({
        success: true,
        message: "Removed 2",
        app_ids: [10, 20],
        rom_ids: [1, 2],
      });
      vi.mocked(getLiveRomMShortcutAppIds).mockRejectedValue(new Error("Steam went away"));
      const view = await pageOn("grid-images");
      await press(button(view, "Scan for orphaned images"));
      expect(view.getByTestId("data-row-grid-images").textContent).toContain("2");

      selectRow(view, "shortcuts");
      fireEvent.click(button(view, "Remove all shortcuts"));
      await press(button(view, "Remove every RomM shortcut?"));
      await waitFor(() => expect(view.getByTestId("status-shortcuts").textContent).toBe("Failed to remove shortcuts"));

      expect(vi.mocked(removeShortcut)).toHaveBeenCalledWith(10);
      expect(vi.mocked(removeShortcut)).toHaveBeenCalledWith(20);
      expect(view.getByTestId("data-row-grid-images").textContent).toContain("scan");
    });

    it("keeps the grid count when the shortcut removal is refused", async () => {
      vi.mocked(backend.cleanupOrphanedGridImages).mockResolvedValue({ success: true, candidate_count: 2 });
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 1 }));
      vi.mocked(backend.removeAllShortcuts).mockResolvedValue({
        success: false,
        reason: "sync_active",
        message: "A sync is running",
      });
      const view = await pageOn("grid-images");
      await press(button(view, "Scan for orphaned images"));

      selectRow(view, "shortcuts");
      fireEvent.click(button(view, "Remove all shortcuts"));
      await press(button(view, "Remove every RomM shortcut?"));
      await waitFor(() => expect(view.getByTestId("status-shortcuts").textContent).toBe("A sync is running"));

      expect(view.getByTestId("data-row-grid-images").textContent).toContain("2");
      expect(view.getByTestId("data-row-grid-images").textContent).not.toContain("scan");
    });

    it("reads scan on Grid images after other non-Steam games were removed", async () => {
      vi.mocked(backend.cleanupOrphanedGridImages).mockResolvedValue({ success: true, candidate_count: 2 });
      stubCollectionStore([2]);
      stubAppStore({ 2: { strDisplayName: "Some Game" } });
      const view = await pageOn("grid-images");
      await press(button(view, "Scan for orphaned images"));
      expect(view.getByTestId("data-row-grid-images").textContent).toContain("2");

      selectRow(view, "non-steam");
      fireEvent.click(button(view, /Remove \d+ /));
      await press(button(view, /Remove \d+ /));
      await waitFor(() => expect(view.getByTestId("status-non-steam").textContent).toBe("Removed 1 non-Steam game"));

      expect(view.getByTestId("data-row-grid-images").textContent).toContain("scan");
    });
  });

  describe("the Other non-Steam games pane", () => {
    it("says so when Steam holds no non-Steam entry", async () => {
      const view = await pageOn("non-steam");

      expect(view.container.textContent).toContain("No other non-Steam games found");
      expect(view.queryByText(/Remove \d+ non-Steam game/)).toBeNull();
    });

    it("states the entries, the protected ones and what would go", async () => {
      stubCollectionStore([1, 2]);
      stubAppStore({ 1: { strDisplayName: "RetroDECK" }, 2: { strDisplayName: "Some Game" } });
      const view = await pageOn("non-steam");

      expect(view.container.textContent).toContain("2 entries");
      expect(view.container.textContent).toContain("1 protected");
      expect(view.container.textContent).toContain("1 would be removed");
    });

    it("first press arms; the second removes everything not whitelisted", async () => {
      stubCollectionStore([1, 2]);
      stubAppStore({ 1: { strDisplayName: "RetroDECK" }, 2: { strDisplayName: "Some Game" } });
      const view = await pageOn("non-steam");

      fireEvent.click(button(view, /Remove \d+ /));
      expect(view.container.textContent).toContain("Remove 1 games (1 whitelisted)?");
      expect(vi.mocked(removeShortcut)).not.toHaveBeenCalled();

      await press(button(view, /Remove \d+ /));

      expect(vi.mocked(removeShortcut)).toHaveBeenCalledWith(2);
      expect(vi.mocked(removeShortcut)).toHaveBeenCalledTimes(1);
      await waitFor(() => expect(view.getByTestId("status-non-steam").textContent).toBe("Removed 1 non-Steam game"));
    });

    it("asks a second time before removing an unprotected RetroDECK", async () => {
      vi.mocked(backend.getWhitelistSettings).mockResolvedValue({
        disabled_defaults: ["retrodeck"],
        custom_names: [],
      });
      stubCollectionStore([1]);
      stubAppStore({ 1: { strDisplayName: "RetroDECK" } });
      const view = await pageOn("non-steam");

      fireEvent.click(button(view, /Remove \d+ /));
      expect(view.container.textContent).toContain("WARNING: RetroDECK not protected!");
      fireEvent.click(button(view, /Remove \d+ /));
      expect(view.container.textContent).toContain("RETRODECK WILL BE REMOVED");
      expect(vi.mocked(removeShortcut)).not.toHaveBeenCalled();

      await press(button(view, /RETRODECK WILL BE REMOVED/));

      expect(vi.mocked(removeShortcut)).toHaveBeenCalledWith(1);
    });

    it("re-counts only once Steam's shortcut store has actually shrunk", async () => {
      // Steam drops a removed shortcut from its store a beat after the removal
      // fires, so the row's count must not re-read until the store says so.
      stubCollectionStore([10, 20]);
      stubAppStore({ 10: { strDisplayName: "Game Ten" }, 20: { strDisplayName: "Game Twenty" } });
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 2 }));
      vi.mocked(backend.removeAllShortcuts).mockResolvedValue({
        success: true,
        message: "Removed all",
        app_ids: [10, 20],
        rom_ids: [1, 2],
      });
      const view = await pageOn("shortcuts");
      expect(view.getByTestId("data-row-non-steam").textContent).toContain("2");

      // Arm under real timers, then drive the settle poll under fake ones so
      // its 250 ms cadence fires without a real wait.
      fireEvent.click(button(view, "Remove all shortcuts"));
      vi.useFakeTimers();
      try {
        await act(async () => {
          fireEvent.click(button(view, "Remove every RomM shortcut?"));
          for (let i = 0; i < 8; i++) await Promise.resolve();
          await vi.advanceTimersByTimeAsync(300);
        });
        // `removeShortcut` is the mocked util, so the store is still full: the
        // count must stay where it was.
        expect(view.getByTestId("data-row-non-steam").textContent).toContain("2");

        collectionStore.deckDesktopApps!.apps.delete(10);
        collectionStore.deckDesktopApps!.apps.delete(20);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(300);
        });

        expect(view.getByTestId("data-row-non-steam").textContent).toContain("0");
      } finally {
        vi.useRealTimers();
      }
    });

    it("re-counts after the settle timeout even if the store never shrinks", async () => {
      stubCollectionStore([10, 20]);
      stubAppStore({ 10: { strDisplayName: "Game Ten" }, 20: { strDisplayName: "Game Twenty" } });
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 2 }));
      vi.mocked(backend.removeAllShortcuts).mockResolvedValue({
        success: true,
        message: "Removed all",
        app_ids: [10, 20],
        rom_ids: [1, 2],
      });
      const view = await pageOn("shortcuts");

      fireEvent.click(button(view, "Remove all shortcuts"));
      vi.useFakeTimers();
      try {
        await act(async () => {
          fireEvent.click(button(view, "Remove every RomM shortcut?"));
          for (let i = 0; i < 8; i++) await Promise.resolve();
          // Past the 3 s ceiling: the poll gives up and re-counts anyway, so a
          // store that never settles cannot wedge the page.
          await vi.advanceTimersByTimeAsync(3500);
        });

        expect(view.getByTestId("status-shortcuts").textContent).toBe("Removed all");
      } finally {
        vi.useRealTimers();
      }
    });
  });

  describe("the whitelist", () => {
    it("is collapsed until asked for, then lists every entry", async () => {
      stubCollectionStore([101, 102, 103]);
      stubAppStore({
        101: { strDisplayName: "Zebra App" },
        102: { strDisplayName: "Apple App" },
        103: { strDisplayName: "Mango App" },
      });
      const view = await pageOn("non-steam");

      expect(view.queryAllByTestId("toggle")).toHaveLength(0);
      fireEvent.click(view.getByText("Configure whitelist (0 protected)"));

      // The enumeration sorts before it renders.
      expect(view.getAllByTestId("toggle").map((row) => row.textContent)).toEqual([
        "Apple App",
        "Mango App",
        "Zebra App",
      ]);
    });

    it("counts a custom name as protected and a disabled default as not", async () => {
      vi.mocked(backend.getWhitelistSettings).mockResolvedValue({
        disabled_defaults: ["firefox"],
        custom_names: ["MyCustomApp"],
      });
      stubCollectionStore([1, 2]);
      stubAppStore({ 1: { strDisplayName: "MyCustomApp" }, 2: { strDisplayName: "Firefox" } });
      const view = await pageOn("non-steam");

      expect(view.getByText("Configure whitelist (1 protected)")).toBeTruthy();
    });

    it("marks a default-pattern entry as automatic", async () => {
      stubCollectionStore([1]);
      stubAppStore({ 1: { strDisplayName: "RetroDECK" } });
      const view = await pageOn("non-steam");

      fireEvent.click(view.getByText("Configure whitelist (1 protected)"));

      expect(view.getByText("RetroDECK (auto)")).toBeTruthy();
    });

    it("toggling a non-default entry ON adds it to the custom names", async () => {
      stubCollectionStore([1]);
      stubAppStore({ 1: { strDisplayName: "Some Game" } });
      const view = await pageOn("non-steam");

      fireEvent.click(view.getByText("Configure whitelist (0 protected)"));
      fireEvent.click(view.getByTestId("toggle").querySelector("input")!);

      expect(vi.mocked(backend.updateWhitelistSettings)).toHaveBeenCalledWith([], ["Some Game"]);
    });

    it("toggling a default-pattern entry OFF disables that default", async () => {
      stubCollectionStore([1]);
      stubAppStore({ 1: { strDisplayName: "RetroDECK" } });
      const view = await pageOn("non-steam");

      fireEvent.click(view.getByText("Configure whitelist (1 protected)"));
      fireEvent.click(view.getByTestId("toggle").querySelector("input")!);

      expect(vi.mocked(backend.updateWhitelistSettings)).toHaveBeenCalledWith(["retrodeck"], []);
    });

    it("filters the list through the search box", async () => {
      stubCollectionStore([1, 2]);
      stubAppStore({ 1: { strDisplayName: "Apple App" }, 2: { strDisplayName: "Zebra App" } });
      const view = await pageOn("non-steam");

      fireEvent.click(view.getByText("Configure whitelist (0 protected)"));
      fireEvent.change(view.getByTestId("text-field"), { target: { value: "zeb" } });

      expect(view.getAllByTestId("toggle").map((row) => row.textContent)).toEqual(["Zebra App"]);
    });

    it("a whitelist change disarms the removal confirm", async () => {
      stubCollectionStore([1]);
      stubAppStore({ 1: { strDisplayName: "Some Game" } });
      const view = await pageOn("non-steam");

      fireEvent.click(button(view, /Remove \d+ /));
      expect(view.container.textContent).toContain("Remove 1 games");

      fireEvent.click(view.getByText("Configure whitelist (0 protected)"));

      expect(view.container.textContent).not.toContain("Remove 1 games (");
    });

    it("logs the failure when the whitelist write rejects", async () => {
      vi.mocked(backend.updateWhitelistSettings).mockRejectedValue(new Error("offline"));
      const logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      stubCollectionStore([1]);
      stubAppStore({ 1: { strDisplayName: "Some Game" } });
      const view = await pageOn("non-steam");

      fireEvent.click(view.getByText("Configure whitelist (0 protected)"));
      fireEvent.click(view.getByTestId("toggle").querySelector("input")!);
      await flushAsync();

      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to update whitelist settings"));
      logSpy.mockRestore();
    });
  });

  describe("the non-Steam enumeration", () => {
    it("warns and shows a dash and the failure line when collectionStore is undefined", async () => {
      vi.stubGlobal("collectionStore", undefined);
      const logSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
      const view = await pageOn("non-steam");

      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("collectionStore not available"));
      expect(view.getByTestId("data-row-non-steam").textContent).toContain("—");
      expect(view.container.textContent).toContain("Steam's shortcut list could not be read");
      expect(view.container.textContent).not.toContain("No other non-Steam games found");
      logSpy.mockRestore();
    });

    it("warns and shows a dash and the failure line when deckDesktopApps.apps is missing", async () => {
      vi.stubGlobal("collectionStore", { deckDesktopApps: undefined, userCollections: [] });
      const logSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => {});
      const view = await pageOn("non-steam");

      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("deckDesktopApps.apps not available"));
      expect(view.getByTestId("data-row-non-steam").textContent).toContain("—");
      expect(view.container.textContent).toContain("Steam's shortcut list could not be read");
      expect(view.container.textContent).not.toContain("No other non-Steam games found");
      logSpy.mockRestore();
    });

    it("falls back to display_name when strDisplayName is missing", async () => {
      stubCollectionStore([200]);
      vi.stubGlobal("appStore", {
        GetAppOverviewByAppID: vi.fn(() => ({ strDisplayName: undefined, display_name: "DisplayOnly", appid: 200 })),
        allApps: [],
      });
      const view = await pageOn("non-steam");

      fireEvent.click(view.getByText("Configure whitelist (0 protected)"));

      expect(view.getByText("DisplayOnly")).toBeTruthy();
    });

    it("falls back to 'Unknown (id)' when no overview is returned", async () => {
      stubCollectionStore([999]);
      vi.stubGlobal("appStore", { GetAppOverviewByAppID: vi.fn(() => null), allApps: [] });
      const view = await pageOn("non-steam");

      fireEvent.click(view.getByText("Configure whitelist (0 protected)"));

      expect(view.getByText("Unknown (999)")).toBeTruthy();
    });

    it("falls back to 'Unknown (id)' when appStore is undefined", async () => {
      stubCollectionStore([42]);
      vi.stubGlobal("appStore", undefined);
      const view = await pageOn("non-steam");

      fireEvent.click(view.getByText("Configure whitelist (0 protected)"));

      expect(view.getByText("Unknown (42)")).toBeTruthy();
    });

    it("logs an error and shows a dash and the failure line when enumeration throws", async () => {
      vi.stubGlobal("collectionStore", {
        deckDesktopApps: {
          apps: {
            get size() {
              return 1;
            },
            keys() {
              throw new Error("iteration boom");
            },
          },
        },
        userCollections: [],
      });
      const logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      const view = await pageOn("non-steam");

      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to enumerate non-steam games"));
      // A list that broke off is not an empty library: the row shows a dash,
      // the pane says the list could not be read, and nothing is offered.
      expect(view.getByTestId("data-row-non-steam").textContent).toContain("—");
      expect(view.container.textContent).not.toContain("No other non-Steam games found");
      expect(view.container.textContent).toContain("Steam's shortcut list could not be read");
      expect(view.queryByText(/Remove .*non-Steam game/)).toBeNull();
      logSpy.mockRestore();
    });
  });

  describe("the mount reads", () => {
    it("asks for the whitelist, the shortcut count and the inventory once each", async () => {
      await renderPage();

      expect(vi.mocked(backend.getWhitelistSettings)).toHaveBeenCalledTimes(1);
      expect(vi.mocked(backend.getSyncStats)).toHaveBeenCalledTimes(1);
      expect(vi.mocked(backend.getDataInventory)).toHaveBeenCalledTimes(1);
      // The per-platform actions left for Library › Platforms.
      expect(vi.mocked(backend.getRegistryPlatforms)).not.toHaveBeenCalled();
    });

    it("logs the failure and shows a dash in the Installed ROMs row when the inventory read rejects", async () => {
      vi.mocked(backend.getDataInventory).mockRejectedValue(new Error("offline"));
      const logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      const view = await renderPage();

      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to read the data inventory"));
      expect(view.getByTestId("data-row-rom-files").textContent).toContain("—");
      logSpy.mockRestore();
    });

    it("logs the failure and shows a dash when the shortcut count rejects", async () => {
      vi.mocked(backend.getSyncStats).mockRejectedValue(new Error("offline"));
      const logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      const view = await renderPage();

      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to read the shortcut count"));
      expect(view.getByTestId("data-row-shortcuts").textContent).toContain("—");
      logSpy.mockRestore();
    });

    it("logs the failure when the whitelist read rejects", async () => {
      vi.mocked(backend.getWhitelistSettings).mockRejectedValue(new Error("offline"));
      const logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      await renderPage();

      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to load whitelist settings"));
      logSpy.mockRestore();
    });

    it("renders the Back row and reports the press", async () => {
      const onBack = vi.fn();
      const view = render(<DataManagementPage onBack={onBack} />);
      await flushAsync();

      fireEvent.click(view.getByText(/Back/));

      expect(onBack).toHaveBeenCalledTimes(1);
    });
  });

  describe("a removal in flight", () => {
    it("shows the progress line and disables every removal button", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 2 }));
      let release: (() => void) | undefined;
      vi.mocked(backend.removeAllShortcuts).mockReturnValue(
        new Promise((resolve) => {
          release = () => resolve({ success: true, message: "Removed", app_ids: [1], rom_ids: [1] });
        }),
      );
      const view = await pageOn("shortcuts");

      fireEvent.click(button(view, "Remove all shortcuts"));
      await act(async () => {
        fireEvent.click(button(view, "Remove every RomM shortcut?"));
        await Promise.resolve();
      });

      expect(view.container.textContent).toContain("Removing shortcuts");
      expect(button(view, "Remove all shortcuts")).toHaveProperty("disabled", true);

      await act(async () => {
        release!();
        await Promise.resolve();
        await Promise.resolve();
      });

      await waitFor(() => expect(view.container.textContent).not.toContain("Removing all shortcuts..."));
    });
  });

  describe("a removal in flight disables every pane's buttons", () => {
    it("the uninstall shows the busy line and disables the grid buttons too", async () => {
      vi.mocked(backend.getDataInventory).mockResolvedValue(inventory({ installed_roms: 3 }));
      let release: (() => void) | undefined;
      vi.mocked(backend.uninstallAllRoms).mockReturnValue(
        new Promise((resolve) => {
          release = () => resolve({ success: true, removed_count: 3, errors: [], app_ids: [] });
        }),
      );
      const view = await pageOn("rom-files");

      fireEvent.click(button(view, "Uninstall all ROM files"));
      await act(async () => {
        fireEvent.click(button(view, "Delete every downloaded ROM file?"));
        await Promise.resolve();
      });

      // The page's own busy line, naming the operation in its own verb.
      expect(view.container.textContent).toContain("Uninstalling ROM files");
      expect(button(view, "Uninstall all ROM files")).toHaveProperty("disabled", true);
      // And a button on a pane the reader is not standing on.
      selectRow(view, "grid-images");
      expect(button(view, "Scan for orphaned images")).toHaveProperty("disabled", true);

      await act(async () => {
        release!();
        await Promise.resolve();
        await Promise.resolve();
      });

      await waitFor(() => expect(button(view, "Scan for orphaned images")).toHaveProperty("disabled", false));
    });

    it("disables the grid removal while another pane's removal runs", async () => {
      vi.mocked(backend.cleanupOrphanedGridImages).mockResolvedValue({ success: true, candidate_count: 2 });
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 1 }));
      let release: (() => void) | undefined;
      vi.mocked(backend.removeAllShortcuts).mockReturnValue(
        new Promise((resolve) => {
          release = () => resolve({ success: true, message: "Removed", app_ids: [], rom_ids: [] });
        }),
      );
      const view = await pageOn("grid-images");
      await press(button(view, "Scan for orphaned images"));

      selectRow(view, "shortcuts");
      fireEvent.click(button(view, "Remove all shortcuts"));
      await act(async () => {
        fireEvent.click(button(view, "Remove every RomM shortcut?"));
        await Promise.resolve();
      });

      selectRow(view, "grid-images");
      expect(button(view, /Remove \d+ orphaned image/)).toHaveProperty("disabled", true);

      await act(async () => {
        release!();
        await Promise.resolve();
        await Promise.resolve();
      });
    });
  });

  describe("the three branches with no other home", () => {
    it("re-counts immediately when Steam's store is unreadable during the removal", async () => {
      // `readShortcutStoreSize` answers null, so the settle poll has no
      // baseline to wait on and must re-count at once rather than sit out its
      // three-second ceiling on a real timer.
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 2 }));
      vi.mocked(backend.removeAllShortcuts).mockResolvedValue({
        success: true,
        message: "Removed all",
        app_ids: [10, 20],
        rom_ids: [1],
      });
      const view = await pageOn("shortcuts");
      vi.stubGlobal("collectionStore", undefined);

      fireEvent.click(button(view, "Remove all shortcuts"));
      await press(button(view, "Remove every RomM shortcut?"));

      // Settling never happened and the flow still finished: the status landed
      // without any timer being advanced.
      expect(view.getByTestId("status-shortcuts").textContent).toBe("Removed all");
    });

    it("disarms the confirm as the removal STARTS, not after it finishes", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 1 }));
      let release: (() => void) | undefined;
      vi.mocked(backend.removeAllShortcuts).mockReturnValue(
        new Promise((resolve) => {
          release = () => resolve({ success: true, message: "Removed", app_ids: [], rom_ids: [] });
        }),
      );
      const view = await pageOn("shortcuts");

      fireEvent.click(button(view, "Remove all shortcuts"));
      await act(async () => {
        fireEvent.click(button(view, "Remove every RomM shortcut?"));
        await Promise.resolve();
      });

      // Mid-removal the label is back to its unarmed form, so a stray press
      // cannot re-enter a second run behind the first.
      expect(view.queryByText("Remove every RomM shortcut?")).toBeNull();
      expect(button(view, "Remove all shortcuts")).toHaveProperty("disabled", true);

      await act(async () => {
        release!();
        await Promise.resolve();
        await Promise.resolve();
      });
    });

    it("disarms the non-Steam confirm as its paced removal starts", async () => {
      stubCollectionStore([1]);
      stubAppStore({ 1: { strDisplayName: "Handmade Shortcut" } });
      const view = await pageOn("non-steam");

      fireEvent.click(button(view, /Remove \d+ /));
      expect(view.container.textContent).toContain("(0 whitelisted)?");
      await press(button(view, /Remove \d+ /));

      expect(view.container.textContent).not.toContain("(0 whitelisted)?");
    });

    it("spins in the whitelist until the settings have loaded", async () => {
      let release: ((value: { disabled_defaults: string[]; custom_names: string[] }) => void) | undefined;
      vi.mocked(backend.getWhitelistSettings).mockReturnValue(
        new Promise((resolve) => {
          release = resolve;
        }),
      );
      stubCollectionStore([1]);
      stubAppStore({ 1: { strDisplayName: "Handmade Shortcut" } });
      const view = await pageOn("non-steam");

      fireEvent.click(view.getByText("Configure whitelist (0 protected)"));

      expect(view.getByTestId("spinner")).toBeTruthy();
      expect(view.queryAllByTestId("toggle")).toHaveLength(0);

      await act(async () => {
        release!({ disabled_defaults: [], custom_names: [] });
        await Promise.resolve();
      });

      expect(view.getAllByTestId("toggle")).toHaveLength(1);
    });
  });

  describe("the sync-running guard", () => {
    it("disables the shortcut and ROM removals and says why while a sync runs", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 4 }));
      vi.mocked(backend.getDataInventory).mockResolvedValue(inventory({ installed_roms: 4 }));
      setSyncProgress({ running: true, stage: "applying", current: 1, total: 4, message: "", runId: "run-1" });
      const view = await pageOn("shortcuts");

      expect(button(view, "Remove all shortcuts")).toHaveProperty("disabled", true);
      expect(view.container.textContent).toContain("sync");

      selectRow(view, "rom-files");
      expect(button(view, "Uninstall all ROM files")).toHaveProperty("disabled", true);
    });

    it("leaves them pressable with no hint when no sync runs", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(stats({ total_shortcuts: 4 }));
      const view = await pageOn("shortcuts");

      expect(button(view, "Remove all shortcuts")).toHaveProperty("disabled", false);
    });
  });
});
