import "@testing-library/jest-dom/vitest";
import type { FC } from "react";
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, waitFor, act, fireEvent, within } from "@testing-library/react";
import { DiscSelector } from "./DiscSelector";
import { useDialogHost } from "./dialogs/useDialogHost";
import { DISC_ACCENT, DISC_GREY } from "../../shared/DiscGlyphs";
import { toaster } from "../../api/host";
import * as backend from "../../api/backend";
import type { CachedGameDetail, DiscSelection, VersionInfo, VersionList } from "../../api/backend";
import { emitHostEvent, hostEventListenerCount } from "../../test-utils/host-event-bus";
import {
  domListenerCount,
  installDomEventListenerSpy,
  uninstallDomEventListenerSpy,
} from "../../test-utils/dom-event-listener-spy";
import { capturePruneLeaseAdmission, isPruneLeaseAdmissionCurrent } from "../../utils/pruneLease";
import type { DownloadCompleteEvent, DownloadFailedEvent, SyncProgress } from "../../types";

vi.mock("../../utils/cachedGameDetailStore", () => ({
  getCachedGameDetail: vi.fn<(appId: number) => Promise<CachedGameDetail>>(),
  invalidateCachedGameDetail: vi.fn(),
}));

vi.mock("../../utils/steamShortcuts", () => ({
  setLaunchOptionsConfirmed: vi.fn<(appId: number, value: string) => Promise<boolean>>().mockResolvedValue(true),
}));

vi.mock("../../utils/versionSwitchApplication", () => ({
  applyCommittedVersionSwitch: vi.fn().mockResolvedValue(true),
}));

import { getCachedGameDetail } from "../../utils/cachedGameDetailStore";
import { setLaunchOptionsConfirmed } from "../../utils/steamShortcuts";
import { applyCommittedVersionSwitch } from "../../utils/versionSwitchApplication";

/** The selector with a dialog host to ask through, as the Play button gives it one. */
const Selector: FC<{ appId: number; fileMissing?: boolean }> = ({ appId, fileMissing = false }) => {
  const dialogs = useDialogHost();
  return (
    <>
      <DiscSelector appId={appId} ask={dialogs.ask} fileMissing={fileMissing} />
      {dialogs.element}
    </>
  );
};

function mockCachedDetail(overrides: Partial<CachedGameDetail> = {}): void {
  vi.mocked(getCachedGameDetail).mockResolvedValue({
    found: true,
    rom_id: 42,
    rom_name: "Final Fantasy VII",
    installed: true,
    ...overrides,
  });
}

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

const discDefaultSelection: DiscSelection = {
  multi_disc: true,
  discs: [
    { filename: "game (Disc 1).chd", label: "Disc 1", index: 1 },
    { filename: "game (Disc 2).chd", label: "Disc 2", index: 2 },
  ],
  selected: "game (Disc 2).chd",
  default: { kind: "disc", label: "Disc 1", filename: "game (Disc 1).chd" },
};

function multiVersionList(overrides: Partial<VersionList> = {}): VersionList {
  return {
    multi_version: true,
    bound_vanished: false,
    server_query_failed: false,
    versions: [
      {
        rom_id: 42,
        name: "Final Fantasy VII (USA)",
        label: "USA (v1.1)",
        regions: ["USA"],
        languages: ["En"],
        revision: "1.1",
        tags: [],
        active: true,
        is_default: true,
        installed: true,
        synced: true,
        switchable: true,
        vanished: false,
      },
      {
        rom_id: 43,
        name: "Final Fantasy VII (Japan)",
        label: "Japan",
        regions: ["Japan"],
        languages: ["Ja"],
        revision: "",
        tags: [],
        active: false,
        is_default: false,
        installed: false,
        synced: false,
        switchable: true,
        vanished: false,
      },
    ],
    ...overrides,
  };
}

function vanishedVersionList(): VersionList {
  return {
    multi_version: true,
    bound_vanished: false,
    server_query_failed: false,
    versions: [
      {
        rom_id: 42,
        name: "Final Fantasy VII (USA)",
        label: "USA (v1.1)",
        regions: ["USA"],
        languages: ["En"],
        revision: "1.1",
        tags: [],
        active: true,
        is_default: true,
        installed: true,
        synced: true,
        switchable: true,
        vanished: false,
      },
      {
        rom_id: 44,
        name: "Final Fantasy VII (Europe)",
        label: "Europe",
        regions: ["Europe"],
        languages: ["En"],
        revision: "",
        tags: [],
        active: false,
        is_default: false,
        installed: false,
        synced: true,
        switchable: false,
        vanished: true,
      },
    ],
  };
}

const flushAsync = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });

async function renderAndOpen(appId = 100, fileMissing = false) {
  const r = render(<Selector appId={appId} fileMissing={fileMissing} />);
  await r.findByTestId("disc-btn");
  await act(async () => {
    fireEvent.click(r.getByTestId("disc-btn"));
  });
  return r;
}

describe("Desktop DiscSelector — render gate", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset();
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue({ multi_version: false, bound_vanished: false });
  });

  it("renders nothing for a single-disc and single-version ROM", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ multi_disc: false });

    const { container } = render(<Selector appId={100} />);

    await waitFor(() => {
      expect(vi.mocked(backend.getDiscSelection)).toHaveBeenCalledWith(42);
    });
    expect(container.querySelector('[data-testid="disc-btn"]')).toBeNull();
  });

  it("renders nothing when the ROM is not found in the cache and single-version", async () => {
    vi.mocked(getCachedGameDetail).mockResolvedValue({ found: false });

    const { container } = render(<Selector appId={100} />);

    await flushAsync();
    expect(vi.mocked(backend.getDiscSelection)).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="disc-btn"]')).toBeNull();
  });

  it("renders nothing when the ROM is not installed and single-version", async () => {
    mockCachedDetail({ installed: false });

    const { container } = render(<Selector appId={100} />);

    await flushAsync();
    expect(vi.mocked(backend.getDiscSelection)).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="disc-btn"]')).toBeNull();
  });
});

describe("Desktop DiscSelector — multi-disc rendering", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset();
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue({ multi_version: false, bound_vanished: false });
  });

  it("shows the stacked-discs face (no number) and lists the m3u default + each disc", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);

    const r = await renderAndOpen();

    expect(r.getByTestId("disc-btn").textContent).toBe("");
    const menu = r.getByTestId("disc-menu");
    const items = within(menu).getAllByRole("menuitem");
    expect(items.map((i) => i.textContent.replace("✓", ""))).toEqual(["All discs (m3u)", "Disc 1", "Disc 2", "Disc 3"]);
    expect(within(menu).queryByTestId("disc-version-separator")).toBeNull();
  });

  it("lists disc-only options (no m3u default) and shows the pinned disc number in the face", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(discDefaultSelection);

    const r = await renderAndOpen();

    expect(r.getByTestId("disc-btn").textContent).toBe("2");
    const menu = r.getByTestId("disc-menu");
    const items = within(menu).getAllByRole("menuitem");
    expect(items.map((i) => i.textContent.replace("✓", ""))).toEqual(["Disc 1", "Disc 2"]);
    expect(within(menu).queryByText("All discs (m3u)")).toBeNull();
  });

  it("closes the menu on outside click", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);

    const r = await renderAndOpen();
    expect(r.getByTestId("disc-menu")).toBeInTheDocument();

    await act(async () => {
      fireEvent.mouseDown(document.body);
    });

    expect(r.queryByTestId("disc-menu")).toBeNull();
  });
});

