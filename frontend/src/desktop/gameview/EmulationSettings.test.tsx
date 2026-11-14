import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import * as backend from "../../api/backend";
import type { BiosAnswer } from "../../api/backend";
import * as sharedReads from "../../api/sharedReads";
import * as coreOverride from "../../utils/coreOverride";
import * as gameDetailStore from "../../utils/gameDetailStore";
import { forgetIdentifications } from "../../utils/firmwareIdentifications";
import { setStrandedAnswer } from "../../test-utils/stranded-panel";
import type { BiosFileStatus, BiosStatus, EmulatorOption, GameLaunch } from "../../types";
import { EmulationSettings } from "./EmulationSettings";

vi.mock("../../api/sharedReads", () => ({
  getBiosStatusShared: vi.fn(),
}));

vi.mock("../../utils/coreOverride", () => ({
  executeGameCoreChange: vi.fn(),
}));

vi.mock("../../utils/gameDetailStore", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../utils/gameDetailStore")>();
  return {
    ...actual,
    refreshBiosStatus: vi.fn().mockResolvedValue(undefined),
    refreshCoreAndBios: vi.fn().mockResolvedValue(undefined),
  };
});

const APP_ID = 12345;
const ROM_ID = 42;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (err: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function makeEntry(label: string, overrides: Partial<EmulatorOption> = {}): EmulatorOption {
  return {
    label,
    kind: "core",
    core_so: `${label.toLowerCase()}_libretro`,
    emulator: `${label.toLowerCase()}_libretro.so`,
    declared_index: 0,
    is_default: false,
    chosen_in_frontend: false,
    reason: null,
    ...overrides,
  };
}

function makeLibMatch(
  fileName: string,
  firmwareId: number,
  md5: string,
  matched: NonNullable<BiosFileStatus["library"]>["matched"] = "content",
): NonNullable<BiosFileStatus["library"]> {
  return {
    firmware_id: firmwareId,
    file_name: fileName,
    platform_slug: "psx",
    matched,
    md5,
    size: 512,
    no_checksum: false,
  };
}

function makeBiosFile(fileName: string, overrides: Partial<BiosFileStatus> = {}): BiosFileStatus {
  return {
    file_name: fileName,
    downloaded: false,
    local_path: `/bios/${fileName}`,
    declared_path: fileName,
    description: "",
    wanted: "needed",
    required_by_active: true,
    on_server: true,
    satisfied: false,
    declared_kind: "file",
    caveats: [],
    images: [],
    checked: null,
    one_of: null,
    fetch_for_required: false,
    supplied_by: null,
    missing_configured_image: null,
    configured_image_not_offered: null,
    declared_by: "DUCKSTATION",
    library: null,
    store_file: null,
    link: null,
    linked_for: null,
    linked_for_name: null,
    place_refusal: null,
    replace_offered: false,
    in_folder: null,
    ...overrides,
  };
}

const flushMacrotask = () =>
  act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });

function makeBiosAnswer(
  statusOverrides: Partial<BiosStatus> = {},
  level: BiosAnswer["bios_level"] = "missing",
): BiosAnswer {
  return {
    bios_level: level,
    bios_label: level === "ok" ? "OK" : "Missing",
    bios_status: {
      needs_bios: true,
      active_core_label: "DuckStation",
      required_count: 1,
      required_downloaded: 0,
      ratio_server_count: 2,
      ratio_local_count: 1,
      files: [makeBiosFile("scph5501.bin")],
      ...statusOverrides,
    },
  };
}

function makeDetail(overrides: Partial<gameDetailStore.GameDetailState> = {}): gameDetailStore.GameDetailState {
  const duck = makeEntry("DuckStation", { is_default: true, emulator: "DUCKSTATION", kind: "program", core_so: null });
  const beetle = makeEntry("Beetle PSX HW", { declared_index: 1, chosen_in_frontend: true });
  return {
    ...gameDetailStore.getGameDetail(-1),
    romId: ROM_ID,
    romName: "Castlevania (USA)",
    platformSlug: "psx",
    platformFolder: "psx",
    platformName: "PlayStation",
    installed: false,
    emulatorDataAvailable: true,
    emulatorSource: { kind: "retrodeck", starts_games: true },
    emulators: [duck, beetle],
    activeCoreLabel: "DuckStation",
    platformCoreLabel: "Beetle PSX HW",
    hasGameOverride: false,
    ...overrides,
  };
}

