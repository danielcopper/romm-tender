/**
 * DiscSelector tests — driven through the `emitHostEvent` event-bus harness.
 *
 * The component owns a custom compact trigger (`DialogButton`) and opens the
 * disc list via `showContextMenu`. This file locally re-mocks `@decky/ui` to
 * render the trigger (so the icon-only face is queryable) and to CAPTURE the
 * menu element handed to `showContextMenu`, which the tests render to assert the
 * option set + drive a selection exactly as a real menu click would.
 */

import "@testing-library/jest-dom/vitest";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, waitFor, act, fireEvent, within } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { toaster } from "../api/host";
import { DiscSelector } from "./DiscSelector";
import { emitHostEvent, hostEventListenerCount } from "../test-utils/host-event-bus";
import * as backend from "../api/backend";
import type { CachedGameDetail, DiscSelection, SelectDiscResult } from "../api/backend";
import type { DownloadCompleteEvent } from "../types";

// --- Local @decky/ui mock: render the trigger, capture the context menu ------
const captured: { menu: ReactNode } = { menu: null };

vi.mock("@decky/ui", () => ({
  DialogButton: (p: { onClick?: (e: unknown) => void; children?: ReactNode; className?: string }) =>
    createElement("button", { "data-testid": "disc-btn", onClick: p.onClick, className: p.className }, p.children),
  Menu: (p: { children?: ReactNode }) => createElement("div", { "data-testid": "disc-menu" }, p.children),
  MenuItem: (p: { onClick?: () => void; children?: ReactNode }) =>
    createElement("div", { role: "menuitem", onClick: p.onClick }, p.children),
  showContextMenu: (menu: ReactNode) => {
    captured.menu = menu;
  },
}));

// Cached-detail store: synchronous resolve so init settles in one tick.
vi.mock("../utils/cachedGameDetailStore", () => ({
  getCachedGameDetail: vi.fn<(appId: number) => Promise<CachedGameDetail>>(),
  invalidateCachedGameDetail: vi.fn(),
}));

// setLaunchOptionsConfirmed lives in steamShortcuts — mock it so a successful
// pick can be asserted without touching SteamClient.
vi.mock("../utils/steamShortcuts", () => ({
  setLaunchOptionsConfirmed: vi.fn<(appId: number, value: string) => Promise<boolean>>().mockResolvedValue(true),
}));

import { getCachedGameDetail } from "../utils/cachedGameDetailStore";
import { setLaunchOptionsConfirmed } from "../utils/steamShortcuts";

function mockCachedDetail(overrides: Partial<CachedGameDetail> = {}): void {
  vi.mocked(getCachedGameDetail).mockResolvedValue({
    found: true,
    rom_id: 42,
    rom_name: "Final Fantasy VII",
    installed: true,
    ...overrides,
  });
}

// A representative multi-disc, m3u-default selection: 3 discs, no pin (follows
// the m3u playlist).
const m3uSelection: DiscSelection = {
  multi_disc: true,
  discs: [
    { filename: "ff7 (Disc 1).cue", label: "Disc 1", index: 1 },
    { filename: "ff7 (Disc 2).cue", label: "Disc 2", index: 2 },
    { filename: "ff7 (Disc 3).cue", label: "Disc 3", index: 3 },
  ],
  selected: null,
  default: { kind: "m3u", label: "All discs (m3u)", filename: "ff7.m3u" },
};

// A no-m3u multi-disc selection: disc 1 is the default (no separate follow
// entry), already pinned to disc 2.
const discDefaultSelection: DiscSelection = {
  multi_disc: true,
  discs: [
    { filename: "game (Disc 1).chd", label: "Disc 1", index: 1 },
    { filename: "game (Disc 2).chd", label: "Disc 2", index: 2 },
  ],
  selected: "game (Disc 2).chd",
  default: { kind: "disc", label: "Disc 1", filename: "game (Disc 1).chd" },
};

// Settles the mount-time init chain (getCachedGameDetail → getDiscSelection).
// Its two callers below take the paths that bail out before the second fetch,
// and those write no state today — the effect returns before reaching any
// setter — so nothing escapes act as the code stands. The flush is what keeps
// those blocks proof against a settle point being added on either path.
const flushAsync = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });

/** Render, wait for the trigger, click it, and render the captured menu. */
async function renderAndOpen(appId = 100) {
  const r = render(<DiscSelector appId={appId} />);
  await r.findByTestId("disc-btn");
  await act(async () => {
    fireEvent.click(r.getByTestId("disc-btn"));
  });
  const menu = render(<>{captured.menu}</>);
  return { r, menu };
}