describe("Desktop DiscSelector — selecting a disc", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset();
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue({ multi_version: false, bound_vanished: false });
    vi.mocked(backend.selectDisc).mockReset();
    vi.mocked(setLaunchOptionsConfirmed).mockClear();
    vi.mocked(setLaunchOptionsConfirmed).mockResolvedValue(true);
    vi.mocked(toaster.toast).mockReset();
  });

  it("calls selectDisc then setLaunchOptionsConfirmed with re-baked launch_options", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);
    vi.mocked(backend.selectDisc).mockResolvedValue({
      success: true,
      launch_options: "flatpak run net.retrodeck.retrodeck '/roms/ff7 (Disc 2).cue'",
      selected: "ff7 (Disc 2).cue",
    });

    const r = await renderAndOpen();
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("Disc 2"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(backend.selectDisc).toHaveBeenCalledWith(42, "ff7 (Disc 2).cue");
    expect(setLaunchOptionsConfirmed).toHaveBeenCalledWith(
      100,
      "flatpak run net.retrodeck.retrodeck '/roms/ff7 (Disc 2).cue'",
    );
    expect(r.queryByTestId("disc-menu")).toBeNull();
  });

  it("selecting the m3u default clears the pin via selectDisc(rid, null)", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ ...m3uSelection, selected: "ff7 (Disc 2).cue" });
    vi.mocked(backend.selectDisc).mockResolvedValue({
      success: true,
      launch_options: "flatpak run net.retrodeck.retrodeck '/roms/ff7.m3u'",
      selected: null,
    });

    const r = await renderAndOpen();
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("All discs (m3u)"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(backend.selectDisc).toHaveBeenCalledWith(42, null);
    expect(setLaunchOptionsConfirmed).toHaveBeenCalledWith(100, "flatpak run net.retrodeck.retrodeck '/roms/ff7.m3u'");
  });

  it("updates the face after a successful pick", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);
    vi.mocked(backend.selectDisc).mockResolvedValue({
      success: true,
      launch_options: "cmd '/roms/ff7 (Disc 3).cue'",
      selected: "ff7 (Disc 3).cue",
    });

    const r = await renderAndOpen();
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("Disc 3"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect((await r.findByTestId("disc-btn")).textContent).toBe("3");
  });

  it("shows error toast when selectDisc fails", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);
    vi.mocked(backend.selectDisc).mockResolvedValue({
      success: false,
      reason: "not_found",
      message: "Disc not found",
    });

    const r = await renderAndOpen();
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("Disc 2"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(toaster.toast).toHaveBeenCalledWith(
      expect.objectContaining({
        title: "Tender",
        body: "Disc not found",
      }),
    );
  });
});