describe("EmulationSettings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    forgetIdentifications();
    vi.mocked(sharedReads.getBiosStatusShared).mockResolvedValue(makeBiosAnswer());
    vi.mocked(backend.getBiosStatus).mockResolvedValue(makeBiosAnswer());
    vi.mocked(backend.getEmulatorLaunches).mockResolvedValue([]);
    vi.mocked(coreOverride.executeGameCoreChange).mockResolvedValue({ status: "applied" });
  });

  it("draws nothing and issues no read before the ROM identity is known", () => {
    const view = render(<EmulationSettings appId={APP_ID} detail={makeDetail({ romId: null })} />);

    expect(view.container).toBeEmptyDOMElement();
    expect(sharedReads.getBiosStatusShared).not.toHaveBeenCalled();
    expect(backend.getEmulatorLaunches).not.toHaveBeenCalled();
  });

  describe("while the panel is stranded", () => {
    it("draws nothing and issues neither BIOS nor launch-verdict reads", () => {
      setStrandedAnswer("reloads");
      render(<EmulationSettings appId={APP_ID} detail={makeDetail({ installed: true })} />);

      expect(screen.queryByTestId("desktop-emulation-card")).not.toBeInTheDocument();
      expect(sharedReads.getBiosStatusShared).not.toHaveBeenCalled();
      expect(backend.getEmulatorLaunches).not.toHaveBeenCalled();
    });
  });

  describe("emulator / core picker", () => {
    it("shows the unavailable reason sentence when the answering source offers no emulator list", async () => {
      render(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            emulatorDataAvailable: false,
            emulatorDataReason: "no_source",
            emulatorSource: null,
            emulators: [],
          })}
        />,
      );

      await screen.findByText(/DuckStation/);
      expect(screen.getByTestId("desktop-emulator-unavailable")).toHaveTextContent(/emulator source/i);
      expect(screen.queryByRole("button", { name: /Use System Override/ })).not.toBeInTheDocument();
    });

    it("renders entry markers without a Use System Override row, installation notes, and disables uninstalled entries", async () => {
      const missing = makeEntry("PCSX ReARMed", { reason: "not_installed" });
      const unest = makeEntry("SwanStation", { reason: "no_find_rule" });
      render(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            emulators: [
              makeEntry("DuckStation", { is_default: true }),
              makeEntry("Beetle PSX HW", { chosen_in_frontend: true }),
              missing,
              unest,
            ],
            activeCoreLabel: "DuckStation",
            platformCoreLabel: "Beetle PSX HW",
            hasGameOverride: false,
          })}
        />,
      );

      await screen.findByTestId("bios-table-header");
      expect(screen.getByText("Switching cores may affect save compatibility")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Use System Override/ })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "DuckStation (default) ✓" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "DuckStation (default) ✓" })).toHaveAttribute("aria-pressed", "false");
      expect(screen.getByRole("button", { name: "Beetle PSX HW (chosen in ES-DE) (system)" })).toBeEnabled();
      expect(screen.getByRole("button", { name: /PCSX ReARMed — emulator not installed/ })).toBeDisabled();
      expect(screen.getByRole("button", { name: /SwanStation — installation not established/ })).toBeEnabled();
      expect(
        screen.getByText(/Beetle PSX HW is chosen in ES-DE; Tender starts this game with the emulator chosen/),
      ).toBeInTheDocument();
    });

    it("pins the default entry to (default) (pinned) on click, unpins on clicking it again, or moves the pin to another option", async () => {
      const storeRefresh = deferred<void>();
      vi.mocked(gameDetailStore.refreshCoreAndBios).mockReturnValueOnce(storeRefresh.promise);
      vi.mocked(coreOverride.executeGameCoreChange).mockImplementation(async ({ onSuccess }) => {
        await onSuccess?.();
        return { status: "applied" };
      });
      const { rerender } = render(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({ activeCoreLabel: "DuckStation", hasGameOverride: false })}
        />,
      );
      await screen.findByTestId("bios-table-header");

      // 1. Unpinned default shows (default); clicking it pins it, and rereadBios waits for refreshCoreAndBios
      fireEvent.click(screen.getByRole("button", { name: "DuckStation (default) ✓" }));
      await waitFor(() => expect(gameDetailStore.refreshCoreAndBios).toHaveBeenCalledWith(APP_ID));
      await flushMacrotask();
      expect(backend.getBiosStatus).not.toHaveBeenCalled();

      await act(async () => {
        storeRefresh.resolve();
      });
      await waitFor(() =>
        expect(coreOverride.executeGameCoreChange).toHaveBeenCalledWith(
          expect.objectContaining({
            romId: ROM_ID,
            coreLabel: "DuckStation",
            coreName: "DuckStation",
            leaseOwner: `desktop-emulation-card:${APP_ID}`,
          }),
        ),
      );
      await waitFor(() => expect(backend.getBiosStatus).toHaveBeenCalledWith(ROM_ID));

      // 2. Pinned default shows (default) (pinned); clicking it again unpins (coreLabel: null)
      rerender(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({ activeCoreLabel: "DuckStation", hasGameOverride: true })}
        />,
      );
      const pinnedDefault = screen.getByRole("button", { name: "DuckStation (default) (pinned) ✓" });
      expect(pinnedDefault).toHaveAttribute("aria-pressed", "true");
      vi.mocked(coreOverride.executeGameCoreChange).mockClear();

      fireEvent.click(pinnedDefault);
      await waitFor(() =>
        expect(coreOverride.executeGameCoreChange).toHaveBeenCalledWith(
          expect.objectContaining({
            romId: ROM_ID,
            coreLabel: null,
          }),
        ),
      );

      // 3. Clicking a different option while default is pinned moves the pin to the new option (unpinning default)
      vi.mocked(coreOverride.executeGameCoreChange).mockClear();
      fireEvent.click(screen.getByRole("button", { name: "Beetle PSX HW (chosen in ES-DE) (system)" }));
      await waitFor(() =>
        expect(coreOverride.executeGameCoreChange).toHaveBeenCalledWith(
          expect.objectContaining({
            romId: ROM_ID,
            coreLabel: "Beetle PSX HW",
            coreName: "Beetle PSX HW",
          }),
        ),
      );

      // 4. With the other option pinned, default no longer shows (pinned), and clicking the pinned option unpins it
      rerender(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({ activeCoreLabel: "Beetle PSX HW", hasGameOverride: true })}
        />,
      );
      expect(screen.getByRole("button", { name: "DuckStation (default)" })).toHaveAttribute("aria-pressed", "false");
      const pinnedOther = screen.getByRole("button", {
        name: "Beetle PSX HW (pinned) (chosen in ES-DE) (system) ✓",
      });
      expect(pinnedOther).toHaveAttribute("aria-pressed", "true");
      vi.mocked(coreOverride.executeGameCoreChange).mockClear();

      fireEvent.click(pinnedOther);
      await waitFor(() =>
        expect(coreOverride.executeGameCoreChange).toHaveBeenCalledWith(
          expect.objectContaining({
            romId: ROM_ID,
            coreLabel: null,
          }),
        ),
      );
    });

    it("shows an inline error when executeGameCoreChange fails, and stays silent when cancelled", async () => {
      vi.mocked(coreOverride.executeGameCoreChange).mockResolvedValueOnce({ status: "cancelled" });
      render(<EmulationSettings appId={APP_ID} detail={makeDetail()} />);
      await screen.findByTestId("bios-table-header");

      fireEvent.click(screen.getByRole("button", { name: /Beetle PSX HW \(chosen in ES-DE\)/ }));
      await waitFor(() => expect(coreOverride.executeGameCoreChange).toHaveBeenCalledTimes(1));
      await flushMacrotask();
      expect(screen.queryByTestId("desktop-core-error")).not.toBeInTheDocument();

      vi.mocked(coreOverride.executeGameCoreChange).mockResolvedValueOnce({
        status: "failed",
        message: "Prune is running",
      });
      fireEvent.click(screen.getByRole("button", { name: /Beetle PSX HW \(chosen in ES-DE\)/ }));
      expect(await screen.findByTestId("desktop-core-error")).toHaveTextContent("Prune is running");
    });

    it("asks per-entry launch verdicts for an installed game and renders Active / Vanished notes", async () => {
      const launches = deferred<GameLaunch[]>();
      vi.mocked(backend.getEmulatorLaunches).mockReturnValueOnce(launches.promise);
      const unestActive = makeEntry("SwanStation", { is_default: true, reason: "no_find_rule" });

      render(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            installed: true,
            emulators: [unestActive, makeEntry("Beetle PSX HW")],
            activeCoreLabel: "SwanStation",
            vanishedPick: {
              label: "OldCore",
              source: "retrodeck",
              layer: "game",
              used: "SwanStation",
              used_layer: "default",
            },
          })}
        />,
      );

      await screen.findByTestId("bios-table-header");
      expect(backend.getEmulatorLaunches).toHaveBeenCalledWith(ROM_ID);
      const beetleBtn = screen.getByRole("button", { name: /^Beetle PSX HW/ });
      expect(within(beetleBtn).getByTestId("spinner")).toBeInTheDocument();

      await act(async () => {
        launches.resolve([
          {
            state: "not_this_emulator",
            entry: "Beetle PSX HW",
            system: "psx",
            platform_name: "PlayStation",
            file: "castlevania.pbp",
            extension: ".pbp",
            starts_from: [".chd"],
            other_entries: ["SwanStation"],
            install_in: null,
            reason: "format_refused",
            contents_unread: false,
            format_unestablished: false,
          },
        ]);
      });

      expect(within(beetleBtn).queryByTestId("spinner")).not.toBeInTheDocument();
      expect(within(beetleBtn).getByText("castlevania.pbp is not a disc image it opens")).toBeInTheDocument();
      expect(screen.getByText(/Whether SwanStation is installed could not be established/)).toBeInTheDocument();
      expect(screen.getByText(/Your choice 'OldCore' no longer exists/)).toBeInTheDocument();
    });
  });

  describe("BIOS read lifecycle, version switches, and events", () => {
    it("renders Checking while pending and the failed message when the initial read rejects", async () => {
      const initial = deferred<BiosAnswer>();
      vi.mocked(sharedReads.getBiosStatusShared).mockReturnValueOnce(initial.promise);
      render(<EmulationSettings appId={APP_ID} detail={makeDetail()} />);

      expect(screen.getByTestId("bios-checking")).toHaveTextContent("Checking…");

      await act(async () => {
        initial.reject(new Error("boom"));
      });

      expect(screen.getByTestId("bios-read-failed")).toHaveTextContent(
        "Could not read the BIOS state. Open the game page again to retry.",
      );
    });

    it("explains that a library sync is needed when platformFolder is unrecorded, and re-reads once recorded", async () => {
      vi.mocked(sharedReads.getBiosStatusShared).mockResolvedValueOnce({
        bios_status: null,
        bios_level: null,
        bios_label: null,
        bios_status_unknown: true,
      });
      const { rerender } = render(<EmulationSettings appId={APP_ID} detail={makeDetail({ platformFolder: "" })} />);

      expect(await screen.findByTestId("bios-read-failed")).toHaveTextContent(
        "Could not read the BIOS state because this platform's folder in RomM is not recorded yet. Run a library sync in Tender to record it.",
      );

      rerender(<EmulationSettings appId={APP_ID} detail={makeDetail({ platformFolder: "psx" })} />);
      expect(await screen.findByText("scph5501.bin")).toBeInTheDocument();
      expect(backend.getBiosStatus).toHaveBeenCalledWith(ROM_ID);
    });

    it("keeps the held BIOS status and shows the stale warning when a re-read fails", async () => {
      render(<EmulationSettings appId={APP_ID} detail={makeDetail()} />);
      await screen.findByTestId("bios-table-header");

      vi.mocked(backend.getBiosStatus).mockRejectedValueOnce(new Error("offline"));
      await act(async () => {
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "bios", platform_slug: "psx" } }),
        );
      });

      expect(await screen.findByTestId("bios-stale")).toHaveTextContent("Could not re-read the BIOS state");
      expect(screen.getByTestId("bios-table-header")).toBeInTheDocument();
    });

    it("drops out-of-order BIOS reads for the same ROM so an older answer never overwrites a newer one", async () => {
      render(<EmulationSettings appId={APP_ID} detail={makeDetail()} />);
      await screen.findByText("scph5501.bin");

      const first = deferred<BiosAnswer>();
      const second = deferred<BiosAnswer>();
      vi.mocked(backend.getBiosStatus).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);

      act(() => {
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "bios", platform_slug: "psx" } }),
        );
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "bios", platform_slug: "psx" } }),
        );
      });

      await act(async () => {
        second.resolve(makeBiosAnswer({ files: [makeBiosFile("new-bios.bin")] }));
      });
      expect(await screen.findByText("new-bios.bin")).toBeInTheDocument();

      await act(async () => {
        first.resolve(makeBiosAnswer({ files: [makeBiosFile("stale-bios.bin")] }));
      });
      expect(screen.queryByText("stale-bios.bin")).not.toBeInTheDocument();
      expect(screen.getByText("new-bios.bin")).toBeInTheDocument();
    });

    it("re-keys to a switched ROM via direct getBiosStatus and ignores the previous version's late answer", async () => {
      const oldRead = deferred<BiosAnswer>();
      vi.mocked(sharedReads.getBiosStatusShared).mockReturnValueOnce(oldRead.promise);
      vi.mocked(backend.getBiosStatus).mockResolvedValueOnce(makeBiosAnswer({ files: [makeBiosFile("version2.bin")] }));

      const { rerender } = render(<EmulationSettings appId={APP_ID} detail={makeDetail({ romId: 42 })} />);
      expect(screen.getByTestId("bios-checking")).toBeInTheDocument();

      rerender(<EmulationSettings appId={APP_ID} detail={makeDetail({ romId: 99 })} />);
      expect(await screen.findByText("version2.bin")).toBeInTheDocument();
      expect(backend.getBiosStatus).toHaveBeenCalledWith(99);

      await act(async () => {
        oldRead.resolve(makeBiosAnswer({ files: [makeBiosFile("version1.bin")] }));
      });
      expect(screen.queryByText("version1.bin")).not.toBeInTheDocument();
    });

    it("re-reads on matching bios, core_changed, and emulator_sources events, ignoring other platforms", async () => {
      render(<EmulationSettings appId={APP_ID} detail={makeDetail({ platformSlug: "psx", platformFolder: "psx" })} />);
      await screen.findByTestId("bios-table-header");
      vi.mocked(backend.getBiosStatus).mockClear();

      act(() => {
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "bios", platform_slug: "gba" } }),
        );
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "core_changed", platform_fs_slug: "gba" } }),
        );
      });
      await flushMacrotask();
      expect(backend.getBiosStatus).not.toHaveBeenCalled();

      act(() => {
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "core_changed", platform_fs_slug: "psx" } }),
        );
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "emulator_sources" } }));
      });
      await waitFor(() => expect(backend.getBiosStatus).toHaveBeenCalledTimes(2));
    });

    it("smooth-scrolls into view when romm_tab_switch fires for bios", async () => {
      render(<EmulationSettings appId={APP_ID} detail={makeDetail()} />);
      const card = await screen.findByTestId("desktop-emulation-card");
      const scrollSpy = vi.fn();
      card.scrollIntoView = scrollSpy;

      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_tab_switch", { detail: { tab: "achievements" } }));
      });
      expect(scrollSpy).not.toHaveBeenCalled();

      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_tab_switch", { detail: { tab: "bios" } }));
      });
      expect(scrollSpy).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
    });
  });

  describe("BIOS rows, tabs, unclaimed library files, and confirmation dialogs", () => {
    it("switches shown rows via emulator tab pills and renders group blocks, store-only note, and legend", async () => {
      const answer = makeBiosAnswer({
        store_only: "unsupported",
        one_of_groups: [
          {
            state: "partial",
            covered: ["ntsc-u"],
            missing: ["ntsc-j"],
            unchecked: [],
            game_regions: ["ntsc-u"],
            regions: ["ntsc-u", "ntsc-j"],
            options: [
              { file_name: "scph5501.bin", regions: ["ntsc-u"], satisfied: true },
              { file_name: "scph5500.bin", regions: ["ntsc-j"], satisfied: false },
            ],
          },
        ],
        files: [makeBiosFile("scph5501.bin", { declared_by: "DUCKSTATION", downloaded: true, satisfied: true })],
        emulator_tabs: [
          { emulator: "DUCKSTATION", label: "DuckStation", launching: true, requirements_met: true },
          {
            emulator: "mednafen_psx_hw_libretro.so",
            label: "Beetle PSX HW",
            launching: false,
            requirements_met: false,
            rows: [makeBiosFile("scph5502.bin", { declared_by: "mednafen_psx_hw_libretro.so" })],
          },
        ],
      });
      vi.mocked(sharedReads.getBiosStatusShared).mockResolvedValueOnce(answer);

      render(<EmulationSettings appId={APP_ID} detail={makeDetail()} />);
      expect(await screen.findByText("scph5501.bin")).toBeInTheDocument();
      expect(screen.getByTestId("bios-store-only")).toBeInTheDocument();
      expect(screen.getByTestId("bios-group")).toBeInTheDocument();
      expect(screen.getByTestId("bios-legend")).toBeInTheDocument();

      const tabs = screen.getByTestId("bios-emulator-tabs");
      fireEvent.click(within(tabs).getByRole("button", { name: "Beetle PSX HW" }));
      expect(screen.getByText("scph5502.bin")).toBeInTheDocument();
      expect(screen.queryByText("scph5501.bin")).not.toBeInTheDocument();
    });

    it("runs row Download, confirms Replace and row Delete, and confirms bulk Delete downloads", async () => {
      vi.mocked(backend.downloadPlatformFirmwareFile).mockResolvedValue({
        success: true,
        message: "Downloaded",
        downloaded: 1,
      });
      vi.mocked(backend.replaceBiosFile).mockResolvedValue({
        success: true,
        placed: 1,
        left_in_place: 0,
        removed: 0,
        link_failures: [],
        backup_path: "/backups/scph5500.bin",
      });
      vi.mocked(backend.deleteBiosFile).mockResolvedValue({
        success: true,
        message: "Deleted file",
        deleted_count: 1,
      });
      vi.mocked(backend.deletePlatformBios).mockResolvedValue({
        success: true,
        message: "Deleted platform BIOS",
        deleted_count: 2,
      });
      vi.mocked(backend.identifyLibraryFirmware).mockResolvedValue({
        success: true,
        known_as: ["PS1 BIOS"],
      });

      const answer = makeBiosAnswer({
        deletable_count: 2,
        backup_root: "/backups",
        emulator_tabs: [{ emulator: "DUCKSTATION", label: "DuckStation", launching: true, requirements_met: false }],
        files: [
          makeBiosFile("missing.bin", { library: makeLibMatch("missing.bin", 11, "aaa") }),
          makeBiosFile("wrong.bin", { on_server: false, downloaded: true, replace_offered: true }),
          makeBiosFile("owned.bin", {
            on_server: false,
            downloaded: true,
            satisfied: true,
            deletable_count: 1,
            delete_name: "owned.bin",
          }),
          makeBiosFile("extra.bin", {
            declared_by: null,
            wanted: "not_needed",
            required_by_active: false,
            library: makeLibMatch("extra.bin", 22, "bbb", null),
          }),
        ],
      });
      vi.mocked(sharedReads.getBiosStatusShared).mockResolvedValueOnce(answer);
      vi.mocked(backend.getBiosStatus).mockResolvedValue(answer);

      render(<EmulationSettings appId={APP_ID} detail={makeDetail()} />);
      await screen.findByText("In your RomM library, asked for by no emulator here");
      await waitFor(() => expect(backend.identifyLibraryFirmware).toHaveBeenCalledWith("bbb"));

      const missingRow = screen.getByText("missing.bin").closest("[data-testid='bios-file-row']") as HTMLElement;
      fireEvent.click(within(missingRow).getByRole("button", { name: "Download" }));
      await waitFor(() =>
        expect(backend.downloadPlatformFirmwareFile).toHaveBeenCalledWith("psx", "psx", "missing.bin", 11, ROM_ID),
      );
      expect(gameDetailStore.refreshBiosStatus).toHaveBeenCalledWith(APP_ID);

      fireEvent.click(screen.getByRole("button", { name: "Replace…" }));
      const replaceDialog = await screen.findByRole("dialog", { name: "Replace wrong.bin?" });
      fireEvent.click(within(replaceDialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await flushMacrotask();
      expect(backend.replaceBiosFile).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole("button", { name: "Replace…" }));
      const replaceDialog2 = await screen.findByRole("dialog", { name: "Replace wrong.bin?" });
      fireEvent.click(within(replaceDialog2).getByRole("button", { name: "Replace" }));
      await waitFor(() =>
        expect(backend.replaceBiosFile).toHaveBeenCalledWith("psx", "psx", "/bios/wrong.bin", ROM_ID),
      );

      const ownedRow = screen.getByText("owned.bin").closest("[data-testid='bios-file-row']") as HTMLElement;
      fireEvent.click(within(ownedRow).getByRole("button", { name: "Delete" }));
      const deleteDialog = await screen.findByRole("dialog", { name: "Delete owned.bin?" });
      fireEvent.click(within(deleteDialog).getByRole("button", { name: "Delete" }));
      await waitFor(() => expect(backend.deleteBiosFile).toHaveBeenCalledWith("psx", "psx", "owned.bin"));

      fireEvent.click(screen.getByRole("button", { name: "Delete downloads (2)" }));
      const bulkDeleteDialog = await screen.findByRole("dialog", {
        name: "Delete Tender's BIOS downloads for PlayStation?",
      });
      fireEvent.click(within(bulkDeleteDialog).getByRole("button", { name: "Delete" }));
      await waitFor(() => expect(backend.deletePlatformBios).toHaveBeenCalledWith("psx", "psx"));
      expect(await screen.findByTestId("bios-action-status")).toHaveTextContent("Deleted platform BIOS");
    });

    it("pins header and row column tracks to fixed widths and keeps row action buttons a constant width across states", async () => {
      const pendingDownload = deferred<Awaited<ReturnType<typeof backend.downloadPlatformFirmwareFile>>>();
      vi.mocked(backend.downloadPlatformFirmwareFile).mockReturnValueOnce(pendingDownload.promise);
      vi.mocked(backend.identifyLibraryFirmware).mockResolvedValue({ success: true, known_as: [] });

      const layoutAnswer = makeBiosAnswer({
        emulator_tabs: [{ emulator: "DUCKSTATION", label: "DuckStation", launching: true, requirements_met: false }],
        files: [
          makeBiosFile("unfetchable.bin", { on_server: false }),
          makeBiosFile("fetchable.bin", { library: makeLibMatch("fetchable.bin", 31, "ccc") }),
          makeBiosFile("replace.bin", { on_server: false, downloaded: true, replace_offered: true }),
          makeBiosFile("deletable.bin", {
            on_server: false,
            downloaded: true,
            satisfied: true,
            deletable_count: 1,
            delete_name: "deletable.bin",
          }),
          makeBiosFile("unclaimed.bin", {
            declared_by: null,
            wanted: "not_needed",
            required_by_active: false,
            library: makeLibMatch("unclaimed.bin", 41, "ddd", null),
          }),
        ],
      });
      vi.mocked(sharedReads.getBiosStatusShared).mockResolvedValueOnce(layoutAnswer);

      render(<EmulationSettings appId={APP_ID} detail={makeDetail()} />);
      const shownHeader = await screen.findByTestId("bios-table-header");
      const unclaimedHeader = screen.getByTestId("bios-unclaimed-header");
      const expectedColumns = "minmax(0, 1fr) 56px 96px 88px";

      expect(shownHeader).toHaveStyle({ gridTemplateColumns: expectedColumns });
      expect(unclaimedHeader).toHaveStyle({ gridTemplateColumns: expectedColumns });
      for (const cells of screen.getAllByTestId("bios-file-cells")) {
        expect(cells).toHaveStyle({ gridTemplateColumns: expectedColumns });
      }

      const missingRow = screen.getByText("fetchable.bin").closest("[data-testid='bios-file-row']") as HTMLElement;
      const replaceRow = screen.getByText("replace.bin").closest("[data-testid='bios-file-row']") as HTMLElement;
      const ownedRow = screen.getByText("deletable.bin").closest("[data-testid='bios-file-row']") as HTMLElement;

      const downloadBtn = within(missingRow).getByRole("button", { name: "Download" });
      const replaceBtn = within(replaceRow).getByRole("button", { name: "Replace…" });
      const deleteBtn = within(ownedRow).getByRole("button", { name: "Delete" });

      for (const btn of [downloadBtn, replaceBtn, deleteBtn]) {
        expect(btn).toHaveStyle({
          width: "88px",
          minWidth: "88px",
          maxWidth: "88px",
          boxSizing: "border-box",
          justifyContent: "center",
        });
      }

      fireEvent.click(downloadBtn);
      expect(within(downloadBtn).getByTestId("spinner")).toBeInTheDocument();
      expect(downloadBtn).toHaveStyle({ width: "88px", minWidth: "88px", maxWidth: "88px" });

      await act(async () => {
        pendingDownload.resolve({ success: false, reason: "download_failed", message: "Download failed" });
      });
      expect(await within(downloadBtn).findByText("Failed")).toBeInTheDocument();
      expect(downloadBtn).toHaveStyle({ width: "88px", minWidth: "88px", maxWidth: "88px" });
    });
  });
});