describe("DiscSelector — render gate", () => {
  beforeEach(() => {
    captured.menu = null;
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset();
  });

  it("renders nothing for a single-disc (multi_disc:false) ROM", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ multi_disc: false });

    const { container } = render(<DiscSelector appId={100} />);

    await waitFor(() => {
      expect(vi.mocked(backend.getDiscSelection)).toHaveBeenCalledWith(42);
    });
    expect(container.querySelector('[data-testid="disc-btn"]')).toBeNull();
  });

  it("renders nothing when the ROM is not found in the cache", async () => {
    vi.mocked(getCachedGameDetail).mockResolvedValue({ found: false });

    const { container } = render(<DiscSelector appId={100} />);

    await flushAsync();
    expect(vi.mocked(backend.getDiscSelection)).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="disc-btn"]')).toBeNull();
  });

  it("renders nothing when the ROM is not installed", async () => {
    mockCachedDetail({ installed: false });

    const { container } = render(<DiscSelector appId={100} />);

    await flushAsync();
    expect(vi.mocked(backend.getDiscSelection)).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="disc-btn"]')).toBeNull();
  });
});

describe("DiscSelector — multi-disc rendering", () => {
  beforeEach(() => {
    captured.menu = null;
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset();
  });

  it("shows the stacked-discs face (icon-only, no number) and lists the m3u default + each disc", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);

    const { r, menu } = await renderAndOpen();

    // Playlist default face is the 3-disc stack — icon-only, carries no number.
    expect(r.getByTestId("disc-btn").textContent).toBe("");
    // The menu lists the m3u default followed by each disc.
    const items = within(menu.container).getAllByRole("menuitem");
    expect(items.map((i) => i.textContent.replace("✓", ""))).toEqual(["All discs (m3u)", "Disc 1", "Disc 2", "Disc 3"]);
  });

  it("lists disc-only options (no m3u default) and shows the pinned disc number in the face", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(discDefaultSelection);

    const { r, menu } = await renderAndOpen();

    // Active face = pinned disc 2 → the icon-only face shows just the number "2".
    expect(r.getByTestId("disc-btn").textContent).toBe("2");
    // No "All discs" entry — the discs ARE the options.
    const items = within(menu.container).getAllByRole("menuitem");
    expect(items.map((i) => i.textContent.replace("✓", ""))).toEqual(["Disc 1", "Disc 2"]);
    expect(within(menu.container).queryByText("All discs (m3u)")).toBeNull();
  });
});

describe("DiscSelector — selecting a disc", () => {
  beforeEach(() => {
    captured.menu = null;
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset();
    vi.mocked(backend.selectDisc).mockReset();
    vi.mocked(setLaunchOptionsConfirmed).mockClear();
    vi.mocked(setLaunchOptionsConfirmed).mockResolvedValue(true);
    vi.mocked(toaster.toast).mockReset();
  });

  it("calls selectDisc and writes nothing to the shortcut", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);
    vi.mocked(backend.selectDisc).mockResolvedValue({
      success: true,
      selected: "ff7 (Disc 2).cue",
    });

    const { menu } = await renderAndOpen();
    await act(async () => {
      fireEvent.click(within(menu.container).getByText("Disc 2"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(backend.selectDisc).toHaveBeenCalledWith(42, "ff7 (Disc 2).cue");
    // The next start opens the picked disc; the shortcut carries its appId.
    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
  });

  it("selecting the m3u default clears the pin via selectDisc(rid, null)", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ ...m3uSelection, selected: "ff7 (Disc 2).cue" });
    vi.mocked(backend.selectDisc).mockResolvedValue({
      success: true,
      selected: null,
    });

    const { menu } = await renderAndOpen();
    await act(async () => {
      fireEvent.click(within(menu.container).getByText("All discs (m3u)"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(backend.selectDisc).toHaveBeenCalledWith(42, null);
    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
  });

  it("updates the icon-only face after a successful pick", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);
    vi.mocked(backend.selectDisc).mockResolvedValue({
      success: true,
      selected: "ff7 (Disc 3).cue",
    });

    const { r, menu } = await renderAndOpen();
    await act(async () => {
      fireEvent.click(within(menu.container).getByText("Disc 3"));
      await Promise.resolve();
      await Promise.resolve();
    });

    // The face now reflects the pinned disc — its number is "3".
    expect((await r.findByTestId("disc-btn")).textContent).toBe("3");
  });

  it("toasts the failure message and does NOT confirm-set launch options when selectDisc fails", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);
    vi.mocked(backend.selectDisc).mockResolvedValue({
      success: false,
      reason: "not_found",
      message: "Disc not found in the install directory",
    });

    const { menu } = await renderAndOpen();
    await act(async () => {
      fireEvent.click(within(menu.container).getByText("Disc 2"));
      await Promise.resolve();
      await Promise.resolve();
    });

    // Non-vacuous: the exact backend message is toasted, and no shortcut write.
    expect(toaster.toast).toHaveBeenCalledWith({
      title: "Tender",
      body: "Disc not found in the install directory",
    });
    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
  });

  it("toasts a fallback on a selectDisc rejection (non-vacuous catch)", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);
    vi.mocked(backend.selectDisc).mockRejectedValue(new Error("network down"));

    const { menu } = await renderAndOpen();
    await act(async () => {
      fireEvent.click(within(menu.container).getByText("Disc 2"));
      await Promise.resolve();
      await Promise.resolve();
    });

    // Observable catch effect: a fallback toast, and no confirm-set.
    expect(toaster.toast).toHaveBeenCalledWith({ title: "Tender", body: "Failed to select disc" });
    expect(setLaunchOptionsConfirmed).not.toHaveBeenCalled();
  });
});