describe("Desktop DiscSelector — multi-version rendering and switching", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset();
    vi.mocked(backend.getVersionList).mockReset();
    vi.mocked(backend.switchVersion).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "imgdata" });
    vi.mocked(applyCommittedVersionSwitch).mockReset().mockResolvedValue(true);
    vi.mocked(toaster.toast).mockReset();
  });

  it("renders layer group trigger when only versions exist (single-disc)", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockResolvedValue(multiVersionList());

    const r = await renderAndOpen();

    const btn = r.getByTestId("disc-btn");
    expect(btn).toHaveAttribute("aria-label", "Select Version");

    const menu = r.getByTestId("disc-menu");
    const items = within(menu).getAllByRole("menuitem");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveTextContent("USA (v1.1)");
    expect(items[0]).toHaveTextContent("Default");
    expect(items[0]).toHaveTextContent("Downloaded");
    expect(items[0]).toHaveTextContent("✓");
    expect(items[1]).toHaveTextContent("Japan");
    expect(items[1]).toHaveTextContent("not synced");
    expect(within(menu).queryByTestId("disc-version-separator")).toBeNull();
  });

  it("switches version on clicking an inactive switchable version", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockResolvedValue(multiVersionList());
    vi.mocked(backend.switchVersion).mockResolvedValue({
      success: true,
      app_id: 100,
      rom_id: 43,
      target_installed: false,
      launch_options: "cmd '/roms/ff7-jp.chd'",
    });

    const r = await renderAndOpen();
    const menu = r.getByTestId("disc-menu");

    await act(async () => {
      fireEvent.click(within(menu).getByText("Japan"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(backend.switchVersion).toHaveBeenCalledWith(100, 43, false);
    expect(applyCommittedVersionSwitch).toHaveBeenCalledWith(
      expect.objectContaining({ success: true, rom_id: 43 }),
      expect.any(Function),
      expect.any(Object),
    );
    expect(r.queryByTestId("disc-menu")).toBeNull();
  });

  it("shows unsynced saves dialog when switchVersion returns unsynced_saves", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockResolvedValue(multiVersionList());
    vi.mocked(backend.switchVersion).mockResolvedValueOnce({
      success: false,
      reason: "unsynced_saves",
      message: "Unsynced saves exist",
      stranding: "unsynced",
      unsynced_rom_id: 42,
      unsynced_version_name: "USA (v1.1)",
      server_reachable: true,
    });

    const r = await renderAndOpen();
    const menu = r.getByTestId("disc-menu");

    await act(async () => {
      fireEvent.click(within(menu).getByText("Japan"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(r.getByRole("dialog")).toBeInTheDocument();
    expect(r.getByText(/USA \(v1\.1\).*has saves that haven't been synced to RomM/i)).toBeInTheDocument();

    vi.mocked(backend.switchVersion).mockResolvedValueOnce({
      success: true,
      app_id: 100,
      rom_id: 43,
      target_installed: false,
      launch_options: "cmd '/roms/ff7-jp.chd'",
    });

    await act(async () => {
      fireEvent.click(r.getByRole("button", { name: "Switch anyway" }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(backend.switchVersion).toHaveBeenCalledWith(100, 43, true);
    expect(r.queryByRole("dialog")).toBeNull();
  });

  it("allows cancelling unsynced saves dialog", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockResolvedValue(multiVersionList());
    vi.mocked(backend.switchVersion).mockResolvedValueOnce({
      success: false,
      reason: "unsynced_saves",
      message: "Unsynced saves exist",
      stranding: "unsynced",
      unsynced_rom_id: 42,
      unsynced_version_name: "USA (v1.1)",
      server_reachable: true,
    });

    const r = await renderAndOpen();
    const menu = r.getByTestId("disc-menu");

    await act(async () => {
      fireEvent.click(within(menu).getByText("Japan"));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(r.getByRole("dialog")).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(r.getByRole("button", { name: "Cancel" }));
    });

    expect(r.queryByRole("dialog")).toBeNull();
    expect(backend.switchVersion).toHaveBeenCalledTimes(1);
  });

  it("renders vanished version with hint and disables switching", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockResolvedValue(vanishedVersionList());

    const r = await renderAndOpen();
    const menu = r.getByTestId("disc-menu");

    const vanishedRow = within(menu).getByText("Europe").closest("button");
    expect(vanishedRow).toBeInTheDocument();
    expect(vanishedRow).toBeDisabled();
    expect(within(vanishedRow!).getByText("No longer available on RomM")).toBeInTheDocument();

    await act(async () => {
      fireEvent.click(vanishedRow!);
      await Promise.resolve();
    });

    expect(backend.switchVersion).not.toHaveBeenCalled();
  });
});

describe("Desktop DiscSelector — combined multi-disc and multi-version", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset();
    vi.mocked(backend.getVersionList).mockReset();
    vi.mocked(backend.selectDisc).mockReset();
    vi.mocked(backend.switchVersion).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
    vi.mocked(applyCommittedVersionSwitch).mockReset().mockResolvedValue(true);
  });

  it("orders discs first, then versions, separated by a light grey spacer bar, and labels button accordingly", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);
    vi.mocked(backend.getVersionList).mockResolvedValue(multiVersionList());

    const r = await renderAndOpen();

    const btn = r.getByTestId("disc-btn");
    expect(btn).toHaveAttribute("aria-label", "Select Disc or Version");

    const menu = r.getByTestId("disc-menu");
    expect(within(menu).getByTestId("disc-version-separator")).toBeInTheDocument();
    expect(within(menu).queryByText("Discs")).toBeNull();
    expect(within(menu).queryByText("Versions")).toBeNull();

    const menuItems = within(menu).getAllByRole("menuitem");
    expect(menuItems).toHaveLength(6);

    const itemTexts = menuItems.map((item) => item.textContent || "");
    expect(itemTexts[0]).toContain("All discs (m3u)");
    expect(itemTexts[1]).toContain("Disc 1");
    expect(itemTexts[2]).toContain("Disc 2");
    expect(itemTexts[3]).toContain("Disc 3");

    expect(itemTexts[4]).toContain("USA (v1.1)");
    expect(itemTexts[5]).toContain("Japan");

    const rows = Array.from(menu.querySelectorAll('[role="menuitem"], [role="separator"]'), (el) =>
      el.getAttribute("role"),
    );
    expect(rows).toEqual(["menuitem", "menuitem", "menuitem", "menuitem", "separator", "menuitem", "menuitem"]);
  });

  it("allows selecting a disc from combined menu", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);
    vi.mocked(backend.getVersionList).mockResolvedValue(multiVersionList());
    vi.mocked(backend.selectDisc).mockResolvedValue({
      success: true,
      launch_options: "flatpak run net.retrodeck.retrodeck '/roms/ff7 (Disc 2).cue'",
      selected: "ff7 (Disc 2).cue",
    });

    const r = await renderAndOpen();
    const menu = r.getByTestId("disc-menu");

    await act(async () => {
      fireEvent.click(within(menu).getByText("Disc 2"));
      await Promise.resolve();
    });

    expect(backend.selectDisc).toHaveBeenCalledWith(42, "ff7 (Disc 2).cue");
  });

  it("allows switching a version from combined menu", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);
    vi.mocked(backend.getVersionList).mockResolvedValue(multiVersionList());
    vi.mocked(backend.switchVersion).mockResolvedValue({
      success: true,
      app_id: 100,
      rom_id: 43,
      target_installed: false,
      launch_options: "cmd '/roms/ff7-jp.chd'",
    });

    const r = await renderAndOpen();
    const menu = r.getByTestId("disc-menu");

    await act(async () => {
      fireEvent.click(within(menu).getByText("Japan"));
      await Promise.resolve();
    });

    expect(backend.switchVersion).toHaveBeenCalledWith(100, 43, false);
    expect(applyCommittedVersionSwitch).toHaveBeenCalled();
  });
});

describe("Desktop DiscSelector — events lifecycle", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset();
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue({ multi_version: false, bound_vanished: false });
  });

  it("re-fetches getDiscSelection on a matching download_complete (newly multi-disc)", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValueOnce({ multi_disc: false });

    const { queryByTestId, findByTestId } = render(<Selector appId={100} />);
    await waitFor(() => expect(vi.mocked(backend.getDiscSelection)).toHaveBeenCalledTimes(1));
    expect(queryByTestId("disc-btn")).toBeNull();

    vi.mocked(backend.getDiscSelection).mockResolvedValueOnce(m3uSelection);

    await act(async () => {
      emitHostEvent("download_complete", {
        rom_id: 42,
        rom_name: "Final Fantasy VII",
        app_id: 100,
        launch_options: "flatpak run net.retrodeck.retrodeck '/roms/ff7.m3u'",
      } as DownloadCompleteEvent);
      await Promise.resolve();
    });

    expect(await findByTestId("disc-btn")).toBeInTheDocument();
    expect(vi.mocked(backend.getDiscSelection)).toHaveBeenCalledTimes(2);
  });

  it("hides on romm_rom_uninstalled if single-version", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);

    const { findByTestId, container } = render(<Selector appId={100} />);
    await findByTestId("disc-btn");

    await act(async () => {
      globalThis.dispatchEvent(new CustomEvent("romm_rom_uninstalled", { detail: { rom_id: 42 } }));
    });

    expect(container.querySelector('[data-testid="disc-btn"]')).toBeNull();
  });
});

function versionsWith(over: Partial<VersionInfo>[]): VersionList {
  const base = multiVersionList().versions ?? [];
  return multiVersionList({ versions: base.map((v, i) => ({ ...v, ...over[i] })) });
}

