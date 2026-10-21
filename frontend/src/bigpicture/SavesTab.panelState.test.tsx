import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, fireEvent, act, waitFor } from "@testing-library/react";
import { useEffect, useState, type ComponentProps, type ReactElement } from "react";
import { showModal } from "@decky/ui";
import { SavesTab } from "./SavesTab";
import * as backend from "../api/backend";
import { setRommConnectionState } from "../utils/connectionState";
import type { SaveFileStatus, SaveSlotSummary, SaveStatus, SaveVersionEntry } from "../types";

// SavesTab.test.tsx stubs SlotPanel to read its props; the state a panel keeps
// across a restore, an activation or a copy lives inside the real SlotPanel and
// VersionHistoryPanel, so this file renders both.

function makeSlot(slot: string): SaveSlotSummary {
  return { slot, source: "server", count: 1, latest_updated_at: null };
}

function makeFile(filename: string, serverSaveId: number): SaveFileStatus {
  return {
    filename,
    local_path: `/data/${filename}`,
    local_hash: "h",
    local_mtime: "2025-06-15T10:00:00Z",
    local_size: 100,
    server_save_id: serverSaveId,
    server_file_name: null,
    server_emulator: null,
    server_updated_at: null,
    server_size: null,
    last_sync_at: null,
    status: "synced",
  };
}

function makeStatus(files: SaveFileStatus[]): SaveStatus {
  return {
    rom_id: 1,
    files,
    playtime: {
      total_seconds: 0,
      session_count: 0,
      last_session_start: null,
      last_session_duration_sec: null,
      last_played: null,
    },
    device_id: "dev",
    last_sync_check_at: null,
  };
}

function makeVersion(id: number, fileName: string): SaveVersionEntry {
  return {
    id,
    file_name: fileName,
    emulator: "mgba",
    updated_at: "2025-06-14T10:00:00Z",
    file_size_bytes: 1024,
    device_syncs: [],
    uploaded_by_us: true,
  };
}

const SLOTS = [makeSlot("alpha"), makeSlot("beta")];
const ALPHA_STATUS = makeStatus([makeFile("a.srm", 1), makeFile("b.srm", 2)]);
const BETA_STATUS = makeStatus([makeFile("c.srm", 3)]);

function props(overrides: Partial<ComponentProps<typeof SavesTab>> = {}): ComponentProps<typeof SavesTab> {
  return {
    appId: 100,
    romId: 1,
    saveStatus: ALPHA_STATUS,
    conflicts: [],
    activeSlot: "alpha",
    activeSlotKnown: true,
    availableSlots: SLOTS,
    lastKnownSlots: null,
    slotsLoading: false,
    onSlotSwitched: vi.fn(),
    ...overrides,
  };
}

/** Holds the active slot the way the game page does: a switch answers it, and a
 *  save change makes the page read it again — here, as the copy into `beta` left it. */
function Page({ followCopyInto }: { followCopyInto?: string }): ReactElement {
  const [activeSlot, setActiveSlot] = useState("alpha");
  const [saveStatus, setSaveStatus] = useState(ALPHA_STATUS);
  useEffect(() => {
    if (followCopyInto === undefined) return;
    const reread = () => {
      setActiveSlot(followCopyInto);
      setSaveStatus(BETA_STATUS);
    };
    globalThis.addEventListener("romm_data_changed", reread);
    return () => globalThis.removeEventListener("romm_data_changed", reread);
  }, [followCopyInto]);
  return (
    <SavesTab
      {...props({
        activeSlot,
        saveStatus,
        onSlotSwitched: (slot, status) => {
          setActiveSlot(slot);
          setSaveStatus(status);
        },
      })}
    />
  );
}

function panel(container: HTMLElement, slot: string): HTMLElement {
  const name = Array.from(container.querySelectorAll(".romm-slot-name")).find((n) => n.textContent === slot);
  const found = name?.closest<HTMLElement>(".romm-slot-panel");
  if (!found) throw new Error(`no panel for slot ${slot}`);
  return found;
}

function header(container: HTMLElement, slot: string): HTMLElement {
  const found = panel(container, slot).querySelector<HTMLElement>(".romm-slot-header");
  if (!found) throw new Error(`no header for slot ${slot}`);
  return found;
}

const isOpen = (container: HTMLElement, slot: string): boolean => header(container, slot).textContent.includes("▾");

function buttonsIn(root: HTMLElement, text: string): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>("button")).filter((b) => b.textContent.includes(text));
}

const flushAsync = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });

