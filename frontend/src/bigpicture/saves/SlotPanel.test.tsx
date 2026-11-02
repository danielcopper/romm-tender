import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, fireEvent, act } from "@testing-library/react";
import { createElement } from "react";
import { SlotPanel } from "./SlotPanel";
import * as backend from "../../api/backend";
import { toaster } from "../../api/host";
import { showModal } from "@decky/ui";
import type { SaveStatus, SaveSlotSummary, SlotSaveFile } from "../../types";
import { getRommConnectionState, setRommConnectionState } from "../../utils/connectionState";

// showModal in the @decky/ui mock receives the <ConfirmModal> element. We
// capture it so tests can read `.props.onOK`, `.props.strDescription`, etc.
type AnyProps = Record<string, unknown> & { children?: unknown };
interface ConfirmModalProps {
  onOK?: () => void | Promise<void>;
  strDescription?: string;
  strTitle?: string;
  strOKButtonText?: string;
}

vi.mock("@decky/ui", () => {
  // ConfirmModal is just a marker component — never actually rendered in tests.
  // showModal captures the element and tests pull props off it directly.
  const ConfirmModal = (p: AnyProps) => createElement("div", {}, p.children as never);
  return {
    ConfirmModal,
    DialogButton: ({
      children,
      onClick,
      disabled,
    }: AnyProps & {
      onClick?: () => void;
      disabled?: boolean;
    }) => createElement("button", { onClick, disabled }, children as never),
    // Used transitively by InactiveSlotBody, which SlotPanel renders.
    Focusable: (p: AnyProps) => createElement("div", {}, p.children as never),
    showModal: vi.fn(),
  };
});

function lastConfirmModalProps(): ConfirmModalProps | null {
  const calls = vi.mocked(showModal).mock.calls;
  if (calls.length === 0) return null;
  const el = calls[calls.length - 1]?.[0] as { props?: ConfirmModalProps } | undefined;
  return el?.props ?? null;
}

// Stub VersionHistoryPanel so we don't need to wire its own endpoint mocks.
// We render a tiny marker so tests can assert it appeared per file.
vi.mock("./VersionHistoryPanel", () => ({
  VersionHistoryPanel: (p: { filename: string; isOffline: boolean }) =>
    createElement("div", { "data-testid": `vhp-${p.filename}`, "data-offline": String(p.isOffline) }),
}));

function makeSummary(overrides: Partial<SaveSlotSummary> = {}): SaveSlotSummary {
  return {
    slot: "default",
    source: "local",
    count: 0,
    latest_updated_at: null,
    ...overrides,
  };
}

function makeStatus(overrides: Partial<SaveStatus> = {}): SaveStatus {
  return {
    rom_id: 1,
    files: [],
    playtime: {
      total_seconds: 0,
      session_count: 0,
      last_session_start: null,
      last_session_duration_sec: null,
      last_played: null,
    },
    device_id: "dev",
    last_sync_check_at: null,
    ...overrides,
  };
}

/** The line the panel hands an active slot with no save. */
const NO_SAVE_LINE = "the no-save line the panel hands the slot";

function defaultProps(overrides: Partial<React.ComponentProps<typeof SlotPanel>> = {}) {
  return {
    romId: 1,
    slot: makeSummary(),
    isActive: false,
    defaultExpanded: false,
    saveStatus: null,
    conflicts: [],
    noSaveLine: NO_SAVE_LINE,
    isOffline: false,
    onSlotSwitched: vi.fn(),
    onVersionRestored: vi.fn(),
    onSlotDeleted: vi.fn(),
    onCopy: vi.fn(),
    ...overrides,
  };
}

const flushAsync = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });

