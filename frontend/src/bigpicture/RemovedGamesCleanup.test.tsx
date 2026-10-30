import { act, fireEvent, render, waitFor, within, type RenderResult } from "@testing-library/react";
import { createElement, type ReactElement } from "react";
import { toaster } from "../api/host";
import { showModal } from "@decky/ui";
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import * as backend from "../api/backend";
import { HostTransportError } from "../api/hostSocket";
import {
  beginPrunePreview,
  beginPruneRun,
  getPruneState,
  resetPruneState,
  setPruneComplete,
  setPruneProgress,
  type PruneComplete,
} from "../utils/pruneStore";
import {
  openRemovedGamesCleanupModal,
  RemovedGamesCleanupSection,
  stageInstalledSelections,
  STAGE_LABELS,
} from "./RemovedGamesCleanup";
import { FOCUSED_ROW_FILL, GREEN, NOTE_FONT, RED, SECONDARY_FONT } from "./layout/pane";
import { lineWithText, throbberIn } from "../test-utils/throbber";

const preview: backend.PrunePreviewResult = {
  success: true,
  preview_id: "preview-1",
  scope: "bulk",
  items: [
    {
      rom_id: 7,
      name: "Removed Game",
      fs_name: "Removed Game.gba",
      fs_name_no_ext: "Removed Game",
      platform_slug: "gba",
      group_id: "group-1",
      group_size: 1,
      bound_count: 0,
      bound: false,
      is_main_sibling: false,
      candidate: true,
      gone_from_romm: true,
      installed: true,
      installed_bytes: 200,
      warning: null,
    },
  ],
  offset: 0,
  limit: 50,
  total: 1,
  free_bytes: 100,
  recovery_root: "/home/deck/romm-tender-recovery",
};

function shownModal(): ReactElement {
  const calls = vi.mocked(showModal).mock.calls;
  const element = calls[calls.length - 1]?.[0] as ReactElement | undefined;
  if (!element) throw new Error("Expected cleanup modal");
  return element;
}

/** What `hostSocket.ts` throws when the host refuses an answer over its cap (`host/dispatch.py`). */
function answerTooLarge(): HostTransportError {
  return new HostTransportError("payload_too_large", "answer is 13000000 bytes, over the 12582912-byte limit");
}