describe("Desktop DiscSelector — what a version row offers", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockReset();
    vi.mocked(backend.switchVersion).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
  });

  it("offers no switch to a version another shortcut of the game is on, and says so", async () => {
    mockCachedDetail();
    vi.mocked(backend.getVersionList).mockResolvedValue(versionsWith([{}, { bound_elsewhere: true }]));

    const r = await renderAndOpen();
    const row = within(r.getByTestId("disc-menu")).getByText("Japan").closest("button");

    expect(row).toBeDisabled();
    expect(row).toHaveTextContent("On another shortcut of this game");
  });

  it("offers no switch to a version RomM holds no file for unless it is downloaded", async () => {
    mockCachedDetail();
    vi.mocked(backend.getVersionList).mockResolvedValue(versionsWith([{}, { no_file: true }]));

    const r = await renderAndOpen();
    const row = within(r.getByTestId("disc-menu")).getByText("Japan").closest("button");

    expect(row).toBeDisabled();
    expect(row).toHaveTextContent("No file on RomM");
  });

  it("offers the switch to a downloaded version RomM holds no file for, with the hint", async () => {
    mockCachedDetail();
    vi.mocked(backend.getVersionList).mockResolvedValue(versionsWith([{}, { no_file: true, installed: true }]));

    const r = await renderAndOpen();
    const row = within(r.getByTestId("disc-menu")).getByText("Japan").closest("button");

    expect(row).toBeEnabled();
    expect(row).toHaveTextContent("No file on RomM");
  });

  it("offers no action on a version gone from RomM whose local data is still there", async () => {
    mockCachedDetail();
    vi.mocked(backend.getVersionList).mockResolvedValue(vanishedVersionList());

    const r = await renderAndOpen();
    const row = within(r.getByTestId("disc-menu")).getByText("Europe").closest("button");

    expect(row).toBeDisabled();
    expect(within(row!).queryByRole("img")).toBeNull();
  });

  it("draws nothing for a single version whose binding is gone from RomM", async () => {
    mockCachedDetail();
    const bound = multiVersionList().versions![0]!;
    vi.mocked(backend.getVersionList).mockResolvedValue({
      multi_version: false,
      bound_vanished: true,
      bound_version: { ...bound, vanished: true },
    });

    const { container } = render(<Selector appId={100} />);
    await waitFor(() => expect(backend.getVersionList).toHaveBeenCalled());
    await flushAsync();

    expect(container).toBeEmptyDOMElement();
  });
});

describe("Desktop DiscSelector — the trigger's colour for versions", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
  });

  const faceColour = async (list: VersionList): Promise<string | undefined> => {
    mockCachedDetail();
    vi.mocked(backend.getVersionList).mockResolvedValue(list);
    const r = render(<Selector appId={100} />);
    const btn = await r.findByTestId("disc-btn");
    return btn.querySelector("svg")?.style.color;
  };

  it("is the accent where RomM holds a default and the active version is not it", async () => {
    expect(await faceColour(versionsWith([{ is_default: false }, { is_default: true }]))).toBe(DISC_ACCENT);
  });

  it("is grey where the active version is the default", async () => {
    expect(await faceColour(multiVersionList())).toBe(DISC_GREY);
  });

  it("is grey where RomM holds no default", async () => {
    expect(await faceColour(versionsWith([{ is_default: false }, { is_default: false }]))).toBe(DISC_GREY);
  });
});

describe("Desktop DiscSelector — while a switch is under way", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue(multiVersionList());
    vi.mocked(backend.switchVersion).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
  });

  it("disables and dims the trigger until the switch is answered, with no throbber", async () => {
    mockCachedDetail();
    let answer: (v: Awaited<ReturnType<typeof backend.switchVersion>>) => void = () => {};
    vi.mocked(backend.switchVersion).mockReturnValue(new Promise((resolve) => (answer = resolve)));

    const r = await renderAndOpen();
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("Japan"));
      await Promise.resolve();
    });

    expect(r.getByTestId("disc-btn")).toBeDisabled();
    expect(r.getByTestId("disc-btn").style.opacity).toBe("0.55");
    expect(r.getByTestId("disc-btn").ownerDocument.getElementById("romm-throbber-style")).toBeNull();

    await act(async () => {
      answer({ success: false, reason: "server_unreachable", message: "offline" });
      await Promise.resolve();
    });

    expect(r.getByTestId("disc-btn")).toBeEnabled();
    expect(r.getByTestId("disc-btn").style.opacity).toBe("1");
  });
});

describe("Desktop DiscSelector — a missing file", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue(m3uSelection);
    vi.mocked(backend.getVersionList).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
  });

  it("leaves the discs out and keeps the versions", async () => {
    mockCachedDetail();
    vi.mocked(backend.getVersionList).mockResolvedValue(multiVersionList());

    const r = await renderAndOpen(100, true);
    const menu = r.getByTestId("disc-menu");

    expect(r.getByTestId("disc-btn")).toHaveAttribute("aria-label", "Select Version");
    expect(within(menu).queryByText("All discs (m3u)")).toBeNull();
    expect(within(menu).queryByTestId("disc-version-separator")).toBeNull();
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(2);
  });

  it("draws nothing for a single-version game", async () => {
    mockCachedDetail();
    vi.mocked(backend.getVersionList).mockResolvedValue({ multi_version: false, bound_vanished: false });

    const { container } = render(<Selector appId={100} fileMissing />);
    await waitFor(() => expect(backend.getDiscSelection).toHaveBeenCalled());
    await flushAsync();

    expect(container).toBeEmptyDOMElement();
  });
});

describe("Desktop DiscSelector — asking again", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset();
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue(multiVersionList());
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
  });

  it("asks for the versions again when a sync run ends, and not while it runs", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ multi_disc: false });

    const r = render(<Selector appId={100} />);
    await r.findByTestId("disc-btn");
    expect(backend.getVersionList).toHaveBeenCalledTimes(1);

    await act(async () => {
      emitHostEvent("sync_progress", { running: true, stage: "applying" } as SyncProgress);
      await Promise.resolve();
    });
    expect(backend.getVersionList).toHaveBeenCalledTimes(1);

    await act(async () => {
      emitHostEvent("sync_progress", { running: false, stage: "done" } as SyncProgress);
      await Promise.resolve();
    });
    await waitFor(() => expect(backend.getVersionList).toHaveBeenCalledTimes(2));
  });

  it("drops the discs when a switch binds a version that is not downloaded", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);

    const r = await renderAndOpen();
    expect(within(r.getByTestId("disc-menu")).getByText("Disc 1")).toBeInTheDocument();

    mockCachedDetail({ rom_id: 43, installed: false });
    await act(async () => {
      globalThis.dispatchEvent(
        new CustomEvent("romm_data_changed", { detail: { type: "version_switched", app_id: 100 } }),
      );
      await Promise.resolve();
    });

    await waitFor(() => expect(r.getByTestId("disc-btn")).toHaveAttribute("aria-label", "Select Version"));
    expect(backend.getDiscSelection).toHaveBeenCalledTimes(1);
  });
});

function deferred<T>(): { promise: Promise<T>; resolve: (v: T) => void; reject: (e: unknown) => void } {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const dataChanged = (detail: unknown) =>
  act(async () => {
    globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail }));
    await Promise.resolve();
  });

const syncFrame = (frame: Partial<SyncProgress>) =>
  act(async () => {
    emitHostEvent("sync_progress", frame as SyncProgress);
    await Promise.resolve();
  });