describe("SlotPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("expand/collapse", () => {
    it("renders collapsed by default", () => {
      const { container } = render(<SlotPanel {...defaultProps()} />);
      expect(container.textContent).toContain("▸");
      expect(container.textContent).not.toContain("▾");
    });

    it("renders expanded when defaultExpanded is true", () => {
      const { container } = render(<SlotPanel {...defaultProps({ defaultExpanded: true })} />);
      expect(container.textContent).toContain("▾");
    });

    it("toggles expanded state when the header is clicked", async () => {
      const { container } = render(<SlotPanel {...defaultProps()} />);
      const header = container.querySelector("button");
      if (!header) throw new Error("no header button");
      fireEvent.click(header);
      await flushAsync();
      expect(container.textContent).toContain("▾");
      fireEvent.click(header);
      await flushAsync();
      expect(container.textContent).toContain("▸");
    });

    it("calls getSlotSaves exactly once on first expand for an inactive slot (caches)", async () => {
      vi.mocked(backend.getSlotSaves).mockResolvedValue({
        success: true,
        slot: "default",
        saves: [],
      });
      const { container } = render(<SlotPanel {...defaultProps()} />);
      const header = container.querySelector("button");
      if (!header) throw new Error("no header");
      fireEvent.click(header);
      await flushAsync();
      fireEvent.click(header); // collapse
      fireEvent.click(header); // expand again — should NOT refetch (cached)
      await flushAsync();
      expect(vi.mocked(backend.getSlotSaves)).toHaveBeenCalledTimes(1);
    });

    it("does NOT call getSlotSaves when expanding an active slot", async () => {
      const { container } = render(<SlotPanel {...defaultProps({ isActive: true, saveStatus: makeStatus() })} />);
      const header = container.querySelector("button");
      if (!header) throw new Error("no header");
      fireEvent.click(header);
      await flushAsync();
      expect(vi.mocked(backend.getSlotSaves)).not.toHaveBeenCalled();
    });

    it("falls back to an empty list when getSlotSaves returns success=false", async () => {
      vi.mocked(backend.getSlotSaves).mockResolvedValue({
        success: false,
        reason: "server_unreachable",
        message: "Server unreachable — check your URL and ensure RomM is running",
      });
      const { container } = render(<SlotPanel {...defaultProps()} />);
      fireEvent.click(container.querySelector("button")!);
      await flushAsync();
      expect(container.textContent).toContain("No saves in this slot");
    });

    it("falls back to an empty list when getSlotSaves throws", async () => {
      vi.mocked(backend.getSlotSaves).mockRejectedValue(new Error("network"));
      const { container } = render(<SlotPanel {...defaultProps()} />);
      fireEvent.click(container.querySelector("button")!);
      await flushAsync();
      expect(container.textContent).toContain("No saves in this slot");
    });
  });

  describe("active slot body", () => {
    it("renders one SaveFileRow + VersionHistoryPanel per file", () => {
      const status = makeStatus({
        files: [
          {
            filename: "a.srm",
            local_path: "/data/a.srm",
            local_hash: "h",
            local_mtime: "2025-06-15T10:00:00Z",
            local_size: 100,
            server_save_id: 1,
            server_file_name: null,
            server_emulator: null,
            server_updated_at: null,
            server_size: null,
            last_sync_at: null,
            status: "synced",
          },
          {
            filename: "b.srm",
            local_path: "/data/b.srm",
            local_hash: "h",
            local_mtime: "2025-06-15T10:00:00Z",
            local_size: 100,
            server_save_id: 2,
            server_file_name: null,
            server_emulator: null,
            server_updated_at: null,
            server_size: null,
            last_sync_at: null,
            status: "synced",
          },
        ],
      });
      const { container, queryByTestId } = render(
        <SlotPanel
          {...defaultProps({
            isActive: true,
            defaultExpanded: true,
            saveStatus: status,
          })}
        />,
      );
      expect(container.textContent).toContain("a.srm");
      expect(container.textContent).toContain("b.srm");
      expect(queryByTestId("vhp-a.srm")).not.toBeNull();
      expect(queryByTestId("vhp-b.srm")).not.toBeNull();
    });

    // The header count is the number of SAVES in the slot, from the (fresh)
    // SaveSlotSummary — not the tracked-file count, which a copy that adds a
    // version would never move off "1 save".
    const oneFile: SaveStatus["files"][number] = {
      filename: "a.srm",
      local_path: "/data/a.srm",
      local_hash: "h",
      local_mtime: "2025-06-15T10:00:00Z",
      local_size: 100,
      server_save_id: 1,
      server_file_name: null,
      server_emulator: null,
      server_updated_at: null,
      server_size: null,
      last_sync_at: null,
      status: "synced",
    };

    it("shows the server-side slot count in the active header, not the tracked-file count", () => {
      const { container } = render(
        <SlotPanel
          {...defaultProps({
            isActive: true,
            slot: makeSummary({ count: 3 }),
            saveStatus: makeStatus({ files: [oneFile] }),
          })}
        />,
      );
      // One tracked file, but the slot holds 3 saves → "3 saves", not "1 save".
      expect(container.textContent).toContain("3 saves");
      expect(container.textContent).not.toContain("1 save");
    });

    it("falls back to the tracked-file count when the summary count is 0 (placeholder active slot)", () => {
      const { container } = render(
        <SlotPanel
          {...defaultProps({
            isActive: true,
            slot: makeSummary({ count: 0 }),
            saveStatus: makeStatus({ files: [oneFile] }),
          })}
        />,
      );
      expect(container.textContent).toContain("1 save");
    });

    it("multi-file slot: shows component list + #908 note and NO VersionHistoryPanel", () => {
      const status = makeStatus({
        files: [
          {
            filename: "rally.bkr",
            local_path: "/data/rally.bkr",
            local_hash: "h",
            local_mtime: "2025-06-15T10:00:00Z",
            local_size: 100,
            server_save_id: 1,
            server_file_name: null,
            server_emulator: null,
            server_updated_at: null,
            server_size: null,
            last_sync_at: null,
            status: "synced",
          },
        ],
        multi_file: true,
        component_files: ["rally.bcr", "rally.bkr", "rally.smpc"],
        rollback_supported: false,
      });
      const { container, queryByTestId } = render(
        <SlotPanel
          {...defaultProps({
            isActive: true,
            defaultExpanded: true,
            saveStatus: status,
          })}
        />,
      );
      // The component file list is shown.
      expect(container.textContent).toContain("Files in this save (3)");
      expect(container.textContent).toContain("rally.bcr");
      expect(container.textContent).toContain("rally.smpc");
      // The calm #908 note replaces Previous Versions / rollback.
      expect(container.textContent).toContain(
        "This save spans 3 files. Per-version rollback isn't available for multi-file saves yet.",
      );
      // No version-history / rollback control is rendered for the file.
      expect(queryByTestId("vhp-rally.bkr")).toBeNull();
    });

    it("shows the no-save line once when the active slot has no files", () => {
      const { container } = render(
        <SlotPanel
          {...defaultProps({
            isActive: true,
            defaultExpanded: true,
            saveStatus: makeStatus({ files: [] }),
          })}
        />,
      );
      expect(container.textContent.split(NO_SAVE_LINE)).toHaveLength(2);
    });

    it("forwards isOffline to VersionHistoryPanel for each file", () => {
      const status = makeStatus({
        files: [
          {
            filename: "a.srm",
            local_path: "/data/a.srm",
            local_hash: "h",
            local_mtime: "2025-06-15T10:00:00Z",
            local_size: 100,
            server_save_id: 1,
            server_file_name: null,
            server_emulator: null,
            server_updated_at: null,
            server_size: null,
            last_sync_at: null,
            status: "synced",
          },
        ],
      });
      const { getByTestId } = render(
        <SlotPanel
          {...defaultProps({
            isActive: true,
            defaultExpanded: true,
            saveStatus: status,
            isOffline: true,
          })}
        />,
      );
      expect(getByTestId("vhp-a.srm").getAttribute("data-offline")).toBe("true");
    });
  });

  describe("handleActivate", () => {
    it("calls onSlotSwitched on a successful switch", async () => {
      vi.mocked(backend.getSlotSaves).mockResolvedValue({
        success: true,
        slot: "default",
        saves: [],
      });
      const newStatus = makeStatus();
      vi.mocked(backend.switchSlot).mockResolvedValue({
        success: true,
        save_status: newStatus,
      });

      const onSlotSwitched = vi.fn();
      const { container, getByText } = render(<SlotPanel {...defaultProps({ onSlotSwitched })} />);
      fireEvent.click(container.querySelector("button")!); // expand
      await flushAsync();
      fireEvent.click(getByText("Activate Slot"));
      await flushAsync();
      await flushAsync();
      expect(onSlotSwitched).toHaveBeenCalledTimes(1);
      expect(onSlotSwitched).toHaveBeenCalledWith("default", newStatus);
    });

    it("shows the 'pending_uploads' error", async () => {
      vi.useFakeTimers();
      try {
        vi.mocked(backend.getSlotSaves).mockResolvedValue({
          success: true,
          slot: "default",
          saves: [],
        });
        vi.mocked(backend.switchSlot).mockResolvedValue({
          success: false,
          reason: "pending_uploads",
          message: "Pending local changes — upload or discard first",
        });

        const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
        fireEvent.click(container.querySelector("button")!);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
        });
        fireEvent.click(getByText("Activate Slot"));
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
          await vi.advanceTimersByTimeAsync(0);
        });
        expect(container.textContent).toContain("Sync your saves first — local changes haven't been uploaded");
      } finally {
        vi.useRealTimers();
      }
    });

    it.each([
      {
        reason: "save_shape_unsupported" as const,
        message: "Save sync is unavailable: this emulator writes saves inside the game file itself.",
        shown: "Save sync is unavailable: this emulator writes saves inside the game file itself.",
      },
      {
        reason: "savefiles_in_content_dir" as const,
        message: "anything",
        shown: "Can't switch — this game's saves are written beside the game file",
      },
    ])("a $reason refusal shows its own explanation, not the generic failure", async ({ reason, message, shown }) => {
      vi.useFakeTimers();
      try {
        vi.mocked(backend.getSlotSaves).mockResolvedValue({ success: true, slot: "default", saves: [] });
        vi.mocked(backend.switchSlot).mockResolvedValue({ success: false, reason, message });

        const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
        fireEvent.click(container.querySelector("button")!);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
        });
        fireEvent.click(getByText("Activate Slot"));
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
          await vi.advanceTimersByTimeAsync(0);
        });
        expect(container.textContent).toContain(shown);
        expect(container.textContent).not.toContain("Failed to switch slot");
      } finally {
        vi.useRealTimers();
      }
    });

    it("auto-clears the switchError after 5 seconds", async () => {
      vi.useFakeTimers();
      try {
        vi.mocked(backend.getSlotSaves).mockResolvedValue({
          success: true,
          slot: "default",
          saves: [],
        });
        vi.mocked(backend.switchSlot).mockResolvedValue({
          success: false,
          reason: "pending_uploads",
          message: "Pending local changes — upload or discard first",
        });

        const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
        fireEvent.click(container.querySelector("button")!);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
        });
        fireEvent.click(getByText("Activate Slot"));
        // Flush the awaited switchSlot promise + the setSwitchError state
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
          await vi.advanceTimersByTimeAsync(0);
        });
        expect(container.textContent).toContain("Sync your saves first — local changes haven't been uploaded");
        // The 5s clear timer fires
        await act(async () => {
          await vi.advanceTimersByTimeAsync(5001);
        });
        expect(container.textContent).not.toContain("Sync your saves first — local changes haven't been uploaded");
      } finally {
        vi.useRealTimers();
      }
    });

    it("shows the 'server_unreachable' error and drives the connection store offline", async () => {
      vi.useFakeTimers();
      setRommConnectionState("connected");
      try {
        vi.mocked(backend.getSlotSaves).mockResolvedValue({
          success: true,
          slot: "default",
          saves: [],
        });
        vi.mocked(backend.switchSlot).mockResolvedValue({
          success: false,
          reason: "server_unreachable",
          message: "Server unreachable — check your URL and ensure RomM is running",
        });
        const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
        fireEvent.click(container.querySelector("button")!);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
        });
        fireEvent.click(getByText("Activate Slot"));
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
          await vi.advanceTimersByTimeAsync(0);
        });
        expect(container.textContent).toContain("Can't switch — RomM server is not reachable");
        expect(getRommConnectionState()).toBe("offline");
      } finally {
        vi.useRealTimers();
      }
    });

    it("explains the not_installed rejection (bound but not downloaded)", async () => {
      vi.useFakeTimers();
      try {
        vi.mocked(backend.getSlotSaves).mockResolvedValue({
          success: true,
          slot: "default",
          saves: [],
        });
        vi.mocked(backend.switchSlot).mockResolvedValue({
          success: false,
          reason: "not_installed",
          message: "ROM is not installed",
        });
        const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
        fireEvent.click(container.querySelector("button")!);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
        });
        fireEvent.click(getByText("Activate Slot"));
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
          await vi.advanceTimersByTimeAsync(0);
        });
        expect(container.textContent).toContain("Can't switch — download the game first");
      } finally {
        vi.useRealTimers();
      }
    });

    it("shows the generic error on unknown reason", async () => {
      vi.useFakeTimers();
      try {
        vi.mocked(backend.getSlotSaves).mockResolvedValue({
          success: true,
          slot: "default",
          saves: [],
        });
        vi.mocked(backend.switchSlot).mockResolvedValue({
          success: false,
          reason: "sync_disabled",
          message: "Save sync is disabled",
        });
        const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
        fireEvent.click(container.querySelector("button")!);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
        });
        fireEvent.click(getByText("Activate Slot"));
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
          await vi.advanceTimersByTimeAsync(0);
        });
        expect(container.textContent).toContain("Failed to switch slot");
      } finally {
        vi.useRealTimers();
      }
    });

    it("gives a second failure's error its own 5 seconds, not what was left of the first's", async () => {
      vi.useFakeTimers();
      try {
        vi.mocked(backend.getSlotSaves).mockResolvedValue({ success: true, slot: "default", saves: [] });
        vi.mocked(backend.switchSlot).mockResolvedValue({
          success: false,
          reason: "sync_disabled",
          message: "Save sync is disabled",
        });
        const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
        fireEvent.click(container.querySelector("button")!);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
        });
        fireEvent.click(getByText("Activate Slot"));
        await act(async () => {
          await vi.advanceTimersByTimeAsync(3000);
        });
        fireEvent.click(getByText("Activate Slot"));
        await act(async () => {
          await vi.advanceTimersByTimeAsync(2500);
        });
        expect(container.textContent).toContain("Failed to switch slot");
        await act(async () => {
          await vi.advanceTimersByTimeAsync(2501);
        });
        expect(container.textContent).not.toContain("Failed to switch slot");
      } finally {
        vi.useRealTimers();
      }
    });

    it("writes no debug line for a failure answer", async () => {
      const debugSpy = vi.spyOn(backend, "debugLog").mockResolvedValue(undefined);
      vi.useFakeTimers();
      try {
        vi.mocked(backend.getSlotSaves).mockResolvedValue({ success: true, slot: "default", saves: [] });
        vi.mocked(backend.switchSlot).mockResolvedValue({
          success: false,
          reason: "not_installed",
          message: "Not installed",
        });
        const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
        fireEvent.click(container.querySelector("button")!);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
        });
        debugSpy.mockClear();
        fireEvent.click(getByText("Activate Slot"));
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
          await vi.advanceTimersByTimeAsync(0);
        });
        expect(container.textContent).toContain("Can't switch — download the game first");
        expect(debugSpy).not.toHaveBeenCalled();
      } finally {
        vi.useRealTimers();
        debugSpy.mockRestore();
      }
    });

    it("catches thrown errors and surfaces an error line", async () => {
      vi.useFakeTimers();
      try {
        vi.mocked(backend.getSlotSaves).mockResolvedValue({
          success: true,
          slot: "default",
          saves: [],
        });
        vi.mocked(backend.switchSlot).mockRejectedValue(new Error("boom"));
        const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
        fireEvent.click(container.querySelector("button")!);
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
        });
        fireEvent.click(getByText("Activate Slot"));
        await act(async () => {
          await vi.advanceTimersByTimeAsync(0);
          await vi.advanceTimersByTimeAsync(0);
        });
        expect(container.textContent).toContain("An error occurred while switching slots");
      } finally {
        vi.useRealTimers();
      }
    });
  });

  describe("a switch that stopped partway", () => {
    it.each([
      {
        answer: {
          success: false as const,
          reason: "switch_stopped" as const,
          message: "Could not move pokemon.rtc into .romm-backup.",
          set_aside: ["pokemon.srm"],
        },
        shown: "The slot wasn't switched: a save couldn't be moved to .romm-backup. Already moved there: pokemon.srm.",
      },
      {
        answer: {
          success: false as const,
          reason: "switch_incomplete" as const,
          message: "Switched to slot but 1 save(s) failed to download — retry",
        },
        shown: "Switched slots, but not every save of the new slot downloaded. Sync Save Files to finish.",
      },
    ])("toasts its own sentence for $answer.reason and reloads the SAVES tab", async ({ answer, shown }) => {
      vi.mocked(backend.getSlotSaves).mockResolvedValue({ success: true, slot: "default", saves: [] });
      vi.mocked(backend.switchSlot).mockResolvedValue(answer);
      const details: unknown[] = [];
      const listener = (e: Event) => details.push((e as CustomEvent).detail);
      globalThis.addEventListener("romm_data_changed", listener);
      try {
        const onSlotSwitched = vi.fn();
        const { container, getByText } = render(<SlotPanel {...defaultProps({ onSlotSwitched })} />);
        fireEvent.click(container.querySelector("button")!);
        await flushAsync();
        fireEvent.click(getByText("Activate Slot"));
        await flushAsync();
        await flushAsync();

        expect(vi.mocked(toaster.toast)).toHaveBeenCalledWith(expect.objectContaining({ body: shown }));
        expect(container.textContent).not.toContain(shown);
        expect(container.textContent).not.toContain("Failed to switch slot");
        expect(onSlotSwitched).not.toHaveBeenCalled();
      } finally {
        globalThis.removeEventListener("romm_data_changed", listener);
      }
      expect(details).toEqual([{ type: "save_sync", rom_id: defaultProps().romId }]);
    });
  });

  describe("handleDelete", () => {
    it("toasts the failure reason and skips the confirm modal when getSlotDeleteInfo returns !success", async () => {
      vi.mocked(backend.getSlotSaves).mockResolvedValue({
        success: true,
        slot: "default",
        saves: [],
      });
      vi.mocked(backend.getSlotDeleteInfo).mockResolvedValue({
        success: false,
        reason: "sync_disabled",
        message: "Cannot delete the active slot. Switch to a different slot first.",
      });
      const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
      fireEvent.click(container.querySelector("button")!);
      await flushAsync();
      fireEvent.click(getByText("Delete Slot"));
      await flushAsync();
      await flushAsync();
      expect(vi.mocked(toaster.toast)).toHaveBeenCalledWith(
        expect.objectContaining({
          body: "Cannot delete the active slot. Switch to a different slot first.",
        }),
      );
      expect(vi.mocked(showModal)).not.toHaveBeenCalled();
    });

    it("opens the ConfirmModal with a server-delete message when source=server and saves > 0", async () => {
      vi.mocked(backend.getSlotSaves).mockResolvedValue({
        success: true,
        slot: "default",
        saves: [],
      });
      vi.mocked(backend.getSlotDeleteInfo).mockResolvedValue({
        success: true,
        slot: "default",
        source: "server",
        server_save_count: 3,
        server_save_ids: [1, 2, 3],
        local_file_count: 2,
        local_filenames: ["a.srm", "b.srm"],
        is_active: false,
      });
      vi.mocked(backend.deleteSlot).mockResolvedValue({
        success: true,
        deleted_server_saves: 3,
        cleaned_files: 2,
      });

      const onSlotDeleted = vi.fn();
      const { container, getByText } = render(<SlotPanel {...defaultProps({ onSlotDeleted })} />);
      fireEvent.click(container.querySelector("button")!);
      await flushAsync();
      fireEvent.click(getByText("Delete Slot"));
      await flushAsync();
      await flushAsync();

      expect(vi.mocked(showModal)).toHaveBeenCalledTimes(1);
      const modalProps = lastConfirmModalProps();
      expect(modalProps).not.toBeNull();
      expect(modalProps?.strTitle).toBe("Delete Slot");
      expect(modalProps?.strDescription).toContain("3 saves from slot 'default'");
      expect(modalProps?.strDescription).toContain("2 tracked files will be unlinked");
      expect(modalProps?.strDescription).toContain("This cannot be undone.");

      // Run the OK callback
      await modalProps?.onOK?.();
      await flushAsync();
      expect(vi.mocked(backend.deleteSlot)).toHaveBeenCalledWith(1, "default");
      expect(vi.mocked(toaster.toast)).toHaveBeenCalledWith(
        expect.objectContaining({ body: "Slot 'default' deleted" }),
      );
      expect(onSlotDeleted).toHaveBeenCalledTimes(1);
    });

    it("uses 'remove from local config' wording when source=local", async () => {
      vi.mocked(backend.getSlotSaves).mockResolvedValue({
        success: true,
        slot: "default",
        saves: [],
      });
      vi.mocked(backend.getSlotDeleteInfo).mockResolvedValue({
        success: true,
        slot: "default",
        source: "local",
        server_save_count: 0,
        server_save_ids: [],
        local_file_count: 1,
        local_filenames: ["a.srm"],
        is_active: false,
      });

      const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
      fireEvent.click(container.querySelector("button")!);
      await flushAsync();
      fireEvent.click(getByText("Delete Slot"));
      await flushAsync();
      await flushAsync();
      const modalProps = lastConfirmModalProps();
      expect(modalProps?.strDescription).toContain("remove slot 'default' from your local configuration");
      expect(modalProps?.strDescription).toContain("1 tracked file will be unlinked");
    });

    it("toasts the failure message when deleteSlot returns success=false", async () => {
      vi.mocked(backend.getSlotSaves).mockResolvedValue({
        success: true,
        slot: "default",
        saves: [],
      });
      vi.mocked(backend.getSlotDeleteInfo).mockResolvedValue({
        success: true,
        slot: "default",
        source: "local",
        server_save_count: 0,
        server_save_ids: [],
        local_file_count: 0,
        local_filenames: [],
        is_active: false,
      });
      vi.mocked(backend.deleteSlot).mockResolvedValue({
        success: false,
        reason: "server_unreachable",
        message: "couldn't reach server",
      });

      const onSlotDeleted = vi.fn();
      const { container, getByText } = render(<SlotPanel {...defaultProps({ onSlotDeleted })} />);
      fireEvent.click(container.querySelector("button")!);
      await flushAsync();
      fireEvent.click(getByText("Delete Slot"));
      await flushAsync();
      await flushAsync();

      await lastConfirmModalProps()?.onOK?.();
      await flushAsync();
      expect(vi.mocked(toaster.toast)).toHaveBeenCalledWith(expect.objectContaining({ body: "couldn't reach server" }));
      expect(onSlotDeleted).not.toHaveBeenCalled();
    });

    it("toasts a generic message when deleteSlot throws", async () => {
      vi.mocked(backend.getSlotSaves).mockResolvedValue({
        success: true,
        slot: "default",
        saves: [],
      });
      vi.mocked(backend.getSlotDeleteInfo).mockResolvedValue({
        success: true,
        slot: "default",
        source: "local",
        server_save_count: 0,
        server_save_ids: [],
        local_file_count: 0,
        local_filenames: [],
        is_active: false,
      });
      vi.mocked(backend.deleteSlot).mockRejectedValue(new Error("network"));

      const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
      fireEvent.click(container.querySelector("button")!);
      await flushAsync();
      fireEvent.click(getByText("Delete Slot"));
      await flushAsync();
      await flushAsync();

      await lastConfirmModalProps()?.onOK?.();
      await flushAsync();
      expect(vi.mocked(toaster.toast)).toHaveBeenCalledWith(
        expect.objectContaining({ body: "An error occurred while deleting the slot" }),
      );
    });

    it("toasts a generic 'Failed to load slot info' when getSlotDeleteInfo throws", async () => {
      vi.mocked(backend.getSlotSaves).mockResolvedValue({
        success: true,
        slot: "default",
        saves: [],
      });
      vi.mocked(backend.getSlotDeleteInfo).mockRejectedValue(new Error("network"));

      const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
      fireEvent.click(container.querySelector("button")!);
      await flushAsync();
      fireEvent.click(getByText("Delete Slot"));
      await flushAsync();
      await flushAsync();

      expect(vi.mocked(toaster.toast)).toHaveBeenCalledWith(
        expect.objectContaining({ body: "Failed to load slot info" }),
      );
      expect(vi.mocked(showModal)).not.toHaveBeenCalled();
    });

    it("ends 'Deleting...' once the info has loaded, with the confirmation still unanswered", async () => {
      vi.mocked(backend.getSlotSaves).mockResolvedValue({ success: true, slot: "default", saves: [] });
      let answerInfo: (info: Awaited<ReturnType<typeof backend.getSlotDeleteInfo>>) => void = () => {};
      vi.mocked(backend.getSlotDeleteInfo).mockReturnValue(
        new Promise((resolve) => {
          answerInfo = resolve;
        }),
      );

      const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
      fireEvent.click(container.querySelector("button")!);
      await flushAsync();
      fireEvent.click(getByText("Delete Slot"));
      await flushAsync();
      expect((getByText("Deleting...") as HTMLButtonElement).disabled).toBe(true);

      await act(async () => {
        answerInfo({
          success: true,
          slot: "default",
          source: "local",
          server_save_count: 0,
          server_save_ids: [],
          local_file_count: 0,
          local_filenames: [],
          is_active: false,
        });
        await Promise.resolve();
      });
      await flushAsync();

      expect(vi.mocked(showModal)).toHaveBeenCalledTimes(1);
      expect(backend.deleteSlot).not.toHaveBeenCalled();
      expect((getByText("Delete Slot") as HTMLButtonElement).disabled).toBe(false);
    });

    it("ends 'Deleting...' when the info call answers a failure", async () => {
      vi.mocked(backend.getSlotSaves).mockResolvedValue({ success: true, slot: "default", saves: [] });
      vi.mocked(backend.getSlotDeleteInfo).mockResolvedValueOnce({
        success: false,
        reason: "server_unreachable",
        message: "RomM server is not reachable",
      });

      const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
      fireEvent.click(container.querySelector("button")!);
      await flushAsync();
      fireEvent.click(getByText("Delete Slot"));
      await flushAsync();
      await flushAsync();

      expect(vi.mocked(toaster.toast)).toHaveBeenCalledWith(
        expect.objectContaining({ body: "RomM server is not reachable" }),
      );
      expect(vi.mocked(showModal)).not.toHaveBeenCalled();
      expect((getByText("Delete Slot") as HTMLButtonElement).disabled).toBe(false);
    });

    it("ends 'Deleting...' when opening the confirmation throws", async () => {
      const errorSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      try {
        vi.mocked(backend.getSlotSaves).mockResolvedValue({ success: true, slot: "default", saves: [] });
        vi.mocked(backend.getSlotDeleteInfo).mockResolvedValue({
          success: true,
          slot: "default",
          source: "local",
          server_save_count: 0,
          server_save_ids: [],
          local_file_count: 0,
          local_filenames: [],
          is_active: false,
        });
        vi.mocked(showModal).mockImplementationOnce(() => {
          throw new Error("modal broke");
        });

        const { container, getByText } = render(<SlotPanel {...defaultProps()} />);
        fireEvent.click(container.querySelector("button")!);
        await flushAsync();
        fireEvent.click(getByText("Delete Slot"));
        await flushAsync();
        await flushAsync();

        expect(vi.mocked(showModal)).toHaveBeenCalledTimes(1);
        expect((getByText("Delete Slot") as HTMLButtonElement).disabled).toBe(false);
      } finally {
        errorSpy.mockRestore();
      }
    });
  });

  describe("badges and summary line", () => {
    it("renders the 'local' source badge for source=local", () => {
      const { container } = render(<SlotPanel {...defaultProps({ slot: makeSummary({ source: "local" }) })} />);
      const badge = container.querySelector(".romm-slot-badge-local");
      expect(badge).not.toBeNull();
      expect(badge?.textContent).toBe("local");
    });

    it("renders the 'server' source badge for source=server", () => {
      const { container } = render(<SlotPanel {...defaultProps({ slot: makeSummary({ source: "server" }) })} />);
      const badge = container.querySelector(".romm-slot-badge-server");
      expect(badge).not.toBeNull();
      expect(badge?.textContent).toBe("server");
    });

    it("shows the 'active' badge only when isActive is true", () => {
      const { container, rerender } = render(<SlotPanel {...defaultProps()} />);
      expect(container.querySelector(".romm-slot-badge-active")).toBeNull();
      rerender(<SlotPanel {...defaultProps({ isActive: true, saveStatus: makeStatus() })} />);
      expect(container.querySelector(".romm-slot-badge-active")).not.toBeNull();
    });

    it("shows the sync summary line only when active AND syncSummaryText is non-null", () => {
      // Inactive: no summary line
      const { container, rerender } = render(<SlotPanel {...defaultProps()} />);
      expect(container.querySelector(".romm-slot-sync-summary")).toBeNull();
      // Active with empty files: the no-save line appears
      rerender(<SlotPanel {...defaultProps({ isActive: true, saveStatus: makeStatus() })} />);
      const summary = container.querySelector(".romm-slot-sync-summary");
      expect(summary).not.toBeNull();
      expect(summary?.textContent).toBe(NO_SAVE_LINE);
    });

    it("renders the slot count using slot.count when inactive and no slot files loaded", () => {
      const { container } = render(<SlotPanel {...defaultProps({ slot: makeSummary({ count: 5 }) })} />);
      expect(container.textContent).toContain("5 saves");
    });

    it("uses singular 'save' wording for count === 1", () => {
      const { container } = render(<SlotPanel {...defaultProps({ slot: makeSummary({ count: 1 }) })} />);
      expect(container.textContent).toContain("1 save");
      expect(container.textContent).not.toContain("1 saves");
    });
  });

  it("renders 'Manual archive' for an empty slot name", () => {
    const { container } = render(<SlotPanel {...defaultProps({ slot: makeSummary({ slot: "" }) })} />);
    expect(container.textContent).toContain("Manual archive");
    expect(container.textContent).not.toContain("(no slot)");
  });

  it("the legacy '' slot is read-only: no Activate/Delete, note + muted styling, files shown (#1478)", async () => {
    // The slot-less legacy bucket is read-only (#1276 retired switching in;
    // #1478 removed deletion). It may be expanded
    // to view its saves, but carries neither control.
    const files: SlotSaveFile[] = [{ id: 1, filename: "legacy.srm", size: 512, updated_at: "", emulator: "mgba" }];
    vi.mocked(backend.getSlotSaves).mockResolvedValue({ success: true, slot: "", saves: files });
    const { container, queryByText } = render(<SlotPanel {...defaultProps({ slot: makeSummary({ slot: "" }) })} />);
    // Muted styling + the read-only note are visible without expanding.
    expect(container.querySelector(".romm-slot-panel-legacy")).not.toBeNull();
    expect(container.textContent).toContain(
      "Saves uploaded to RomM without a slot. RomM keeps them as a manual archive and never syncs them between devices.",
    );
    fireEvent.click(container.querySelector("button")!); // expand
    await flushAsync();
    expect(container.textContent).toContain("legacy.srm");
    // Both controls are gone — the panel is fully read-only.
    expect(queryByText("Activate Slot")).toBeNull();
    expect(queryByText("Delete Slot")).toBeNull();
  });

  it("a named slot carries neither the legacy note nor the muted class (#1478)", () => {
    const { container } = render(<SlotPanel {...defaultProps({ slot: makeSummary({ slot: "speedrun" }) })} />);
    expect(container.querySelector(".romm-slot-panel-legacy")).toBeNull();
    expect(container.textContent).not.toContain("RomM keeps them as a manual archive");
  });

  it("loads and renders inactive slot files when expanded", async () => {
    const files: SlotSaveFile[] = [{ id: 1, filename: "remote-a.srm", size: 1024, updated_at: "", emulator: "mgba" }];
    vi.mocked(backend.getSlotSaves).mockResolvedValue({
      success: true,
      slot: "default",
      saves: files,
    });
    const { container } = render(<SlotPanel {...defaultProps()} />);
    fireEvent.click(container.querySelector("button")!);
    await flushAsync();
    expect(container.textContent).toContain("remote-a.srm");
  });

  describe("a throw from the panel's own code after an answer", () => {
    const DELETE_INFO = {
      success: true as const,
      slot: "default",
      source: "local" as const,
      server_save_count: 0,
      server_save_ids: [],
      local_file_count: 0,
      local_filenames: [],
      is_active: false,
    };

    async function openInactivePanel(overrides: Partial<React.ComponentProps<typeof SlotPanel>> = {}) {
      vi.mocked(backend.getSlotSaves).mockResolvedValue({ success: true, slot: "default", saves: [] });
      const view = render(<SlotPanel {...defaultProps(overrides)} />);
      fireEvent.click(view.container.querySelector("button")!);
      await flushAsync();
      return view;
    }

    it("logs a throw from onSlotSwitched as an error, and shows no error line for it", async () => {
      const errorSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      try {
        vi.mocked(backend.switchSlot).mockResolvedValue({ success: true, save_status: makeStatus() });
        const onSlotSwitched = vi.fn(() => {
          throw new Error("parent broke");
        });
        const { container, getByText } = await openInactivePanel({ onSlotSwitched });
        fireEvent.click(getByText("Activate Slot"));
        await flushAsync();
        await flushAsync();
        expect(onSlotSwitched).toHaveBeenCalledOnce();
        expect(errorSpy).toHaveBeenCalledExactlyOnceWith(
          "SavesTab: slot 'default' threw after its switch: Error: parent broke",
        );
        expect(container.textContent).not.toContain("An error occurred while switching slots");
        expect(container.textContent).not.toContain("Failed to switch slot");
      } finally {
        errorSpy.mockRestore();
      }
    });

    it("logs a throw from opening the confirmation as an error, and toasts nothing for it", async () => {
      const errorSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      try {
        vi.mocked(backend.getSlotDeleteInfo).mockResolvedValue(DELETE_INFO);
        vi.mocked(showModal).mockImplementationOnce(() => {
          throw new Error("modal broke");
        });
        const { getByText } = await openInactivePanel();
        fireEvent.click(getByText("Delete Slot"));
        await flushAsync();
        await flushAsync();
        expect(errorSpy).toHaveBeenCalledExactlyOnceWith(
          "SavesTab: slot 'default' threw before its delete: Error: modal broke",
        );
        expect(vi.mocked(toaster.toast)).not.toHaveBeenCalled();
      } finally {
        errorSpy.mockRestore();
      }
    });

    it("logs a throw from onSlotDeleted as an error, and toasts only the deletion", async () => {
      const errorSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
      try {
        vi.mocked(backend.getSlotDeleteInfo).mockResolvedValue(DELETE_INFO);
        vi.mocked(backend.deleteSlot).mockResolvedValue({ success: true, deleted_server_saves: 0, cleaned_files: 0 });
        const onSlotDeleted = vi.fn(() => {
          throw new Error("parent broke");
        });
        const { getByText } = await openInactivePanel({ onSlotDeleted });
        fireEvent.click(getByText("Delete Slot"));
        await flushAsync();
        await flushAsync();
        await lastConfirmModalProps()?.onOK?.();
        await flushAsync();
        expect(onSlotDeleted).toHaveBeenCalledOnce();
        expect(errorSpy).toHaveBeenCalledExactlyOnceWith(
          "SavesTab: slot 'default' threw after its delete: Error: parent broke",
        );
        expect(vi.mocked(toaster.toast)).toHaveBeenCalledExactlyOnceWith(
          expect.objectContaining({ body: "Slot 'default' deleted" }),
        );
      } finally {
        errorSpy.mockRestore();
      }
    });
  });

  describe("an active slot with no save (#2149 D3)", () => {
    const line = "No save yet — mGBA's saves for this game sync with RomM once it writes one.";

    it("reads the line it is handed once, on its summary line", () => {
      const { container } = render(
        <SlotPanel
          {...defaultProps({ isActive: true, defaultExpanded: true, saveStatus: makeStatus({ files: [] }) })}
          noSaveLine={line}
        />,
      );

      expect(container.querySelector(".romm-slot-sync-summary")?.textContent).toBe(line);
      expect(container.textContent.split(line)).toHaveLength(2);
      expect(container.textContent).not.toContain("No save files tracked yet");
    });

    it("says nothing under the slot while no status is held (#2149 D10)", () => {
      const { container } = render(
        <SlotPanel {...defaultProps({ isActive: true, defaultExpanded: true, saveStatus: null })} noSaveLine={line} />,
      );

      expect(container.querySelector(".romm-slot-sync-summary")).toBeNull();
      expect(container.querySelector(".romm-slot-body")?.textContent).toBe("");
    });

    it("speaks only for this device where RomM could not be asked (#2149 D10)", () => {
      const { container } = render(
        <SlotPanel
          {...defaultProps({
            isActive: true,
            defaultExpanded: true,
            saveStatus: makeStatus({ files: [], server_query_failed: true, server_query_reason: "server_unreachable" }),
          })}
          noSaveLine={line}
        />,
      );

      expect(container.querySelector(".romm-slot-sync-summary")?.textContent).toBe("Server unreachable");
      expect(container.querySelector(".romm-slot-body")?.textContent).toBe("No save on this device.");
      expect(container.textContent).not.toContain(line);
    });

    it("leaves an inactive slot's empty list as it was", () => {
      const { container } = render(<SlotPanel {...defaultProps({ isActive: false })} noSaveLine={line} />);

      expect(container.textContent).not.toContain(line);
    });
  });
});