describe("DiscSelector — event-driven re-fetch + cleanup", () => {
  beforeEach(() => {
    captured.menu = null;
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset();
  });

  it("registers download_complete + romm_rom_uninstalled listeners on mount", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ multi_disc: false });

    render(<DiscSelector appId={100} />);

    await waitFor(() => {
      expect(hostEventListenerCount("download_complete")).toBe(1);
    });
  });

  it("re-fetches getDiscSelection on a matching download_complete (newly multi-disc)", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValueOnce({ multi_disc: false });

    const { findByTestId } = render(<DiscSelector appId={100} />);
    await waitFor(() => expect(vi.mocked(backend.getDiscSelection)).toHaveBeenCalledTimes(1));

    vi.mocked(backend.getDiscSelection).mockResolvedValueOnce(m3uSelection);

    await act(async () => {
      const event: DownloadCompleteEvent = {
        rom_id: 42,
        rom_name: "Final Fantasy VII",
        platform_name: "PSX",
        file_path: "/roms/ff7.m3u",
      };
      emitHostEvent<DownloadCompleteEvent>("download_complete", event);
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(vi.mocked(backend.getDiscSelection)).toHaveBeenCalledTimes(2);
    expect(await findByTestId("disc-btn")).toBeInTheDocument();
  });

  it("ignores download_complete for a different rom_id", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ multi_disc: false });

    render(<DiscSelector appId={100} />);
    await waitFor(() => expect(vi.mocked(backend.getDiscSelection)).toHaveBeenCalledTimes(1));

    await act(async () => {
      emitHostEvent<DownloadCompleteEvent>("download_complete", {
        rom_id: 999,
        rom_name: "Other",
        platform_name: "PSX",
        file_path: "/roms/other.m3u",
      });
      await Promise.resolve();
    });

    expect(vi.mocked(backend.getDiscSelection)).toHaveBeenCalledTimes(1);
  });

  it("hides the trigger when a matching romm_rom_uninstalled fires", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);

    const { findByTestId, container } = render(<DiscSelector appId={100} />);
    await findByTestId("disc-btn");

    await act(async () => {
      globalThis.dispatchEvent(new CustomEvent("romm_rom_uninstalled", { detail: { rom_id: 42 } }));
      await Promise.resolve();
    });

    expect(container.querySelector('[data-testid="disc-btn"]')).toBeNull();
  });

  it("removes both listeners on unmount", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ multi_disc: false });

    const { unmount } = render(<DiscSelector appId={100} />);
    await waitFor(() => expect(hostEventListenerCount("download_complete")).toBe(1));

    unmount();
    expect(hostEventListenerCount("download_complete")).toBe(0);
  });
});

function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void } {
  let resolve!: (v: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const dataChanged = (detail: unknown) =>
  act(async () => {
    globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail }));
    await Promise.resolve();
  });

