import { describe, it, expect } from "vitest";
import { isValidElement, type ReactNode } from "react";
import { desktopLaunchPrompts, desktopUnsyncedSavesDialog } from "./desktopDialogs";
import { DesktopCoreChangeDialog } from "./DesktopCoreChangeDialog";
import { DesktopFallbackLaunchDialog } from "./DesktopFallbackLaunchDialog";
import { DesktopOfflineDriftDialog } from "./DesktopOfflineDriftDialog";
import { DesktopSaveConflictDialog } from "./DesktopSaveConflictDialog";
import { DesktopSyncWaitDialog } from "./DesktopSyncWaitDialog";
import { DesktopUncheckedSourceDialog } from "./DesktopUncheckedSourceDialog";
import { DesktopUnsyncedSavesDialog } from "./DesktopUnsyncedSavesDialog";
import type { AskDialog } from "./askDialog";
import type { SyncConflict } from "../../../types";

/**
 * An `ask` that answers each question the way its dialog would: it calls the
 * drawn element's own `onChoice` or `onDone` with the next of `answers`, and
 * settles with whatever that hands to the `resolve` the prompt gave it. It
 * records what it was asked: the dismissal answer and the dialog it drew.
 */
function scriptedAsk(answers: unknown[]): { ask: AskDialog; asked: { dismissed: unknown; dialog: ReactNode }[] } {
  const asked: { dismissed: unknown; dialog: ReactNode }[] = [];
  const ask = (<T,>(dismissed: T, render: (resolve: (value: T) => void) => ReactNode): Promise<T> =>
    new Promise<T>((resolve) => {
      const dialog = render(resolve);
      asked.push({ dismissed, dialog });
      const props = isValidElement<{ onChoice?: (value: unknown) => void; onDone?: (value: unknown) => void }>(dialog)
        ? dialog.props
        : {};
      const answer = props.onChoice ?? props.onDone;
      if (!answer) throw new Error("the dialog has no onChoice or onDone");
      answer(answers.shift());
    })) as AskDialog;
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
  it.each([true, false])(
    "asks the core-change question through its dialog, dismissed as a no (answered %s)",
    async (choice) => {
      const { ask, asked } = scriptedAsk([choice]);

      expect(await desktopLaunchPrompts(ask).confirmCoreChange("Old", "New")).toBe(choice);
      expect(asked).toHaveLength(1);
      expect(first(asked).dismissed).toBe(false);
      expect(typeOf(first(asked).dialog)).toBe(DesktopCoreChangeDialog);
      const { dialog } = first(asked);
      expect(isValidElement<{ oldLabel?: string; newLabel?: string }>(dialog) && dialog.props).toMatchObject({
        oldLabel: "Old",
        newLabel: "New",
      });
    },
  );

  it("asks the offline-drift question through its dialog, dismissed as a cancel", async () => {
    const { ask, asked } = scriptedAsk(["retry"]);

    expect(await desktopLaunchPrompts(ask).askOfflineDrift()).toBe("retry");
    expect(first(asked).dismissed).toBe("cancel");
    expect(typeOf(first(asked).dialog)).toBe(DesktopOfflineDriftDialog);
  });

  it.each([true, false])(
    "asks the fallback question through its dialog with the sync's message, dismissed as a no (answered %s)",
    async (choice) => {
      const { ask, asked } = scriptedAsk([choice]);

      expect(await desktopLaunchPrompts(ask).confirmFallbackLaunch("Server said no")).toBe(choice);
      expect(first(asked).dismissed).toBe(false);
      expect(typeOf(first(asked).dialog)).toBe(DesktopFallbackLaunchDialog);
      const { dialog } = first(asked);
      expect(isValidElement<{ message?: string }>(dialog) && dialog.props.message).toBe("Server said no");
    },
  );

  it.each([true, false])(
    "asks the unchecked-source question through its dialog, dismissed as a no (answered %s)",
    async (choice) => {
      const { ask, asked } = scriptedAsk([choice]);

      expect(await desktopLaunchPrompts(ask).confirmUncheckedSource()).toBe(choice);
      expect(first(asked).dismissed).toBe(false);
      expect(typeOf(first(asked).dialog)).toBe(DesktopUncheckedSourceDialog);
    },
  );

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

describe("desktopUnsyncedSavesDialog", () => {
  it("asks a switch's unsynced-saves question through its dialog, dismissed as a cancel", async () => {
    const { ask, asked } = scriptedAsk(["sync_and_switch"]);
    const args = { versionName: "USA", stranding: "unsynced" as const, serverReachable: true, versionGone: false };

    expect(await desktopUnsyncedSavesDialog(ask)(args)).toBe("sync_and_switch");
    expect(asked).toHaveLength(1);
    expect(first(asked).dismissed).toBe("cancel");
    expect(typeOf(first(asked).dialog)).toBe(DesktopUnsyncedSavesDialog);
    const { dialog } = first(asked);
    expect(isValidElement(dialog) && dialog.props).toMatchObject(args);
  });
});
