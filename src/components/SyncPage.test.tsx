// CATCH-REJECTION ASSERTION RULE (applies to every orchestration shell test):
// Every catch block with an observable side effect MUST have that side effect
// asserted — the rendered status line, the reverted control, the logged
// message. Asserting only that the rejecting call was invoked is vacuous: the
// rejection happens after the call returns, so the test passes with or without
// the `.catch`.
//
// SyncPage catch sites (asserted below):
//   - computePreview try/catch → the "Could not work out what would change"
//     line, and the optimistic run frame retracted.
//   - computePreview's discard `.catch` → logError, asserted through the spy.
//   - applyPreview try/catch → "Failed to apply sync".
//   - cancelPreview `.catch` → logError, asserted through the spy.
//   - cancelRun catch → "Failed to cancel sync" with the run left in flight.
//   - changeSkipPreview catch → the toggle put back and the line that says so.
//   - forceFullSync catch → "Failed to clear sync cache".
//   - loadRuns catch → the failed-read line, with the rows already held kept.
//   - getSettings `.catch` → logError; the toggle stays off.
//
// WHAT THIS FILE CANNOT SEE: gamepad focus. happy-dom has no navigation tree, so
// a table whose rows are unreachable renders exactly like one whose rows are
// not. What the row tests assert is the SHAPE that makes a row a focus stop —
// an activate handler on the `Focusable` — which is the only half a test can
// carry (docs/architecture/qam-panel.md, "Building blocks").

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, fireEvent, act } from "@testing-library/react";
import type { ReactElement } from "react";
import { SyncPage } from "./SyncPage";
import * as backend from "../api/backend";
import { showModal } from "@decky/ui";
import * as syncManager from "../utils/syncManager";
import { getSyncProgress, setSyncProgress } from "../utils/syncProgress";
import { resetEta } from "../utils/syncEta";
import { adoptPreview, resetPendingPreviewStoreForTests } from "../utils/pendingPreviewStore";
import { attachRunUnitsMirror, resetRunUnitsStoreForTests, seedRunUnits } from "../utils/runUnitsStore";
import { resetSyncStatsStoreForTests } from "../utils/syncStatsStore";
import { NEW_ITEM_SEC, UPDATED_ITEM_SEC, COVER_DOWNLOAD_SEC, FETCH_ALLOWANCE_SEC } from "../utils/syncEstimate";
import { PREVIEW_COUNTDOWN_TICK_MS } from "../utils/previewState";
import type {
  PluginSettings,
  SessionBudgetStatus,
  SyncPlanUnit,
  SyncPreview,
  SyncPreviewSummary,
  SyncRunRecord,
  SyncStats,
} from "../types";

vi.mock("../utils/syncManager", () => ({
  requestSyncCancel: vi.fn(),
  reconcileStaleShortcuts: vi.fn().mockResolvedValue(undefined),
  isCancelRequested: vi.fn().mockReturnValue(false),
  resetSyncCancel: vi.fn(),
}));

// The wide frame reaches Steam's tabbed page, its scroll panel and its
// controller glyphs through this module, which is a webpack probe with no answer
// under happy-dom. Every export is named: Vitest throws on an import of a name a
// mock factory omits.
vi.mock("../utils/deckyUiInternals", () => ({
  quickAccessMenuClasses: undefined,
  ScrollPanel: undefined,
  Tabs: undefined,
  ControllerGlyph: undefined,
  GLYPH_BUTTON_B: 1,
  findSP: () => undefined,
}));

vi.mock("@decky/ui", async () => {
  const { createElement: ce } = await import("react");
  type AnyProps = Record<string, unknown> & { children?: unknown };
  const passthrough = (tag: string) => (p: AnyProps) => ce(tag, {}, p.children as never);
  return {
    PanelSection: passthrough("section"),
    PanelSectionRow: passthrough("div"),
    ButtonItem: ({ children, onClick, disabled }: AnyProps & { onClick?: () => void; disabled?: boolean }) =>
      ce("button", { onClick, disabled }, children as never),
    Field: (p: AnyProps & { label?: unknown; description?: unknown }) =>
      ce(
        "div",
        { "data-testid": "field" },
        ce("span", { "data-testid": "field-label" }, p.label as never),
        ce("span", { "data-testid": "field-desc" }, p.description as never),
        p.children as never,
      ),
    // `onActivate` is what makes a `Focusable` a focus STOP rather than a
    // container that passes focus to its children, and it is the only half of
    // reachability a test can see: happy-dom has no nav tree. Surfaced as a
    // marker attribute so its absence is assertable too. The style rides along
    // because a table row's own register — its padding and type size — is set
    // on the `Focusable` the row IS.
    Focusable: (p: AnyProps & { onActivate?: () => void; onFocus?: (e: unknown) => void; style?: unknown }) =>
      ce(
        "div",
        {
          "data-testid": (p["data-testid"] as string | undefined) ?? "focusable",
          "data-activate": p.onActivate ? "true" : undefined,
          onFocus: p.onFocus,
          style: p.style,
        },
        p.children as never,
      ),
    DialogButton: ({ children, onClick, disabled }: AnyProps & { onClick?: () => void; disabled?: boolean }) =>
      ce("button", { "data-testid": "dialog-button", onClick, disabled }, children as never),
    ToggleField: (
      p: AnyProps & { checked?: boolean; disabled?: boolean; onChange?: (v: boolean) => void; label?: unknown },
    ) =>
      ce(
        "div",
        { "data-testid": "toggle" },
        ce("input", {
          type: "checkbox",
          "data-testid": "toggle-input",
          checked: p.checked ?? false,
          disabled: p.disabled ?? false,
          onChange: (e: { target: { checked: boolean } }) => p.onChange?.(e.target.checked),
        }),
        typeof p.label === "string" ? p.label : null,
      ),
    ProgressBar: (p: AnyProps & { nProgress?: number; indeterminate?: boolean }) =>
      ce(
        "div",
        { "data-testid": "progress" },
        ce("span", { "data-testid": "progress-progress" }, String(p.nProgress)),
        ce("span", { "data-testid": "progress-indeterminate" }, String(p.indeterminate)),
      ),
    Spinner: () => ce("div", { "data-testid": "spinner" }),
    // A no-render stub: the modal body is inspected off the showModal mock.
    ConfirmModal: () => null,
    showModal: vi.fn(),
    useQuickAccessVisible: () => true,
  };
});

const flushAsync = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

function lastConfirmModalProps<T = Record<string, unknown>>(): T | null {
  const calls = vi.mocked(showModal).mock.calls;
  if (calls.length === 0) return null;
  const el = calls[calls.length - 1]?.[0] as ReactElement<T> | undefined;
  return el?.props ?? null;
}

function buttonByExactText(container: HTMLElement, text: string): HTMLButtonElement | null {
  const btn = Array.from(container.querySelectorAll("button")).find((b) => b.textContent === text);
  return (btn as HTMLButtonElement | undefined) ?? null;
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
  return { last_sync: "2026-07-11T17:48:00", platforms: 3, collections: 1, roms: 120, total_shortcuts: 120 };
}

function defaultBudget(): SessionBudgetStatus {
  return {
    success: true,
    rss_kb: 1_200_000,
    warn_kb: 1_800_000,
    ceiling_kb: 2_200_000,
    cliff_kb: 2_450_000,
    memory_delta_kb: 300_000,
    resume_ready: null,
    run_done_items: null,
    run_total_items: null,
  };
}

function summary(overrides: Partial<SyncPreviewSummary> = {}): SyncPreviewSummary {
  return {
    new_count: 0,
    changed_count: 0,
    unchanged_count: 0,
    remove_count: 0,
    disabled_platform_remove_count: 0,
    ...overrides,
  };
}

function preview(overrides: Partial<SyncPreview> = {}): SyncPreview {
  return {
    success: true,
    summary: summary({ new_count: 13, changed_count: 3, remove_count: 6 }),
    new_names: [],
    changed_names: [],
    preview_id: "p1",
    ...overrides,
  };
}

function runRecord(overrides: Partial<SyncRunRecord> = {}): SyncRunRecord {
  return {
    id: "r1",
    started_at: "2026-07-11T09:41:00",
    finished_at: "2026-07-11T09:55:00",
    status: "completed",
    platforms_planned: 14,
    roms_planned: 2001,
    platforms_completed: Array.from({ length: 14 }, (_, i) => `p${i}`),
    collections_completed: ["Favorites", "Shooters", "RPGs"],
    error: null,
    ...overrides,
  };
}

function planUnit(overrides: Partial<SyncPlanUnit> = {}): SyncPlanUnit {
  return { id: 1, type: "platform", name: "PlayStation", rom_count: 40, ...overrides } as SyncPlanUnit;
}

/** Every row of a table on the page, by its rendered text. */
function rowTexts(container: HTMLElement, testIdPrefix: string): string[] {
  return Array.from(container.querySelectorAll(`[data-testid^="${testIdPrefix}"]`)).map((n) => n.textContent);
}

/** The cells of the table row whose first cell starts with *label* — the name
 *  and then one entry per column, so a count can be read off the column it is
 *  actually in rather than out of the row's concatenated text. */
function rowCells(container: HTMLElement, label: string): string[] {
  const row = Array.from(container.querySelectorAll('[data-testid="focusable"]')).find((node) =>
    node.textContent.startsWith(label),
  );
  const grid = row?.querySelector("div");
  return Array.from(grid?.children ?? []).map((cell) => cell.textContent);
}