const downloadComplete = (romId: number) =>
  act(async () => {
    emitHostEvent<DownloadCompleteEvent>("download_complete", {
      rom_id: romId,
      rom_name: "Final Fantasy VII",
      platform_name: "PSX",
      file_path: "/roms/ff7.m3u",
    });
    await Promise.resolve();
  });

// The version a switch binds to appId 100 in place of rom 42: its own discs, no pin.
const newVersionSelection: DiscSelection = {
  multi_disc: true,
  discs: [
    { filename: "ff7-jp (Disc 1).chd", label: "Disc 1", index: 1 },
    { filename: "ff7-jp (Disc 2).chd", label: "Disc 2", index: 2 },
    { filename: "ff7-jp (Disc 3).chd", label: "Disc 3", index: 3 },
    { filename: "ff7-jp (Disc 4).chd", label: "Disc 4", index: 4 },
  ],
  selected: null,
  default: { kind: "disc", label: "Disc 1", filename: "ff7-jp (Disc 1).chd" },
};

/** Answers each ROM's disc read from *byRom*; a ROM missing there gets a read that never lands. */
function discsByRom(byRom: Record<number, DiscSelection | Promise<DiscSelection>>): void {
  vi.mocked(backend.getDiscSelection).mockImplementation((romId: number) =>
    Promise.resolve(byRom[romId] ?? new Promise<DiscSelection>(() => undefined)),
  );
}

function openMenu(r: ReturnType<typeof render>) {
  fireEvent.click(r.getByTestId("disc-btn"));
  return render(<>{captured.menu}</>);
}