/** A preview answer the test settles by hand, so the dialog can be caught between two pages. */
function pendingPage(): {
  promise: Promise<backend.PrunePreviewResult>;
  resolve: (value: backend.PrunePreviewResult) => void;
  reject: (reason: Error) => void;
} {
  let resolve!: (value: backend.PrunePreviewResult) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<backend.PrunePreviewResult>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/** The preview's one row again as `romId`, a game of its own, for the pages after the first. */
function rowAs(romId: number, name: string): backend.PrunePreviewItem {
  return {
    ...preview.items![0]!,
    rom_id: romId,
    name,
    fs_name: `${name}.gba`,
    fs_name_no_ext: name,
    group_id: `group-${romId}`,
  };
}

/** `a` comes before `b` in document order — which is the order focus walks. */
function precedes(a: Element, b: Element): boolean {
  return (a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING) !== 0;
}

/** The Confirm button, by the count it names. */
function confirmButton(modal: RenderResult): HTMLButtonElement {
  return modal.getByRole("button", { name: /^Remove \d+ versions?$/ }) as HTMLButtonElement;
}

/** A colour as the DOM writes it back. */
function asCssColour(colour: string): string {
  const probe = document.createElement("span");
  probe.style.color = colour;
  return probe.style.color;
}

/**
 * The list's row whose first line reads `text` — a game's name or a version's file name.
 *
 * A row that carries a Back up file switch is a plain element marked with the
 * list's own test id; a row with nothing to press is a focus stop, which the
 * `Focusable` stub renders under its own id. Either way it is the nearest of
 * the two above the text. Throws rather than returning nothing: a missing row
 * is the failure.
 */
function rowFor(modal: RenderResult, text: string): HTMLElement {
  const row = modal.getByText(text).closest<HTMLElement>('[data-testid^="cleanup-row-"], [data-testid="focusable"]');
  if (!row) throw new Error(`No table row for ${text}`);
  return row;
}

/**
 * The Back up file checkbox belonging to the row of the version whose file is `gameName`.
 *
 * The dialog's option toggles and its per-row content toggles share one flat
 * `toggle-input` list, so indexing it ties every row assertion to the number of
 * options rendered above the table. Throws rather than returning nothing: a row
 * that has lost its content toggle is the failure, not a silently skipped click.
 */
function contentToggleFor(modal: RenderResult, gameName: string): HTMLInputElement {
  const input = rowFor(modal, gameName).querySelector<HTMLInputElement>(
    '[data-testid="toggle"] [data-testid="toggle-input"]',
  );
  if (!input) throw new Error(`No Back up file switch in the row for ${gameName}`);
  return input;
}

/**
 * The dialog's run options, by the label text each one renders.
 *
 * All but `confirmWithoutRecovery` are always on screen; that one is rendered
 * only while the recovery bundle is switched off.
 */
const OPTION_LABELS = {
  removeRows: "Remove gone versions",
  removeDeadGames: "Remove games that are gone entirely",
  recovery: "Back up before removing",
  confirmWithoutRecovery: "I understand there is no recovery bundle",
  backUpFiles: "Back up downloaded game files too",
} as const;

/**
 * The option checkbox carrying `label`.
 *
 * Sibling of `contentToggleFor` for the toggles above the item list. Throws on
 * anything but a single match: a missing option is the failure, and a label
 * that has become ambiguous would otherwise flip whichever toggle came first.
 */
function optionToggleFor(modal: RenderResult, label: string): HTMLInputElement {
  const matches = [...modal.container.querySelectorAll<HTMLElement>('[data-testid="toggle"]')].filter((toggle) =>
    toggle.textContent.includes(label),
  );
  if (matches.length !== 1) throw new Error(`Expected one option toggle labelled "${label}", found ${matches.length}`);
  const input = matches[0]!.querySelector<HTMLInputElement>('[data-testid="toggle-input"]');
  if (!input) throw new Error(`No checkbox in the option toggle labelled "${label}"`);
  return input;
}

describe("RemovedGamesCleanup", () => {
  // logInfo/logWarn/logError are plain wrappers over the frontend_log endpoint,
  // so they are spied rather than module-mocked. Fresh per test: the confirm
  // path asserts on them in both directions, and a call leaking in from a
  // sibling test would satisfy either.
  let logs: { info: MockInstance; warn: MockInstance; error: MockInstance };

  beforeEach(() => {
    vi.mocked(backend.getPrunePreview).mockReset();
    vi.mocked(backend.stagePruneInstalledSelection).mockReset();
    vi.mocked(backend.startPrune).mockReset();
    vi.mocked(backend.cancelPrune).mockReset();
    vi.mocked(showModal).mockReset();
    vi.mocked(toaster.toast).mockReset();
    logs = {
      info: vi.spyOn(backend, "logInfo").mockImplementation(() => {}),
      warn: vi.spyOn(backend, "logWarn").mockImplementation(() => {}),
      error: vi.spyOn(backend, "logError").mockImplementation(() => {}),
    };
    resetPruneState();
    vi.mocked(backend.getPrunePreview).mockResolvedValue(preview);
    vi.mocked(backend.stagePruneInstalledSelection).mockResolvedValue({
      success: true,
      selection_id: "selection-1",
      selected_count: 1,
      finalized: true,
    });
    vi.mocked(backend.startPrune).mockResolvedValue({ success: true, run_id: "run-1", status: "running" });
  });

  afterEach(() => vi.restoreAllMocks());

  it("performs the first local scan before showing the confirmation modal", async () => {
    await expect(openRemovedGamesCleanupModal()).resolves.toBe(true);

    expect(vi.mocked(backend.getPrunePreview)).toHaveBeenCalledWith({
      scope: "bulk",
      rom_id: null,
      preview_id: null,
      offset: 0,
      limit: 50,
    });
    expect(showModal).toHaveBeenCalledTimes(1);
  });

  it("returns false and shows no modal when the scan is empty", async () => {
    vi.mocked(backend.getPrunePreview).mockResolvedValue({ ...preview, items: [], total: 0 });

    await expect(openRemovedGamesCleanupModal(7)).resolves.toBe(false);

    expect(vi.mocked(backend.getPrunePreview)).toHaveBeenCalledWith({
      scope: "rom",
      rom_id: 7,
      preview_id: null,
      offset: 0,
      limit: 50,
    });
    expect(showModal).not.toHaveBeenCalled();
  });

  it("uses the shipped option defaults and blocks confirmation when selected content exceeds free space", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    // Whole-game removal is on: it is what the dialog exists for, and the
    // default-on recovery bundle is what makes it recoverable. Per-ROM content
    // stays off — it is the only one that can exhaust the disk.
    expect(optionToggleFor(modal, OPTION_LABELS.removeRows).checked).toBe(true);
    expect(optionToggleFor(modal, OPTION_LABELS.removeDeadGames).checked).toBe(true);
    expect(optionToggleFor(modal, OPTION_LABELS.recovery).checked).toBe(true);
    expect(contentToggleFor(modal, "Removed Game.gba").checked).toBe(false);
    // The acknowledgement exists only to gate a run that keeps no bundle, so it
    // must not be on screen while the bundle is on.
    expect(modal.container.textContent).not.toContain(OPTION_LABELS.confirmWithoutRecovery);
    const confirm = confirmButton(modal);
    expect(confirm.disabled).toBe(false);
    expect(rowFor(modal, "Removed Game.gba").textContent).toContain("Removed, file deleted");

    fireEvent.click(contentToggleFor(modal, "Removed Game.gba"));

    expect(modal.container.textContent).toContain("Not enough free space.");
    fireEvent.click(confirm);
    await act(async () => Promise.resolve());
    expect(backend.startPrune).not.toHaveBeenCalled();
    expect(modal.container.textContent).toContain("doesn't fit in the free space");
  });

  it("offers no option that moves a vanished version's shortcut onto another version", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    expect(modal.container.textContent).not.toMatch(/repoint/i);
  });

  it("submits all temporary options and re-enables confirmation after a failed start", async () => {
    vi.mocked(backend.startPrune).mockRejectedValueOnce(new Error("bridge offline"));
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    const confirm = confirmButton(modal);

    fireEvent.click(confirm);
    await waitFor(() => expect(modal.container.textContent).toContain("bridge offline"));

    expect(confirm.disabled).toBe(false);
    expect(vi.mocked(backend.startPrune)).toHaveBeenCalledWith({
      preview_id: "preview-1",
      confirmed: true,
      remove_rows: true,
      remove_fully_vanished: true,
      create_recovery_bundle: true,
      installed_selection_id: null,
    });
  });

  it("stays open and renders live progress through completion", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    const confirm = confirmButton(modal);

    fireEvent.click(confirm);
    await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));
    expect(confirm.disabled).toBe(true);

    act(() => {
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current: 1,
        total: 2,
        stage: "creating_recovery",
        rom_ids: [7],
        name: "Removed Game",
      });
    });
    expect(modal.container.textContent).toContain("Backing up — Removed Game");

    act(() => {
      setPruneComplete({
        success: true,
        partial: false,
        run_id: "run-1",
        preview_id: "preview-1",
        removed_rom_ids: [7],
        affected_app_ids: [],
        results: [{ group_id: "group-1", rom_ids: [7], status: "removed", message: "Removed." }],
      });
    });
    expect(modal.container.textContent).toContain("1 removed; 0 skipped, partial, or failed.");
    expect(modal.getByRole("button", { name: "Close" })).toBeTruthy();
  });

  it("adopts a matching run frame when the successful start response is lost", async () => {
    vi.useFakeTimers();
    vi.mocked(backend.startPrune).mockImplementation(() => new Promise(() => {}));
    try {
      await openRemovedGamesCleanupModal();
      const modal = render(shownModal());
      fireEvent.click(confirmButton(modal));
      await act(async () => Promise.resolve());

      act(() => {
        setPruneProgress({
          run_id: "adopted-run",
          preview_id: "preview-1",
          current: 1,
          total: 2,
          stage: "checking",
          rom_ids: [7],
          name: "Removed Game",
        });
      });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(15_000);
      });

      expect(modal.container.textContent).toContain("Cleanup running...");
      expect(modal.container.textContent).toContain("Checking with RomM — Removed Game");
      expect(modal.container.textContent).not.toContain("Cleanup could not start");
    } finally {
      vi.useRealTimers();
    }
  });

  it("shows an enabled Close control immediately when completion wins the start-response race", async () => {
    vi.mocked(backend.startPrune).mockImplementation(() => new Promise(() => {}));
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(confirmButton(modal));
    await act(async () => Promise.resolve());

    act(() => {
      setPruneComplete({
        success: true,
        partial: false,
        run_id: "event-first-run",
        preview_id: "preview-1",
        removed_rom_ids: [7],
        affected_app_ids: [],
        results: [],
      });
    });

    const close = modal.getByRole("button", { name: "Close" }) as HTMLButtonElement;
    expect(close.disabled).toBe(false);
    expect(modal.queryByRole("button", { name: "Cancel" })).toBeNull();
    expect(modal.container.textContent).toContain("1 removed");
  });

  it("surfaces scan rejection and re-enables the scan on Data Management", async () => {
    vi.mocked(backend.getPrunePreview).mockRejectedValue(new Error("offline"));
    const section = render(createElement(RemovedGamesCleanupSection));
    const button = section.getByRole("button", { name: "Clean Up Removed RomM Games" }) as HTMLButtonElement;

    fireEvent.click(button);
    await waitFor(() =>
      expect(toaster.toast).toHaveBeenCalledWith({
        title: "Tender",
        body: "Could not scan removed RomM games.",
        subtext: "Error: offline",
      }),
    );

    expect(button.disabled).toBe(false);
  });

  it("names the reason when the scan's first page is refused by the host", async () => {
    vi.mocked(backend.getPrunePreview).mockRejectedValue(answerTooLarge());
    const section = render(createElement(RemovedGamesCleanupSection));

    fireEvent.click(section.getByRole("button", { name: "Clean Up Removed RomM Games" }));

    await waitFor(() =>
      expect(toaster.toast).toHaveBeenCalledWith({
        title: "Tender",
        body: "Could not scan removed RomM games.",
        subtext: "HostTransportError: answer is 13000000 bytes, over the 12582912-byte limit",
      }),
    );
  });

  it("loads every page by itself and unlocks Confirm only once the last has arrived", async () => {
    const second = pendingPage();
    const third = pendingPage();
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce({ ...preview, total: 3 })
      .mockReturnValueOnce(second.promise)
      .mockReturnValueOnce(third.promise);
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    const confirm = confirmButton(modal);

    expect(modal.queryByRole("button", { name: /Load more/ })).toBeNull();
    await waitFor(() =>
      expect(backend.getPrunePreview).toHaveBeenLastCalledWith({
        scope: "bulk",
        rom_id: null,
        preview_id: "preview-1",
        offset: 1,
        limit: 50,
      }),
    );
    // Pressing early must refuse and SAY so — the run may not start against a
    // half-disclosed list, and a press that does nothing is the reported defect.
    fireEvent.click(confirm);
    await act(async () => Promise.resolve());
    expect(modal.container.textContent).toContain("Cleanup did not start: The list is still loading (1 of 3 entries).");

    await act(async () => second.resolve({ ...preview, offset: 1, total: 3, items: [rowAs(8, "Second Page Game")] }));
    expect(modal.container.textContent).toContain("Second Page Game");
    expect(vi.mocked(backend.getPrunePreview).mock.calls[2]?.[0]).toMatchObject({ offset: 2 });
    fireEvent.click(confirm);
    await act(async () => Promise.resolve());
    expect(modal.container.textContent).toContain("Cleanup did not start: The list is still loading (2 of 3 entries).");
    expect(backend.startPrune).not.toHaveBeenCalled();

    await act(async () => third.resolve({ ...preview, offset: 2, total: 3, items: [rowAs(9, "Third Page Game")] }));
    expect(modal.container.textContent).toContain("Third Page Game");
    fireEvent.click(confirm);
    await waitFor(() => expect(backend.startPrune).toHaveBeenCalled());
    expect(backend.getPrunePreview).toHaveBeenCalledTimes(3);
  });

  it("stops at a page that fails, keeps Confirm refusing, and resumes on Retry", async () => {
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce({ ...preview, total: 2 })
      .mockRejectedValueOnce(answerTooLarge())
      .mockResolvedValueOnce({ ...preview, offset: 1, total: 2, items: [rowAs(8, "Second Page Game")] });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    await waitFor(() =>
      expect(modal.container.textContent).toContain(
        "Could not load the rest of the list: HostTransportError: answer is 13000000 bytes, over the 12582912-byte limit",
      ),
    );
    expect(modal.container.textContent).toContain(
      "The list stopped loading at 1 of 2 entries. Retry loading before confirming.",
    );
    expect(logs.warn).toHaveBeenCalledWith(expect.stringContaining("[prune] Preview page at offset 1 of 2 failed"));
    // A failed page is not retried behind the user's back.
    await act(async () => Promise.resolve());
    expect(backend.getPrunePreview).toHaveBeenCalledTimes(2);
    fireEvent.click(confirmButton(modal));
    await act(async () => Promise.resolve());
    expect(backend.startPrune).not.toHaveBeenCalled();
    expect(modal.container.textContent).toContain("Cleanup did not start: The list stopped loading at 1 of 2 entries.");

    fireEvent.click(modal.getByRole("button", { name: "Retry loading" }));
    await waitFor(() => expect(modal.container.textContent).toContain("Second Page Game"));
    expect(modal.container.textContent).not.toContain("Cleanup did not start");
    expect(vi.mocked(backend.getPrunePreview).mock.calls[2]?.[0]).toMatchObject({ preview_id: "preview-1", offset: 1 });
    expect(modal.queryByRole("button", { name: "Retry loading" })).toBeNull();
    expect(modal.container.textContent).not.toContain("Could not load the rest of the list");
    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(backend.startPrune).toHaveBeenCalled());
  });

  it("clears a Confirm refused for a list still loading once the last page has arrived", async () => {
    const second = pendingPage();
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce({ ...preview, total: 2 })
      .mockReturnValueOnce(second.promise);
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    await waitFor(() => expect(backend.getPrunePreview).toHaveBeenCalledTimes(2));
    fireEvent.click(confirmButton(modal));
    await act(async () => Promise.resolve());
    expect(modal.container.textContent).toContain("Cleanup did not start: The list is still loading");

    await act(async () => second.resolve({ ...preview, offset: 1, total: 2, items: [rowAs(8, "Second Page Game")] }));

    expect(modal.container.textContent).toContain("Second Page Game");
    expect(modal.container.textContent).not.toContain("The list is still loading");
  });

  it("clears a Confirm refused for a list still loading once a page fails", async () => {
    const second = pendingPage();
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce({ ...preview, total: 2 })
      .mockReturnValueOnce(second.promise);
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    await waitFor(() => expect(backend.getPrunePreview).toHaveBeenCalledTimes(2));
    fireEvent.click(confirmButton(modal));
    await act(async () => Promise.resolve());
    expect(modal.container.textContent).toContain("Cleanup did not start: The list is still loading (1 of 2 entries).");

    await act(async () => second.reject(new Error("offline")));

    expect(modal.container.textContent).not.toContain("The list is still loading");
    expect(modal.container.textContent).toContain("The list stopped loading at 1 of 2 entries.");
  });

  it("clears a Confirm refused for a stopped list as soon as Retry is pressed", async () => {
    const retried = pendingPage();
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce({ ...preview, total: 2 })
      .mockRejectedValueOnce(new Error("offline"))
      .mockReturnValueOnce(retried.promise);
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    await waitFor(() => expect(modal.getByRole("button", { name: "Retry loading" })).toBeTruthy());
    fireEvent.click(confirmButton(modal));
    await act(async () => Promise.resolve());
    expect(modal.container.textContent).toContain("Cleanup did not start: The list stopped loading at 1 of 2 entries.");

    fireEvent.click(modal.getByRole("button", { name: "Retry loading" }));
    await waitFor(() => expect(backend.getPrunePreview).toHaveBeenCalledTimes(3));

    expect(modal.container.textContent).not.toContain("Cleanup did not start");
    expect(modal.container.textContent).toContain("The list is still loading (1 of 2 entries).");
  });

  it("keeps a status that is not about the list once the last page has arrived", async () => {
    const second = pendingPage();
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce({ ...preview, total: 2 })
      .mockReturnValueOnce(second.promise)
      .mockRejectedValueOnce(new Error("offline"));
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    await waitFor(() => expect(backend.getPrunePreview).toHaveBeenCalledTimes(2));
    fireEvent.click(modal.getByRole("button", { name: "Refresh free space" }));
    await waitFor(() =>
      expect(modal.container.textContent).toContain("Could not refresh recovery space: Error: offline"),
    );

    await act(async () => second.resolve({ ...preview, offset: 1, total: 2, items: [rowAs(8, "Second Page Game")] }));

    expect(modal.container.textContent).toContain("Second Page Game");
    expect(modal.container.textContent).toContain("Could not refresh recovery space: Error: offline");
  });

  it("keeps a status that is not about the list when a page fails", async () => {
    const second = pendingPage();
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce({ ...preview, total: 2 })
      .mockReturnValueOnce(second.promise)
      .mockRejectedValueOnce(new Error("offline"));
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    await waitFor(() => expect(backend.getPrunePreview).toHaveBeenCalledTimes(2));
    fireEvent.click(modal.getByRole("button", { name: "Refresh free space" }));
    await waitFor(() =>
      expect(modal.container.textContent).toContain("Could not refresh recovery space: Error: offline"),
    );

    await act(async () => second.reject(new Error("offline")));

    expect(modal.container.textContent).toContain("The list stopped loading at 1 of 2 entries.");
    expect(modal.container.textContent).toContain("Could not refresh recovery space: Error: offline");
  });

  it("keeps a status that is not about the list when Retry is pressed", async () => {
    const retried = pendingPage();
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce({ ...preview, total: 2 })
      .mockRejectedValueOnce(new Error("offline"))
      .mockRejectedValueOnce(new Error("offline"))
      .mockReturnValueOnce(retried.promise);
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    await waitFor(() => expect(modal.getByRole("button", { name: "Retry loading" })).toBeTruthy());
    fireEvent.click(modal.getByRole("button", { name: "Refresh free space" }));
    await waitFor(() =>
      expect(modal.container.textContent).toContain("Could not refresh recovery space: Error: offline"),
    );

    fireEvent.click(modal.getByRole("button", { name: "Retry loading" }));
    await waitFor(() => expect(backend.getPrunePreview).toHaveBeenCalledTimes(4));

    expect(modal.container.textContent).toContain("Could not refresh recovery space: Error: offline");
  });

  it("shows the backend's refusal of a page and loads no further", async () => {
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce({ ...preview, total: 3 })
      .mockResolvedValueOnce({
        success: false,
        reason: "stale_preview",
        message: "This cleanup preview is out of date. Scan again.",
      });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    await waitFor(() =>
      expect(modal.container.textContent).toContain("This cleanup preview is out of date. Scan again."),
    );
    expect(modal.getByRole("button", { name: "Retry loading" })).toBeTruthy();
    expect(backend.getPrunePreview).toHaveBeenCalledTimes(2);
  });

  it("treats an empty page short of the total as a failure rather than asking again", async () => {
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce({ ...preview, total: 2 })
      .mockResolvedValueOnce({ ...preview, offset: 1, total: 2, items: [] })
      .mockResolvedValueOnce({ ...preview, offset: 1, total: 2, items: [rowAs(8, "Second Page Game")] });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    await waitFor(() => expect(modal.container.textContent).toContain("The list ended before every entry arrived."));
    expect(backend.getPrunePreview).toHaveBeenCalledTimes(2);
    expect(modal.container.textContent).toContain("The list stopped loading at 1 of 2 entries.");
  });

  it("writes nothing and asks for nothing more once the dialog is closed mid-load", async () => {
    const second = pendingPage();
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce({ ...preview, total: 3 })
      .mockReturnValueOnce(second.promise);
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    await waitFor(() => expect(backend.getPrunePreview).toHaveBeenCalledTimes(2));

    modal.unmount();
    await act(async () => second.resolve({ ...preview, offset: 1, total: 3, items: [rowAs(8, "Second Page Game")] }));

    expect(backend.getPrunePreview).toHaveBeenCalledTimes(2);
  });

  it("clears and disables installed-content selections when recovery is off", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(contentToggleFor(modal, "Removed Game.gba"));
    expect(contentToggleFor(modal, "Removed Game.gba").checked).toBe(true);

    fireEvent.click(optionToggleFor(modal, OPTION_LABELS.recovery));
    await waitFor(() => {
      expect(contentToggleFor(modal, "Removed Game.gba").checked).toBe(false);
      expect(modal.container.textContent).toContain("Selected: 0 B of 200 B");
    });
    expect(rowFor(modal, "Removed Game.gba").textContent).toContain("Removed, file deleted");
  });

  it("allows a run with one option off and resets an old completion when opening", async () => {
    setPruneComplete({
      success: true,
      partial: false,
      run_id: "old",
      preview_id: "preview-old",
      removed_rom_ids: [99],
      affected_app_ids: [],
      results: [],
    });
    await openRemovedGamesCleanupModal(7);
    const modal = render(shownModal());
    fireEvent.click(optionToggleFor(modal, OPTION_LABELS.removeRows));
    const confirm = confirmButton(modal);
    expect(confirm.disabled).toBe(false);

    fireEvent.click(confirm);
    await waitFor(() => expect(backend.startPrune).toHaveBeenCalled());
    expect(vi.mocked(backend.startPrune).mock.calls[0]?.[0].remove_rows).toBe(false);
    expect(modal.container.textContent).toContain("RomM no longer has 1 version this device still holds.");
    expect(modal.container.textContent).not.toContain("of this game");
  });

  it("refreshes the free-space snapshot without replacing the preview", async () => {
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce(preview)
      .mockResolvedValueOnce({ ...preview, items: [], limit: 0, free_bytes: 500 });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    fireEvent.click(modal.getByRole("button", { name: "Refresh free space" }));
    await waitFor(() => expect(modal.container.textContent).toContain("500 B free"));
    expect(vi.mocked(backend.getPrunePreview).mock.calls[1]?.[0]).toMatchObject({
      preview_id: "preview-1",
      limit: 0,
    });
  });

  it("stages the checked installed content and carries its selection id into the run", async () => {
    vi.mocked(backend.getPrunePreview).mockResolvedValue({
      ...preview,
      total: 2,
      free_bytes: 1,
      items: [
        { ...rowAs(11, "Game 11"), installed_bytes: 0 },
        { ...rowAs(12, "Game 12"), installed_bytes: 0 },
      ],
    });
    vi.mocked(backend.stagePruneInstalledSelection).mockResolvedValue({
      success: true,
      selection_id: "selection-final",
      selected_count: 2,
      finalized: true,
    });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(contentToggleFor(modal, "Game 11.gba"));
    fireEvent.click(contentToggleFor(modal, "Game 12.gba"));

    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(backend.startPrune).toHaveBeenCalled());

    expect(vi.mocked(backend.stagePruneInstalledSelection)).toHaveBeenCalledExactlyOnceWith({
      preview_id: "preview-1",
      selection_id: null,
      rom_ids: [11, 12],
      final: true,
    });
    expect(vi.mocked(backend.startPrune).mock.calls[0]?.[0].installed_selection_id).toBe("selection-final");
  });

  it("does not start the run when the staged selection is refused", async () => {
    vi.mocked(backend.getPrunePreview).mockResolvedValue({
      ...preview,
      free_bytes: 1,
      items: [{ ...preview.items![0]!, installed_bytes: 0 }],
    });
    vi.mocked(backend.stagePruneInstalledSelection).mockResolvedValue({
      success: false,
      reason: "stale_preview",
      message: "That cleanup preview is no longer current.",
    });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(contentToggleFor(modal, "Removed Game.gba"));

    fireEvent.click(confirmButton(modal));
    await waitFor(() => {
      expect(modal.container.textContent).toContain("That cleanup preview is no longer current.");
      // Back at its resting label, so the whole Confirm path has run and the
      // control is usable for a retry.
      expect(confirmButton(modal).disabled).toBe(false);
    });

    // The run would otherwise execute against a selection the backend never accepted.
    expect(backend.startPrune).not.toHaveBeenCalled();
  });

  describe("the review's layout", () => {
    const withSibling: backend.PrunePreviewResult = {
      ...preview,
      total: 2,
      candidate_total: 1,
      items: [
        { ...preview.items![0]!, group_size: 2, warning: "Save ownership is shared." },
        {
          ...preview.items![0]!,
          rom_id: 8,
          candidate: false,
          gone_from_romm: false,
          installed: false,
          installed_bytes: null,
          name: "Removed Game",
          fs_name: "Live sibling.gba",
          fs_name_no_ext: "Live sibling",
          group_size: 2,
        },
      ],
    };

    it("draws the list under the four column names, a game's row over its versions", async () => {
      vi.mocked(backend.getPrunePreview).mockResolvedValue(withSibling);
      await openRemovedGamesCleanupModal();
      const modal = render(shownModal());

      const header = modal.getByTestId("cleanup-header");
      expect([...header.children].map((cell) => cell.textContent)).toEqual([
        "Game and versions",
        "What happens",
        "Downloaded",
        "Back up file",
      ]);
      const game = rowFor(modal, "Removed Game");
      expect(game.textContent).toContain("gba · 1 of 2 versions gone");
      expect(game.textContent).toContain("Game stays");
      const gone = rowFor(modal, "Removed Game.gba");
      expect(gone.textContent).toContain("Gone from RomM");
      expect(gone.textContent).toContain("200 B");
      // The version still on RomM sits under its own game, not in a section of its own.
      const sibling = rowFor(modal, "Live sibling.gba");
      expect(sibling.textContent).toContain("On RomM");
      expect(sibling.textContent).toContain("Stays");
      expect(precedes(game, gone)).toBe(true);
      expect(precedes(gone, sibling)).toBe(true);
      expect(modal.getByTestId("cleanup-table").textContent).toContain(
        "Downloaded: the game files of that version on this device. A file is deleted only where the row says so.",
      );
    });

    it("makes the Back up file switch the stop of a row whose file goes, and every other row a stop itself", async () => {
      vi.mocked(backend.getPrunePreview).mockResolvedValue(withSibling);
      await openRemovedGamesCleanupModal();
      const modal = render(shownModal());

      // A stop on the wrapper as well would put a dead step in front of the switch.
      const deleting = rowFor(modal, "Removed Game.gba");
      expect(deleting.getAttribute("data-activate")).toBeNull();
      expect(within(deleting).getAllByTestId("toggle")).toHaveLength(1);
      // A row with nothing to press has to be a stop, or the list cannot be walked.
      const sibling = rowFor(modal, "Live sibling.gba");
      expect(sibling.getAttribute("data-activate")).toBe("true");
      expect(within(sibling).queryAllByTestId("toggle")).toHaveLength(0);
      expect(rowFor(modal, "Removed Game").getAttribute("data-activate")).toBe("true");
    });

    it("keeps every per-row warning on screen, under its own row", async () => {
      vi.mocked(backend.getPrunePreview).mockResolvedValue(withSibling);
      await openRemovedGamesCleanupModal();
      const modal = render(shownModal());

      expect(rowFor(modal, "Removed Game.gba").textContent).toContain("Save ownership is shared.");
    });

    it("puts the four options in one column, the acknowledgement under the backup it answers for", async () => {
      await openRemovedGamesCleanupModal();
      const modal = render(shownModal());
      fireEvent.click(optionToggleFor(modal, OPTION_LABELS.recovery));

      const toggles = [
        OPTION_LABELS.removeRows,
        OPTION_LABELS.removeDeadGames,
        OPTION_LABELS.recovery,
        OPTION_LABELS.confirmWithoutRecovery,
        OPTION_LABELS.backUpFiles,
      ].map((label) => optionToggleFor(modal, label));
      const column = (input: HTMLInputElement) => input.closest('[data-testid="focusable"]')!;
      expect(new Set(toggles.map(column)).size).toBe(1);
      for (let index = 1; index < toggles.length; index++) {
        expect(precedes(toggles[index - 1]!, toggles[index]!)).toBe(true);
      }
    });

    it("puts the run's controls between the options and the list, where focus reaches them first", async () => {
      vi.mocked(backend.getPrunePreview).mockResolvedValue(withSibling);
      await openRemovedGamesCleanupModal();
      const modal = render(shownModal());
      const table = modal.getByTestId("cleanup-table");
      const lastOption = optionToggleFor(modal, OPTION_LABELS.backUpFiles);

      // Focus follows element order, not the picture: a control after the rows
      // is one press per row away however it is drawn.
      for (const control of [modal.getByRole("button", { name: "Cancel" }), confirmButton(modal)]) {
        expect(precedes(lastOption, control)).toBe(true);
        expect(precedes(control, table)).toBe(true);
      }
      // Refresh free space sits with the space line it refreshes, under the fourth option.
      const refresh = modal.getByRole("button", { name: "Refresh free space" });
      expect(precedes(lastOption, refresh)).toBe(true);
      expect(modal.getByTestId("cleanup-space").contains(refresh)).toBe(true);
      expect(precedes(modal.getByText(/^Selected: /), table)).toBe(true);
    });

    it("keeps a running run's Stop and a finished run's details above the list too", async () => {
      await openRemovedGamesCleanupModal();
      const modal = render(shownModal());
      fireEvent.click(confirmButton(modal));
      await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));
      act(() => {
        setPruneProgress({
          run_id: "run-1",
          preview_id: "preview-1",
          current: 1,
          total: 2,
          stage: "checking",
          rom_ids: [7],
          name: "Removed Game",
        });
      });

      expect(precedes(modal.getByRole("button", { name: "Stop Cleanup" }), modal.getByTestId("cleanup-table"))).toBe(
        true,
      );

      act(() => {
        setPruneComplete({
          success: true,
          partial: false,
          run_id: "run-1",
          preview_id: "preview-1",
          removed_rom_ids: [7],
          affected_app_ids: [],
          results: [],
        });
      });

      expect(precedes(modal.getByRole("region", { name: "Cleanup details" }), modal.getByTestId("cleanup-table"))).toBe(
        true,
      );
    });

    it("puts Retry loading and its failure in the bar, beside the Confirm it unblocks", async () => {
      vi.mocked(backend.getPrunePreview)
        .mockResolvedValueOnce({ ...preview, total: 2 })
        .mockRejectedValueOnce(new Error("offline"));
      await openRemovedGamesCleanupModal();
      const modal = render(shownModal());
      const bar = modal.getByTestId("cleanup-bar");
      const table = modal.getByTestId("cleanup-table");
      const retry = await modal.findByRole("button", { name: "Retry loading" });

      // Every row is a stop, so after the list it would be a list's length
      // away from the Confirm that refuses until the list has loaded.
      expect(bar.contains(retry)).toBe(true);
      expect(precedes(retry, confirmButton(modal))).toBe(true);
      expect(precedes(retry, table)).toBe(true);
      expect(bar.textContent).toContain("Could not load the rest of the list");
    });

    it("tells two gone versions of one game apart on screen, not only in a title", async () => {
      const disc = (romId: number, file: string): backend.PrunePreviewItem => ({
        ...preview.items![0]!,
        rom_id: romId,
        name: "Chrono Cross",
        fs_name: `${file}.chd`,
        fs_name_no_ext: file,
        group_id: "chrono",
        group_size: 2,
      });
      vi.mocked(backend.getPrunePreview).mockResolvedValue({
        ...preview,
        total: 2,
        candidate_total: 2,
        items: [disc(21, "Chrono Cross (Disc 1)"), disc(22, "Chrono Cross (Disc 2)")],
      });
      await openRemovedGamesCleanupModal();
      const modal = render(shownModal());

      // A title is a hover the controller cannot perform, and each row carries
      // its own Back up file switch.
      const first = modal.getByTestId("cleanup-row-21");
      const second = modal.getByTestId("cleanup-row-22");
      expect(first.textContent).toContain("Chrono Cross (Disc 1).chd");
      expect(second.textContent).toContain("Chrono Cross (Disc 2).chd");
      expect(first.textContent).toContain("Gone from RomM");
      expect(second.textContent).toContain("Gone from RomM");
    });

    it("scrolls the dialog to its end when the last row takes focus, and not before", async () => {
      vi.useFakeTimers();
      try {
        vi.mocked(backend.getPrunePreview).mockResolvedValue(withSibling);
        await openRemovedGamesCleanupModal();
        const modal = render(shownModal());
        const body = [...modal.container.querySelectorAll<HTMLElement>("div")].find(
          (el) => el.style.overflowY === "auto",
        )!;
        Object.defineProperty(body, "scrollHeight", { value: 2000, configurable: true });
        Object.defineProperty(body, "clientHeight", { value: 600, configurable: true });
        const scrollTo = vi.fn();
        body.scrollTo = scrollTo as unknown as typeof body.scrollTo;

        fireEvent.focusIn(contentToggleFor(modal, "Removed Game.gba"));
        act(() => {
          vi.runAllTimers();
        });
        expect(scrollTo).not.toHaveBeenCalled();

        // Steam stops at the control itself, leaving what sits under the last
        // row — its own lines, the note after the list — below the view.
        fireEvent.focusIn(rowFor(modal, "Live sibling.gba"));
        act(() => {
          vi.runAllTimers();
        });
        expect(scrollTo).toHaveBeenCalledExactlyOnceWith({ top: 2000, behavior: "smooth" });
      } finally {
        vi.useRealTimers();
      }
    });
  });

  describe("what a cleanup would do", () => {
    /** A version of `group`, gone and offered unless the overrides say otherwise. */
    const version = (
      romId: number,
      file: string,
      group: string,
      size: number,
      overrides: Partial<backend.PrunePreviewItem> = {},
    ): backend.PrunePreviewItem => ({
      ...preview.items![0]!,
      rom_id: romId,
      name: group,
      fs_name: file,
      fs_name_no_ext: file.replace(/\.[^.]+$/, ""),
      group_id: group,
      group_size: size,
      installed: false,
      installed_bytes: null,
      ...overrides,
    });
    const onRomm = { candidate: false, gone_from_romm: false };

    /** The three games of the layout study: one keeps a version, one keeps everything, one goes whole. */
    const threeGames: backend.PrunePreviewResult = {
      ...preview,
      total: 10,
      candidate_total: 3,
      free_bytes: 10_000_000,
      items: [
        version(31, "Rally Cup (USA).zip", "Rally Cup", 2, { bound_count: 1 }),
        version(41, "Island Hopper (Netherlands).gba", "Island Hopper", 7, { bound: true, bound_count: 1 }),
        version(51, "Moon Patrol (USA) (Proto 2).zip", "Moon Patrol", 1, {
          bound: true,
          bound_count: 1,
          installed: true,
          installed_bytes: 1_887_436,
        }),
        version(32, "Rally Cup (Europe).zip", "Rally Cup", 2, {
          ...onRomm,
          bound: true,
          bound_count: 1,
          installed: true,
          installed_bytes: 467_664_896,
        }),
        ...[42, 43, 44, 45, 46, 47].map((romId) =>
          version(romId, `Island Hopper (${romId}).gba`, "Island Hopper", 7, { ...onRomm, bound_count: 1 }),
        ),
      ],
    };

    const counts = (modal: RenderResult): string[] =>
      [...modal.getByTestId("cleanup-summary").querySelectorAll("li")].map((item) => item.textContent);

    async function open(result: backend.PrunePreviewResult, romId?: number): Promise<RenderResult> {
      vi.mocked(backend.getPrunePreview).mockResolvedValue(result);
      await openRemovedGamesCleanupModal(romId);
      return render(shownModal());
    }

    it.each([
      {
        rule: "the version a shortcut is on, over a gone version sorted first and over the main sibling",
        bound: 202,
        main: 203,
        title: "Madagascar (USA)",
      },
      {
        rule: "RomM's main sibling where no shortcut is on a version",
        bound: null,
        main: 203,
        title: "Madagascar (Europe)",
      },
      { rule: "the first row where neither is set", bound: null, main: null, title: "Madagascar (Japan)" },
    ])("names the game after $rule", async ({ bound, main, title }) => {
      const row = (romId: number, region: string, overrides: Partial<backend.PrunePreviewItem> = {}) =>
        version(romId, `Madagascar (${region}).zip`, "Madagascar", 3, {
          name: `Madagascar (${region})`,
          bound: romId === bound,
          bound_count: bound === null ? 0 : 1,
          is_main_sibling: romId === main,
          ...overrides,
        });
      const modal = await open({
        ...preview,
        total: 3,
        candidate_total: 1,
        items: [row(201, "Japan"), row(202, "USA", onRomm), row(203, "Europe", onRomm)],
      });

      const gameLine = modal.getByText("gba · 1 of 3 versions gone");
      expect(gameLine.previousElementSibling!.textContent).toBe(title);
    });

    it("gives the names the width and wraps every name, where-line and game outcome rather than cutting it short", async () => {
      const modal = await open(threeGames);
      // A controller shows no tooltip, so nothing between a line and its row may clip it.
      const cutsShort = (line: HTMLElement): boolean => {
        for (
          let el: HTMLElement | null = line;
          el && !el.matches('[data-testid="cleanup-table"]');
          el = el.parentElement
        ) {
          if (
            el.style.textOverflow === "ellipsis" ||
            el.style.whiteSpace === "nowrap" ||
            el.style.overflow === "hidden"
          ) {
            return true;
          }
        }
        return false;
      };

      expect(modal.getByTestId("cleanup-header").style.gridTemplateColumns).toBe(
        "minmax(0, 3fr) minmax(0, 2fr) 64px 72px",
      );
      for (const text of [
        "Moon Patrol",
        "Game removed, with its shortcut",
        "Moon Patrol (USA) (Proto 2).zip",
        "Gone from RomM · your shortcut",
      ]) {
        expect(cutsShort(modal.getAllByText(text)[0]!), text).toBe(false);
      }
    });

    it("sets what happens and a version's name smaller than the game's name, and leaves the row switch unlabelled", async () => {
      const modal = await open(threeGames);
      const moon = rowFor(modal, "Moon Patrol (USA) (Proto 2).zip");

      expect(within(moon).getByText("Removed, file deleted").style.fontSize).toBe(SECONDARY_FONT);
      expect(within(moon).getByText("Switch on “Back up file” to keep a copy.").style.fontSize).toBe(NOTE_FONT);
      expect(within(rowFor(modal, "Moon Patrol")).getByText("Game removed, with its shortcut").style.fontSize).toBe(
        SECONDARY_FONT,
      );
      expect(within(moon).getByText("Moon Patrol (USA) (Proto 2).zip").style.fontSize).toBe(SECONDARY_FONT);
      expect(within(rowFor(modal, "Moon Patrol")).getByText("Moon Patrol").style.fontSize).toBe("13px");
      expect(within(moon).getByTestId("toggle").textContent).toBe("");
    });

    it("draws the row switch with no box of its own, at the end of its column, and shows its focus on the row", async () => {
      const modal = await open(threeGames);
      const row = modal.getByTestId("cleanup-row-51");
      const toggle = within(row).getByTestId("toggle");

      // The bare switch, with no Field around it to draw a box or take width.
      expect(toggle.getAttribute("data-bare")).toBe("true");
      expect(toggle.parentElement!.style.justifyContent).toBe("flex-end");
      fireEvent.focusIn(within(toggle).getByTestId("toggle-input"));
      expect(row.style.background).toBe(asCssColour(FOCUSED_ROW_FILL));
      fireEvent.focusOut(within(toggle).getByTestId("toggle-input"));
      expect(row.style.background).toBe("transparent");
    });

    it("groups the list by game, says what happens to each game and version, and follows the options", async () => {
      const modal = await open(threeGames);

      const rally = rowFor(modal, "Rally Cup");
      expect(rally.textContent).toContain("gba · 1 of 2 versions gone");
      expect(rally.textContent).toContain("Game stays");
      expect(rowFor(modal, "Rally Cup (USA).zip").textContent).toContain("Removed");
      const kept = rowFor(modal, "Rally Cup (Europe).zip");
      expect(kept.textContent).toContain("Stays");
      expect(kept.textContent).toContain("Its files stay on this device.");
      expect(rowFor(modal, "Island Hopper").textContent).toContain("Nothing removed");
      const moon = rowFor(modal, "Moon Patrol");
      expect(moon.textContent).toContain("gba · every version gone");
      expect(moon.textContent).toContain("Game removed, with its shortcut");
      expect(rowFor(modal, "Moon Patrol (USA) (Proto 2).zip").textContent).toContain("Removed, file deleted");
      // Every game row is a stop of its own.
      for (const game of ["Rally Cup", "Island Hopper", "Moon Patrol"]) {
        expect(rowFor(modal, game).getAttribute("data-activate")).toBe("true");
      }

      fireEvent.click(optionToggleFor(modal, OPTION_LABELS.removeRows));
      expect(rowFor(modal, "Rally Cup").textContent).toContain("Nothing removed");
      const rallyGone = rowFor(modal, "Rally Cup (USA).zip");
      expect(rallyGone.textContent).toContain("Kept");
      expect(rallyGone.textContent).toContain("“Remove gone versions” is off.");
      expect(counts(modal)).toContain("1 version removed");

      fireEvent.click(optionToggleFor(modal, OPTION_LABELS.removeDeadGames));
      expect(rowFor(modal, "Moon Patrol").textContent).toContain("Nothing removed");
      expect(rowFor(modal, "Moon Patrol (USA) (Proto 2).zip").textContent).toContain(
        "“Remove games that are gone entirely” is off.",
      );
      expect(counts(modal)).not.toContain("1 version removed");
    });

    it("folds more than two other versions on RomM into one row that opens them in place", async () => {
      const modal = await open(threeGames);

      const show = modal.getByRole("button", { name: "6 more versions, all on RomM · Show" });
      expect(modal.queryByText("Island Hopper (42).gba")).toBeNull();
      // Rally Cup's one other version on RomM is not folded.
      expect(modal.getByText("Rally Cup (Europe).zip")).toBeTruthy();

      fireEvent.click(show);

      expect(modal.queryByRole("button", { name: /more versions, all on RomM/ })).toBeNull();
      for (const romId of [42, 43, 44, 45, 46, 47]) {
        expect(rowFor(modal, `Island Hopper (${romId}).gba`).textContent).toContain("On RomM");
      }
      expect(precedes(rowFor(modal, "Island Hopper (47).gba"), rowFor(modal, "Moon Patrol"))).toBe(true);
    });

    it("keeps the version a shortcut is on in view while the game's other versions on RomM fold", async () => {
      const modal = await open({
        ...preview,
        total: 5,
        candidate_total: 1,
        items: [
          version(101, "Tower (USA).zip", "Tower", 5),
          version(102, "Tower (Japan).zip", "Tower", 5, { ...onRomm, bound: true, bound_count: 1 }),
          ...[103, 104, 105].map((romId) => version(romId, `Tower (${romId}).zip`, "Tower", 5, onRomm)),
        ],
      });

      expect(rowFor(modal, "Tower (Japan).zip").textContent).toContain("On RomM · your shortcut");
      expect(modal.getByRole("button", { name: "3 more versions, all on RomM · Show" })).toBeTruthy();
      expect(modal.queryByText("Tower (103).zip")).toBeNull();
    });

    it("opens with the summary and counts that follow the options, a zero count left out", async () => {
      const modal = await open(threeGames);

      expect(modal.getByTestId("cleanup-summary").textContent).toContain(
        "RomM no longer has 3 versions this device still holds. Each one is checked against RomM again before anything is removed. If you clean up now:",
      );
      expect(counts(modal)).toEqual([
        "2 versions removed",
        "1 game removed with its shortcut",
        "1 downloaded file deleted",
        "1 version kept because a shortcut is on it",
      ]);

      fireEvent.click(contentToggleFor(modal, "Moon Patrol (USA) (Proto 2).zip"));

      expect(counts(modal)).toEqual([
        "2 versions removed",
        "1 game removed with its shortcut",
        "1 version kept because a shortcut is on it",
      ]);
    });

    it("leaves out a count of games with a shortcut where the game removed has none", async () => {
      const modal = await open({ ...preview, items: [{ ...preview.items![0]!, installed: false }] });

      expect(rowFor(modal, "Removed Game").textContent).toContain("Game removed");
      expect(rowFor(modal, "Removed Game").textContent).not.toContain("with its shortcut");
      expect(counts(modal)).toEqual(["1 version removed"]);
    });

    it("switches every row's backup with the fourth option, each row on its own too, and the space line follows", async () => {
      const modal = await open({
        ...preview,
        total: 2,
        candidate_total: 2,
        free_bytes: 1000,
        items: [
          { ...rowAs(61, "First"), installed_bytes: 100 },
          { ...rowAs(62, "Second"), installed_bytes: 300 },
        ],
      });
      const every = () => optionToggleFor(modal, OPTION_LABELS.backUpFiles);
      expect(modal.container.textContent).toContain("Selected: 0 B of 400 B · 1000 B free");

      fireEvent.click(every());
      expect(contentToggleFor(modal, "First.gba").checked).toBe(true);
      expect(contentToggleFor(modal, "Second.gba").checked).toBe(true);
      expect(modal.container.textContent).toContain("Selected: 400 B of 400 B · 1000 B free");

      fireEvent.click(contentToggleFor(modal, "First.gba"));
      expect(contentToggleFor(modal, "Second.gba").checked).toBe(true);
      expect(every().checked).toBe(false);
      expect(modal.container.textContent).toContain("Selected: 300 B of 400 B · 1000 B free");

      fireEvent.click(every());
      expect(contentToggleFor(modal, "First.gba").checked).toBe(true);
      fireEvent.click(every());
      expect(contentToggleFor(modal, "First.gba").checked).toBe(false);
      expect(contentToggleFor(modal, "Second.gba").checked).toBe(false);
      expect(modal.container.textContent).toContain("Selected: 0 B of 400 B · 1000 B free");
    });

    it("offers the Back up file switch only on a row whose file the run deletes, and marks only the shortcut's version", async () => {
      const modal = await open(threeGames);

      // Rally Cup (Europe) is downloaded too, but stays: its file is not the run's to delete.
      const [only, ...others] = within(modal.getByTestId("cleanup-table")).getAllByTestId("toggle");
      expect(others).toHaveLength(0);
      expect(rowFor(modal, "Moon Patrol (USA) (Proto 2).zip").contains(only!)).toBe(true);
      expect(rowFor(modal, "Rally Cup (Europe).zip").textContent).toContain("On RomM · your shortcut");
      expect(rowFor(modal, "Island Hopper (Netherlands).gba").textContent).toContain("Gone from RomM · your shortcut");
      expect(rowFor(modal, "Rally Cup (USA).zip").textContent).not.toContain("your shortcut");
    });

    it("names the count on Confirm, red and last beside Cancel", async () => {
      const modal = await open(threeGames);

      const confirm = modal.getByRole("button", { name: "Remove 2 versions" });
      const red = document.createElement("span");
      red.style.color = RED;
      expect(confirm.querySelector("span")!.style.color).toBe(red.style.color);
      expect(confirm.parentElement!.lastElementChild).toBe(confirm);
      expect(confirm.previousElementSibling?.textContent).toBe("Cancel");
    });

    it("reads an old entry beside a version on RomM with the same file name as replaced, and removes it", async () => {
      const modal = await open({
        ...preview,
        total: 4,
        candidate_total: 2,
        items: [
          version(71, "Dump (USA).zip", "Dump", 2),
          version(81, "Other (USA).zip", "Other", 2),
          version(72, "Dump (USA).7z", "Dump", 2, onRomm),
          version(82, "Other (Europe).zip", "Other", 2, onRomm),
        ],
      });

      const replaced = rowFor(modal, "Dump (USA).zip");
      expect(replaced.textContent).toContain("Replaced on RomM by the same file under a new ID");
      expect(replaced.textContent).not.toContain("Gone from RomM");
      expect(replaced.textContent).toContain("Removed");
      expect(replaced.textContent).toContain("Its save files stay; the new entry uses them.");
      // Another file name is no replacement.
      const other = rowFor(modal, "Other (USA).zip");
      expect(other.textContent).toContain("Gone from RomM");
      expect(other.textContent).not.toContain("the new entry uses them");
      expect(counts(modal)).toEqual(["2 versions removed"]);
    });

    it("never reads an empty file name as the same file", async () => {
      const modal = await open({
        ...preview,
        total: 2,
        candidate_total: 1,
        items: [
          version(73, "", "Nameless", 2, { fs_name_no_ext: "" }),
          version(74, "", "Nameless", 2, { ...onRomm, fs_name_no_ext: "" }),
        ],
      });

      expect(modal.container.textContent).not.toContain("Replaced on RomM");
    });

    it("says a backed-up version goes into the backup, a file deleted how to keep it, and why a version is kept", async () => {
      const modal = await open(threeGames);
      const moonVersion = () => rowFor(modal, "Moon Patrol (USA) (Proto 2).zip");
      expect(moonVersion().textContent).toContain("Switch on “Back up file” to keep a copy.");
      expect(rowFor(modal, "Island Hopper (Netherlands).gba").textContent).toContain(
        "Switch to another version first, then this one can be removed.",
      );

      fireEvent.click(contentToggleFor(modal, "Moon Patrol (USA) (Proto 2).zip"));
      expect(moonVersion().textContent).toContain("Removed");
      expect(moonVersion().textContent).not.toContain("file deleted");
      expect(moonVersion().textContent).toContain("A copy of its file goes into the backup first.");

      fireEvent.click(optionToggleFor(modal, OPTION_LABELS.recovery));
      expect(moonVersion().textContent).toContain("Removed, file deleted");
      // With the backup off the switch is disabled, so nothing points at it.
      expect(moonVersion().textContent).not.toContain("Switch on “Back up file”");
    });

    it("keeps the shortcut's reason where an option would keep the version too", async () => {
      const modal = await open(threeGames);
      fireEvent.click(optionToggleFor(modal, OPTION_LABELS.removeRows));

      const bound = rowFor(modal, "Island Hopper (Netherlands).gba");
      expect(bound.textContent).toContain("Switch to another version first");
      expect(bound.textContent).not.toContain("is off.");
      expect(counts(modal)).toContain("1 version kept because a shortcut is on it");
    });

    it("says nothing is removed, with the kept count, and disables Confirm where no version would go", async () => {
      const modal = await open(
        {
          ...preview,
          scope: "rom",
          total: 2,
          items: [
            version(41, "Island Hopper (Netherlands).gba", "Island Hopper", 2, { bound: true, bound_count: 1 }),
            version(42, "Island Hopper (Europe).gba", "Island Hopper", 2, { ...onRomm, bound_count: 1 }),
          ],
        },
        41,
      );

      expect(modal.getByTestId("cleanup-summary").textContent).toContain("If you clean up now: nothing is removed.");
      expect(counts(modal)).toEqual(["1 version kept because a shortcut is on it"]);
      expect(confirmButton(modal).disabled).toBe(true);
    });

    it("offers a game whose every version is gone whole from the trash icon, every row listed with the option off", async () => {
      const modal = await open(
        {
          ...preview,
          scope: "rom",
          total: 2,
          candidate_total: 2,
          items: [version(91, "Gone (USA).zip", "Gone", 2), version(92, "Gone (Europe).zip", "Gone", 2)],
        },
        91,
      );

      expect(rowFor(modal, "Gone").textContent).toContain("Game removed");
      expect(counts(modal)).toEqual(["2 versions removed"]);
      expect(confirmButton(modal).textContent).toBe("Remove 2 versions");

      fireEvent.click(optionToggleFor(modal, OPTION_LABELS.removeDeadGames));

      expect(rowFor(modal, "Gone").textContent).toContain("Nothing removed");
      for (const file of ["Gone (USA).zip", "Gone (Europe).zip"]) {
        expect(rowFor(modal, file).textContent).toContain("“Remove games that are gone entirely” is off.");
      }
    });

    it("offers the version the trash icon was opened on as gone, as the preview says of it", async () => {
      // What the preview answers for a version RomM 404s while its platform's
      // last fetch still holds it: the chosen row gone and offered, its sibling on RomM.
      const modal = await open(
        {
          ...preview,
          scope: "rom",
          total: 2,
          candidate_total: 1,
          items: [version(96, "Fresh (USA).zip", "Fresh", 2), version(97, "Fresh (Japan).zip", "Fresh", 2, onRomm)],
        },
        96,
      );

      const chosen = rowFor(modal, "Fresh (USA).zip");
      expect(chosen.textContent).toContain("Gone from RomM");
      expect(chosen.textContent).toContain("Removed");
      expect(confirmButton(modal).textContent).toBe("Remove 1 version");
    });

    it("keeps a gone version the shortcut is on in the trash icon's dialog for the shortcut's reason", async () => {
      const modal = await open(
        {
          ...preview,
          scope: "rom",
          total: 3,
          candidate_total: 1,
          items: [
            version(93, "Chosen (USA).zip", "Chosen", 3, { bound_count: 1 }),
            version(94, "Chosen (Europe).zip", "Chosen", 3, { candidate: false, bound: true, bound_count: 1 }),
            version(95, "Chosen (Japan).zip", "Chosen", 3, { ...onRomm, bound_count: 1 }),
          ],
        },
        93,
      );

      const bound = rowFor(modal, "Chosen (Europe).zip");
      expect(bound.textContent).toContain("Switch to another version first, then this one can be removed.");
      expect(bound.textContent).not.toContain("Only the version you chose");
      expect(counts(modal)).toEqual(["1 version removed", "1 version kept because a shortcut is on it"]);
    });

    it("draws what is lost or leaves Steam in red, a record alone in light, what is kept in blue and what stays in green", async () => {
      const modal = await open({
        ...threeGames,
        total: 11,
        candidate_total: 4,
        items: [...threeGames.items!, version(61, "Lone Star (USA).zip", "Lone Star", 1)],
      });
      const colourOf = (row: HTMLElement, text: string): string =>
        within(row)
          .getAllByText(text)
          .map((el) => el.style.color)
          .find(Boolean)!;

      const blue = colourOf(rowFor(modal, "Island Hopper"), "Nothing removed");
      expect(colourOf(rowFor(modal, "Island Hopper (Netherlands).gba"), "Kept")).toBe(blue);
      expect(colourOf(rowFor(modal, "Rally Cup (Europe).zip"), "Stays")).toBe(asCssColour(GREEN));
      expect(colourOf(rowFor(modal, "Moon Patrol (USA) (Proto 2).zip"), "Removed, file deleted")).toBe(
        asCssColour(RED),
      );
      expect(colourOf(rowFor(modal, "Moon Patrol"), "Game removed, with its shortcut")).toBe(asCssColour(RED));
      expect([asCssColour(RED), asCssColour(GREEN)]).not.toContain(blue);
      const light = colourOf(rowFor(modal, "Rally Cup (USA).zip"), "Removed");
      expect([asCssColour(RED), asCssColour(GREEN), blue]).not.toContain(light);
      expect(colourOf(rowFor(modal, "Lone Star"), "Game removed")).toBe(light);
    });

    it("keeps a gone version other than the one chosen from the trash icon, from the row's own field", async () => {
      const modal = await open(
        {
          ...preview,
          scope: "rom",
          total: 3,
          candidate_total: 1,
          items: [
            version(93, "Chosen (USA).zip", "Chosen", 3),
            version(94, "Chosen (Europe).zip", "Chosen", 3, { candidate: false }),
            version(95, "Chosen (Japan).zip", "Chosen", 3, onRomm),
          ],
        },
        93,
      );

      const other = rowFor(modal, "Chosen (Europe).zip");
      expect(other.textContent).toContain("Gone from RomM");
      expect(other.textContent).toContain("Kept");
      expect(other.textContent).toContain("Only the version you chose is removed here.");
      expect(rowFor(modal, "Chosen (USA).zip").textContent).toContain("Removed");
      expect(rowFor(modal, "Chosen (Japan).zip").textContent).toContain("On RomM");
      expect(counts(modal)).toEqual(["1 version removed"]);
    });
  });

  describe("stageInstalledSelections", () => {
    // Enough to cross the page boundary twice and leave a short final page. The
    // dialog would need one rendered row and one click per id to reach it.
    const romIds = Array.from({ length: 257 }, (_, index) => index + 1);

    it("splits a selection into bounded pages, each chained onto the one before", async () => {
      let page = 0;
      vi.mocked(backend.stagePruneInstalledSelection).mockImplementation(async (request) => ({
        success: true,
        selection_id: `selection-${++page}`,
        selected_count: request.rom_ids.length,
        finalized: request.final,
      }));

      await expect(stageInstalledSelections("preview-1", romIds, vi.fn())).resolves.toEqual({
        ok: true,
        selectionId: "selection-3",
      });

      const pages = vi.mocked(backend.stagePruneInstalledSelection).mock.calls.map(([request]) => request);
      expect(pages.map((request) => request.rom_ids.length)).toEqual([100, 100, 57]);
      expect(pages.map((request) => request.selection_id)).toEqual([null, "selection-1", "selection-2"]);
      // Only the last page finalizes — an early close would drop the rest.
      expect(pages.map((request) => request.final)).toEqual([false, false, true]);
      expect(pages.flatMap((request) => request.rom_ids)).toEqual(romIds);
    });

    it("stops at a refused page and reports the backend's message", async () => {
      const setStatus = vi.fn();
      vi.mocked(backend.stagePruneInstalledSelection)
        .mockResolvedValueOnce({ success: true, selection_id: "selection-1", selected_count: 100, finalized: false })
        .mockResolvedValueOnce({
          success: false,
          reason: "stale_preview",
          message: "That cleanup preview is no longer current.",
        });

      await expect(stageInstalledSelections("preview-1", romIds, setStatus)).resolves.toEqual({ ok: false });

      // The third page is never sent: a partially staged selection must not be
      // extended past the point the backend stopped accepting it.
      expect(backend.stagePruneInstalledSelection).toHaveBeenCalledTimes(2);
      expect(setStatus).toHaveBeenCalledExactlyOnceWith("That cleanup preview is no longer current.");
      expect(logs.warn).toHaveBeenCalledWith(
        expect.stringContaining("Confirm aborted while staging installed content"),
      );
    });

    it("treats a page that reports success without a selection id as refused", async () => {
      const setStatus = vi.fn();
      vi.mocked(backend.stagePruneInstalledSelection).mockResolvedValue({
        success: true,
        selected_count: 100,
        message: "The selection was accepted but not stored.",
      });

      await expect(stageInstalledSelections("preview-1", romIds, setStatus)).resolves.toEqual({ ok: false });

      // There is nothing to chain the next page onto. Carrying on would open a
      // second, unrelated selection and stage the remaining ids into it, and the
      // run would then be started against whichever one Confirm happened to hold.
      expect(backend.stagePruneInstalledSelection).toHaveBeenCalledTimes(1);
      expect(setStatus).toHaveBeenCalledExactlyOnceWith("The selection was accepted but not stored.");
    });

    it("says something of its own when a refusal carries no message", async () => {
      const setStatus = vi.fn();
      vi.mocked(backend.stagePruneInstalledSelection).mockResolvedValue({ success: false, reason: "stale_preview" });

      await expect(stageInstalledSelections("preview-1", romIds, setStatus)).resolves.toEqual({ ok: false });

      // Confirm reports through this string alone, so an unexplained refusal
      // must not surface as a blank line in the dialog.
      expect(setStatus).toHaveBeenCalledExactlyOnceWith("Installed-content selections could not be staged.");
      expect(logs.warn).toHaveBeenCalledWith(expect.stringContaining("installed content: no message"));
    });
  });

  it("surfaces bounded save warnings in terminal details", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));

    act(() => {
      setPruneComplete({
        success: false,
        partial: true,
        run_id: "run-1",
        preview_id: "preview-1",
        removed_rom_ids: [],
        affected_app_ids: [],
        results: [
          {
            group_id: "group-1",
            rom_ids: [7],
            status: "partial",
            message: "Local cleanup was incomplete.",
            warnings: ["Shared save was retained."],
            warning_count: 7,
            warnings_omitted: true,
          },
        ],
      });
    });

    expect(modal.container.textContent).toContain("Warning: Shared save was retained.");
    expect(modal.container.textContent).toContain("6 additional warning(s) omitted.");
    const details = modal.getByRole("region", { name: "Cleanup details" });
    // The nav option is what makes the region a stop; the tabindex is Steam's to
    // write (`docs/architecture/qam-panel.md`). Asserting that attribute instead
    // stayed green throughout the period the region was unreachable on the device.
    expect(details.getAttribute("data-focusable-if-empty")).toBe("true");
    expect(modal.getByRole("status").textContent).not.toContain("Shared save was retained");
  });

  it("surfaces warnings from successful removals and a run-level terminal error", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));

    act(() => {
      setPruneComplete({
        success: false,
        partial: true,
        run_id: "run-1",
        preview_id: "preview-1",
        reason: "unknown",
        message: "The run stopped after committed work.",
        removed_rom_ids: [7],
        affected_app_ids: [],
        results: [
          {
            group_id: "group-1",
            rom_ids: [7],
            status: "removed",
            message: "Removed.",
            warnings: ["A shared save was retained."],
            warning_count: 1,
          },
        ],
      });
    });

    expect(modal.container.textContent).toContain("unknown: The run stopped after committed work.");
    expect(modal.container.textContent).toContain("Warning: A shared save was retained.");
    expect(modal.container.textContent).not.toContain("0 additional warning(s)");
  });

  describe("the finished run's line on Data Management", () => {
    const finishedRun = (overrides: Partial<PruneComplete>): PruneComplete => ({
      success: false,
      partial: false,
      run_id: "run-1",
      preview_id: "preview-1",
      removed_rom_ids: [],
      affected_app_ids: [],
      results: [],
      ...overrides,
    });

    it("joins the run's message and each failed or warned group's, and counts no omitted warnings when none were left out", () => {
      const section = render(createElement(RemovedGamesCleanupSection));
      act(() => {
        beginPrunePreview("preview-1");
        beginPruneRun("run-1", "preview-1");
        setPruneComplete(
          finishedRun({
            message: "The run stopped.",
            results: [
              { group_id: "g-1", rom_ids: [1], status: "failed", message: "Nothing was removed." },
              { group_id: "g-2", rom_ids: [2], status: "removed", message: "Removed without a word." },
              {
                group_id: "g-3",
                rom_ids: [3],
                status: "removed",
                message: "Removed with a warning.",
                warnings: ["A save was kept aside."],
                warning_count: 1,
                warnings_omitted: false,
              },
            ],
          }),
        );
      });
      expect(section.container.textContent).toContain(
        "The run stopped. · Nothing was removed. · Removed with a warning. · Warning: A save was kept aside.",
      );
      expect(section.container.textContent).not.toContain("Removed without a word.");
      expect(section.container.textContent).not.toContain("omitted");
    });

    it("names each game whose result it shows, as the dialog does", () => {
      const section = render(createElement(RemovedGamesCleanupSection));
      act(() => {
        beginPrunePreview("preview-1");
        beginPruneRun("run-1", "preview-1");
        setPruneComplete(
          finishedRun({
            results: [
              {
                group_id: "g-1",
                name: "Celeste",
                rom_ids: [1],
                status: "skipped",
                reason: "game_running",
                message: "This game is running, so it was left as it is. Quit it and scan again to remove it.",
              },
            ],
          }),
        );
      });

      expect(section.container.textContent).toContain(
        "Celeste: This game is running, so it was left as it is. Quit it and scan again to remove it.",
      );
    });

    it("counts omitted warnings on Data Management", () => {
      const section = render(createElement(RemovedGamesCleanupSection));
      act(() => {
        beginPrunePreview("preview-1");
        beginPruneRun("run-1", "preview-1");
        setPruneComplete(
          finishedRun({
            results: [
              {
                group_id: "g-1",
                rom_ids: [1],
                status: "removed",
                message: "Removed.",
                warnings: ["One", "Two", "Three", "Four", "Five"],
                warning_count: 7,
                warnings_omitted: true,
              },
            ],
          }),
        );
      });

      expect(section.container.textContent).toContain("Warning: Five · 2 additional warning(s) omitted.");
    });
  });

  it("blocks confirmation when a selected installed ROM has no measurable size", async () => {
    vi.mocked(backend.getPrunePreview).mockResolvedValue({
      ...preview,
      free_bytes: 1_000_000,
      items: [{ ...preview.items![0]!, installed_bytes: null }],
    });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    const confirm = confirmButton(modal);
    // Unselected, an unmeasurable ROM blocks nothing — it is simply not copied.
    expect(confirm.disabled).toBe(false);
    expect(modal.container.textContent).not.toContain("no safe measurable size");

    fireEvent.click(contentToggleFor(modal, "Removed Game.gba"));

    // Selected for recovery, its required space cannot be proven, so the run is
    // refused rather than started on an unbacked estimate.
    fireEvent.click(confirm);
    await act(async () => Promise.resolve());
    expect(backend.startPrune).not.toHaveBeenCalled();
    expect(modal.container.textContent).toContain("A selected installed ROM has no safe measurable size.");
    expect(modal.container.textContent).not.toContain("Not enough free space.");
  });

  it("counts only removable rows in the headline and lists a sibling as on RomM", async () => {
    vi.mocked(backend.getPrunePreview).mockResolvedValue({
      ...preview,
      total: 2,
      candidate_total: 1,
      items: [
        { ...preview.items![0]!, group_size: 2 },
        {
          ...preview.items![0]!,
          rom_id: 8,
          candidate: false,
          gone_from_romm: false,
          installed: false,
          installed_bytes: null,
          fs_name: "Live sibling.gba",
          fs_name_no_ext: "Live sibling",
          group_size: 2,
        },
      ],
    });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    const text = modal.container.textContent;

    // The headline must not inflate itself with the sibling RomM still serves.
    expect(text).toContain("RomM no longer has 1 version this device still holds.");
    expect(text).not.toContain("RomM no longer has 2 versions");
    expect(rowFor(modal, "Live sibling.gba").textContent).toContain("On RomM");
  });

  it("keeps every row listed with whole-game removal switched off, and changes only their outcomes", async () => {
    vi.mocked(backend.getPrunePreview).mockResolvedValue({
      ...preview,
      total: 3,
      candidate_total: 2,
      items: [
        { ...preview.items![0]!, group_size: 2 },
        {
          ...preview.items![0]!,
          rom_id: 8,
          candidate: false,
          gone_from_romm: false,
          installed: false,
          installed_bytes: null,
          fs_name: "Live sibling.gba",
          fs_name_no_ext: "Live sibling",
          group_size: 2,
        },
        { ...rowAs(9, "Dead Game"), installed: false, installed_bytes: null },
      ],
    });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    expect(rowFor(modal, "Dead Game").textContent).toContain("Game removed");

    fireEvent.click(optionToggleFor(modal, OPTION_LABELS.removeDeadGames));

    expect(rowFor(modal, "Live sibling.gba").textContent).toContain("Stays");
    expect(rowFor(modal, "Dead Game").textContent).toContain("Nothing removed");
    const dead = rowFor(modal, "Dead Game.gba");
    expect(dead.textContent).toContain("Kept");
    expect(dead.textContent).toContain("“Remove games that are gone entirely” is off.");
    // The headline counted candidates only, so it does not move.
    expect(modal.container.textContent).toContain("RomM no longer has 2 versions this device still holds.");
  });

  it("stages and counts no backup for a file the run no longer deletes once an option keeps its row", async () => {
    vi.mocked(backend.getPrunePreview).mockResolvedValue({
      ...preview,
      total: 3,
      candidate_total: 2,
      free_bytes: 10_000,
      items: [
        { ...preview.items![0]!, group_size: 2, installed: false, installed_bytes: null },
        {
          ...preview.items![0]!,
          rom_id: 8,
          candidate: false,
          gone_from_romm: false,
          installed: false,
          installed_bytes: null,
          fs_name: "Live sibling.gba",
          fs_name_no_ext: "Live sibling",
          group_size: 2,
        },
        { ...rowAs(9, "Dead Game"), installed_bytes: 512 },
      ],
    });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    fireEvent.click(contentToggleFor(modal, "Dead Game.gba"));
    expect(modal.container.textContent).toContain("Selected: 512 B of 512 B");

    fireEvent.click(optionToggleFor(modal, OPTION_LABELS.removeDeadGames));
    expect(within(rowFor(modal, "Dead Game.gba")).queryAllByTestId("toggle")).toHaveLength(0);
    expect(modal.container.textContent).not.toContain("Selected: 512 B");

    // A selection the run would not act on must not be staged behind the user's back.
    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(backend.startPrune).toHaveBeenCalled());
    expect(backend.stagePruneInstalledSelection).not.toHaveBeenCalled();
    expect(vi.mocked(backend.startPrune).mock.calls[0]?.[0].installed_selection_id).toBeNull();
  });

  it("states what each option takes and what the backup keeps", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    const description = (label: string) =>
      optionToggleFor(modal, label).closest('[data-testid="toggle"]')!.querySelector('[data-testid="toggle-desc"]')!
        .textContent;

    expect(description(OPTION_LABELS.removeRows)).toBe("Of games that still have another version on RomM.");
    expect(description(OPTION_LABELS.recovery)).toBe(
      "Saves, playtime and the shortcut’s Steam details, under /home/deck/romm-tender-recovery.",
    );
    expect(description(OPTION_LABELS.backUpFiles)).toBe("Selected: 0 B of 200 B · 100 B free");
  });

  it("names the shortcut removal as conditional on the game having one", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    // A game gone entirely may have no shortcut at all, so the option must not
    // promise one is removed.
    const option = optionToggleFor(modal, OPTION_LABELS.removeDeadGames).closest('[data-testid="toggle"]')!;
    expect(option.textContent).toContain("where it has one");
    expect(option.textContent).not.toContain("With their Steam shortcut");
  });

  it("disables the file backup and says why when the run would delete no downloaded file", async () => {
    vi.mocked(backend.getPrunePreview).mockResolvedValue({
      ...preview,
      items: [{ ...preview.items![0]!, installed: false, installed_bytes: null }],
    });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    const option = optionToggleFor(modal, OPTION_LABELS.backUpFiles).closest('[data-testid="toggle"]')!;
    expect(option.getAttribute("data-disabled")).toBe("true");
    expect(option.textContent).toContain("No downloaded file would be deleted, so there is nothing to back up.");
    expect(modal.container.textContent).not.toContain("Selected: ");
  });

  it("claims no file is deleted only once every page has arrived", async () => {
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce({
        ...preview,
        total: 2,
        items: [{ ...preview.items![0]!, installed: false, installed_bytes: null }],
      })
      .mockReturnValueOnce(pendingPage().promise);
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    // An unloaded page could still hold a file the run deletes.
    expect(modal.container.textContent).not.toContain("No downloaded file would be deleted");
    expect(modal.container.textContent).toContain("Selected: 0 B of 0 B");
    fireEvent.click(confirmButton(modal));
    await act(async () => Promise.resolve());
    expect(backend.startPrune).not.toHaveBeenCalled();
  });

  it("keeps the confirm dialog to one scroll region so the space line cannot cover the last row", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    const scrollers = [...modal.container.querySelectorAll<HTMLElement>("div")].filter(
      (el) => el.style.overflowY === "auto",
    );
    expect(scrollers).toHaveLength(1);
    // The space line scrolls WITH the list rather than sitting pinned over it.
    expect(scrollers[0]!.textContent).toContain("Removed Game");
    expect(scrollers[0]!.textContent).toContain("Selected: ");
  });

  it("brings the intro back into view when the first option takes focus", async () => {
    vi.useFakeTimers();
    try {
      await openRemovedGamesCleanupModal();
      const modal = render(shownModal());
      const body = [...modal.container.querySelectorAll<HTMLElement>("div")].find(
        (el) => el.style.overflowY === "auto",
      )!;
      Object.defineProperty(body, "scrollHeight", { value: 2000, configurable: true });
      Object.defineProperty(body, "clientHeight", { value: 600, configurable: true });
      const scrollTo = vi.fn();
      body.scrollTo = scrollTo as unknown as typeof body.scrollTo;

      fireEvent.focusIn(optionToggleFor(modal, OPTION_LABELS.removeRows));
      expect(scrollTo).not.toHaveBeenCalled();
      act(() => {
        vi.runAllTimers();
      });

      // Steam's focus engine stops at the control itself, stranding the intro
      // above it off-screen on a controller.
      expect(scrollTo).toHaveBeenCalledExactlyOnceWith({ top: 0, behavior: "smooth" });
    } finally {
      vi.useRealTimers();
    }
  });

  it("says why Confirm is unavailable instead of leaving a dead control", async () => {
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce({ ...preview, total: 2 })
      .mockReturnValueOnce(pendingPage().promise);
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    // The dialog scrolls, so the reason has to be readable from where the button is.
    expect(confirmButton(modal).disabled).toBe(false);
    expect(modal.container.textContent).toContain(
      "The list is still loading (1 of 2 entries). A cleanup can start once every entry has arrived.",
    );
    // Pressable, but locked while the second page is still pending.
    fireEvent.click(confirmButton(modal));
    await act(async () => Promise.resolve());
    expect(backend.startPrune).not.toHaveBeenCalled();
  });

  it("reports a locally refused Confirm in the dialog and the log", async () => {
    vi.mocked(backend.getPrunePreview)
      .mockResolvedValueOnce({ ...preview, total: 2 })
      .mockReturnValueOnce(pendingPage().promise);
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    fireEvent.click(confirmButton(modal));
    await act(async () => Promise.resolve());

    expect(logs.info).toHaveBeenCalledWith(expect.stringContaining("[prune] Confirm pressed"));
    expect(logs.warn).toHaveBeenCalledWith(expect.stringContaining("[prune] Confirm refused locally"));
    expect(backend.startPrune).not.toHaveBeenCalled();
  });

  it("logs the confirm press and the accepted run id", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));

    expect(logs.info).toHaveBeenCalledWith(expect.stringContaining("[prune] Confirm pressed"));
    expect(logs.info).toHaveBeenCalledWith("[prune] startPrune accepted: run=run-1");
  });

  it("logs a backend refusal alongside the message it shows", async () => {
    vi.mocked(backend.startPrune).mockResolvedValue({
      success: false,
      reason: "prune_active",
      message: "A removed-game cleanup is already running.",
    });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("already running"));

    expect(logs.warn).toHaveBeenCalledWith(expect.stringContaining("reason=prune_active"));
  });

  it("does not claim a run is progressing when the store refused to adopt it", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    // Another preview claimed the pending slot between opening and confirming.
    act(() => beginPrunePreview("preview-2"));

    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("lost track of it"));

    // The run is executing; its frames can never be admitted, so "running..."
    // would promise a progress line that cannot arrive.
    expect(modal.container.textContent).not.toContain("Cleanup running...");
    expect(getPruneState().runId).toBeNull();
    expect(logs.error).toHaveBeenCalledWith(expect.stringContaining("no longer pending"));
  });

  it("surfaces a success response that carries no run id instead of wedging admission", async () => {
    vi.mocked(backend.startPrune).mockResolvedValue({ success: true, status: "running" });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());

    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("carried no run id"));

    // No run was adopted by id, and the control stays usable for a retry.
    expect(getPruneState().runId).toBeNull();
    expect(confirmButton(modal).disabled).toBe(false);
  });

  it("rejects a scan response that carries no preview id", async () => {
    const malformed: backend.PrunePreviewResult = { ...preview };
    delete malformed.preview_id;
    vi.mocked(backend.getPrunePreview).mockResolvedValue(malformed);

    await expect(openRemovedGamesCleanupModal()).rejects.toThrow("carried no preview id");
    expect(showModal).not.toHaveBeenCalled();
  });

  it("keeps Data Management showing a run that has not emitted progress yet", async () => {
    const section = render(createElement(RemovedGamesCleanupSection));
    const button = section.getByRole("button", { name: "Clean Up Removed RomM Games" }) as HTMLButtonElement;
    expect(button.disabled).toBe(false);

    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-1", "preview-1");
    });

    // The window between start and the first frame is exactly where a second
    // scan would collide, so the entry point has to close on the run id.
    expect(button.disabled).toBe(true);
    expect(section.container.textContent).toContain("Cleanup starting...");
    expect(section.container.textContent).toContain("A cleanup is running.");
  });

  it("shows a sealed recovery bundle's whole path on Data Management", () => {
    const bundlePath = `/home/deck/romm-tender-recovery/bundles/${"A-Very-Long-Game-Name-".repeat(20)}2026-07-31_07f4953b`;
    const section = render(createElement(RemovedGamesCleanupSection));

    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-1", "preview-1");
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current: 1,
        total: 2,
        stage: "creating_recovery",
        rom_ids: [7],
        name: "Removed Game",
        bundle_path: bundlePath,
      });
    });

    expect(section.container.textContent).toContain(`Recovery sealed: ${bundlePath}`);
  });

  it("offers a Stop control on Data Management and sets expectations for it", async () => {
    vi.mocked(backend.cancelPrune).mockResolvedValue({ success: true, message: "ok", already_cancelling: false });
    const section = render(createElement(RemovedGamesCleanupSection));
    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-7", "preview-1");
    });

    // The copy must not promise a rollback the backend never performs.
    expect(section.container.textContent).toContain("The one being processed now finishes");

    fireEvent.click(section.getByRole("button", { name: "Stop Cleanup" }));
    await waitFor(() => expect(backend.cancelPrune).toHaveBeenCalledWith("run-7"));
    expect(logs.info).toHaveBeenCalledWith("[prune] Cancel pressed for run run-7");
  });

  it("surfaces a refused cancellation instead of pretending the run stopped", async () => {
    vi.mocked(backend.cancelPrune).mockResolvedValue({
      success: false,
      reason: "stale_run",
      message: "That cleanup run is not running.",
    });
    const section = render(createElement(RemovedGamesCleanupSection));
    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-7", "preview-1");
    });

    fireEvent.click(section.getByRole("button", { name: "Stop Cleanup" }));

    await waitFor(() => expect(section.container.textContent).toContain("That cleanup run is not running."));
    expect(logs.warn).toHaveBeenCalledWith(expect.stringContaining("reason=stale_run"));
  });

  it("reports a thrown cancellation rather than leaving the button spinning", async () => {
    vi.mocked(backend.cancelPrune).mockRejectedValue(new Error("bridge offline"));
    const section = render(createElement(RemovedGamesCleanupSection));
    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-7", "preview-1");
    });

    fireEvent.click(section.getByRole("button", { name: "Stop Cleanup" }));

    await waitFor(() => expect(section.container.textContent).toContain("Could not request cancellation"));
    expect((section.getByRole("button", { name: "Stop Cleanup" }) as HTMLButtonElement).disabled).toBe(false);
    expect(logs.error).toHaveBeenCalledWith(expect.stringContaining("bridge offline"));
  });

  it("offers Stop inside the modal while a run is progressing", async () => {
    vi.mocked(backend.cancelPrune).mockResolvedValue({ success: true, message: "ok", already_cancelling: false });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));

    act(() => {
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current: 1,
        total: 2,
        stage: "checking",
        rom_ids: [7],
        name: "Removed Game",
      });
    });

    fireEvent.click(modal.getByRole("button", { name: "Stop Cleanup" }));
    await waitFor(() => expect(backend.cancelPrune).toHaveBeenCalledWith("run-1"));
  });

  it("renders a real progress bar with the phase in words", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));

    act(() => {
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current: 1,
        total: 4,
        stage: "creating_recovery",
        rom_ids: [7],
        name: "Removed Game",
      });
    });

    // A stage slug is not a sentence — the bar and the phase have to be readable.
    expect(modal.container.textContent).toContain("Backing up — Removed Game");
    expect(modal.container.textContent).toContain("1 / 4");
    // The first group is in flight, so nothing is behind the run yet.
    expect(modal.container.querySelector('[data-testid="progress-progress"]')?.textContent).toBe("0");
    expect(modal.container.querySelector('[data-testid="progress-indeterminate"]')?.textContent).toBe("false");
  });

  // The bar answers "how much of this run is behind me", so the group being
  // worked on is not counted until a later frame moves past it. Filling by the
  // RUNNING group put a one-group run at 100% from its first frame and parked a
  // multi-minute content backup under a full bar.
  it.each([
    { current: 1, total: 2, fill: "0" },
    { current: 2, total: 2, fill: "50" },
    { current: 1, total: 1, fill: "0" },
  ])("fills the bar with the groups already finished ($current of $total)", ({ current, total, fill }) => {
    const section = render(createElement(RemovedGamesCleanupSection));
    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-1", "preview-1");
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current,
        total,
        stage: "creating_recovery",
        rom_ids: [7],
        name: "Removed Game",
      });
    });

    expect(section.container.querySelector('[data-testid="progress-progress"]')?.textContent).toBe(fill);
    expect(section.container.querySelector('[data-testid="progress-indeterminate"]')?.textContent).toBe("false");
    // The caption keeps naming the group in flight — only the bar counts finished ones.
    expect(section.container.textContent).toContain(`${current} / ${total}`);
  });

  it.each([
    { current: 0, total: 2, fill: "0" },
    { current: 5, total: 2, fill: "100" },
  ])("keeps an out-of-order frame inside the bar ($current of $total)", ({ current, total, fill }) => {
    const section = render(createElement(RemovedGamesCleanupSection));
    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-1", "preview-1");
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current,
        total,
        stage: "checking",
        rom_ids: [7],
        name: "Removed Game",
      });
    });

    expect(section.container.querySelector('[data-testid="progress-progress"]')?.textContent).toBe(fill);
  });

  it("fills the Data Management bar only when the run reaches its terminal frame", () => {
    const section = render(createElement(RemovedGamesCleanupSection));
    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-1", "preview-1");
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current: 2,
        total: 2,
        stage: "removing",
        rom_ids: [7],
        name: "Removed Game",
      });
    });

    expect(section.container.querySelector('[data-testid="progress-progress"]')?.textContent).toBe("50");

    act(() => {
      setPruneComplete({
        success: true,
        partial: false,
        run_id: "run-1",
        preview_id: "preview-1",
        removed_rom_ids: [7],
        affected_app_ids: [],
        results: [],
      });
    });

    // The run is over, so the bar is full — and the summary beside it says how
    // it ended. A bar that only ever disappears never reports being finished.
    expect(section.container.querySelector('[data-testid="progress-progress"]')?.textContent).toBe("100");
    expect(section.container.textContent).toContain("1 removed");
  });

  it("fills the modal bar when the run reaches its terminal frame", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));

    act(() => {
      setPruneComplete({
        success: false,
        partial: true,
        run_id: "run-1",
        preview_id: "preview-1",
        reason: "cancelled",
        message: "Cleanup was cancelled.",
        removed_rom_ids: [],
        affected_app_ids: [],
        results: [],
      });
    });

    // Full means the run has ended, not that it did everything — a cancelled
    // run gets the same last frame, with its outcome spelled out next to it.
    expect(modal.container.querySelector('[data-testid="progress-progress"]')?.textContent).toBe("100");
    expect(modal.container.textContent).toContain("cancelled: Cleanup was cancelled.");
  });

  it("keeps the bar indeterminate while the run has no group total", () => {
    const section = render(createElement(RemovedGamesCleanupSection));
    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-1", "preview-1");
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current: 0,
        total: 0,
        stage: "checking",
        rom_ids: [],
        name: "",
      });
    });

    // Nothing to divide by: an empty bar would claim a run that has not started.
    expect(section.container.querySelector('[data-testid="progress-indeterminate"]')?.textContent).toBe("true");
    expect(section.container.querySelector('[data-testid="progress-progress"]')?.textContent).toBe("undefined");
  });

  // Every stage services/prune/executor.py emits, from its emit_progress call
  // sites. A caption is only correct if it survives a real run, so each one is
  // driven through the component rather than asserted against the map alone.
  const BACKEND_STAGES = [
    "checking",
    "creating_recovery",
    "recovery_sealed",
    "removing_shortcut",
    "removing",
    "removed",
  ];

  it("maps exactly the stages the backend emits — no more, no fewer", () => {
    // An entry for a stage that is never emitted is how three real captions
    // ended up rendering as raw slugs while the map looked complete.
    expect(Object.keys(STAGE_LABELS).sort()).toEqual([...BACKEND_STAGES].sort());
  });

  it.each(BACKEND_STAGES)("renders a plain-language caption for the %s stage", (stage) => {
    const section = render(createElement(RemovedGamesCleanupSection));
    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-1", "preview-1");
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current: 1,
        total: 2,
        stage,
        rom_ids: [7],
        name: "Removed Game",
      });
    });

    expect(section.container.textContent).toContain(`${STAGE_LABELS[stage]} — Removed Game`);
    // The raw slug must never reach the caption for a stage we do know.
    expect(section.container.textContent).not.toContain(stage.replace(/_/g, " ") + " — Removed Game");
  });

  it("names the running stage in words on the Confirm button", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));

    act(() => {
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current: 1,
        total: 2,
        stage: "creating_recovery",
        rom_ids: [7],
        name: "Removed Game",
      });
    });

    expect(modal.getByRole("button", { name: "Backing up..." })).toBeTruthy();
    expect(modal.queryByRole("button", { name: "creating recovery..." })).toBeNull();
  });

  it("degrades an unknown backend stage to something readable", async () => {
    const section = render(createElement(RemovedGamesCleanupSection));
    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-1", "preview-1");
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current: 2,
        total: 3,
        stage: "some_new_stage",
        rom_ids: [7],
        name: "Removed Game",
      });
    });

    // A stage the frontend has not learned yet must still say something.
    expect(section.container.textContent).toContain("some new stage — Removed Game");
    expect(section.container.querySelector('[data-testid="progress"]')).toBeTruthy();
  });

  it("leads a result line with the game's name, not its metadata key", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));

    act(() => {
      setPruneComplete({
        success: false,
        partial: false,
        run_id: "run-1",
        preview_id: "preview-1",
        removed_rom_ids: [],
        affected_app_ids: [],
        results: [
          {
            group_id: "igdb:1217:53",
            name: "Shenmue II",
            rom_ids: [4375],
            status: "skipped",
            reason: "liveness_uncertain",
            message: "RomM could not confirm 1 of this game's version(s); nothing was removed.",
          },
        ],
      });
    });

    // A metadata key in front of plain-language copy undoes the copy.
    expect(modal.container.textContent).toContain("Shenmue II: RomM could not confirm");
    expect(modal.container.textContent).not.toContain("igdb:1217:53");
  });

  it("falls back to the group key when a result carries no name", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));

    act(() => {
      setPruneComplete({
        success: false,
        partial: false,
        run_id: "run-1",
        preview_id: "preview-1",
        removed_rom_ids: [],
        affected_app_ids: [],
        results: [
          {
            group_id: "igdb:1217:53",
            rom_ids: [4375],
            status: "skipped",
            message: "Nothing was removed.",
          },
        ],
      });
    });

    // Identifying the group badly beats not identifying it at all.
    expect(modal.container.textContent).toContain("igdb:1217:53: Nothing was removed.");
  });

  it("says a bundle was left behind when a group backed up but removed nothing", async () => {
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));

    act(() => {
      setPruneComplete({
        success: true,
        partial: false,
        run_id: "run-1",
        preview_id: "preview-1",
        removed_rom_ids: [],
        affected_app_ids: [],
        results: [
          {
            group_id: "group-1",
            rom_ids: [7],
            status: "removed",
            message: "Cancelled after the backup sealed.",
            bundle_path: "/home/deck/romm-tender-recovery/bundles/Shenmue-II_2026-07-31_07f4953b",
            removed_rom_ids: [],
          },
        ],
      });
    });

    // Otherwise the leftover folder is a mystery the user has to reverse-engineer.
    expect(modal.container.textContent).toContain("Backup created, nothing removed.");
    expect(modal.container.textContent).toContain("Shenmue-II_2026-07-31_07f4953b");
  });

  it("keeps Stop locked after the cancel endpoint call resolves, until the run ends", async () => {
    vi.mocked(backend.cancelPrune).mockResolvedValue({ success: true, message: "ok", already_cancelling: false });
    const section = render(createElement(RemovedGamesCleanupSection));
    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-7", "preview-1");
    });

    fireEvent.click(section.getByRole("button", { name: "Stop Cleanup" }));
    await act(async () => Promise.resolve());

    // The endpoint call has resolved. That means the request was received, NOT that
    // the run stopped — tying the lock to it let seven presses through in three
    // seconds on device.
    expect(backend.cancelPrune).toHaveBeenCalledTimes(1);
    const stop = section.getByRole("button", { name: "Stopping..." }) as HTMLButtonElement;
    expect(stop.disabled).toBe(true);
    expect(section.container.textContent).toContain("finishing the current safe step");

    act(() => {
      setPruneComplete({
        success: true,
        partial: false,
        run_id: "run-7",
        preview_id: "preview-1",
        removed_rom_ids: [],
        affected_app_ids: [],
        results: [],
      });
    });

    // The terminal frame is the exit — the control is gone with the run.
    expect(section.queryByRole("button", { name: "Stopping..." })).toBeNull();
    expect(section.queryByRole("button", { name: "Stop Cleanup" })).toBeNull();
  });

  it("does not re-arm Stop for a run that never stops reporting", async () => {
    vi.mocked(backend.cancelPrune).mockResolvedValue({ success: true, message: "ok", already_cancelling: false });
    const section = render(createElement(RemovedGamesCleanupSection));
    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-7", "preview-1");
    });

    fireEvent.click(section.getByRole("button", { name: "Stop Cleanup" }));
    await act(async () => Promise.resolve());
    // Progress keeps arriving while the in-flight group finishes; that is not
    // an invitation to press again.
    act(() => {
      setPruneProgress({
        run_id: "run-7",
        preview_id: "preview-1",
        current: 2,
        total: 3,
        stage: "removing",
        rom_ids: [7],
        name: "Removed Game",
      });
    });

    expect((section.getByRole("button", { name: "Stopping..." }) as HTMLButtonElement).disabled).toBe(true);
    expect(backend.cancelPrune).toHaveBeenCalledTimes(1);
  });

  it("re-opens Stop when the backend says the run is not running", async () => {
    vi.mocked(backend.cancelPrune).mockResolvedValue({
      success: false,
      reason: "stale_run",
      message: "That cleanup run is not running.",
    });
    const section = render(createElement(RemovedGamesCleanupSection));
    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-7", "preview-1");
    });

    fireEvent.click(section.getByRole("button", { name: "Stop Cleanup" }));
    await waitFor(() => expect(section.container.textContent).toContain("That cleanup run is not running."));

    // A refusal is the one outcome with no terminal frame behind it, so it must
    // re-open the control rather than leave it locked forever.
    expect((section.getByRole("button", { name: "Stop Cleanup" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("keeps the modal's Stop locked after its endpoint call resolves", async () => {
    vi.mocked(backend.cancelPrune).mockResolvedValue({ success: true, message: "ok", already_cancelling: false });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));
    act(() => {
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current: 1,
        total: 2,
        stage: "checking",
        rom_ids: [7],
        name: "Removed Game",
      });
    });

    fireEvent.click(modal.getByRole("button", { name: "Stop Cleanup" }));
    await act(async () => Promise.resolve());

    expect(backend.cancelPrune).toHaveBeenCalledTimes(1);
    expect((modal.getByRole("button", { name: "Stopping..." }) as HTMLButtonElement).disabled).toBe(true);
    expect(modal.container.textContent).toContain("finishing the current safe step");
  });

  it("puts the throbber beside Scanning… while the scan for removed games runs", async () => {
    vi.mocked(backend.getPrunePreview).mockReturnValue(new Promise(() => {}));
    const section = render(createElement(RemovedGamesCleanupSection));
    expect(section.queryByTestId("spinner")).toBeNull();

    fireEvent.click(section.getByRole("button", { name: "Clean Up Removed RomM Games" }));
    await act(async () => Promise.resolve());

    expect(throbberIn(section.getByRole("button", { name: "Scanning…" }))).not.toBeNull();
  });

  it("puts the throbber beside Cleanup starting... until the run's first frame brings its bar", () => {
    const section = render(createElement(RemovedGamesCleanupSection));
    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-1", "preview-1");
    });
    expect(throbberIn(lineWithText(section.container, "Cleanup starting..."))).not.toBeNull();

    act(() => {
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current: 1,
        total: 3,
        stage: "checking",
        rom_ids: [7],
        name: "Removed Game",
      });
    });
    expect(section.container.textContent).not.toContain("Cleanup starting...");
    expect(section.queryByTestId("spinner")).toBeNull();
  });

  it("puts the throbber beside the section's Stopping... until the run ends", async () => {
    vi.mocked(backend.cancelPrune).mockResolvedValue({ success: true, message: "ok", already_cancelling: false });
    const section = render(createElement(RemovedGamesCleanupSection));
    act(() => {
      beginPrunePreview("preview-1");
      beginPruneRun("run-7", "preview-1");
      setPruneProgress({
        run_id: "run-7",
        preview_id: "preview-1",
        current: 1,
        total: 3,
        stage: "removing",
        rom_ids: [7],
        name: "Removed Game",
      });
    });
    expect(throbberIn(section.getByRole("button", { name: "Stop Cleanup" }))).toBeNull();

    fireEvent.click(section.getByRole("button", { name: "Stop Cleanup" }));
    await act(async () => Promise.resolve());
    expect(throbberIn(section.getByRole("button", { name: "Stopping..." }))).not.toBeNull();

    act(() => {
      setPruneComplete({
        success: true,
        partial: false,
        run_id: "run-7",
        preview_id: "preview-1",
        removed_rom_ids: [],
        affected_app_ids: [],
        results: [],
      });
    });
    expect(section.queryByTestId("spinner")).toBeNull();
  });

  it("puts the throbber beside the dialog's Starting... and its status line until the run's bar takes over", async () => {
    let answerStart: ((result: { success: true; run_id: string; status: "running" }) => void) | undefined;
    vi.mocked(backend.startPrune).mockReturnValue(
      new Promise((resolve) => {
        answerStart = resolve;
      }),
    );
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    expect(modal.queryByTestId("spinner")).toBeNull();

    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("Starting cleanup..."));
    expect(throbberIn(modal.getByRole("button", { name: "Starting..." }))).not.toBeNull();
    expect(throbberIn(lineWithText(modal.container, "Starting cleanup..."))).not.toBeNull();

    await act(async () => {
      answerStart?.({ success: true, run_id: "run-1", status: "running" });
      await Promise.resolve();
    });
    await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));
    expect(throbberIn(lineWithText(modal.container, "Cleanup running..."))).not.toBeNull();

    act(() => {
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current: 1,
        total: 2,
        stage: "checking",
        rom_ids: [7],
        name: "Removed Game",
      });
    });
    expect(lineWithText(modal.container, "Cleanup running...")).not.toBeUndefined();
    expect(modal.queryByTestId("spinner")).toBeNull();
  });

  it("takes the throbber off the dialog's Starting... once a frame of the run brings its bar before the start answers", async () => {
    vi.mocked(backend.startPrune).mockReturnValue(new Promise(() => {}));
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.getByRole("button", { name: "Starting..." })).toBeTruthy());

    act(() => {
      setPruneProgress({
        run_id: "adopted-run",
        preview_id: "preview-1",
        current: 1,
        total: 2,
        stage: "checking",
        rom_ids: [7],
        name: "Removed Game",
      });
    });

    expect(modal.container.textContent).toContain("Checking with RomM — Removed Game");
    expect(modal.queryByTestId("spinner")).toBeNull();
  });

  it("puts the throbber beside the dialog's Stopping... while the stop is asked", async () => {
    vi.mocked(backend.cancelPrune).mockResolvedValue({ success: true, message: "ok", already_cancelling: false });
    await openRemovedGamesCleanupModal();
    const modal = render(shownModal());
    fireEvent.click(confirmButton(modal));
    await waitFor(() => expect(modal.container.textContent).toContain("Cleanup running..."));
    act(() => {
      setPruneProgress({
        run_id: "run-1",
        preview_id: "preview-1",
        current: 1,
        total: 2,
        stage: "checking",
        rom_ids: [7],
        name: "Removed Game",
      });
    });
    expect(throbberIn(modal.getByRole("button", { name: "Stop Cleanup" }))).toBeNull();

    fireEvent.click(modal.getByRole("button", { name: "Stop Cleanup" }));
    await act(async () => Promise.resolve());

    expect(throbberIn(modal.getByRole("button", { name: "Stopping..." }))).not.toBeNull();
  });

  it("recovers the entry point and warns when a run's result never arrives", async () => {
    vi.useFakeTimers();
    try {
      const section = render(createElement(RemovedGamesCleanupSection));
      const button = section.getByRole("button", { name: "Clean Up Removed RomM Games" }) as HTMLButtonElement;

      act(() => {
        beginPrunePreview("preview-1");
        beginPruneRun("run-lost", "preview-1");
        setPruneProgress({
          run_id: "run-lost",
          preview_id: "preview-1",
          current: 1,
          total: 2,
          stage: "removing_rows",
          rom_ids: [7],
          name: "Removed Game",
        });
      });
      expect(button.disabled).toBe(true);

      // The terminal chunk never lands (a dropped completion frame).
      await act(async () => {
        await vi.advanceTimersByTimeAsync(15 * 60_000);
      });

      expect(button.disabled).toBe(false);
      expect(section.container.textContent).toContain(
        "The cleanup result was lost — check your library and run the scan again.",
      );
      expect(section.container.textContent).not.toContain("removing rows");
    } finally {
      vi.useRealTimers();
    }
  });
});
