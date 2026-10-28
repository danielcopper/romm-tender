import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi } from "vitest";
import { act, render, screen, fireEvent } from "@testing-library/react";
import { DesktopSyncWaitDialog } from "./DesktopSyncWaitDialog";
import type { SyncFinish } from "../../../utils/launchGate";

const SYNCING = { kind: "syncing" } as const;
const never = (): Promise<SyncFinish> => new Promise(() => {});

function settleable(): { finished: Promise<SyncFinish>; finish: (value: SyncFinish) => Promise<void> } {
  let settle: (value: SyncFinish) => void = () => undefined;
  const finished = new Promise<SyncFinish>((resolve) => {
    settle = resolve;
  });
  return {
    finished,
    finish: async (value) => {
      settle(value);
      await act(async () => {
        await finished;
      });
    },
  };
}

describe("DesktopSyncWaitDialog", () => {
  it("names why the start waits and offers Wait, Play Anyway and Cancel", () => {
    render(<DesktopSyncWaitDialog reason={SYNCING} finished={never()} onChoice={vi.fn()} />);

    const dialog = screen.getByRole("dialog", { name: "Waiting for Save Sync" });
    expect(dialog).toHaveTextContent(
      "This game's saves are still syncing with RomM. Wait a little longer, or play with the saves on this device?",
    );
    expect(screen.getByRole("button", { name: "Wait" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Play Anyway" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it.each([
    ["Wait", "wait"],
    ["Play Anyway", "play_anyway"],
    ["Cancel", "cancel"],
  ])("answers %s with %s", (label, choice) => {
    const onChoice = vi.fn();
    render(<DesktopSyncWaitDialog reason={SYNCING} finished={never()} onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(onChoice).toHaveBeenCalledExactlyOnceWith(choice);
  });

  it("answers cancel on Escape", () => {
    const onChoice = vi.fn();
    render(<DesktopSyncWaitDialog reason={SYNCING} finished={never()} onChoice={onChoice} />);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onChoice).toHaveBeenCalledExactlyOnceWith("cancel");
  });

  it("turns into Saves Ready when the sync finishes, and starts nothing by itself", async () => {
    const onChoice = vi.fn();
    const { finished, finish } = settleable();
    render(<DesktopSyncWaitDialog reason={SYNCING} finished={finished} onChoice={onChoice} />);

    await finish("ready");

    expect(screen.getByRole("dialog", { name: "Saves Ready" })).toHaveTextContent("Your saves are ready. Play now?");
    expect(screen.queryByRole("button", { name: "Wait" })).not.toBeInTheDocument();
    expect(onChoice).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(onChoice).toHaveBeenCalledExactlyOnceWith("answered");
  });

  it("offers Cancel on Saves Ready too", async () => {
    const onChoice = vi.fn();
    const { finished, finish } = settleable();
    render(<DesktopSyncWaitDialog reason={SYNCING} finished={finished} onChoice={onChoice} />);

    await finish("ready");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    expect(onChoice).toHaveBeenCalledExactlyOnceWith("cancel");
  });

  it("closes with answered when the sync ends on an answer that needs acting on", async () => {
    const onChoice = vi.fn();
    const { finished, finish } = settleable();
    render(<DesktopSyncWaitDialog reason={SYNCING} finished={finished} onChoice={onChoice} />);

    await finish("answered");

    expect(onChoice).toHaveBeenCalledExactlyOnceWith("answered");
  });

  it("answers nothing for a sync that finishes after the dialog has gone", async () => {
    const onChoice = vi.fn();
    const { finished, finish } = settleable();
    const { unmount } = render(<DesktopSyncWaitDialog reason={SYNCING} finished={finished} onChoice={onChoice} />);

    unmount();
    await finish("answered");

    expect(onChoice).not.toHaveBeenCalled();
  });
});