describe("DiscSelector — a version switch", () => {
  beforeEach(() => {
    captured.menu = null;
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset();
    vi.mocked(backend.selectDisc).mockReset();
  });

  it("drops the old version's discs and pin at the switch, and shows the new version's once its read lands", async () => {
    mockCachedDetail();
    const newRead = deferred<DiscSelection>();
    discsByRom({ 42: discDefaultSelection, 43: newRead.promise });
    const r = render(<DiscSelector appId={100} />);
    expect((await r.findByTestId("disc-btn")).textContent).toBe("2");

    mockCachedDetail({ rom_id: 43 });
    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });

    expect(r.queryByTestId("disc-btn")).toBeNull();

    await act(async () => {
      newRead.resolve(newVersionSelection);
      await Promise.resolve();
    });

    expect((await r.findByTestId("disc-btn")).textContent).toBe("1");
    const menu = openMenu(r);
    const items = within(menu.container).getAllByRole("menuitem");
    expect(items.map((i) => i.textContent.replace("✓", ""))).toEqual(["Disc 1", "Disc 2", "Disc 3", "Disc 4"]);
  });

  it("pins a disc picked after the switch on the new version", async () => {
    mockCachedDetail();
    discsByRom({ 42: discDefaultSelection, 43: newVersionSelection });
    vi.mocked(backend.selectDisc).mockResolvedValue({ success: true, selected: "ff7-jp (Disc 4).chd" });
    const r = render(<DiscSelector appId={100} />);
    await r.findByTestId("disc-btn");

    mockCachedDetail({ rom_id: 43 });
    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });
    await waitFor(() => expect(r.getByTestId("disc-btn").textContent).toBe("1"));

    const menu = openMenu(r);
    await act(async () => {
      fireEvent.click(within(menu.container).getByText("Disc 4"));
      await Promise.resolve();
    });

    expect(backend.selectDisc).toHaveBeenCalledExactlyOnceWith(43, "ff7-jp (Disc 4).chd");
    expect(r.getByTestId("disc-btn").textContent).toBe("4");
  });

  it("keeps its discs for another game's switch and for a change that is no switch", async () => {
    mockCachedDetail();
    discsByRom({ 42: discDefaultSelection });
    const r = render(<DiscSelector appId={100} />);
    await r.findByTestId("disc-btn");

    mockCachedDetail({ rom_id: 43 });
    await dataChanged({ type: "version_switched", app_id: 200, rom_id: 43 });
    await dataChanged({ type: "rom_pruned", app_ids: [100], rom_ids: [42] });

    expect(r.getByTestId("disc-btn").textContent).toBe("2");
    expect(getCachedGameDetail).toHaveBeenCalledTimes(1);
  });

  it("leaves the selector as the new version's read set it when the old version's disc read lands late", async () => {
    mockCachedDetail();
    const oldRead = deferred<DiscSelection>();
    discsByRom({ 42: oldRead.promise, 43: newVersionSelection });
    const r = render(<DiscSelector appId={100} />);
    await waitFor(() => expect(backend.getDiscSelection).toHaveBeenCalledWith(42));

    mockCachedDetail({ rom_id: 43 });
    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });
    await waitFor(() => expect(r.getByTestId("disc-btn").textContent).toBe("1"));

    await act(async () => {
      oldRead.resolve(discDefaultSelection);
      await Promise.resolve();
    });

    expect(r.getByTestId("disc-btn").textContent).toBe("1");
    const items = within(openMenu(r).container).getAllByRole("menuitem");
    expect(items).toHaveLength(4);
  });

  it("binds the newest detail read when an older one lands after it", async () => {
    const firstDetail = deferred<CachedGameDetail>();
    vi.mocked(getCachedGameDetail).mockReturnValueOnce(firstDetail.promise);
    discsByRom({ 42: discDefaultSelection, 43: newVersionSelection });
    const r = render(<DiscSelector appId={100} />);
    await waitFor(() => expect(getCachedGameDetail).toHaveBeenCalledTimes(1));

    mockCachedDetail({ rom_id: 43 });
    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });
    await waitFor(() => expect(r.getByTestId("disc-btn").textContent).toBe("1"));

    await act(async () => {
      firstDetail.resolve({ found: true, rom_id: 42, rom_name: "Final Fantasy VII", installed: true });
      await Promise.resolve();
    });
    await flushAsync();

    expect(backend.getDiscSelection).not.toHaveBeenCalledWith(42);
    expect(r.getByTestId("disc-btn").textContent).toBe("1");
  });

  it("reads the discs again on the new version's download_complete, and not on the old version's", async () => {
    mockCachedDetail();
    discsByRom({ 42: discDefaultSelection, 43: newVersionSelection });
    const r = render(<DiscSelector appId={100} />);
    await r.findByTestId("disc-btn");
    mockCachedDetail({ rom_id: 43 });
    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });
    await waitFor(() => expect(r.getByTestId("disc-btn").textContent).toBe("1"));
    vi.mocked(backend.getDiscSelection).mockClear();

    await downloadComplete(42);
    expect(backend.getDiscSelection).not.toHaveBeenCalled();

    await downloadComplete(43);
    expect(backend.getDiscSelection).toHaveBeenCalledExactlyOnceWith(43);
  });

  it("ignores a pick's answer for the old version that lands after a switch", async () => {
    mockCachedDetail();
    discsByRom({ 42: discDefaultSelection, 43: newVersionSelection });
    const pick = deferred<SelectDiscResult>();
    vi.mocked(backend.selectDisc).mockReturnValue(pick.promise);
    const r = render(<DiscSelector appId={100} />);
    expect((await r.findByTestId("disc-btn")).textContent).toBe("2");
    const menu = openMenu(r);
    await act(async () => {
      fireEvent.click(within(menu.container).getByText("Disc 1"));
      await Promise.resolve();
    });
    expect(backend.selectDisc).toHaveBeenCalledWith(42, "game (Disc 1).chd");

    mockCachedDetail({ rom_id: 43 });
    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });
    await waitFor(() => expect(r.getByTestId("disc-btn").textContent).toBe("1"));

    await act(async () => {
      pick.resolve({ success: true, selected: "game (Disc 1).chd" });
      await Promise.resolve();
    });
    await flushAsync();

    expect(r.getByTestId("disc-btn").textContent).toBe("1");
  });

  it("stops listening for switches once unmounted", async () => {
    mockCachedDetail();
    discsByRom({ 42: discDefaultSelection });
    const r = render(<DiscSelector appId={100} />);
    await r.findByTestId("disc-btn");
    r.unmount();

    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });

    expect(getCachedGameDetail).toHaveBeenCalledTimes(1);
  });

  it("drops a disc read for the previous game that lands after the page moved to another game", async () => {
    mockCachedDetail();
    const oldRead = deferred<DiscSelection>();
    discsByRom({ 42: oldRead.promise });
    const r = render(<DiscSelector appId={100} />);
    await waitFor(() => expect(backend.getDiscSelection).toHaveBeenCalledWith(42));

    vi.mocked(getCachedGameDetail).mockReturnValue(new Promise<CachedGameDetail>(() => undefined));
    r.rerender(<DiscSelector appId={200} />);
    await act(async () => {
      oldRead.resolve(discDefaultSelection);
      await Promise.resolve();
    });

    expect(r.queryByTestId("disc-btn")).toBeNull();
  });
});