describe("SavesTab — what the slot panels keep", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setRommConnectionState("connected");
    vi.mocked(backend.getVersionList).mockResolvedValue({ multi_version: false, bound_vanished: false });
    vi.mocked(backend.checkLocalDrift).mockResolvedValue({
      drifted: false,
      only_here: false,
      stranding: "none",
      rom_id: 0,
    });
    vi.mocked(backend.getSlotSaves).mockImplementation((_romId, slot) =>
      Promise.resolve({
        success: true,
        slot,
        saves: [{ filename: `${slot}-server.srm`, id: 90, size: 10, updated_at: "2025-06-14T10:00:00Z", emulator: "" }],
      }),
    );
    vi.mocked(backend.savesListFileVersions).mockImplementation((_romId, _slot, filename) =>
      Promise.resolve({ status: "ok", versions: [makeVersion(filename === "a.srm" ? 11 : 21, `${filename}-v1`)] }),
    );
  });

  it("after a restore, an open inactive slot and another file's open Previous Versions are still open", async () => {
    vi.mocked(backend.savesRollbackToVersion).mockResolvedValue({ status: "ok" });
    const { container } = render(<SavesTab {...props()} />);
    await flushAsync();

    fireEvent.click(header(container, "beta"));
    await flushAsync();
    const [aVersions, bVersions] = buttonsIn(panel(container, "alpha"), "Previous Versions");
    if (!aVersions || !bVersions) throw new Error("no Previous Versions toggles");
    fireEvent.click(aVersions);
    fireEvent.click(bVersions);
    await flushAsync();
    expect(container.textContent).toContain("beta-server.srm");
    expect(container.textContent).toContain("a.srm-v1");
    expect(container.textContent).toContain("b.srm-v1");

    const [restoreA] = buttonsIn(panel(container, "alpha"), "Restore");
    if (!restoreA) throw new Error("no Restore button");
    fireEvent.click(restoreA);
    await flushAsync();
    await flushAsync();

    expect(isOpen(container, "beta")).toBe(true);
    expect(container.textContent).toContain("beta-server.srm");
    expect(container.textContent).toContain("b.srm-v1");
    // The restored file's own Previous Versions is the one panel that closes.
    expect(container.textContent).not.toContain("a.srm-v1");
  });

  it("after Activate Slot, the new active slot is open and the old one closed; reopening the old one shows its files", async () => {
    vi.mocked(backend.switchSlot).mockResolvedValue({ success: true, save_status: BETA_STATUS });
    const { container } = render(<Page />);
    await flushAsync();
    expect(isOpen(container, "alpha")).toBe(true);

    fireEvent.click(header(container, "beta"));
    await flushAsync();
    const [activate] = buttonsIn(panel(container, "beta"), "Activate Slot");
    if (!activate) throw new Error("no Activate Slot button");
    fireEvent.click(activate);
    await flushAsync();

    expect(isOpen(container, "beta")).toBe(true);
    expect(panel(container, "beta").textContent).toContain("c.srm");
    expect(isOpen(container, "alpha")).toBe(false);

    fireEvent.click(header(container, "alpha"));
    await flushAsync();
    expect(vi.mocked(backend.getSlotSaves)).toHaveBeenLastCalledWith(1, "alpha");
    expect(panel(container, "alpha").textContent).toContain("alpha-server.srm");
  });

  it("after a copy that makes the target slot active, the target slot is open", async () => {
    vi.mocked(backend.copySaveToSlot).mockResolvedValue({ status: "ok" });
    const { container } = render(<Page followCopyInto="beta" />);
    await flushAsync();
    expect(isOpen(container, "beta")).toBe(false);

    const [copy] = buttonsIn(panel(container, "alpha"), "Copy to slot");
    if (!copy) throw new Error("no Copy to slot button");
    fireEvent.click(copy);
    const calls = vi.mocked(showModal).mock.calls;
    const picker = calls[calls.length - 1]?.[0] as ReactElement<{ onSubmit: (target: string) => void }>;
    await act(async () => {
      picker.props.onSubmit("beta");
      await new Promise((r) => setTimeout(r, 0));
    });
    await flushAsync();

    expect(isOpen(container, "beta")).toBe(true);
    expect(panel(container, "beta").textContent).toContain("c.srm");
    expect(isOpen(container, "alpha")).toBe(false);
  });
});

describe("SavesTab — a restore reloads nothing it closed", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setRommConnectionState("connected");
    vi.mocked(backend.getVersionList).mockResolvedValue({ multi_version: false, bound_vanished: false });
    vi.mocked(backend.checkLocalDrift).mockResolvedValue({
      drifted: false,
      only_here: false,
      stranding: "none",
      rom_id: 0,
    });
  });

  it("the restored file's Previous Versions loads fresh when reopened", async () => {
    vi.mocked(backend.savesListFileVersions)
      .mockResolvedValueOnce({ status: "ok", versions: [makeVersion(11, "before.srm")] })
      .mockResolvedValue({ status: "ok", versions: [makeVersion(12, "after.srm")] });
    vi.mocked(backend.savesRollbackToVersion).mockResolvedValue({ status: "ok" });
    const { container } = render(<SavesTab {...props({ saveStatus: makeStatus([makeFile("a.srm", 1)]) })} />);
    await flushAsync();

    const [toggle] = buttonsIn(panel(container, "alpha"), "Previous Versions");
    if (!toggle) throw new Error("no Previous Versions toggle");
    fireEvent.click(toggle);
    await flushAsync();
    const [restore] = buttonsIn(panel(container, "alpha"), "Restore");
    if (!restore) throw new Error("no Restore button");
    fireEvent.click(restore);
    await flushAsync();
    await flushAsync();
    // Closed, and not loading behind its own back.
    expect(vi.mocked(backend.savesListFileVersions)).toHaveBeenCalledTimes(1);

    fireEvent.click(toggle);
    await waitFor(() => expect(container.textContent).toContain("after.srm"));
    expect(vi.mocked(backend.savesListFileVersions)).toHaveBeenCalledTimes(2);
  });
});