/** The cell ELEMENTS of the row whose first cell starts with *label* — for the
 *  questions about a cell that its text cannot answer. */
function rowCellNodes(container: HTMLElement, label: string): HTMLElement[] {
  const row = Array.from(container.querySelectorAll('[data-testid="focusable"]')).find((node) =>
    node.textContent.startsWith(label),
  );
  const grid = row?.querySelector("div");
  return Array.from(grid?.children ?? []) as HTMLElement[];
}

async function renderPage() {
  const result = render(<SyncPage onBack={vi.fn()} />);
  await flushAsync();
  return result;
}

/** Render the page and press the button that works out a preview — the page's
 *  own, and since #1814 the only thing that asks for one. */
async function renderAndStartPreview() {
  const result = await renderPage();
  await act(async () => {
    fireEvent.click(buttonByExactText(result.container, "Sync Library")!);
    await Promise.resolve();
    await Promise.resolve();
  });
  return result;
}

describe("SyncPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    resetEta();
    resetSyncStatsStoreForTests();
    resetPendingPreviewStoreForTests();
    resetRunUnitsStoreForTests();
    setSyncProgress({ running: false, stage: "", current: 0, total: 0, message: "" });

    vi.mocked(syncManager.isCancelRequested).mockReturnValue(false);
    vi.mocked(syncManager.reconcileStaleShortcuts).mockResolvedValue(undefined);
    vi.mocked(backend.getSettings).mockResolvedValue(defaultSettings());
    vi.mocked(backend.getSyncStats).mockResolvedValue(defaultStats());
    vi.mocked(backend.getSessionBudgetStatus).mockResolvedValue(defaultBudget());
    vi.mocked(backend.getPendingPreview).mockResolvedValue({ success: true, preview: null });
    vi.mocked(backend.getSyncRuns).mockResolvedValue({ success: true, runs: [] });
    vi.mocked(backend.syncPreview).mockResolvedValue(preview());
    vi.mocked(backend.syncApplyDelta).mockResolvedValue({ success: true, message: "" });
    vi.mocked(backend.syncCancelPreview).mockResolvedValue({ success: true, message: "" });
    vi.mocked(backend.startSync).mockResolvedValue({ success: true, message: "" });
    vi.mocked(backend.cancelSync).mockResolvedValue({ success: true, message: "Cancelled" });
    vi.mocked(backend.clearSyncCache).mockResolvedValue({ success: true, message: "Cleared" });
    vi.mocked(backend.saveSkipPreview).mockResolvedValue({ success: true });
  });

  // ===========================================================================
  // The left column shows exactly one of three things.
  // ===========================================================================
  describe("the left column's three states", () => {
    it("nothing pending: says so, and offers the button that changes it", async () => {
      const { container } = await renderPage();
      expect(container.textContent).toContain("Nothing is waiting to be applied.");
      expect(buttonByExactText(container, "Sync Library")).not.toBeNull();
      expect(container.querySelector('[data-testid="progress"]')).toBeNull();
    });

    it("a preview pending: the table, and the three buttons that end it", async () => {
      adoptPreview(preview());
      const { container } = await renderPage();
      expect(buttonByExactText(container, "Apply Sync")).not.toBeNull();
      expect(buttonByExactText(container, "Refresh")).not.toBeNull();
      expect(buttonByExactText(container, "Cancel")).not.toBeNull();
      expect(buttonByExactText(container, "Sync Library")).toBeNull();
    });

    it("a run in flight owns the column, even while the store still holds a preview", async () => {
      // The preview is not dropped — the store keeps it and the table comes back
      // when the run ends — but the progress rows are the true state of the
      // machine and must not be rendered over.
      adoptPreview(preview());
      setSyncProgress({ running: true, stage: "applying", step: 1, totalSteps: 3, message: "GBA: 1/2" });
      const { container } = await renderPage();

      expect(buttonByExactText(container, "Cancel Sync")).not.toBeNull();
      expect(buttonByExactText(container, "Apply Sync")).toBeNull();
      expect(buttonByExactText(container, "Sync Library")).toBeNull();
    });

    it("switches back to the table the moment the run stops", async () => {
      adoptPreview(preview());
      setSyncProgress({ running: true, stage: "applying", step: 1, totalSteps: 3, message: "GBA: 1/2" });
      const { container } = await renderPage();
      expect(buttonByExactText(container, "Apply Sync")).toBeNull();

      await act(async () => {
        setSyncProgress({ running: false, stage: "done", message: "Sync complete" });
        await Promise.resolve();
      });

      expect(buttonByExactText(container, "Apply Sync")).not.toBeNull();
    });
  });

  // ===========================================================================
  // The preview table.
  // ===========================================================================
  describe("the preview table", () => {
    const breakdown = [
      { slug: "psx", name: "PlayStation", synced: true, new_count: 4, changed_count: 1, remove_count: 0 },
      { slug: "snes", name: "SNES", synced: true, new_count: 8, changed_count: 2, remove_count: 0 },
      { slug: "saturn", name: "Sega Saturn", synced: false, new_count: 1, changed_count: 0, remove_count: 6 },
    ];

    it("draws one row per platform, and a total from the SUMMARY rather than from the rows", async () => {
      // The summary disagrees with its own breakdown on purpose: the three rows
      // add up to 13 / 3 / 6, and the totals row must show what the summary
      // says. A page that added the rows up would read 13 / 3 / 6 here.
      adoptPreview(
        preview({
          summary: summary({ new_count: 99, changed_count: 7, remove_count: 5, platform_breakdown: breakdown }),
        }),
      );
      const { container } = await renderPage();

      expect(rowCells(container, "PlayStation").slice(1)).toEqual(["4", "1", "0"]);
      expect(rowCells(container, "SNES").slice(1)).toEqual(["8", "2", "0"]);
      expect(rowCells(container, "Total").slice(1)).toEqual(["99", "7", "5"]);
    });

    it("marks a platform the run's platform list does not hold", async () => {
      adoptPreview(preview({ summary: summary({ new_count: 13, remove_count: 6, platform_breakdown: breakdown }) }));
      const { container } = await renderPage();
      expect(container.textContent).toContain("Sega Saturn · not synced as a platform");
      expect(container.textContent).not.toContain("PlayStation · not synced");
    });

    it("builds one row for collections, naming them on the row's second line", async () => {
      adoptPreview(
        preview({
          summary: summary({
            new_count: 13,
            remove_count: 6,
            platform_breakdown: breakdown,
            collection_diff: { has_changes: true, added: ["Favorites"], removed: ["Beat 'em ups"] },
          }),
        }),
      );
      const { container } = await renderPage();
      expect(container.textContent).toContain("Collections");
      expect(container.textContent).toContain("Added: Favorites · Removed: Beat 'em ups");
    });

    it("names a change to the Steam collections kept per platform, which nothing else states", async () => {
      // The only thing this preview would change is a platform collection. The
      // page must not read "Everything is up to date." with Apply Sync live over
      // it — the two answers come from the same field for exactly that reason.
      adoptPreview(
        preview({
          summary: summary({ platform_collection_diff: { has_changes: true, added_count: 2, removed_count: 1 } }),
        }),
      );
      const { container } = await renderPage();

      expect(container.textContent).toContain("Platform collections");
      expect(container.textContent).toContain("Added: 2 · Removed: 1");
      expect(container.textContent).not.toContain("Everything is up to date.");
      expect(buttonByExactText(container, "Apply Sync")?.disabled).toBe(false);
    });

    it("keeps collection counts out of the game columns, so the columns still make the total", async () => {
      adoptPreview(
        preview({
          summary: summary({
            new_count: 13,
            changed_count: 3,
            remove_count: 6,
            platform_breakdown: breakdown,
            collection_diff: { has_changes: true, added: ["Favorites"], removed: ["Beat 'em ups"] },
            platform_collection_diff: { has_changes: true, added_count: 2, removed_count: 0 },
          }),
        }),
      );
      const { container } = await renderPage();

      // Both collection rows say what changed on their second line and draw a
      // dash in the game columns: 4 + 8 + 1 new, 1 + 2 updated and 6 removed are
      // the platform rows alone, and they are what the total states.
      expect(rowCells(container, "Collections").slice(1)).toEqual(["—", "—", "—"]);
      expect(rowCells(container, "Platform collections").slice(1)).toEqual(["—", "—", "—"]);
      expect(rowCells(container, "Total").slice(1)).toEqual(["13", "3", "6"]);
    });

    it("states under the total what the game columns cannot carry (#1814)", async () => {
      // The device case: the only change is one collection's membership, so
      // every game column is a dash and the total is three zeros — under a live
      // Apply Sync. The line under the total is what stops the total being the
      // last word.
      adoptPreview(
        preview({
          summary: summary({
            collection_diff: { has_changes: true, added: ["Favorites"], removed: [] },
          }),
        }),
      );
      const { container } = await renderPage();

      expect(rowCells(container, "Total").slice(1)).toEqual(["0", "0", "0"]);
      expect(container.textContent).toContain("plus 1 collection added");
      expect(buttonByExactText(container, "Apply Sync")?.disabled).toBe(false);
    });

    it("counts both sides of both collection diffs on that line", async () => {
      adoptPreview(
        preview({
          summary: summary({
            new_count: 13,
            changed_count: 3,
            remove_count: 6,
            platform_breakdown: breakdown,
            collection_diff: { has_changes: true, added: ["Favorites"], removed: ["Beat 'em ups", "RPGs"] },
            // No names come for these, so the two sides are one changed count —
            // which is what the row above them says about them too.
            platform_collection_diff: { has_changes: true, added_count: 2, removed_count: 1 },
          }),
        }),
      );
      const { container } = await renderPage();

      expect(container.textContent).toContain(
        "plus 1 collection added, 2 collections removed, 3 platform collections changed",
      );
    });

    it("says nothing under the total where the columns already tell the whole story", async () => {
      adoptPreview(preview({ summary: summary({ new_count: 13, platform_breakdown: breakdown }) }));
      const { container } = await renderPage();
      expect(container.textContent).not.toContain("plus ");
    });

    it("never reads 'Everything is up to date.' under a live Apply Sync", async () => {
      // One case per leg of `previewHasChanges`, which is the condition the
      // button reads: the sentence and the button now come from the same answer,
      // so no leg can arm one and not the other.
      const legs: SyncPreviewSummary[] = [
        summary({ new_count: 1 }),
        summary({ remove_count: 1 }),
        summary({ collection_diff: { has_changes: true, added: ["Favorites"], removed: [] } }),
        summary({ platform_collection_diff: { has_changes: true, added_count: 1, removed_count: 0 } }),
        summary({ cover_refresh_count: 3 }),
        summary({ restamp_platform_count: 1 }),
      ];
      for (const leg of legs) {
        resetPendingPreviewStoreForTests();
        adoptPreview(preview({ summary: leg }));
        const { container, unmount } = await renderPage();
        expect(buttonByExactText(container, "Apply Sync")?.disabled).toBe(false);
        expect(container.textContent).not.toContain("Everything is up to date.");
        unmount();
      }
    });

    it("says the split is unavailable when the backend sent none, and adds nothing up itself", async () => {
      adoptPreview(preview({ summary: summary({ new_count: 13, changed_count: 3, remove_count: 6 }) }));
      const { container } = await renderPage();

      expect(container.textContent).toContain("did not send the per-platform split");
      // The totals row still stands, straight from the summary — there are no
      // rows here that could have been added up into it.
      expect(rowCells(container, "Total").slice(1)).toEqual(["13", "3", "6"]);
    });

    it("clips a name too long for its column instead of letting it run into the counts (#1814)", async () => {
      // The device case: "Game Boy Advance · not synced as a platform" ran into
      // the New column's digit. The clip is on the CELL, because a grid item is
      // blockified and an inline span nested inside it is not — the same three
      // properties on the span do nothing at all.
      adoptPreview(
        preview({
          summary: summary({
            remove_count: 6,
            platform_breakdown: [
              { slug: "gba", name: "Game Boy Advance", synced: false, new_count: 0, changed_count: 0, remove_count: 6 },
            ],
          }),
        }),
      );
      const { container } = await renderPage();

      const nameCell = rowCellNodes(container, "Game Boy Advance")[0];
      expect(nameCell?.style.overflow).toBe("hidden");
      expect(nameCell?.style.textOverflow).toBe("ellipsis");
      expect(nameCell?.style.whiteSpace).toBe("nowrap");
      expect(nameCell?.style.minWidth).toBe("0");
      // What the clip takes away is handed back whole, note included.
      expect(nameCell?.querySelector("span")?.getAttribute("title")).toBe(
        "Game Boy Advance · not synced as a platform",
      );
      // The counts keep their own columns, so the clip has room to work.
      expect(rowCells(container, "Game Boy Advance").slice(1)).toEqual(["0", "0", "6"]);
    });

    it("every row is a focus stop, so the reader can walk the table and scroll it", async () => {
      adoptPreview(preview({ summary: summary({ new_count: 13, remove_count: 6, platform_breakdown: breakdown }) }));
      const { container } = await renderPage();

      const platformRows = Array.from(container.querySelectorAll('[data-testid="focusable"]')).filter((n) =>
        ["PlayStation", "SNES", "Sega Saturn", "Total"].some((name) => n.textContent.startsWith(name)),
      );
      expect(platformRows).toHaveLength(4);
      for (const row of platformRows) expect(row.getAttribute("data-activate")).toBe("true");
    });

    it("shows the sentence instead of a table of zeros when nothing would change", async () => {
      adoptPreview(preview({ summary: summary() }));
      const { container } = await renderPage();
      expect(container.textContent).toContain("Everything is up to date.");
      expect(container.textContent).not.toContain("Platform");
    });

    it("names cover-only work, which still has an Apply to press (#1386)", async () => {
      adoptPreview(preview({ summary: summary({ cover_refresh_count: 12 }) }));
      const { container } = await renderPage();
      expect(container.textContent).toContain("No shortcut changes — 12 cover updates.");
      expect(buttonByExactText(container, "Apply Sync")?.disabled).toBe(false);
    });

    it("names the re-stamp work behind an empty delta (#1416)", async () => {
      adoptPreview(preview({ summary: summary({ restamp_platform_count: 2 }) }));
      const { container } = await renderPage();
      expect(container.textContent).toContain("No changes — finishing a previous sync.");
      expect(buttonByExactText(container, "Apply Sync")?.disabled).toBe(false);
    });

    it("disables Apply — rendered, never hidden — when there is nothing to do", async () => {
      adoptPreview(preview({ summary: summary() }));
      const { container } = await renderPage();
      expect(buttonByExactText(container, "Apply Sync")?.disabled).toBe(true);
    });

    it("names the full re-sync above the table so a big 'updated' count is not a surprise (#1318)", async () => {
      adoptPreview(
        preview({ summary: summary({ changed_count: 800, sync_platform_count: 3, restamp_platform_count: 3 }) }),
      );
      const { container } = await renderPage();
      expect(container.textContent).toContain("Full re-sync — all platforms re-fetched.");
    });

    it("states the scope and the estimated duration under the table", async () => {
      // 1000 new * (0.36s walk + 0.15s cover) + 45s allowance = 555s → ~9 min.
      adoptPreview(
        preview({ summary: summary({ new_count: 1000, sync_platform_count: 3, sync_collection_count: 2 }) }),
      );
      const { container } = await renderPage();
      expect(container.textContent).toContain("Syncing 3 platforms · 2 collections · estimated duration 9 min");
    });

    it("appends the sleep caveat only past ten minutes", async () => {
      adoptPreview(preview({ summary: summary({ new_count: 1 }) }));
      const short = await renderPage();
      expect(short.container.textContent).toContain("Progress is saved about every 200 games — cancelling is safe.");
      expect(short.container.textContent).not.toContain("Long syncs pause during sleep");
      short.unmount();

      resetPendingPreviewStoreForTests();
      adoptPreview(preview({ summary: summary({ new_count: 1200 }) }));
      const long = await renderPage();
      expect(long.container.textContent).toContain("Long syncs pause during sleep; keep the Deck powered.");
    });

    it("shows the budget advisory when the backend expects the run to pause", async () => {
      adoptPreview(preview({ pause_likely: true }));
      const { container } = await renderPage();
      expect(container.querySelector('[data-testid="budget-advisory"]')).not.toBeNull();
    });

    it("shows no advisory when the backend does not expect a pause", async () => {
      adoptPreview(preview());
      const { container } = await renderPage();
      expect(container.querySelector('[data-testid="budget-advisory"]')).toBeNull();
    });
  });

  // ===========================================================================
  // The three ways a preview ends. Each has to end it on BOTH sides.
  // ===========================================================================
  describe("the three ways a preview ends", () => {
    it("Apply: applies by id, clears the store, and shows the run", async () => {
      adoptPreview(preview({ preview_id: "p-apply" }));
      const { container } = await renderPage();

      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Apply Sync")!);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(vi.mocked(backend.syncApplyDelta)).toHaveBeenCalledWith("p-apply");
      expect(buttonByExactText(container, "Apply Sync")).toBeNull();
      expect(getSyncProgress().running).toBe(true);
    });

    it("Apply seeds the run's ETA from the delta the reader approved", async () => {
      adoptPreview(preview({ summary: summary({ new_count: 100, changed_count: 200, unchanged_count: 600 }) }));
      const { container } = await renderPage();

      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Apply Sync")!);
        await Promise.resolve();
        await Promise.resolve();
      });

      // The 600 unchanged are skipped entirely by the delta apply (#1383).
      expect(getSyncProgress().etaSeconds).toBeCloseTo(
        100 * NEW_ITEM_SEC + 200 * UPDATED_ITEM_SEC + 100 * COVER_DOWNLOAD_SEC + FETCH_ALLOWANCE_SEC,
      );
    });

    it("Apply: a refusal is said, and the optimistic run frame is retracted", async () => {
      vi.mocked(backend.syncApplyDelta).mockResolvedValue({ success: false, message: "Sync already in progress" });
      adoptPreview(preview());
      const { container } = await renderPage();

      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Apply Sync")!);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(container.textContent).toContain("Sync already in progress");
      expect(getSyncProgress().running).toBe(false);
    });

    it("Apply: a rejection is said too", async () => {
      vi.mocked(backend.syncApplyDelta).mockRejectedValue(new Error("boom"));
      adoptPreview(preview());
      const { container } = await renderPage();

      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Apply Sync")!);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(container.textContent).toContain("Failed to apply sync");
      expect(getSyncProgress().running).toBe(false);
    });

    it("Cancel: drops it locally and tells the backend", async () => {
      adoptPreview(preview());
      const { container } = await renderPage();

      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Cancel")!);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(vi.mocked(backend.syncCancelPreview)).toHaveBeenCalled();
      expect(buttonByExactText(container, "Sync Library")).not.toBeNull();
    });

    it("Cancel: a failed discard is logged, and the page is right either way", async () => {
      const logSpy = vi.spyOn(backend, "logError");
      vi.mocked(backend.syncCancelPreview).mockRejectedValue(new Error("offline"));
      adoptPreview(preview());
      const { container } = await renderPage();

      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Cancel")!);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to discard the pending preview"));
      expect(buttonByExactText(container, "Sync Library")).not.toBeNull();
    });

    it("Refresh: discards on both sides, then works out another and adopts it", async () => {
      const order: string[] = [];
      vi.mocked(backend.syncCancelPreview).mockImplementation(async () => {
        order.push("cancel");
        return { success: true, message: "" };
      });
      vi.mocked(backend.syncPreview).mockImplementation(async () => {
        order.push("preview");
        return preview({ preview_id: "p-fresh", summary: summary({ new_count: 1 }) });
      });
      adoptPreview(preview({ preview_id: "p-old" }));
      const { container } = await renderPage();

      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Refresh")!);
        await Promise.resolve();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(order).toEqual(["cancel", "preview"]);
      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Apply Sync")!);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(vi.mocked(backend.syncApplyDelta)).toHaveBeenCalledWith("p-fresh");
    });
  });

  // ===========================================================================
  // Expiry.
  // ===========================================================================
  describe("the preview's deadline", () => {
    it("counts down, and advances as the clock runs", async () => {
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
      try {
        // `ceil`, not `floor`: the readout floors minutes (a deadline must never
        // promise more time than remains), so a sub-second truncation here would
        // make a 30-minute deadline read 29.
        adoptPreview(preview({ expires_at: Math.ceil(Date.now() / 1000) + 30 * 60 }));
        const { container } = render(<SyncPage onBack={vi.fn()} />);
        await act(async () => {
          await Promise.resolve();
          await Promise.resolve();
          await Promise.resolve();
        });
        expect(container.textContent).toContain("expires in 30 min");

        await act(async () => {
          await vi.advanceTimersByTimeAsync(5 * 60_000);
        });
        expect(container.textContent).toContain("expires in 25 min");
      } finally {
        vi.useRealTimers();
      }
    });

    it("at zero the table stays, Apply goes, and Refresh is what is left", async () => {
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
      try {
        adoptPreview(
          preview({
            expires_at: Math.floor(Date.now() / 1000) + 60,
            summary: summary({ new_count: 13, changed_count: 3, remove_count: 6 }),
          }),
        );
        const { container } = render(<SyncPage onBack={vi.fn()} />);
        await act(async () => {
          await Promise.resolve();
          await Promise.resolve();
          await Promise.resolve();
        });

        await act(async () => {
          await vi.advanceTimersByTimeAsync(61_000);
        });

        expect(container.textContent).toContain("expired");
        expect(container.textContent).toContain("This preview is too old to apply.");
        expect(buttonByExactText(container, "Apply Sync")?.disabled).toBe(true);
        expect(buttonByExactText(container, "Refresh")?.disabled).toBe(false);
        // Nothing disappeared on its own: the change table is still readable.
        expect(container.textContent).toContain("Total");
      } finally {
        vi.useRealTimers();
      }
    });

    it("a press that beat the tick is spent re-reading the clock, not on a refused apply", async () => {
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
      try {
        adoptPreview(preview({ expires_at: Math.ceil(Date.now() / 1000) + 60 }));
        const { container } = render(<SyncPage onBack={vi.fn()} />);
        await act(async () => {
          await Promise.resolve();
          await Promise.resolve();
          await Promise.resolve();
        });
        expect(buttonByExactText(container, "Apply Sync")?.disabled).toBe(false);

        // The clock moves past the deadline WITHOUT the countdown's tick firing —
        // the sub-second window the guard exists for. The button is still the
        // enabled one the last render drew.
        vi.setSystemTime(new Date(Date.now() + 120_000));
        await act(async () => {
          fireEvent.click(buttonByExactText(container, "Apply Sync")!);
          await Promise.resolve();
          await Promise.resolve();
        });

        // The press was not spent on an apply the backend would refuse; it re-read
        // the clock, which is what flips the panel to its expired form.
        expect(vi.mocked(backend.syncApplyDelta)).not.toHaveBeenCalled();
        expect(getSyncProgress().running).toBe(false);
        expect(container.textContent).toContain("This preview is too old to apply.");
        expect(buttonByExactText(container, "Apply Sync")?.disabled).toBe(true);
      } finally {
        vi.useRealTimers();
      }
    });

    it("shows no deadline clause at all when the backend sent none", async () => {
      adoptPreview(preview());
      const { container } = await renderPage();
      expect(container.textContent).not.toContain("expires in");
      expect(container.textContent).not.toContain("expired");
    });

    it("tears the countdown down on unmount, leaving nothing ticking", async () => {
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
      try {
        const setIntervalSpy = vi.spyOn(globalThis, "setInterval");
        const clearIntervalSpy = vi.spyOn(globalThis, "clearInterval");
        adoptPreview(preview({ expires_at: Math.ceil(Date.now() / 1000) + 30 * 60 }));
        const { container, unmount } = render(<SyncPage onBack={vi.fn()} />);
        await act(async () => {
          await Promise.resolve();
          await Promise.resolve();
          await Promise.resolve();
        });
        expect(container.textContent).toContain("expires in 30 min");

        // Nothing paused and no run, so the budget poll is not armed: the page's
        // only 1 Hz timer is the countdown's.
        const armed = setIntervalSpy.mock.calls.findIndex((call) => call[1] === PREVIEW_COUNTDOWN_TICK_MS);
        expect(armed).toBeGreaterThanOrEqual(0);
        const countdownTimer = setIntervalSpy.mock.results[armed]?.value;

        unmount();
        expect(clearIntervalSpy).toHaveBeenCalledWith(countdownTimer);
        expect(vi.getTimerCount()).toBe(0);
      } finally {
        vi.useRealTimers();
      }
    });
  });

  // ===========================================================================
  // The run view.
  // ===========================================================================
  describe("the run view", () => {
    let detachMirror: (() => void) | null = null;

    afterEach(() => {
      detachMirror?.();
      detachMirror = null;
    });

    function seedPlan() {
      seedRunUnits(
        [
          planUnit({ id: 1, name: "PlayStation", rom_count: 40, new_shortcut_count: 4 }),
          planUnit({ id: 2, name: "SNES", rom_count: 800, new_shortcut_count: 120 }),
          planUnit({ id: 3, name: "Game Boy Advance", rom_count: 212, predicted_skip: true }),
        ],
        "run-live",
      );
      detachMirror = attachRunUnitsMirror();
    }

    it("shows the whole run as one bar with the stage and the step counter", async () => {
      setSyncProgress({
        running: true,
        stage: "applying",
        step: 2,
        totalSteps: 3,
        current: 120,
        total: 800,
        message: "SNES: 120/800",
        runId: "run-live",
      });
      const { container } = await renderPage();

      expect(container.querySelector('[data-testid="run-stage"]')?.textContent).toBe("Applying shortcuts");
      expect(container.textContent).toContain("unit 2 of 3");
      expect(container.querySelector('[data-testid="progress-progress"]')?.textContent).not.toBe("undefined");
    });

    it("draws the plan's three row states, each a focus stop", async () => {
      seedPlan();
      await act(async () => {
        setSyncProgress({
          running: true,
          stage: "applying",
          step: 2,
          totalSteps: 3,
          current: 120,
          total: 800,
          message: "SNES: 120/800",
          runId: "run-live",
        });
      });
      const { container } = await renderPage();

      const rows = rowTexts(container, "run-unit-");
      expect(rows).toHaveLength(3);
      // Done, running with its own bar, and waiting with what the plan holds.
      expect(rows[0]).toContain("PlayStation");
      expect(rows[0]).toContain("done");
      expect(rows[1]).toContain("SNES");
      expect(rows[1]).toContain("applying shortcuts");
      expect(rows[2]).toContain("Game Boy Advance");
      expect(rows[2]).toContain("waiting");
      expect(rows[2]).toContain("expected to skip");

      for (const row of container.querySelectorAll('[data-testid^="run-unit-"]')) {
        expect(row.getAttribute("data-activate")).toBe("true");
      }
      expect(container.querySelector('[data-testid="unit-bar"]')).not.toBeNull();
    });

    it("sets its rows in the same register the preview's are set in (#1814)", async () => {
      // One register for both tables, so the page reads as one family and the
      // preview's rows are no taller than the plan's.
      seedPlan();
      const run = await renderPage();
      await act(async () => {
        setSyncProgress({ running: true, stage: "applying", step: 2, totalSteps: 3, runId: "run-live" });
        await Promise.resolve();
      });
      const runRow = run.container.querySelector('[data-testid="run-unit-platform-1"]') as HTMLElement | null;
      expect(runRow?.style.padding).toBe("2px 16px");
      expect(runRow?.style.fontSize).toBe("12px");
      expect(runRow?.style.lineHeight).toBe("1.25");
      run.unmount();

      setSyncProgress({ running: false, stage: "", current: 0, total: 0, message: "" });
      adoptPreview(
        preview({
          summary: summary({
            new_count: 4,
            platform_breakdown: [
              { slug: "psx", name: "PlayStation", synced: true, new_count: 4, changed_count: 0, remove_count: 0 },
            ],
          }),
        }),
      );
      const previewPage = await renderPage();
      const previewRow = Array.from(previewPage.container.querySelectorAll('[data-testid="focusable"]')).find((node) =>
        node.textContent.startsWith("PlayStation"),
      ) as HTMLElement | undefined;
      expect(previewRow?.style.padding).toBe("2px 16px");
      expect(previewRow?.style.fontSize).toBe("12px");
      expect(previewRow?.style.lineHeight).toBe("1.25");
    });

    it("a waiting unit the plan carries no new-shortcut count for shows its ROM count", async () => {
      seedRunUnits([planUnit({ id: 7, type: "collection", name: "Favorites", rom_count: 12 })], "run-live");
      detachMirror = attachRunUnitsMirror();
      setSyncProgress({ running: true, stage: "fetching", step: 0, totalSteps: 1, message: "", runId: "run-live" });
      const { container } = await renderPage();

      const rows = rowTexts(container, "run-unit-");
      expect(rows[0]).toContain("12 ROMs");
      expect(rows[0]).toContain("collection");
    });

    it("with no rows the frame's own detail line takes their place (#1814)", async () => {
      // The plan arrives once per run, so a store that started empty after a
      // reload stays empty for the rest of it — and so does a preview run, which
      // seeds no rows at all. The frames still carry the fine-detail line the
      // whole panel reads, so that is what the column shows.
      setSyncProgress({
        running: true,
        stage: "applying",
        step: 2,
        totalSteps: 3,
        current: 10,
        total: 20,
        message: "SNES: 10/20",
        runId: "run-after-reload",
      });
      const { container } = await renderPage();

      expect(container.textContent).toContain("unit 2 of 3");
      expect(container.querySelector('[data-testid="progress"]')).not.toBeNull();
      expect(container.textContent).toContain("SNES: 10/20");
      expect(container.textContent).not.toContain("Per-unit detail is not available for this run.");
      expect(rowTexts(container, "run-unit-")).toEqual([]);
    });

    it("with neither rows nor a detail line, the bar stands and the gap is said", async () => {
      // A run whose frames carry no fine detail either: the bar and the counter
      // are still the run's, so what is missing is said rather than an empty
      // table being drawn.
      setSyncProgress({ running: true, stage: "applying", step: 2, totalSteps: 3, message: "", runId: "run-bare" });
      const { container } = await renderPage();

      expect(container.textContent).toContain("unit 2 of 3");
      expect(container.textContent).toContain("Per-unit detail is not available for this run.");
      expect(rowTexts(container, "run-unit-")).toEqual([]);
    });

    // =========================================================================
    // The unit list's own scrolling region (#1814).
    //
    // happy-dom lays nothing out and has no gamepad, so the geometry is mocked
    // and what is pinned is the DECISION — which element is scrolled, and to
    // what offset — never the scroll a reader would see. That the running row
    // then looks centred on the Deck is the device round's to settle.
    // =========================================================================
    describe("keeping the running unit in view", () => {
      const RUNNING_ROW = "run-unit-platform-2";

      /** A region 500 tall over *scrollHeight* of content, with the running row
       *  700 down it. Everything else is a 20 px row at the top. */
      function mockGeometry(scrollHeight: number): void {
        vi.spyOn(HTMLElement.prototype, "scrollHeight", "get").mockReturnValue(scrollHeight);
        vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(500);
        vi.spyOn(HTMLElement.prototype, "clientTop", "get").mockReturnValue(0);
        vi.spyOn(Element.prototype, "scrollTop", "get").mockReturnValue(0);
        vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
          const id = this.getAttribute("data-testid");
          if (id === "run-units") return { top: 0, height: 500 } as DOMRect;
          if (id === RUNNING_ROW) return { top: 700, height: 20 } as DOMRect;
          return { top: 0, height: 20 } as DOMRect;
        });
      }

      /** Every `scrollTo` the render provoked, with the element it was called
       *  on — spied on the prototype because the scroll happens in a mount
       *  effect, before a test could reach the element to spy on it.
       *
       *  Read through {@link scrollsOn} rather than whole. The page performs a
       *  second kind of scroll that has nothing to do with this pane: a
       *  `ScrollRegion` reveals its own top when entry focus lands in it
       *  (`revealEdge`), and the frame places that focus on a timer. Asserting
       *  over every scroll therefore asserted more than these cases mean, and
       *  raced the frame — about one full-suite run in four came back with a
       *  stray `{ focusable, 0 }` ahead of the pane's own. The timers are frozen
       *  below so that chain cannot fire here at all; reading per region is what
       *  keeps the cases honest about which scroll each one is about. */
      function recordScrolls(): Array<{ testId: string | null; top: number }> {
        const calls: Array<{ testId: string | null; top: number }> = [];
        vi.spyOn(HTMLElement.prototype, "scrollTo").mockImplementation(function (
          this: HTMLElement,
          options?: ScrollToOptions | number,
        ) {
          calls.push({
            testId: this.getAttribute("data-testid"),
            top: typeof options === "object" ? (options.top ?? 0) : 0,
          });
        });
        return calls;
      }

      /** The offsets one element was scrolled to, in order — `testId` `null` for
       *  an element carrying none, which is every ancestor of the region and so
       *  the shape a pane scrolling the wrong thing would leave. */
      function scrollsOn(calls: Array<{ testId: string | null; top: number }>, testId: string | null): number[] {
        return calls.filter((call) => call.testId === testId).map((call) => call.top);
      }

      async function renderRunning(): Promise<HTMLElement> {
        seedPlan();
        const { container } = await renderPage();
        await act(async () => {
          setSyncProgress({
            running: true,
            stage: "applying",
            step: 2,
            totalSteps: 3,
            current: 120,
            total: 800,
            message: "SNES: 120/800",
            runId: "run-live",
          });
          await Promise.resolve();
        });
        return container;
      }

      // The pane centres the running row synchronously, in its own layout
      // effect; everything the frame does to focus and to reveal a region's edge
      // is behind a timer. Freezing them — and never advancing — leaves the
      // pane's own scroll the only one these cases can see, which is the only
      // one they are about.
      beforeEach(() => {
        vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
      });

      afterEach(() => {
        vi.useRealTimers();
        vi.restoreAllMocks();
      });

      it("gives the unit list a region of its own, with Cancel Sync outside it", async () => {
        const container = await renderRunning();

        const region = container.querySelector('[data-testid="run-units"]');
        expect(region).not.toBeNull();
        expect(region?.querySelector(`[data-testid="${RUNNING_ROW}"]`)).not.toBeNull();
        // Cancel stays under the region rather than scrolling away with the rows.
        expect(region?.textContent).not.toContain("Cancel Sync");
        expect(buttonByExactText(container, "Cancel Sync")).not.toBeNull();
      });

      it("scrolls that region — and only that region — to put the running row in the middle", async () => {
        mockGeometry(1200);
        const calls = recordScrolls();
        await renderRunning();

        // 700 down a 500-tall region, half the row's own 20 px, less half the
        // region: the row's middle lands on the region's middle.
        expect(scrollsOn(calls, "run-units")).toEqual([460]);
        // ...and only that region: an untagged element is an ancestor of it, so
        // a pane that scrolled the column instead of the list shows up here.
        expect(scrollsOn(calls, null)).toEqual([]);
      });

      it("stops at the top rather than scrolling past the start of the list", async () => {
        mockGeometry(1200);
        const calls = recordScrolls();
        // Unit 1, which the geometry above leaves at the region's own top.
        seedRunUnits([planUnit({ id: 1, name: "PlayStation", rom_count: 40, new_shortcut_count: 4 })], "run-live");
        detachMirror = attachRunUnitsMirror();
        const { container } = await renderPage();
        await act(async () => {
          setSyncProgress({ running: true, stage: "applying", step: 1, totalSteps: 1, runId: "run-live" });
          await Promise.resolve();
        });

        // The only row is the running one, and it sits at the region's own top:
        // centring it would ask for a negative offset.
        expect(container.querySelector('[data-testid="run-units"]')).not.toBeNull();
        expect(scrollsOn(calls, "run-units")).toEqual([0]);
      });

      it("scrolls nothing while every row fits", async () => {
        mockGeometry(300);
        const calls = recordScrolls();
        await renderRunning();

        expect(scrollsOn(calls, "run-units")).toEqual([]);
      });
    });

    it("Cancel Sync scopes the cancel to the run and disarms until the terminal frame (#1202)", async () => {
      setSyncProgress({ running: true, stage: "applying", step: 1, totalSteps: 2, message: "x", runId: "run-live" });
      const { container } = await renderPage();

      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Cancel Sync")!);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(vi.mocked(syncManager.requestSyncCancel)).toHaveBeenCalled();
      expect(vi.mocked(backend.cancelSync)).toHaveBeenCalledWith("run-live");
      expect(buttonByExactText(container, "Cancelling…")?.disabled).toBe(true);
    });

    it("a cancel whose CALL failed re-arms the button and leaves the run in flight (#1019)", async () => {
      vi.mocked(backend.cancelSync).mockRejectedValue(new Error("offline"));
      setSyncProgress({ running: true, stage: "applying", step: 1, totalSteps: 2, message: "x", runId: "run-live" });
      const { container } = await renderPage();

      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Cancel Sync")!);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(container.textContent).toContain("Failed to cancel sync");
      expect(buttonByExactText(container, "Cancel Sync")?.disabled).toBe(false);
      expect(getSyncProgress().running).toBe(true);
    });

    it("re-arms Cancel when the run it was draining ends", async () => {
      setSyncProgress({ running: true, stage: "applying", step: 1, totalSteps: 2, message: "x", runId: "run-live" });
      const { container } = await renderPage();

      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Cancel Sync")!);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(buttonByExactText(container, "Cancelling…")).not.toBeNull();

      await act(async () => {
        setSyncProgress({ running: false, stage: "cancelled", message: "Sync cancelled", runId: "run-live" });
        await Promise.resolve();
      });
      await act(async () => {
        setSyncProgress({ running: true, stage: "fetching", step: 1, totalSteps: 2, message: "y", runId: "run-two" });
        await Promise.resolve();
      });

      // The next run finds a live button, not the previous run's drain state.
      expect(buttonByExactText(container, "Cancelling…")).toBeNull();
      expect(buttonByExactText(container, "Cancel Sync")?.disabled).toBe(false);
    });

    it("re-arms Cancel for the run after a cancelled PREVIEW, which ends with no terminal stage", async () => {
      // The whole sequence the button has to survive: cancel a preview being
      // worked out, then start another. The preview run stops through the page's
      // own optimistic retraction, so a button waiting for a terminal stage
      // would stay dead for the whole of the next run.
      let finishFirst: (p: SyncPreview) => void = () => {};
      vi.mocked(backend.syncPreview).mockReturnValueOnce(
        new Promise<SyncPreview>((res) => {
          finishFirst = res;
        }),
      );
      const { container } = await renderAndStartPreview();

      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Cancel Sync")!);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(buttonByExactText(container, "Cancelling…")?.disabled).toBe(true);

      vi.mocked(syncManager.isCancelRequested).mockReturnValue(true);
      await act(async () => {
        finishFirst(preview());
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(buttonByExactText(container, "Sync Library")).not.toBeNull();

      vi.mocked(syncManager.isCancelRequested).mockReturnValue(false);
      let finishSecond: (p: SyncPreview) => void = () => {};
      vi.mocked(backend.syncPreview).mockReturnValue(
        new Promise<SyncPreview>((res) => {
          finishSecond = res;
        }),
      );
      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Sync Library")!);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(buttonByExactText(container, "Cancelling…")).toBeNull();
      expect(buttonByExactText(container, "Cancel Sync")?.disabled).toBe(false);

      await act(async () => {
        finishSecond(preview());
        await Promise.resolve();
        await Promise.resolve();
      });
    });
  });

  // ===========================================================================
  // Options — the persisted setting and the destructive button.
  // ===========================================================================
  describe("options", () => {
    const toggle = (c: HTMLElement) => c.querySelector('[data-testid="toggle-input"]') as HTMLInputElement;

    it("reads the persisted Skip preview intent back on mount", async () => {
      vi.mocked(backend.getSettings).mockResolvedValue({ ...defaultSettings(), skip_preview: true });
      const { container } = await renderPage();
      expect(toggle(container).checked).toBe(true);
    });

    it("treats an older backend that omits the setting as off", async () => {
      const { container } = await renderPage();
      expect(toggle(container).checked).toBe(false);
    });

    it("persists a flip", async () => {
      const { container } = await renderPage();
      await act(async () => {
        fireEvent.click(toggle(container));
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(vi.mocked(backend.saveSkipPreview)).toHaveBeenCalledWith(true);
      expect(toggle(container).checked).toBe(true);
    });

    it("puts the toggle back and says so when the write is refused", async () => {
      vi.mocked(backend.saveSkipPreview).mockResolvedValue({ success: false });
      const { container } = await renderPage();
      await act(async () => {
        fireEvent.click(toggle(container));
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(toggle(container).checked).toBe(false);
      expect(container.textContent).toContain("Could not save that; the setting was put back.");
    });

    it("puts the toggle back and says so when the write rejects", async () => {
      vi.mocked(backend.saveSkipPreview).mockRejectedValue(new Error("boom"));
      const { container } = await renderPage();
      await act(async () => {
        fireEvent.click(toggle(container));
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(toggle(container).checked).toBe(false);
      expect(container.textContent).toContain("Could not save that; the setting was put back.");
    });

    it("logs a failed settings read and leaves the toggle off", async () => {
      const logSpy = vi.spyOn(backend, "logError");
      vi.mocked(backend.getSettings).mockRejectedValue(new Error("boom"));
      const { container } = await renderPage();
      expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to read the skip-preview setting"));
      expect(toggle(container).checked).toBe(false);
    });

    it("with Skip preview on, the start button starts the run instead of asking for a preview", async () => {
      vi.mocked(backend.getSettings).mockResolvedValue({ ...defaultSettings(), skip_preview: true });
      const { container } = await renderPage();

      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Sync Library")!);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(vi.mocked(backend.startSync)).toHaveBeenCalled();
      expect(vi.mocked(backend.syncPreview)).not.toHaveBeenCalled();
    });

    it("Force Full Sync is behind a confirmation that states what it forgets", async () => {
      const { container } = await renderPage();
      fireEvent.click(buttonByExactText(container, "Force Full Sync")!);

      const props = lastConfirmModalProps<{ strTitle?: string; strDescription?: string; strOKButtonText?: string }>();
      expect(props?.strTitle).toBe("Force a full re-sync?");
      expect(props?.strDescription).toContain("forgets what has already been synced");
      expect(props?.strOKButtonText).toBe("Force Full Sync");
      // Nothing happened on the press alone.
      expect(vi.mocked(backend.clearSyncCache)).not.toHaveBeenCalled();
    });

    it("confirming clears the cache and surfaces the answer", async () => {
      const { container } = await renderPage();
      fireEvent.click(buttonByExactText(container, "Force Full Sync")!);
      const props = lastConfirmModalProps<{ onOK?: () => void }>();

      await act(async () => {
        props?.onOK?.();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(vi.mocked(backend.clearSyncCache)).toHaveBeenCalled();
      expect(container.textContent).toContain("Cleared");
    });

    it("a failed clear is said", async () => {
      vi.mocked(backend.clearSyncCache).mockRejectedValue(new Error("boom"));
      const { container } = await renderPage();
      fireEvent.click(buttonByExactText(container, "Force Full Sync")!);
      const props = lastConfirmModalProps<{ onOK?: () => void }>();

      await act(async () => {
        props?.onOK?.();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(container.textContent).toContain("Failed to clear sync cache");
    });

    it("a refused clear is said, and changes nothing else", async () => {
      vi.mocked(backend.clearSyncCache).mockResolvedValue({ success: false, message: "Migration in progress" });
      adoptPreview(preview());
      const { container } = await renderPage();
      fireEvent.click(buttonByExactText(container, "Force Full Sync")!);

      await act(async () => {
        lastConfirmModalProps<{ onOK?: () => void }>()?.onOK?.();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(container.textContent).toContain("Migration in progress");
      // Nothing was cleared, so the preview still describes what a run would do.
      expect(vi.mocked(backend.syncCancelPreview)).not.toHaveBeenCalled();
      expect(buttonByExactText(container, "Apply Sync")).not.toBeNull();
      expect(buttonByExactText(container, "Force Full Sync")?.disabled).toBe(false);
    });

    it("ends the pending preview, which described the state the clear discarded (#1814)", async () => {
      adoptPreview(preview());
      const { container } = await renderPage();
      expect(buttonByExactText(container, "Apply Sync")).not.toBeNull();
      fireEvent.click(buttonByExactText(container, "Force Full Sync")!);

      await act(async () => {
        lastConfirmModalProps<{ onOK?: () => void }>()?.onOK?.();
        await Promise.resolve();
        await Promise.resolve();
      });

      // Ended on both sides, the way Cancel ends it: the table goes and the
      // backend is told to drop the snapshot.
      expect(vi.mocked(backend.syncCancelPreview)).toHaveBeenCalled();
      expect(buttonByExactText(container, "Apply Sync")).toBeNull();
      expect(container.textContent).toContain("Nothing is waiting to be applied.");
    });

    it("a failed discard after a clear is logged, and the preview is gone either way", async () => {
      const logged = vi.mocked(backend.logError);
      vi.mocked(backend.syncCancelPreview).mockRejectedValue(new Error("gone"));
      adoptPreview(preview());
      const { container } = await renderPage();
      fireEvent.click(buttonByExactText(container, "Force Full Sync")!);

      await act(async () => {
        lastConfirmModalProps<{ onOK?: () => void }>()?.onOK?.();
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(logged.mock.calls.some(([m]) => String(m).includes("after a full-sync clear"))).toBe(true);
      expect(buttonByExactText(container, "Apply Sync")).toBeNull();
    });

    it("goes dead once the clear has been made, and the line under it says why (#1814)", async () => {
      const { container } = await renderPage();
      fireEvent.click(buttonByExactText(container, "Force Full Sync")!);

      await act(async () => {
        lastConfirmModalProps<{ onOK?: () => void }>()?.onOK?.();
        await Promise.resolve();
        await Promise.resolve();
      });

      // Rendered, never hidden — and the line explains the state rather than
      // describing a press that would now do nothing.
      const button = buttonByExactText(container, "Force Full Sync");
      expect(button).not.toBeNull();
      expect(button?.disabled).toBe(true);
      expect(container.textContent).toContain("Cleared. Pressing again would clear nothing.");
    });

    it("comes back once a run has been and gone, because there is something to forget again", async () => {
      const { container } = await renderPage();
      fireEvent.click(buttonByExactText(container, "Force Full Sync")!);
      await act(async () => {
        lastConfirmModalProps<{ onOK?: () => void }>()?.onOK?.();
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(buttonByExactText(container, "Force Full Sync")?.disabled).toBe(true);

      await act(async () => {
        setSyncProgress({ running: true, stage: "applying", step: 1, totalSteps: 1, runId: "run-after" });
        await Promise.resolve();
      });
      await act(async () => {
        setSyncProgress({ running: false, stage: "done", message: "Sync complete", runId: "run-after" });
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(buttonByExactText(container, "Force Full Sync")?.disabled).toBe(false);
    });

    it("is rendered and disabled — never hidden — while a run is in flight", async () => {
      setSyncProgress({ running: true, stage: "applying", step: 1, totalSteps: 2, message: "x", runId: "r" });
      const { container } = await renderPage();
      expect(buttonByExactText(container, "Force Full Sync")?.disabled).toBe(true);
      expect(container.textContent).toContain("Not while a run is in flight.");
    });

    it("is disabled on a pristine install, where there is nothing to clear", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue({ ...defaultStats(), last_sync: null });
      const { container } = await renderPage();
      expect(buttonByExactText(container, "Force Full Sync")?.disabled).toBe(true);
    });

    it("is disabled until the stats answer, since not knowing is not evidence of something to clear", async () => {
      let finishStats: (s: SyncStats) => void = () => {};
      vi.mocked(backend.getSyncStats).mockReturnValue(
        new Promise<SyncStats>((res) => {
          finishStats = res;
        }),
      );
      const { container } = await renderPage();
      expect(buttonByExactText(container, "Force Full Sync")?.disabled).toBe(true);

      await act(async () => {
        finishStats(defaultStats());
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(buttonByExactText(container, "Force Full Sync")?.disabled).toBe(false);
    });

    it("stays pressable when the stats read FAILED, and the line says the reading is missing (#1814)", async () => {
      // A failed read is not evidence that there is nothing to clear. The page
      // issues one stats read on mount and one more only when a run ends, so a
      // read that never answered would otherwise leave the button dead for as
      // long as the page is open — which is what a cancelled preview left
      // behind on the device.
      vi.mocked(backend.getSyncStats).mockRejectedValue(new Error("database is locked"));
      const { container } = await renderPage();

      expect(buttonByExactText(container, "Force Full Sync")?.disabled).toBe(false);
      expect(container.textContent).toContain("Could not read what has already been synced");
    });

    it("says it is still reading while the answer is on its way", async () => {
      let finishStats: (s: SyncStats) => void = () => {};
      vi.mocked(backend.getSyncStats).mockReturnValue(
        new Promise<SyncStats>((res) => {
          finishStats = res;
        }),
      );
      const { container } = await renderPage();
      expect(container.textContent).toContain("Reading what has already been synced");

      await act(async () => {
        finishStats({ ...defaultStats(), last_sync: null });
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(container.textContent).toContain("Nothing has been synced yet, so there is nothing to forget.");
    });

    it("leaves Skip preview live during a run — the setting is read by the next press", async () => {
      setSyncProgress({ running: true, stage: "applying", step: 1, totalSteps: 2, message: "x", runId: "r" });
      const { container } = await renderPage();
      expect(toggle(container).disabled).toBe(false);
    });
  });

  // ===========================================================================
  // Steam memory, and the run history.
  // ===========================================================================
  describe("Steam memory", () => {
    it("shows the reading now and what the last run did to it", async () => {
      const { container } = await renderPage();
      expect(container.querySelector('[data-testid="memory-now"]')?.textContent).toContain("1.2 GB");
      expect(container.querySelector('[data-testid="memory-last-run"]')?.textContent).toContain("+0.3");
    });

    it("says the reading is unavailable rather than showing a zero", async () => {
      vi.mocked(backend.getSessionBudgetStatus).mockResolvedValue({
        ...defaultBudget(),
        rss_kb: null,
        memory_delta_kb: null,
      });
      const { container } = await renderPage();
      expect(container.querySelector('[data-testid="memory-now"]')?.textContent).toContain("unavailable");
      expect(container.querySelector('[data-testid="memory-last-run"]')?.textContent).toContain("not recorded");
    });
  });

  describe("last runs", () => {
    it("lists the recorded runs, newest first, each a focus stop", async () => {
      vi.mocked(backend.getSyncRuns).mockResolvedValue({
        success: true,
        runs: [runRecord({ id: "r1" }), runRecord({ id: "r2", status: "paused" })],
      });
      const { container } = await renderPage();

      const rows = Array.from(container.querySelectorAll('[data-testid^="run-r"]'));
      expect(rows).toHaveLength(2);
      expect(rows[0]?.textContent).toContain("completed");
      expect(rows[0]?.textContent).toContain("14 platforms · 3 collections");
      expect(rows[1]?.textContent).toContain("paused");
      for (const row of rows) expect(row.getAttribute("data-activate")).toBe("true");
    });

    it("a run that recorded no completed list says what it planned, and the status says why", async () => {
      vi.mocked(backend.getSyncRuns).mockResolvedValue({
        success: true,
        runs: [
          runRecord({
            id: "r9",
            status: "interrupted",
            finished_at: null,
            platforms_completed: null,
            collections_completed: null,
          }),
        ],
      });
      const { container } = await renderPage();

      const row = container.querySelector('[data-testid="run-r9"]');
      // Never "0 platforms": an empty list would read as a run that synced nothing.
      expect(row?.textContent).toContain("14 platforms planned");
      expect(row?.textContent).toContain("interrupted");
      expect(row?.textContent).not.toContain("0 platforms");
    });

    it("a partial run states how far it got", async () => {
      vi.mocked(backend.getSyncRuns).mockResolvedValue({
        success: true,
        runs: [
          runRecord({ id: "r5", status: "cancelled", platforms_completed: ["a", "b"], collections_completed: [] }),
        ],
      });
      const { container } = await renderPage();
      expect(container.querySelector('[data-testid="run-r5"]')?.textContent).toContain("2 of 14 platforms");
    });

    it("carries a stopped run's error text on the row", async () => {
      vi.mocked(backend.getSyncRuns).mockResolvedValue({
        success: true,
        runs: [runRecord({ id: "r7", status: "errored", error: "RomM refused the token" })],
      });
      const { container } = await renderPage();
      expect(container.querySelector('[data-testid="run-r7"]')?.textContent).toContain("RomM refused the token");
    });

    it("says nothing has run yet rather than showing an empty list", async () => {
      const { container } = await renderPage();
      expect(container.textContent).toContain("No sync has run yet.");
    });

    it("a refused read says so", async () => {
      vi.mocked(backend.getSyncRuns).mockResolvedValue({ success: false, runs: [] });
      const { container } = await renderPage();
      expect(container.textContent).toContain("Could not read the run history.");
      expect(container.textContent).not.toContain("No sync has run yet.");
    });

    it("a rejected read says so and keeps nothing it never had", async () => {
      vi.mocked(backend.getSyncRuns).mockRejectedValue(new Error("boom"));
      const { container } = await renderPage();
      expect(container.textContent).toContain("Could not read the run history.");
    });

    it("re-reads the history when the run in flight ends", async () => {
      setSyncProgress({ running: true, stage: "applying", step: 1, totalSteps: 2, message: "x", runId: "run-live" });
      await renderPage();
      vi.mocked(backend.getSyncRuns).mockClear();

      await act(async () => {
        setSyncProgress({ running: false, stage: "done", message: "Sync complete", runId: "run-live" });
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(vi.mocked(backend.getSyncRuns)).toHaveBeenCalled();
    });

    it("re-reads nothing when a preview retracts its own frame, which ended no run", async () => {
      // The retraction stops the same store field a run's end does, and carries
      // no terminal stage — there is no run for the history, the stats or the
      // budget reading to describe.
      let finish: (p: SyncPreview) => void = () => {};
      vi.mocked(backend.syncPreview).mockReturnValue(
        new Promise<SyncPreview>((res) => {
          finish = res;
        }),
      );
      await renderAndStartPreview();
      vi.mocked(backend.getSyncRuns).mockClear();
      vi.mocked(backend.getSyncStats).mockClear();
      vi.mocked(backend.getSessionBudgetStatus).mockClear();

      await act(async () => {
        finish(preview());
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(getSyncProgress().running).toBe(false);
      expect(vi.mocked(backend.getSyncRuns)).not.toHaveBeenCalled();
      expect(vi.mocked(backend.getSyncStats)).not.toHaveBeenCalled();
      expect(vi.mocked(backend.getSessionBudgetStatus)).not.toHaveBeenCalled();
    });

    it("a run whose plan held no platform says what it covered without naming zero platforms", async () => {
      vi.mocked(backend.getSyncRuns).mockResolvedValue({
        success: true,
        runs: [
          runRecord({
            id: "r7",
            platforms_planned: 0,
            platforms_completed: [],
            collections_completed: ["Favorites", "RPGs"],
          }),
          // The same run on the other branch: it recorded no completed list at
          // all, so there is no plan to state either and nothing about its
          // coverage can be said.
          runRecord({
            id: "r8",
            status: "interrupted",
            finished_at: null,
            platforms_planned: 0,
            platforms_completed: null,
            collections_completed: null,
          }),
        ],
      });
      const { container } = await renderPage();

      const row = container.querySelector('[data-testid="run-r7"]');
      expect(row?.textContent).toContain("2 collections");
      expect(row?.textContent).not.toContain("0 platforms");

      const unrecorded = container.querySelector('[data-testid="run-r8"]');
      expect(unrecorded?.textContent).toContain("nothing recorded");
      expect(unrecorded?.textContent).not.toContain("0 platforms");
    });
  });

  // ===========================================================================
  // The session-budget card, at its home.
  // ===========================================================================
  describe("the session-budget card", () => {
    function pausedStats(): SyncStats {
      return {
        ...defaultStats(),
        last_sync: null,
        resumable_games: 30,
        last_attempt: { finished_at: "2026-07-11T17:48:00", status: "paused" },
      };
    }

    it("takes the top of the column after a paused run, with the restart button", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(pausedStats());
      vi.mocked(backend.getSessionBudgetStatus).mockResolvedValue({ ...defaultBudget(), rss_kb: 2_299_000 });
      const { container } = await renderPage();

      const card = container.querySelector('[data-testid="budget-paused-banner"]');
      expect(card).not.toBeNull();
      expect(card?.textContent).toContain("Steam memory is full (2.3 GB)");
      expect(buttonByExactText(container, "Restart Steam now")).not.toBeNull();
      // The preview or the idle state moves UNDER it rather than being replaced.
      expect(buttonByExactText(container, "Resume Sync")).not.toBeNull();
    });

    it("names the button that is actually on the page — Apply Sync while a preview stands", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(pausedStats());
      vi.mocked(backend.getSessionBudgetStatus).mockResolvedValue({ ...defaultBudget(), rss_kb: 2_299_000 });
      adoptPreview(preview());
      const { container } = await renderPage();

      const card = container.querySelector('[data-testid="budget-paused-banner"]');
      expect(card?.textContent).toContain("Apply Sync");
      expect(card?.textContent).not.toContain("Resume Sync");
    });

    it("is not shown over a run in flight — the paused attempt survives into the resume", async () => {
      vi.mocked(backend.getSyncStats).mockResolvedValue(pausedStats());
      vi.mocked(backend.getSessionBudgetStatus).mockResolvedValue({ ...defaultBudget(), rss_kb: 2_299_000 });
      setSyncProgress({ running: true, stage: "applying", step: 1, totalSteps: 2, message: "x", runId: "r" });
      const { container } = await renderPage();

      expect(container.querySelector('[data-testid="budget-paused-banner"]')).toBeNull();
    });

    it("shows the high-heap card after a completed run with a high live reading", async () => {
      vi.mocked(backend.getSessionBudgetStatus).mockResolvedValue({ ...defaultBudget(), rss_kb: 1_900_000 });
      const { container } = await renderPage();
      expect(container.querySelector('[data-testid="budget-high-heap-banner"]')).not.toBeNull();
    });

    it("shows no card at all when nothing paused and the reading is low", async () => {
      const { container } = await renderPage();
      expect(container.querySelector('[data-testid="budget-paused-banner"]')).toBeNull();
      expect(container.querySelector('[data-testid="budget-high-heap-banner"]')).toBeNull();
    });

    it("polls the reading while the last run is paused, and flips once a restart frees memory", async () => {
      vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
      try {
        vi.mocked(backend.getSyncStats).mockResolvedValue(pausedStats());
        // Before the restart the heap is at the ceiling and a resume would
        // re-pause; the poll's read finds the fresh baseline after it.
        vi.mocked(backend.getSessionBudgetStatus)
          .mockResolvedValueOnce({ ...defaultBudget(), rss_kb: 2_299_000, resume_ready: false })
          .mockResolvedValue({ ...defaultBudget(), rss_kb: 500_000, resume_ready: true });

        const { container } = render(<SyncPage onBack={vi.fn()} />);
        await act(async () => {
          await Promise.resolve();
          await Promise.resolve();
          await Promise.resolve();
        });
        const card = () => container.querySelector('[data-testid="budget-paused-banner"]')?.textContent ?? "";
        expect(card()).toContain("Steam memory is full");
        expect(buttonByExactText(container, "Restart Steam now")).not.toBeNull();

        await act(async () => {
          await vi.advanceTimersByTimeAsync(10_000);
        });

        expect(card()).toContain("Steam memory is free again (0.5 GB)");
        expect(buttonByExactText(container, "Restart Steam now")).toBeNull();
      } finally {
        vi.useRealTimers();
      }
    });
  });

  // ===========================================================================
  // Working out a preview — this page's own call, and nobody else's.
  // ===========================================================================
  describe("working out a preview", () => {
    it("works one out when its own button is pressed", async () => {
      const { container } = await renderAndStartPreview();

      expect(vi.mocked(backend.syncPreview)).toHaveBeenCalled();
      expect(buttonByExactText(container, "Apply Sync")).not.toBeNull();
    });

    it("computes none when the page is merely opened — no page asks it to", async () => {
      await renderPage();
      expect(vi.mocked(backend.syncPreview)).not.toHaveBeenCalled();
    });

    it("names the kind of run it started on the frame it writes", async () => {
      // The backend states the kind on every frame it emits, but the first
      // frame of a run is this page's own optimistic one — so it says which
      // kind it just started, and Main's slot is right from its first paint
      // instead of reading "not established" for a round trip.
      let finish: (p: SyncPreview) => void = () => {};
      vi.mocked(backend.syncPreview).mockReturnValue(
        new Promise<SyncPreview>((res) => {
          finish = res;
        }),
      );
      await renderAndStartPreview();

      expect(getSyncProgress().runKind).toBe("preview");

      await act(async () => {
        finish(preview());
        await Promise.resolve();
        await Promise.resolve();
      });
    });

    it("names an apply run's kind on its own frame too", async () => {
      adoptPreview(preview());
      const { container } = await renderPage();

      await act(async () => {
        fireEvent.click(buttonByExactText(container, "Apply Sync")!);
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(getSyncProgress().runKind).toBe("apply");
    });

    it("shows the run while it works it out", async () => {
      let finish: (p: SyncPreview) => void = () => {};
      vi.mocked(backend.syncPreview).mockReturnValue(
        new Promise<SyncPreview>((res) => {
          finish = res;
        }),
      );
      const { container } = await renderAndStartPreview();

      expect(container.querySelector('[data-testid="progress"]')).not.toBeNull();
      expect(getSyncProgress().running).toBe(true);

      await act(async () => {
        finish(preview());
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(buttonByExactText(container, "Apply Sync")).not.toBeNull();
      expect(getSyncProgress().running).toBe(false);
    });

    it("says a refusal where the reader is looking, and returns to idle", async () => {
      // A `@migration_blocked` answer arrives exactly like this: success false,
      // a message, and none of the fields the type declares.
      vi.mocked(backend.syncPreview).mockResolvedValue({
        success: false,
        message: "A RetroDECK migration is pending",
        blocked_by_migration: true,
      } as unknown as SyncPreview);
      const { container } = await renderAndStartPreview();

      expect(container.textContent).toContain("A RetroDECK migration is pending");
      expect(getSyncProgress().running).toBe(false);
      expect(buttonByExactText(container, "Sync Library")).not.toBeNull();
    });

    it("falls back to its own words when a refusal carries none", async () => {
      vi.mocked(backend.syncPreview).mockResolvedValue({ success: false, message: "" } as unknown as SyncPreview);
      const { container } = await renderAndStartPreview();
      expect(container.textContent).toContain("Could not work out what would change.");
    });

    it("says a rejection too", async () => {
      vi.mocked(backend.syncPreview).mockRejectedValue(new Error("boom"));
      const { container } = await renderAndStartPreview();
      expect(container.textContent).toContain("Could not work out what would change.");
      expect(getSyncProgress().running).toBe(false);
    });

    it("reconciles stale shortcuts before asking (#1046)", async () => {
      const order: string[] = [];
      vi.mocked(syncManager.reconcileStaleShortcuts).mockImplementation(async () => {
        order.push("reconcile");
      });
      vi.mocked(backend.syncPreview).mockImplementation(async () => {
        order.push("preview");
        return preview();
      });
      await renderAndStartPreview();
      expect(order).toEqual(["reconcile", "preview"]);
    });

    it("a cancel that landed while the call was open discards the staged snapshot (#1202)", async () => {
      // The backend staged the snapshot just before the cancel reached it, so the
      // run answers success and only the local flag knows the user pressed
      // Cancel. Without telling the backend, the staged snapshot survives.
      vi.mocked(syncManager.isCancelRequested).mockReturnValue(true);
      const { container } = await renderAndStartPreview();

      expect(vi.mocked(backend.syncCancelPreview)).toHaveBeenCalled();
      expect(buttonByExactText(container, "Apply Sync")).toBeNull();
      expect(container.textContent).toContain("Sync cancelled");
    });
  });
});
