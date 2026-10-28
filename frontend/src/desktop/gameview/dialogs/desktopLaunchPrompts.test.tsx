import { describe, it, expect } from "vitest";
import { isValidElement, type ReactNode } from "react";
import { desktopLaunchPrompts } from "./desktopDialogs";
import { DesktopCoreChangeDialog } from "./DesktopCoreChangeDialog";
import { DesktopFallbackLaunchDialog } from "./DesktopFallbackLaunchDialog";
import { DesktopOfflineDriftDialog } from "./DesktopOfflineDriftDialog";
import { DesktopSaveConflictDialog } from "./DesktopSaveConflictDialog";
import { DesktopSyncWaitDialog } from "./DesktopSyncWaitDialog";
import { DesktopUncheckedSourceDialog } from "./DesktopUncheckedSourceDialog";
import type { AskDialog } from "./askDialog";
import type { SyncConflict } from "../../../types";

/**
 * An `ask` that answers each question with the next of `answers`, and records
 * what it was asked: the dismissal answer and the dialog it would draw.
 */
function scriptedAsk(answers: unknown[]): { ask: AskDialog; asked: { dismissed: unknown; dialog: ReactNode }[] } {
  const asked: { dismissed: unknown; dialog: ReactNode }[] = [];
  const ask = (<T,>(dismissed: T, render: (resolve: (value: T) => void) => ReactNode): Promise<T> => {
    asked.push({ dismissed, dialog: render(() => undefined) });
    return Promise.resolve(answers.shift() as T);
  }) as AskDialog;
  return { ask, asked };
}

function first<T>(items: T[]): T {
  const item = items[0];
  if (item === undefined) throw new Error("nothing was asked");
  return item;
}

const typeOf = (node: ReactNode): unknown => (isValidElement(node) ? node.type : undefined);

const conflict = (filename: string): SyncConflict => ({ filename }) as SyncConflict;

describe("desktopLaunchPrompts", () => {
  it("asks the core-change question through its dialog, dismissed as a no", async () => {
    const { ask, asked } = scriptedAsk([true]);

    expect(await desktopLaunchPrompts(ask).confirmCoreChange("Old", "New")).toBe(true);
    expect(asked).toHaveLength(1);
    expect(first(asked).dismissed).toBe(false);
    expect(typeOf(first(asked).dialog)).toBe(DesktopCoreChangeDialog);
    const { dialog } = first(asked);
    expect(isValidElement<{ oldLabel?: string; newLabel?: string }>(dialog) && dialog.props).toMatchObject({
      oldLabel: "Old",
      newLabel: "New",
    });
  });

  it("asks the offline-drift question through its dialog, dismissed as a cancel", async () => {
    const { ask, asked } = scriptedAsk(["retry"]);

    expect(await desktopLaunchPrompts(ask).askOfflineDrift()).toBe("retry");
    expect(first(asked).dismissed).toBe("cancel");
    expect(typeOf(first(asked).dialog)).toBe(DesktopOfflineDriftDialog);
  });

  it("asks the fallback question through its dialog with the sync's message, dismissed as a no", async () => {
    const { ask, asked } = scriptedAsk([true]);

    expect(await desktopLaunchPrompts(ask).confirmFallbackLaunch("Server said no")).toBe(true);
    expect(first(asked).dismissed).toBe(false);
    expect(typeOf(first(asked).dialog)).toBe(DesktopFallbackLaunchDialog);
    const { dialog } = first(asked);
    expect(isValidElement<{ message?: string }>(dialog) && dialog.props.message).toBe("Server said no");
  });

  it("asks the unchecked-source question through its dialog, dismissed as a no", async () => {
    const { ask, asked } = scriptedAsk([true]);

    expect(await desktopLaunchPrompts(ask).confirmUncheckedSource()).toBe(true);
    expect(first(asked).dismissed).toBe(false);
    expect(typeOf(first(asked).dialog)).toBe(DesktopUncheckedSourceDialog);
  });

  it("asks the sync-wait question through its dialog with the reason, dismissed as a cancel", async () => {
    const { ask, asked } = scriptedAsk(["play_anyway"]);
    const reason = { kind: "syncing" } as const;
    const finished = new Promise<never>(() => {});

    expect(await desktopLaunchPrompts(ask).askSyncWait(reason, finished)).toBe("play_anyway");
    expect(first(asked).dismissed).toBe("cancel");
    expect(typeOf(first(asked).dialog)).toBe(DesktopSyncWaitDialog);
    const { dialog } = first(asked);
    expect(isValidElement<{ reason?: unknown; finished?: unknown }>(dialog) && dialog.props).toMatchObject({
      reason,
      finished,
    });
  });

  it("walks the conflicts one dialog at a time and is resolved once each is answered", async () => {
    const { ask, asked } = scriptedAsk(["keep_local", "use_server"]);

    expect(await desktopLaunchPrompts(ask).resolveConflicts([conflict("a.srm"), conflict("b.srm")])).toBe("resolved");
    expect(asked.map((a) => typeOf(a.dialog))).toEqual([DesktopSaveConflictDialog, DesktopSaveConflictDialog]);
    expect(
      asked.map((a) =>
        isValidElement<{ conflict?: SyncConflict }>(a.dialog) ? a.dialog.props.conflict?.filename : null,
      ),
    ).toEqual(["a.srm", "b.srm"]);
    expect(asked.map((a) => isValidElement<{ beforeStart?: boolean }>(a.dialog) && a.dialog.props.beforeStart)).toEqual(
      [true, true],
    );
    expect(first(asked).dismissed).toBe("cancel");
  });

  it("stops the conflict walk at the first cancel", async () => {
    const { ask, asked } = scriptedAsk(["cancel", "keep_local"]);

    expect(await desktopLaunchPrompts(ask).resolveConflicts([conflict("a.srm"), conflict("b.srm")])).toBe("cancel");
    expect(asked).toHaveLength(1);
  });
});