describe("Desktop DiscSelector — its log lines", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue(multiVersionList());
    vi.mocked(backend.switchVersion).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
  });

  it("logs a failed read of the game's detail under its tag", async () => {
    vi.mocked(getCachedGameDetail).mockRejectedValue(new Error("boom"));
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);

    render(<Selector appId={100} />);

    await waitFor(() => expect(logError).toHaveBeenCalledWith("DesktopDiscSelector: init error: Error: boom"));
    logError.mockRestore();
  });

  it("logs a failed version list under its tag", async () => {
    mockCachedDetail();
    vi.mocked(backend.getVersionList).mockReset().mockRejectedValue(new Error("down"));
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);

    render(<Selector appId={100} />);

    await waitFor(() =>
      expect(logError).toHaveBeenCalledWith("DesktopDiscSelector: getVersionList failed: Error: down"),
    );
    logError.mockRestore();
  });

  it("asks for the list again after a switch refused for a version gone, and warns when that fails", async () => {
    mockCachedDetail();
    vi.mocked(backend.switchVersion).mockResolvedValue({
      success: false,
      reason: "version_vanished",
      message: "gone",
    } as never);
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);
    const logWarn = vi.spyOn(backend, "logWarn").mockImplementation(() => undefined);

    const r = await renderAndOpen();
    vi.mocked(backend.getVersionList).mockRejectedValue(new Error("down"));
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("Japan"));
      await Promise.resolve();
    });

    await waitFor(() =>
      expect(logWarn).toHaveBeenCalledWith("DesktopDiscSelector: version-vanished list refresh failed: Error: down"),
    );
    expect(backend.getVersionList).toHaveBeenCalledTimes(2);
    expect(logError).not.toHaveBeenCalledWith(expect.stringContaining("getVersionList failed"));
    logError.mockRestore();
    logWarn.mockRestore();
  });
});

describe("Desktop DiscSelector — what it asks for", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset();
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue({ multi_version: false, bound_vanished: false });
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
  });

  it("draws nothing, and fails nothing, where the disc selection could not be read", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockRejectedValue(new Error("down"));
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);

    const { container } = render(<Selector appId={100} />);
    await waitFor(() => expect(backend.getDiscSelection).toHaveBeenCalled());
    await flushAsync();

    expect(container).toBeEmptyDOMElement();
    expect(logError).not.toHaveBeenCalledWith(expect.stringContaining("init error"));
    logError.mockRestore();
  });

  it("asks for no discs for a game the cache does not hold", async () => {
    mockCachedDetail({ found: false });

    render(<Selector appId={100} />);
    await waitFor(() => expect(getCachedGameDetail).toHaveBeenCalled());
    await flushAsync();

    expect(backend.getDiscSelection).not.toHaveBeenCalled();
  });

  it("asks for no discs where the cache names no ROM", async () => {
    mockCachedDetail({ rom_id: null as never });

    render(<Selector appId={100} />);
    await waitFor(() => expect(getCachedGameDetail).toHaveBeenCalled());
    await flushAsync();

    expect(backend.getDiscSelection).not.toHaveBeenCalled();
  });
});

describe("Desktop DiscSelector — which events it asks again for", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue(m3uSelection);
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue(multiVersionList());
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
  });

  const drawn = async () => {
    mockCachedDetail();
    const r = render(<Selector appId={100} />);
    await r.findByTestId("disc-btn");
    await waitFor(() => expect(backend.getVersionList).toHaveBeenCalledTimes(1));
    return r;
  };

  it("leaves both alone for another ROM's download", async () => {
    await drawn();

    await act(async () => {
      emitHostEvent("download_complete", { rom_id: 99, rom_name: "x", app_id: 7 } as DownloadCompleteEvent);
      await Promise.resolve();
    });
    await flushAsync();

    expect(backend.getDiscSelection).toHaveBeenCalledTimes(1);
    expect(backend.getVersionList).toHaveBeenCalledTimes(1);
  });

  it("asks for the versions again, not the discs, for another member's download", async () => {
    await drawn();

    await act(async () => {
      emitHostEvent("download_complete", { rom_id: 43, rom_name: "x", app_id: 100 } as DownloadCompleteEvent);
      await Promise.resolve();
    });

    await waitFor(() => expect(backend.getVersionList).toHaveBeenCalledTimes(2));
    expect(backend.getDiscSelection).toHaveBeenCalledTimes(1);
  });

  it("keeps the discs when another ROM is uninstalled", async () => {
    const r = await drawn();

    await act(async () => {
      globalThis.dispatchEvent(new CustomEvent("romm_rom_uninstalled", { detail: { rom_id: 99 } }));
    });

    expect(r.getByTestId("disc-btn")).toHaveAttribute("aria-label", "Select Disc or Version");
  });

  it("leaves both alone for a switch on another game", async () => {
    await drawn();

    await dataChanged({ type: "version_switched", app_id: 999, rom_id: 5 });
    await flushAsync();

    expect(getCachedGameDetail).toHaveBeenCalledTimes(1);
    expect(backend.getVersionList).toHaveBeenCalledTimes(1);
  });

  it("asks for both again for a switch on this game", async () => {
    await drawn();

    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });

    await waitFor(() => expect(backend.getVersionList).toHaveBeenCalledTimes(2));
    expect(getCachedGameDetail).toHaveBeenCalledTimes(2);
  });

  it.each([
    ["this game's shortcut", { app_ids: [100], rom_ids: [] }],
    ["a member of its group", { app_ids: [], rom_ids: [43] }],
  ])("asks for the versions again for a prune naming %s", async (_label, names) => {
    await drawn();

    await dataChanged({ type: "rom_pruned", ...names });

    await waitFor(() => expect(backend.getVersionList).toHaveBeenCalledTimes(2));
  });

  it("leaves the versions alone for a prune naming neither", async () => {
    await drawn();

    await dataChanged({ type: "rom_pruned", app_ids: [7], rom_ids: [99] });
    await flushAsync();

    expect(backend.getVersionList).toHaveBeenCalledTimes(1);
  });

  it("leaves the versions alone for a stopped frame that does not end a run", async () => {
    await drawn();

    await syncFrame({ running: false, stage: "fetching" });
    await flushAsync();

    expect(backend.getVersionList).toHaveBeenCalledTimes(1);
  });
});

