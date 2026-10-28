import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { DesktopSaveConflictDialog } from "./DesktopSaveConflictDialog";
import { resolveSyncConflict } from "../../../api/backend";
import { showToast } from "../../../utils/toast";
import { CONFLICT_EXPLANATION, STALE_CONFLICT_MESSAGE } from "../../../utils/saveConflictFlow";
import type { SyncConflict } from "../../../types";

vi.mock("../../../api/backend", () => ({
  resolveSyncConflict: vi.fn(),
  getSaveStatus: vi.fn(),
  isEndpointFailure: vi.fn(() => false),
  logError: vi.fn(),
  debugLog: vi.fn(),
}));

vi.mock("../../../utils/toast", () => ({ showToast: vi.fn() }));

const CONFLICT: SyncConflict = {
  type: "sync_conflict",
  rom_id: 42,
  filename: "Game.srm",
  server_save_id: 9,
  server_updated_at: "2026-01-02T00:00:00Z",
  server_size: 2048,
  local_path: "/saves/Game.srm",
  local_hash: "abc",
  local_mtime: "2026-01-01T00:00:00Z",
  local_size: 0,
  created_at: "2026-01-01T00:00:00Z",
};

beforeEach(() => {
  vi.mocked(resolveSyncConflict).mockReset();
  vi.mocked(showToast).mockClear();
});

describe("DesktopSaveConflictDialog", () => {
  it("names the file and states both sides", () => {
    render(<DesktopSaveConflictDialog conflict={CONFLICT} beforeStart={false} onDone={vi.fn()} />);
    const dialog = screen.getByRole("dialog", { name: "Save conflict for Game.srm" });
    expect(dialog).toHaveTextContent(CONFLICT_EXPLANATION);
    expect(dialog).toHaveTextContent("Your local save");
    expect(dialog).toHaveTextContent("unknown · modified");
    expect(dialog).toHaveTextContent("Server save (id=9)");
    expect(dialog).toHaveTextContent("2.0 KB · uploaded");
  });

  it("closes with the side chosen once the resolution lands, and toasts it", async () => {
    vi.mocked(resolveSyncConflict).mockResolvedValueOnce({ success: true } as never);
    const onDone = vi.fn();
    render(<DesktopSaveConflictDialog conflict={CONFLICT} beforeStart={false} onDone={onDone} />);
    fireEvent.click(screen.getByRole("button", { name: "Keep Local" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledWith("keep_local"));
    expect(resolveSyncConflict).toHaveBeenCalledWith(42, "Game.srm", 9, "keep_local", false);
    expect(showToast).toHaveBeenCalledWith("Conflict resolved — kept your local save (uploaded to server).");
  });

  it("resolves a conflict the launch check found as one before the start", async () => {
    vi.mocked(resolveSyncConflict).mockResolvedValueOnce({ success: true } as never);
    const onDone = vi.fn();
    render(<DesktopSaveConflictDialog conflict={CONFLICT} beforeStart={true} onDone={onDone} />);
    fireEvent.click(screen.getByRole("button", { name: "Use Server" }));

    await waitFor(() => expect(onDone).toHaveBeenCalledWith("use_server"));
    expect(resolveSyncConflict).toHaveBeenCalledWith(42, "Game.srm", 9, "use_server", true);
  });

  it("stays open with the reason when the resolution is refused, and can be retried", async () => {
    vi.mocked(resolveSyncConflict)
      .mockResolvedValueOnce({ success: false, reason: "stale_conflict", message: "stale" } as never)
      .mockResolvedValueOnce({ success: true } as never);
    const onDone = vi.fn();
    render(<DesktopSaveConflictDialog conflict={CONFLICT} beforeStart={false} onDone={onDone} />);
    fireEvent.click(screen.getByRole("button", { name: "Use Server" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(STALE_CONFLICT_MESSAGE);
    expect(onDone).not.toHaveBeenCalled();
    expect(showToast).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Use Server" })).toBeEnabled();

    fireEvent.click(screen.getByRole("button", { name: "Use Server" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledWith("use_server"));
  });

  it("takes the refusal's reason away once it is retried", async () => {
    vi.mocked(resolveSyncConflict)
      .mockResolvedValueOnce({ success: false, reason: "stale_conflict", message: "stale" } as never)
      .mockImplementationOnce(() => new Promise(() => {}) as never);
    render(<DesktopSaveConflictDialog conflict={CONFLICT} beforeStart={false} onDone={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Use Server" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(STALE_CONFLICT_MESSAGE);

    fireEvent.click(screen.getByRole("button", { name: "Use Server" }));
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("cannot be left while a resolution is in flight", async () => {
    let settle!: (value: unknown) => void;
    vi.mocked(resolveSyncConflict).mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          settle = resolve;
        }) as never,
    );
    const onDone = vi.fn();
    render(<DesktopSaveConflictDialog conflict={CONFLICT} beforeStart={false} onDone={onDone} />);
    fireEvent.click(screen.getByRole("button", { name: "Keep Local" }));

    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Use Server" })).toBeDisabled();
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    expect(onDone).not.toHaveBeenCalled();

    await act(async () => {
      settle({ success: false, message: "Upload refused" });
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Upload refused");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onDone).toHaveBeenCalledWith("cancel");
  });

  it("Cancel resolves cancel without a backend call", () => {
    const onDone = vi.fn();
    render(<DesktopSaveConflictDialog conflict={CONFLICT} beforeStart={false} onDone={onDone} />);
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onDone).toHaveBeenCalledWith("cancel");
    expect(resolveSyncConflict).not.toHaveBeenCalled();
  });
});