describe("Desktop DiscSelector — lists that land out of order", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockReset();
    vi.mocked(backend.switchVersion).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
    vi.mocked(applyCommittedVersionSwitch).mockReset().mockResolvedValue(true);
  });

  const older = () => versionsWith([{ label: "Old USA" }, { label: "Old Japan" }]);

  it("draws the newest list when an older one lands after it", async () => {
    mockCachedDetail();
    const first = deferred<VersionList>();
    vi.mocked(backend.getVersionList).mockReturnValueOnce(first.promise).mockResolvedValue(multiVersionList());

    const r = render(<Selector appId={100} />);
    await syncFrame({ running: false, stage: "done" });
    await r.findByTestId("disc-btn");
    await act(async () => {
      first.resolve(older());
      await Promise.resolve();
    });
    fireEvent.click(r.getByTestId("disc-btn"));

    expect(within(r.getByTestId("disc-menu")).getByText("Japan")).toBeInTheDocument();
    expect(within(r.getByTestId("disc-menu")).queryByText("Old Japan")).toBeNull();
  });

  it("logs nothing for an older list that fails after a newer one landed", async () => {
    mockCachedDetail();
    const first = deferred<VersionList>();
    vi.mocked(backend.getVersionList).mockReturnValueOnce(first.promise).mockResolvedValue(multiVersionList());
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);

    const r = render(<Selector appId={100} />);
    await syncFrame({ running: false, stage: "done" });
    await r.findByTestId("disc-btn");
    await act(async () => {
      first.reject(new Error("late"));
      await Promise.resolve();
    });
    await flushAsync();

    expect(logError).not.toHaveBeenCalled();
    logError.mockRestore();
  });

  it("enables the trigger again once the list a successful switch reloads lands", async () => {
    mockCachedDetail();
    vi.mocked(backend.getVersionList).mockResolvedValue(multiVersionList());
    vi.mocked(backend.switchVersion).mockResolvedValue({
      success: true,
      app_id: 100,
      rom_id: 43,
      target_installed: false,
      launch_options: "",
    });

    const r = await renderAndOpen();
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("Japan"));
      await Promise.resolve();
    });
    await waitFor(() => expect(applyCommittedVersionSwitch).toHaveBeenCalled());
    expect(r.getByTestId("disc-btn")).toBeDisabled();

    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });

    await waitFor(() => expect(r.getByTestId("disc-btn")).toBeEnabled());
  });

  it("keeps the trigger disabled when a list asked before the switch lands during it", async () => {
    mockCachedDetail();
    const before = deferred<VersionList>();
    const after = deferred<VersionList>();
    vi.mocked(backend.getVersionList).mockResolvedValueOnce(multiVersionList());
    vi.mocked(backend.switchVersion).mockResolvedValue({
      success: true,
      app_id: 100,
      rom_id: 43,
      target_installed: false,
      launch_options: "",
    });

    const r = await renderAndOpen();
    vi.mocked(backend.getVersionList).mockReturnValueOnce(before.promise).mockReturnValueOnce(after.promise);
    await syncFrame({ running: false, stage: "done" });
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("Japan"));
      await Promise.resolve();
    });
    await waitFor(() => expect(applyCommittedVersionSwitch).toHaveBeenCalled());
    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });

    await act(async () => {
      before.resolve(multiVersionList());
      await Promise.resolve();
    });
    await flushAsync();
    expect(r.getByTestId("disc-btn")).toBeDisabled();

    await act(async () => {
      after.resolve(multiVersionList());
      await Promise.resolve();
    });
    await waitFor(() => expect(r.getByTestId("disc-btn")).toBeEnabled());
  });
});

describe("Desktop DiscSelector — what a row and the trigger draw", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue(multiVersionList());
    vi.mocked(backend.fetchCoverBase64).mockReset();
  });

  it("draws a version's cover once it arrives", async () => {
    mockCachedDetail();
    vi.mocked(backend.fetchCoverBase64).mockResolvedValue({ base64: "imgdata" });

    const r = await renderAndOpen();

    const row = within(r.getByTestId("disc-menu")).getByText("Japan").closest("button")!;
    await waitFor(() => expect(row.querySelector("img")).toHaveAttribute("src", "data:image/png;base64,imgdata"));
  });

  it("draws the disc glyph for a version without a cover", async () => {
    mockCachedDetail();
    vi.mocked(backend.fetchCoverBase64).mockResolvedValue({ base64: "" });

    const r = await renderAndOpen();
    await flushAsync();

    const row = within(r.getByTestId("disc-menu")).getByText("Japan").closest("button")!;
    expect(row.querySelector("img")).toBeNull();
  });

  it("marks no not-synced badge on a version it cannot switch to", async () => {
    mockCachedDetail();
    vi.mocked(backend.fetchCoverBase64).mockResolvedValue({ base64: "" });
    vi.mocked(backend.getVersionList).mockResolvedValue(versionsWith([{}, { switchable: false, synced: false }]));

    const r = await renderAndOpen();

    const row = within(r.getByTestId("disc-menu")).getByText("Japan").closest("button")!;
    expect(row).not.toHaveTextContent("not synced");
  });

  it("draws two stacked discs for the playlist face and one for a pinned disc", async () => {
    mockCachedDetail();
    vi.mocked(backend.getVersionList).mockResolvedValue({ multi_version: false, bound_vanished: false });
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);
    const playlist = render(<Selector appId={100} />);
    const discsOf = (btn: HTMLElement) => btn.querySelectorAll("svg").length - 1;
    expect(discsOf(await playlist.findByTestId("disc-btn"))).toBe(2);
    playlist.unmount();

    vi.mocked(backend.getDiscSelection).mockResolvedValue(discDefaultSelection);
    const pinned = render(<Selector appId={100} />);
    expect(discsOf(await pinned.findByTestId("disc-btn"))).toBe(1);
  });

  it("marks the disc in use and no other", async () => {
    mockCachedDetail();
    vi.mocked(backend.getVersionList).mockResolvedValue({ multi_version: false, bound_vanished: false });
    vi.mocked(backend.getDiscSelection).mockResolvedValue(discDefaultSelection);

    const r = await renderAndOpen();
    const items = within(r.getByTestId("disc-menu")).getAllByRole("menuitem");

    expect(items.map((i) => i.textContent.includes("✓"))).toEqual([false, true]);
  });
});

describe("Desktop DiscSelector — after it is gone", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue(multiVersionList());
    vi.mocked(backend.switchVersion).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
  });

  it("asks for nothing, and logs nothing, when a refusal for a version gone lands after it left", async () => {
    mockCachedDetail();
    const refusal = deferred<Awaited<ReturnType<typeof backend.switchVersion>>>();
    vi.mocked(backend.switchVersion).mockReturnValue(refusal.promise);
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);
    const logWarn = vi.spyOn(backend, "logWarn").mockImplementation(() => undefined);

    const r = await renderAndOpen();
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("Japan"));
      await Promise.resolve();
    });
    r.unmount();
    await act(async () => {
      refusal.resolve({ success: false, reason: "version_vanished", message: "gone" } as never);
      await Promise.resolve();
    });
    await flushAsync();

    expect(backend.getVersionList).toHaveBeenCalledTimes(1);
    expect(logError).not.toHaveBeenCalled();
    expect(logWarn).not.toHaveBeenCalled();
    logError.mockRestore();
    logWarn.mockRestore();
  });
});

describe("Desktop DiscSelector — the discs across a switch", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset();
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue(multiVersionList());
    vi.mocked(backend.selectDisc).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
  });

  it("drops the old version's discs when the read after a switch fails", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(m3uSelection);
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);
    const r = render(<Selector appId={100} />);
    await waitFor(() => expect(r.getByTestId("disc-btn")).toHaveAttribute("aria-label", "Select Disc or Version"));

    vi.mocked(getCachedGameDetail).mockRejectedValue(new Error("socket"));
    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });
    await flushAsync();

    expect(r.getByTestId("disc-btn")).toHaveAttribute("aria-label", "Select Version");
    fireEvent.click(r.getByTestId("disc-btn"));
    expect(within(r.getByTestId("disc-menu")).queryByText("Disc 2")).toBeNull();
    logError.mockRestore();
  });

  it("ignores a disc read for the old version that lands after a switch", async () => {
    mockCachedDetail();
    const oldRead = deferred<DiscSelection>();
    vi.mocked(backend.getDiscSelection).mockReturnValue(oldRead.promise);
    const r = render(<Selector appId={100} />);
    await waitFor(() => expect(backend.getDiscSelection).toHaveBeenCalledWith(42));

    mockCachedDetail({ rom_id: 43, installed: false });
    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });
    await act(async () => {
      oldRead.resolve(m3uSelection);
      await Promise.resolve();
    });
    await flushAsync();

    expect(r.getByTestId("disc-btn")).toHaveAttribute("aria-label", "Select Version");
  });
});

describe("Desktop DiscSelector — a member's install changing", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue(multiVersionList());
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
  });

  it("asks for the versions again when a member's download fails", async () => {
    mockCachedDetail();
    const r = render(<Selector appId={100} />);
    await r.findByTestId("disc-btn");

    await act(async () => {
      emitHostEvent("download_failed", { rom_id: 43 } as DownloadFailedEvent);
      await Promise.resolve();
    });

    await waitFor(() => expect(backend.getVersionList).toHaveBeenCalledTimes(2));
  });

  it("asks for the versions again when a member is uninstalled", async () => {
    mockCachedDetail();
    const r = render(<Selector appId={100} />);
    await r.findByTestId("disc-btn");

    await act(async () => {
      globalThis.dispatchEvent(new CustomEvent("romm_rom_uninstalled", { detail: { rom_id: 43 } }));
    });

    await waitFor(() => expect(backend.getVersionList).toHaveBeenCalledTimes(2));
  });
});

describe("Desktop DiscSelector — a reload during a switch", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue(multiVersionList());
    vi.mocked(backend.switchVersion).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
    vi.mocked(applyCommittedVersionSwitch).mockReset().mockResolvedValue(true);
  });

  it("keeps the trigger disabled when a sync run ends while the switch is under way", async () => {
    mockCachedDetail();
    const answer = deferred<Awaited<ReturnType<typeof backend.switchVersion>>>();
    vi.mocked(backend.switchVersion).mockReturnValue(answer.promise);

    const r = await renderAndOpen();
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("Japan"));
      await Promise.resolve();
    });
    await syncFrame({ running: false, stage: "done" });
    await waitFor(() => expect(backend.getVersionList).toHaveBeenCalledTimes(2));
    await flushAsync();
    expect(r.getByTestId("disc-btn")).toBeDisabled();

    await act(async () => {
      answer.resolve({ success: true, app_id: 100, rom_id: 43, target_installed: false, launch_options: "" });
      await Promise.resolve();
    });
    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });
    await waitFor(() => expect(r.getByTestId("disc-btn")).toBeEnabled());
  });
});

describe("Desktop DiscSelector — what clears the guard", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue(multiVersionList());
    vi.mocked(backend.switchVersion).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
    vi.mocked(applyCommittedVersionSwitch).mockReset().mockResolvedValue(true);
  });

  const success = { success: true as const, app_id: 100, rom_id: 43, target_installed: false, launch_options: "" };

  const pressJapan = async (r: ReturnType<typeof render>) => {
    fireEvent.click(r.getByTestId("disc-btn"));
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("Japan"));
      await Promise.resolve();
    });
  };

  it("keeps the trigger disabled when a prune of this game lands during the switch", async () => {
    mockCachedDetail();
    vi.mocked(backend.switchVersion).mockReturnValue(new Promise(() => undefined));
    const r = render(<Selector appId={100} />);
    await r.findByTestId("disc-btn");

    await pressJapan(r);
    await dataChanged({ type: "rom_pruned", app_ids: [100], rom_ids: [] });
    await waitFor(() => expect(backend.getVersionList).toHaveBeenCalledTimes(2));
    await flushAsync();

    expect(r.getByTestId("disc-btn")).toBeDisabled();
  });

  it("does not let an earlier switch's broadcast clear a later switch", async () => {
    mockCachedDetail();
    vi.mocked(backend.switchVersion)
      .mockResolvedValueOnce(success)
      .mockReturnValueOnce(new Promise(() => undefined));
    const r = render(<Selector appId={100} />);
    await r.findByTestId("disc-btn");

    await pressJapan(r);
    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });
    await waitFor(() => expect(r.getByTestId("disc-btn")).toBeEnabled());

    await pressJapan(r);
    expect(r.getByTestId("disc-btn")).toBeDisabled();
    await syncFrame({ running: false, stage: "done" });
    await flushAsync();

    expect(r.getByTestId("disc-btn")).toBeDisabled();
  });
});

describe("Desktop DiscSelector — the binding and the guard across a switch", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue(m3uSelection);
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue(multiVersionList());
    vi.mocked(backend.selectDisc)
      .mockReset()
      .mockResolvedValue({ success: true, launch_options: "cmd", selected: "ff7 (Disc 2).cue" });
    vi.mocked(backend.switchVersion).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
    vi.mocked(applyCommittedVersionSwitch).mockReset().mockResolvedValue(true);
  });

  const pressJapan = async (r: ReturnType<typeof render>) => {
    fireEvent.click(r.getByTestId("disc-btn"));
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("Japan"));
      await Promise.resolve();
    });
  };

  it("binds the newest detail read when the read from mount lands after a switch's", async () => {
    const mountRead = deferred<CachedGameDetail>();
    vi.mocked(getCachedGameDetail)
      .mockReturnValueOnce(mountRead.promise)
      .mockResolvedValue({ found: true, rom_id: 43, rom_name: "FF7 (Japan)", installed: true });
    const r = render(<Selector appId={100} />);

    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });
    await waitFor(() => expect(backend.getDiscSelection).toHaveBeenCalledWith(43));
    await act(async () => {
      mountRead.resolve({ found: true, rom_id: 42, rom_name: "Final Fantasy VII", installed: true });
      await Promise.resolve();
    });
    await flushAsync();
    fireEvent.click(r.getByTestId("disc-btn"));
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("Disc 2"));
      await Promise.resolve();
    });

    expect(backend.selectDisc).toHaveBeenCalledWith(43, "ff7 (Disc 2).cue");
    expect(backend.getDiscSelection).not.toHaveBeenCalledWith(42);
  });

  it("does not bring the old discs back for the old ROM's download after a switch whose read failed", async () => {
    mockCachedDetail();
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);
    const r = render(<Selector appId={100} />);
    await waitFor(() => expect(r.getByTestId("disc-btn")).toHaveAttribute("aria-label", "Select Disc or Version"));

    vi.mocked(getCachedGameDetail).mockRejectedValue(new Error("socket"));
    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });
    await act(async () => {
      emitHostEvent("download_complete", { rom_id: 42, rom_name: "x", app_id: 100 } as DownloadCompleteEvent);
      await Promise.resolve();
    });
    await flushAsync();

    expect(r.getByTestId("disc-btn")).toHaveAttribute("aria-label", "Select Version");
    logError.mockRestore();
  });

  it("enables the trigger again when the read after the broadcast fails", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.switchVersion).mockResolvedValue({
      success: true,
      app_id: 100,
      rom_id: 43,
      target_installed: false,
      launch_options: "",
    });
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);
    const r = render(<Selector appId={100} />);
    await r.findByTestId("disc-btn");

    await pressJapan(r);
    await waitFor(() => expect(applyCommittedVersionSwitch).toHaveBeenCalled());
    vi.mocked(backend.getVersionList).mockRejectedValueOnce(new Error("down"));
    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 43 });

    await waitFor(() => expect(r.getByTestId("disc-btn")).toBeEnabled());
    logError.mockRestore();
  });

  it("does not count another game's broadcast, nor one from before the switch began", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.switchVersion).mockReturnValue(new Promise(() => undefined));
    const earlier = deferred<VersionList>();
    const r = render(<Selector appId={100} />);
    await r.findByTestId("disc-btn");

    vi.mocked(backend.getVersionList).mockReturnValueOnce(earlier.promise);
    await dataChanged({ type: "version_switched", app_id: 100, rom_id: 42 });
    await pressJapan(r);
    await dataChanged({ type: "version_switched", app_id: 999, rom_id: 7 });
    await syncFrame({ running: false, stage: "done" });
    await flushAsync();

    expect(r.getByTestId("disc-btn")).toBeDisabled();
    await act(async () => {
      earlier.resolve(multiVersionList());
      await Promise.resolve();
    });
  });

  it("logs the shared helpers' failures under its own tag", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockRejectedValueOnce(new Error("disc read")).mockResolvedValue(m3uSelection);
    vi.mocked(backend.selectDisc).mockRejectedValue(new Error("pick"));
    vi.mocked(backend.switchVersion).mockRejectedValue(new Error("switch"));
    const logError = vi.spyOn(backend, "logError").mockImplementation(() => undefined);
    const r = render(<Selector appId={100} />);
    await waitFor(() =>
      expect(logError).toHaveBeenCalledWith("DesktopDiscSelector: getDiscSelection failed: Error: disc read"),
    );

    await act(async () => {
      emitHostEvent("download_complete", { rom_id: 42, rom_name: "x", app_id: 100 } as DownloadCompleteEvent);
      await Promise.resolve();
    });
    await waitFor(() => expect(r.getByTestId("disc-btn")).toHaveAttribute("aria-label", "Select Disc or Version"));
    fireEvent.click(r.getByTestId("disc-btn"));
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("Disc 2"));
      await Promise.resolve();
    });
    await waitFor(() => expect(logError).toHaveBeenCalledWith("DesktopDiscSelector: selectDisc failed: Error: pick"));
    await pressJapan(r);

    await waitFor(() =>
      expect(logError).toHaveBeenCalledWith("DesktopDiscSelector: switchVersion failed: Error: switch"),
    );
    logError.mockRestore();
  });
});

describe("Desktop DiscSelector — a change of game and a late read", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue(multiVersionList());
    vi.mocked(backend.switchVersion).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
  });

  it("enables the trigger for the next game when the game changes while a switch is unanswered", async () => {
    mockCachedDetail();
    vi.mocked(backend.switchVersion).mockReturnValue(new Promise(() => undefined));
    const r = render(<Selector appId={100} />);
    await r.findByTestId("disc-btn");
    fireEvent.click(r.getByTestId("disc-btn"));
    await act(async () => {
      fireEvent.click(within(r.getByTestId("disc-menu")).getByText("Japan"));
      await Promise.resolve();
    });
    expect(r.getByTestId("disc-btn")).toBeDisabled();

    r.rerender(<Selector appId={101} />);
    await flushAsync();

    expect(r.getByTestId("disc-btn")).toBeEnabled();
  });

  it("asks for no discs when the detail read lands after it is gone", async () => {
    const read = deferred<CachedGameDetail>();
    vi.mocked(getCachedGameDetail).mockReturnValue(read.promise);
    const r = render(<Selector appId={100} />);
    await waitFor(() => expect(getCachedGameDetail).toHaveBeenCalled());

    r.unmount();
    await act(async () => {
      read.resolve({ found: true, rom_id: 42, rom_name: "Final Fantasy VII", installed: true });
      await Promise.resolve();
    });
    await flushAsync();

    expect(backend.getDiscSelection).not.toHaveBeenCalled();
  });
});

describe("Desktop DiscSelector — its owners and listeners while mounted", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getDiscSelection).mockReset().mockResolvedValue(m3uSelection);
    vi.mocked(backend.getVersionList).mockReset().mockResolvedValue(multiVersionList());
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
    installDomEventListenerSpy();
  });

  afterEach(() => {
    uninstallDomEventListenerSpy();
  });

  it("holds its own two lease owners while mounted and gives them up when it goes", async () => {
    mockCachedDetail();
    const live = (owner: string) => isPruneLeaseAdmissionCurrent(capturePruneLeaseAdmission(owner));

    const r = render(<Selector appId={100} />);
    await r.findByTestId("disc-btn");
    expect(live("desktop-disc-selector:100")).toBe(true);
    expect(live("desktop-version-picker:100")).toBe(true);

    r.unmount();
    expect(live("desktop-disc-selector:100")).toBe(false);
    expect(live("desktop-version-picker:100")).toBe(false);
  });

  it("stops listening once it is gone", async () => {
    mockCachedDetail();
    const before = {
      uninstalled: domListenerCount("romm_rom_uninstalled"),
      changed: domListenerCount("romm_data_changed"),
    };

    const r = render(<Selector appId={100} />);
    await r.findByTestId("disc-btn");
    expect(hostEventListenerCount("download_complete")).toBe(2);
    r.unmount();

    expect(hostEventListenerCount("download_complete")).toBe(0);
    expect(hostEventListenerCount("download_failed")).toBe(0);
    expect(hostEventListenerCount("sync_progress")).toBe(0);
    expect(domListenerCount("romm_rom_uninstalled")).toBe(before.uninstalled);
    expect(domListenerCount("romm_data_changed")).toBe(before.changed);
  });
});

describe("Desktop DiscSelector — the marks' colours", () => {
  beforeEach(() => {
    vi.mocked(getCachedGameDetail).mockReset();
    vi.mocked(backend.getVersionList).mockReset();
    vi.mocked(backend.fetchCoverBase64).mockReset().mockResolvedValue({ base64: "" });
  });

  it("draws the Downloaded badge in its own green", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue({ multi_disc: false });
    vi.mocked(backend.getVersionList).mockResolvedValue(multiVersionList());

    const r = await renderAndOpen();

    const badge = within(r.getByTestId("disc-menu")).getByText("Downloaded");
    expect(badge.style.color).toBe("#7ac74f");
  });

  it("draws a pinned disc's face in the accent", async () => {
    mockCachedDetail();
    vi.mocked(backend.getDiscSelection).mockResolvedValue(discDefaultSelection);
    vi.mocked(backend.getVersionList).mockResolvedValue({ multi_version: false, bound_vanished: false });

    const r = render(<Selector appId={100} />);
    const btn = await r.findByTestId("disc-btn");

    expect((btn.firstElementChild as HTMLElement).style.color).toBe(DISC_ACCENT);
  });
});
