import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import * as backend from "../../api/backend";
import type { BiosAnswer } from "../../api/backend";
import * as sharedReads from "../../api/sharedReads";
import * as coreOverride from "../../utils/coreOverride";
import * as gameDetailStore from "../../utils/gameDetailStore";
import { BIOS_MISSING_RED } from "../../utils/biosColor";
import { DOWNLOAD_DAMAGED, IDENTIFYING } from "../../utils/biosPlaceWords";
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
    declared_index: null,
    is_default: false,
    chosen_in_frontend: false,
    reason: null,
    ...overrides,
  };
}

function makeLaunch(overrides: Partial<GameLaunch> = {}): GameLaunch {
  return {
    state: "starts",
    entry: "Beetle PSX HW",
    system: "psx",
    platform_name: "PlayStation",
    file: "castlevania.chd",
    extension: ".chd",
    starts_from: [".chd"],
    other_entries: [],
    install_in: null,
    reason: null,
    reason_file: null,
    contents_unread: false,
    format_unestablished: false,
    statements: [],
    start_verdict: null,
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

  it("draws nothing and issues no read before the ROM identity is known", async () => {
    const view = render(<EmulationSettings appId={APP_ID} detail={makeDetail({ romId: null })} />);
    await flushMacrotask();

    expect(view.container).toBeEmptyDOMElement();
    expect(sharedReads.getBiosStatusShared).not.toHaveBeenCalled();
    expect(backend.getEmulatorLaunches).not.toHaveBeenCalled();
  });

  describe("while the panel is stranded", () => {
    it("draws nothing and issues neither BIOS nor launch-verdict reads", async () => {
      setStrandedAnswer("reloads");
      render(<EmulationSettings appId={APP_ID} detail={makeDetail({ installed: true })} />);
      await flushMacrotask();

      expect(screen.queryByTestId("desktop-emulation-card")).not.toBeInTheDocument();
      expect(sharedReads.getBiosStatusShared).not.toHaveBeenCalled();
      expect(backend.getEmulatorLaunches).not.toHaveBeenCalled();
    });
  });

  describe("emulator / core picker", () => {
    it("shows the unavailable reason sentence when the answering source offers no emulator list", async () => {
      const { rerender } = render(
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
      expect(screen.getByTestId("desktop-emulator-unavailable")).toHaveTextContent(
        "No emulator source was found, so Tender cannot tell which emulators this platform offers.",
      );
      expect(screen.queryByRole("button", { name: /Use System Override/ })).not.toBeInTheDocument();

      rerender(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            emulatorDataAvailable: false,
            emulatorDataReason: "catalogue_invalid",
            emulatorSource: { kind: "retrodeck", starts_games: true },
            emulators: [],
          })}
        />,
      );
      expect(screen.getByTestId("desktop-emulator-unavailable")).toHaveTextContent(
        "RetroDECK: ES-DE's systems file is broken, so its emulators are not established.",
      );
    });

    it("renders Checking, startCheck failure, or No emulator starts this game when emulatorDataAvailable has no entries", async () => {
      const { rerender } = render(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            emulatorDataAvailable: true,
            emulators: [],
            activeCoreLabel: null,
            startCheck: { state: "checking" },
          })}
        />,
      );
      await screen.findByTestId("bios-table-header");

      expect(screen.getByTestId("emulator-checking")).toHaveTextContent("Checking…");
      expect(screen.queryByText("Switching cores may affect save compatibility")).not.toBeInTheDocument();

      rerender(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            emulatorDataAvailable: true,
            emulators: [],
            activeCoreLabel: null,
            startCheck: { state: "failed", why: "Could not check whether this game can start." },
          })}
        />,
      );
      expect(screen.getByTestId("desktop-emulator-unavailable")).toHaveTextContent(
        "Could not check whether this game can start.",
      );
      expect(screen.queryByText("Switching cores may affect save compatibility")).not.toBeInTheDocument();

      rerender(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            emulatorDataAvailable: true,
            emulators: [],
            activeCoreLabel: null,
            startCheck: { state: "answered" },
          })}
        />,
      );
      expect(screen.getByTestId("desktop-emulator-unavailable")).toHaveTextContent("No emulator starts this game.");
      expect(screen.queryByText("Switching cores may affect save compatibility")).not.toBeInTheDocument();
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
        screen.getByText("Beetle PSX HW is chosen in ES-DE; Tender starts this game with the emulator chosen here."),
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
      expect(screen.queryByText(/is chosen in ES-DE; Tender starts/)).not.toBeInTheDocument();
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

    it("still re-reads BIOS and announces core_changed when refreshCoreAndBios throws after saving the pin", async () => {
      const logSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => undefined);
      vi.mocked(gameDetailStore.refreshCoreAndBios).mockRejectedValueOnce(new Error("core info timeout"));
      vi.mocked(coreOverride.executeGameCoreChange).mockImplementationOnce(async ({ onSuccess }) => {
        await onSuccess?.();
        return { status: "applied" };
      });
      const events: CustomEvent[] = [];
      const onChanged = (e: Event) => events.push(e as CustomEvent);
      globalThis.addEventListener("romm_data_changed", onChanged);

      try {
        render(<EmulationSettings appId={APP_ID} detail={makeDetail()} />);
        await screen.findByTestId("bios-table-header");
        vi.mocked(backend.getBiosStatus).mockClear();

        fireEvent.click(screen.getByRole("button", { name: /Beetle PSX HW \(chosen in ES-DE\)/ }));
        await waitFor(() => expect(backend.getBiosStatus).toHaveBeenCalledWith(ROM_ID));
        expect(logSpy).toHaveBeenCalledWith(
          "DesktopEmulationSettings: core refresh failed for appId=12345 romId=42: Error: core info timeout",
        );
        expect(events.map((e) => e.detail)).toContainEqual({ type: "core_changed", platform_fs_slug: "psx" });
        expect(screen.queryByTestId("desktop-core-error")).not.toBeInTheDocument();
      } finally {
        globalThis.removeEventListener("romm_data_changed", onChanged);
        logSpy.mockRestore();
      }
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
          makeLaunch({
            state: "not_this_emulator",
            entry: "Beetle PSX HW",
            file: "castlevania.pbp",
            extension: ".pbp",
            other_entries: ["SwanStation"],
            reason: "format_refused",
          }),
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

      expect((await screen.findByTestId("bios-stale")).textContent).toBe(
        "Could not re-read the BIOS state, so what is below may be out of date. Open the game page again to retry.",
      );
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
      await flushMacrotask();
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

      const { rerender } = render(<EmulationSettings appId={APP_ID} detail={makeDetail()} />);
      await screen.findByText("In your RomM library, asked for by no emulator here");
      await waitFor(() => expect(backend.identifyLibraryFirmware).toHaveBeenCalledWith("bbb"));
      // A single-entry emulator_tabs list hides the tab bar (`tabs.length <= 1`).
      expect(screen.queryByTestId("bios-emulator-tabs")).not.toBeInTheDocument();

      const missingRow = screen.getByText("missing.bin").closest("[data-testid='bios-file-row']") as HTMLElement;
      fireEvent.click(within(missingRow).getByRole("button", { name: "Download" }));
      await waitFor(() =>
        expect(backend.downloadPlatformFirmwareFile).toHaveBeenCalledWith("psx", "psx", "missing.bin", 11, ROM_ID),
      );
      expect(gameDetailStore.refreshBiosStatus).toHaveBeenCalledWith(APP_ID);

      fireEvent.click(screen.getByRole("button", { name: "Replace…" }));
      const replaceDialog = await screen.findByRole("dialog", { name: "Replace wrong.bin?" });
      expect(replaceDialog).toHaveTextContent(
        "The file at /bios/wrong.bin goes to /backups, and Tender's copy is linked in its place.",
      );
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
      const cancelDeleteDialog = await screen.findByRole("dialog", { name: "Delete owned.bin?" });
      fireEvent.click(within(cancelDeleteDialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await flushMacrotask();
      expect(backend.deleteBiosFile).not.toHaveBeenCalled();

      // Dismissing an open confirmation dialog via null romId, version switch, or stranded panel settles false.
      fireEvent.click(within(ownedRow).getByRole("button", { name: "Delete" }));
      await screen.findByRole("dialog", { name: "Delete owned.bin?" });
      rerender(<EmulationSettings appId={APP_ID} detail={makeDetail({ romId: null })} />);
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      rerender(<EmulationSettings appId={APP_ID} detail={makeDetail({ romId: ROM_ID })} />);
      await screen.findByText("owned.bin");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

      const ownedRowBeforeSwitch = screen
        .getByText("owned.bin")
        .closest("[data-testid='bios-file-row']") as HTMLElement;
      fireEvent.click(within(ownedRowBeforeSwitch).getByRole("button", { name: "Delete" }));
      await screen.findByRole("dialog", { name: "Delete owned.bin?" });
      rerender(<EmulationSettings appId={APP_ID} detail={makeDetail({ romId: 99 })} />);
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      rerender(<EmulationSettings appId={APP_ID} detail={makeDetail({ romId: ROM_ID })} />);
      await screen.findByText("owned.bin");
      await flushMacrotask();
      expect(backend.deleteBiosFile).not.toHaveBeenCalled();

      const ownedRowConfirm = screen.getByText("owned.bin").closest("[data-testid='bios-file-row']") as HTMLElement;
      fireEvent.click(within(ownedRowConfirm).getByRole("button", { name: "Delete" }));
      const deleteDialog = await screen.findByRole("dialog", { name: "Delete owned.bin?" });
      fireEvent.click(within(deleteDialog).getByRole("button", { name: "Delete" }));
      await waitFor(() => expect(backend.deleteBiosFile).toHaveBeenCalledWith("psx", "psx", "owned.bin"));

      fireEvent.click(screen.getByRole("button", { name: "Delete downloads (2)" }));
      const cancelBulkDialog = await screen.findByRole("dialog", {
        name: "Delete Tender's BIOS downloads for PlayStation?",
      });
      fireEvent.click(within(cancelBulkDialog).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await flushMacrotask();
      expect(backend.deletePlatformBios).not.toHaveBeenCalled();

      fireEvent.click(screen.getByRole("button", { name: "Delete downloads (2)" }));
      const bulkDeleteDialog = await screen.findByRole("dialog", {
        name: "Delete Tender's BIOS downloads for PlayStation?",
      });
      fireEvent.click(within(bulkDeleteDialog).getByRole("button", { name: "Delete" }));
      await waitFor(() => expect(backend.deletePlatformBios).toHaveBeenCalledWith("psx", "psx"));
      expect(await screen.findByTestId("bios-action-status")).toHaveTextContent("Deleted platform BIOS");

      // Becoming stranded while a confirmation dialog is open unmounts it and settles false.
      vi.mocked(backend.deletePlatformBios).mockClear();
      fireEvent.click(screen.getByRole("button", { name: "Delete downloads (2)" }));
      await screen.findByRole("dialog", {
        name: "Delete Tender's BIOS downloads for PlayStation?",
      });
      act(() => setStrandedAnswer("reloads"));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      await flushMacrotask();
      expect(backend.deletePlatformBios).not.toHaveBeenCalled();
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
          makeBiosFile("fetchable_alias.bin", { library: makeLibMatch("fetchable.bin", 31, "ccc") }),
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

      const aliasRow = screen.getByText("fetchable_alias.bin").closest("[data-testid='bios-file-row']") as HTMLElement;
      const aliasDownloadBtn = within(aliasRow).getByRole("button", { name: "Download" });

      fireEvent.click(downloadBtn);
      expect(within(downloadBtn).getByTestId("spinner")).toBeInTheDocument();
      expect(downloadBtn).toHaveStyle({ width: "88px", minWidth: "88px", maxWidth: "88px" });
      // Second click while spinning is ignored (`if (busy) return;`).
      fireEvent.click(downloadBtn);
      expect(backend.downloadPlatformFirmwareFile).toHaveBeenCalledTimes(1);

      await act(async () => {
        pendingDownload.resolve({ success: false, reason: "download_failed", message: "Download failed" });
      });
      expect(await within(downloadBtn).findByText("Failed")).toHaveStyle({ color: BIOS_MISSING_RED });
      // Sibling row sharing the same library file_name is disabled and shows muted Failed (no red span).
      expect(aliasDownloadBtn).toBeDisabled();
      expect(aliasDownloadBtn).toHaveTextContent("Failed");
      expect(aliasDownloadBtn.querySelector("span")).toBeNull();
      expect(downloadBtn).toHaveStyle({ width: "88px", minWidth: "88px", maxWidth: "88px" });
    });

    it("refreshes the newly shown ROM version when a version switch happens during an in-flight BIOS action", async () => {
      const pendingDownload = deferred<Awaited<ReturnType<typeof backend.downloadPlatformFirmwareFile>>>();
      vi.mocked(backend.downloadPlatformFirmwareFile).mockReturnValueOnce(pendingDownload.promise);
      const answer42 = makeBiosAnswer({
        files: [makeBiosFile("missing.bin", { library: makeLibMatch("missing.bin", 11, "aaa") })],
      });
      const answer99Initial = makeBiosAnswer({
        files: [makeBiosFile("v99-before.bin", { library: makeLibMatch("v99-before.bin", 12, "bbb") })],
      });
      const answer99After = makeBiosAnswer({
        files: [makeBiosFile("v99-after.bin", { downloaded: true, satisfied: true })],
      });
      vi.mocked(sharedReads.getBiosStatusShared).mockResolvedValueOnce(answer42);
      vi.mocked(backend.getBiosStatus).mockResolvedValueOnce(answer99Initial).mockResolvedValueOnce(answer99After);

      const events: CustomEvent[] = [];
      const onEvent = (e: Event) => events.push(e as CustomEvent);
      globalThis.addEventListener("romm_data_changed", onEvent);

      try {
        const { rerender } = render(<EmulationSettings appId={APP_ID} detail={makeDetail({ romId: 42 })} />);
        await screen.findByText("missing.bin");

        fireEvent.click(screen.getByRole("button", { name: "Download" }));
        rerender(<EmulationSettings appId={APP_ID} detail={makeDetail({ romId: 99 })} />);
        await screen.findByText("v99-before.bin");

        await act(async () => {
          pendingDownload.resolve({ success: true, message: "Downloaded", downloaded: 1 });
        });

        expect(await screen.findByText("v99-after.bin")).toBeInTheDocument();
        expect(vi.mocked(backend.getBiosStatus).mock.calls.map(([id]) => id)).toEqual([99, 99]);
        expect(events).toHaveLength(1);
        expect(events[0]!.detail).toEqual({ type: "bios", platform_slug: "psx", platform_fs_slug: "psx" });

        // ownBiosDispatch resets in finally so a subsequent external bios event still triggers a re-read.
        vi.mocked(backend.getBiosStatus).mockClear();
        act(() => {
          globalThis.dispatchEvent(
            new CustomEvent("romm_data_changed", {
              detail: { type: "bios", platform_slug: "psx", platform_fs_slug: "psx" },
            }),
          );
        });
        await waitFor(() => expect(backend.getBiosStatus).toHaveBeenCalledWith(99));
      } finally {
        globalThis.removeEventListener("romm_data_changed", onEvent);
      }
    });

    it("covers bulk Download required / Download all busy gating, offline withdrawal, redText states, and duplicate-confirm guards", async () => {
      const launchReqDeferred = deferred<Awaited<ReturnType<typeof backend.downloadRequiredFirmware>>>();
      const reqDeferred = deferred<Awaited<ReturnType<typeof backend.downloadRequiredFirmware>>>();
      const allDeferred = deferred<Awaited<ReturnType<typeof backend.downloadAllFirmware>>>();
      const repDeferred = deferred<Awaited<ReturnType<typeof backend.replaceBiosFile>>>();
      const delRowDeferred = deferred<Awaited<ReturnType<typeof backend.deleteBiosFile>>>();
      const delAllDeferred = deferred<Awaited<ReturnType<typeof backend.deletePlatformBios>>>();
      vi.mocked(backend.downloadRequiredFirmware)
        .mockReturnValueOnce(launchReqDeferred.promise)
        .mockReturnValueOnce(reqDeferred.promise);
      vi.mocked(backend.downloadAllFirmware).mockReturnValueOnce(allDeferred.promise);
      vi.mocked(backend.replaceBiosFile).mockReturnValueOnce(repDeferred.promise);
      vi.mocked(backend.deleteBiosFile).mockReturnValueOnce(delRowDeferred.promise);
      vi.mocked(backend.deletePlatformBios).mockReturnValueOnce(delAllDeferred.promise);

      const makeRichAnswer = (overrides: Partial<BiosStatus> = {}) =>
        makeBiosAnswer({
          deletable_count: 1,
          server_count: 2,
          local_count: 0,
          emulator_tabs: [
            {
              emulator: "DUCKSTATION",
              label: "DuckStation",
              launching: true,
              installed: true,
              requirements_met: false,
            },
            {
              emulator: "mednafen_psx_hw_libretro.so",
              label: "Beetle PSX HW",
              launching: false,
              installed: true,
              requirements_met: false,
              rows: [
                makeBiosFile("scph5502.bin", {
                  declared_by: "mednafen_psx_hw_libretro.so",
                  fetch_for_required: true,
                  library: makeLibMatch("scph5502.bin", 52, "5502"),
                }),
                makeBiosFile("replace.bin", {
                  declared_by: "mednafen_psx_hw_libretro.so",
                  on_server: false,
                  downloaded: true,
                  replace_offered: true,
                  local_path: "/bios/sub/replace.bin",
                }),
                makeBiosFile("owned.bin", {
                  declared_by: "mednafen_psx_hw_libretro.so",
                  on_server: false,
                  downloaded: true,
                  satisfied: true,
                  deletable_count: 1,
                  delete_name: "owned.bin",
                }),
              ],
            },
            {
              emulator: "pcsx_rearmed_libretro.so",
              label: "PCSX ReARMed",
              launching: false,
              installed: false,
              requirements_met: false,
              rows: [],
            },
          ],
          files: [
            makeBiosFile("scph5501.bin", {
              fetch_for_required: true,
              library: makeLibMatch("scph5501.bin", 51, "5501"),
              missing_configured_image: { emulator_label: null, file_name: "scph10000.bin" },
            }),
          ],
          ...overrides,
        });

      vi.mocked(sharedReads.getBiosStatusShared).mockResolvedValueOnce(makeRichAnswer());
      vi.mocked(backend.getBiosStatus).mockImplementation(async () => makeRichAnswer());

      render(<EmulationSettings appId={APP_ID} detail={makeDetail()} />);
      const tabs = await screen.findByTestId("bios-emulator-tabs");
      const duckPill = within(tabs).getByRole("button", { name: "DuckStation" });
      const beetlePill = within(tabs).getByRole("button", { name: "Beetle PSX HW" });
      const rearmedPill = within(tabs).getByRole("button", { name: "PCSX ReARMed" });

      // Tab pill colors and aria-pressed: launching=blue, installed alt=uncolored, uninstalled alt=red.
      expect(duckPill).toHaveAttribute("aria-pressed", "true");
      expect(duckPill.style.backgroundColor).toContain("rgba(79, 163, 255");
      expect(within(duckPill).getByText("DuckStation")).toHaveStyle({ color: "#4fa3ff" });
      expect(beetlePill).toHaveAttribute("aria-pressed", "false");
      expect(beetlePill.style.backgroundColor).toContain("rgba(255, 255, 255");
      expect(within(beetlePill).getByText("Beetle PSX HW").getAttribute("style")).toBeNull();
      expect(within(rearmedPill).getByText("PCSX ReARMed")).toHaveStyle({ color: BIOS_MISSING_RED });
      expect(
        screen.getByText(
          "ℹ DuckStation's settings name scph10000.bin, which is not here — it uses another BIOS from this folder instead.",
        ),
      ).toBeInTheDocument();

      // On the launching tab, Download required passes the launching emulator's identity (`"shown"`).
      const duckReqBtn = screen.getByRole("button", { name: "Download required for DuckStation (1)" });
      fireEvent.click(duckReqBtn);
      expect(backend.downloadRequiredFirmware).toHaveBeenCalledWith("psx", "psx", "DUCKSTATION", ROM_ID);
      // Clicking the spinning Download required button a second time is ignored.
      fireEvent.click(duckReqBtn);
      expect(backend.downloadRequiredFirmware).toHaveBeenCalledTimes(1);
      await act(async () => {
        launchReqDeferred.resolve({ success: true, message: "ok", downloaded: 1 });
      });
      await waitFor(() => expect(screen.getByRole("button", { name: "Download all" })).toBeEnabled());

      // Switch to Beetle PSX HW tab and run Download required for that shown tab.
      fireEvent.click(beetlePill);
      expect(beetlePill).toHaveAttribute("aria-pressed", "true");

      const reqBtn = screen.getByRole("button", { name: "Download required for Beetle PSX HW (1)" });
      const allBtn = screen.getByRole("button", { name: "Download all" });
      const bulkDelBtn = screen.getByRole("button", { name: "Delete downloads (1)" });
      const rowDelBtn = screen.getByRole("button", { name: "Delete" });

      // Enabled delete buttons render red inner spans.
      expect(bulkDelBtn.querySelector("span")).toHaveStyle({ color: BIOS_MISSING_RED });
      expect(rowDelBtn.querySelector("span")).toHaveStyle({ color: BIOS_MISSING_RED });

      fireEvent.click(reqBtn);
      expect(backend.downloadRequiredFirmware).toHaveBeenCalledWith(
        "psx",
        "psx",
        "mednafen_psx_hw_libretro.so",
        ROM_ID,
      );
      expect(reqBtn).toBeEnabled();
      expect(within(reqBtn).getByTestId("spinner")).toBeInTheDocument();
      expect(allBtn).toBeDisabled();
      expect(bulkDelBtn).toBeDisabled();
      expect(rowDelBtn).toBeDisabled();
      // While disabled by another busy action, redText renders plain text without the red span.
      expect(bulkDelBtn.querySelector("span")).toBeNull();
      expect(rowDelBtn.querySelector("span")).toBeNull();

      await act(async () => {
        reqDeferred.resolve({ success: true, message: "ok", downloaded: 1 });
      });
      await waitFor(() => expect(allBtn).toBeEnabled());

      // Download all busy state, then failure + server_offline re-read disabling the Failed span before 2s.
      vi.mocked(backend.getBiosStatus).mockResolvedValueOnce(makeRichAnswer({ server_offline: true }));
      fireEvent.click(allBtn);
      expect(backend.downloadAllFirmware).toHaveBeenCalledWith("psx", "psx", ROM_ID);
      expect(allBtn).toBeEnabled();
      expect(within(allBtn).getByTestId("spinner")).toBeInTheDocument();
      // Clicking the spinning Download all button a second time is ignored.
      fireEvent.click(allBtn);
      expect(backend.downloadAllFirmware).toHaveBeenCalledTimes(1);
      expect(reqBtn).toBeDisabled();
      expect(bulkDelBtn).toBeDisabled();

      await act(async () => {
        allDeferred.resolve({ success: false, reason: "download_failed", message: "boom" });
      });
      expect(within(allBtn).getByText("Failed")).toHaveStyle({ color: BIOS_MISSING_RED });

      // Trigger server_offline re-read while the Failed notice is active (< 2000ms) so allBtn shows muted Failed.
      await act(async () => {
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "bios", platform_slug: "psx" } }),
        );
      });
      await waitFor(() => expect(allBtn).toBeDisabled());
      expect(allBtn).toHaveTextContent("Failed");
      expect(allBtn.querySelector("span")).toBeNull();
      expect(reqBtn).toBeDisabled();

      // Wait out the 2s Failed notice so busyFolder clears and bulkDelBtn re-enables while offline.
      await act(async () => {
        await new Promise((r) => setTimeout(r, 2050));
      });
      await waitFor(() => expect(bulkDelBtn).toBeEnabled());
      expect(allBtn).toHaveTextContent("Download all");
      // Row Download is withdrawn while server_offline is true, while Replace… and Delete remain available.
      expect(screen.queryByRole("button", { name: "Download" })).not.toBeInTheDocument();

      // Replace… without backup_root names "the backup folder", uses primary tone, and ignores clicks while busy.
      const replaceBtn = screen.getByRole("button", { name: "Replace…" });
      fireEvent.click(replaceBtn);
      const repDialog = await screen.findByRole("dialog", { name: "Replace replace.bin?" });
      expect(repDialog).toHaveTextContent(
        "The file at /bios/sub/replace.bin goes to the backup folder, and Tender's copy is linked in its place.",
      );
      const repConfirm = within(repDialog).getByRole("button", { name: "Replace" });
      expect(repConfirm.style.backgroundImage).toContain("#1a9fff");
      fireEvent.click(repConfirm);
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(replaceBtn).toBeEnabled();
      fireEvent.click(replaceBtn);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      vi.mocked(backend.getBiosStatus).mockResolvedValueOnce(makeRichAnswer({ server_offline: true }));
      await act(async () => {
        repDeferred.resolve({
          success: true,
          placed: 1,
          left_in_place: 0,
          removed: 0,
          link_failures: [{ file_name: "replace.bin", place: "/bios/sub/replace.bin", reason: "read_only" }],
          backup_path: "/backups/replace.bin",
        });
      });
      await waitFor(() => expect(bulkDelBtn).toBeEnabled());
      expect(await screen.findByText(/\/backups\/replace\.bin/)).toBeInTheDocument();

      // Row Delete uses danger tone and ignores a second click while busy.
      fireEvent.click(rowDelBtn);
      const delDialog = await screen.findByRole("dialog", { name: "Delete owned.bin?" });
      expect(within(delDialog).getByRole("button", { name: "Delete" }).style.backgroundImage).toContain("#c83c3c");
      fireEvent.click(within(delDialog).getByRole("button", { name: "Delete" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(rowDelBtn).toBeEnabled();
      fireEvent.click(rowDelBtn);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      await act(async () => {
        delRowDeferred.resolve({ success: true, message: "Deleted", deleted_count: 1 });
      });
      await waitFor(() => expect(bulkDelBtn).toBeEnabled());

      // Bulk Delete uses danger tone, disables other buttons while busy, and ignores a second click while busy.
      fireEvent.click(bulkDelBtn);
      const bulkDialog = await screen.findByRole("dialog", {
        name: "Delete Tender's BIOS downloads for PlayStation?",
      });
      expect(within(bulkDialog).getByRole("button", { name: "Delete" }).style.backgroundImage).toContain("#c83c3c");
      fireEvent.click(within(bulkDialog).getByRole("button", { name: "Delete" }));
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(bulkDelBtn).toBeEnabled();
      expect(rowDelBtn).toBeDisabled();
      fireEvent.click(bulkDelBtn);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      await act(async () => {
        delAllDeferred.resolve({ success: true, message: "Deleted all", deleted_count: 1 });
      });
    }, 10000);

    it("renders declared subfolders, descriptions, emulator-scoped mismatch wording, identifying spinner, damaged downloads, and console demand headlines", async () => {
      const idDeferred = deferred<Awaited<ReturnType<typeof backend.identifyLibraryFirmware>>>();
      vi.mocked(backend.identifyLibraryFirmware)
        .mockReturnValueOnce(idDeferred.promise)
        .mockResolvedValueOnce({ success: true, known_as: [] });
      vi.mocked(backend.downloadPlatformFirmwareFile).mockResolvedValueOnce({
        success: false,
        reason: "bios_download_damaged",
        message: "Checksum mismatch",
      });

      const answer = makeBiosAnswer(
        {
          active_core_label: null,
          console_needs_image: true,
          required_count: 2,
          required_downloaded: 1,
          deletable_count: 0,
          one_of_groups: [
            {
              state: "unmet",
              covered: [],
              missing: ["ntsc-u"],
              unchecked: [],
              game_regions: ["ntsc-u"],
              regions: ["ntsc-u"],
              options: [{ file_name: "scph5501.bin", regions: ["ntsc-u"], satisfied: false }],
            },
            {
              state: "met",
              covered: ["pal"],
              missing: [],
              unchecked: [],
              game_regions: ["pal"],
              regions: ["pal"],
              options: [{ file_name: "scph5502.bin", regions: ["pal"], satisfied: true }],
            },
          ],
          files: [
            makeBiosFile("dc_boot.bin", {
              declared_path: "dc/dc_boot.bin",
              declaration: "read",
              description: "Dreamcast boot ROM",
              checked: "mismatch",
              downloaded: true,
              satisfied: false,
              required_by_active: false,
              on_server: false,
            }),
            makeBiosFile("plain.bin", {
              declared_path: "plain.bin",
              description: "",
              downloaded: true,
              satisfied: false,
              required_by_active: false,
              used_by_active: false,
              wanted: "not_needed",
              on_server: true,
            }),
            makeBiosFile("lib_extra.bin", {
              declared_by: null,
              wanted: "not_needed",
              required_by_active: false,
              used_by_active: false,
              library: makeLibMatch("lib_extra.bin", 77, "md5-77", null),
            }),
            makeBiosFile("lib_queued.bin", {
              declared_by: null,
              wanted: "not_needed",
              required_by_active: false,
              used_by_active: false,
              library: makeLibMatch("lib_queued.bin", 78, "md5-78", null),
            }),
          ],
        },
        "missing",
      );
      vi.mocked(sharedReads.getBiosStatusShared).mockResolvedValueOnce(answer);
      vi.mocked(backend.getBiosStatus).mockResolvedValue(answer);

      const { rerender } = render(
        <EmulationSettings appId={APP_ID} detail={makeDetail({ platformName: "PlayStation" })} />,
      );
      await screen.findByText("dc_boot.bin");

      // With active_core_label null and no emulator_tabs, Download required is omitted,
      // tabs are hidden, and Delete downloads (0) has no red span.
      expect(screen.queryByRole("button", { name: /Download required/ })).not.toBeInTheDocument();
      expect(screen.queryByTestId("bios-emulator-tabs")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Delete downloads (0)" }).querySelector("span")).toBeNull();

      // Console demand headline names PlayStation and uses BODY_COLOR; falls back when platformName is empty.
      const headlineRow = screen.getByText(/PlayStation does not start without a BIOS image/).parentElement!;
      expect(headlineRow).toHaveStyle({ color: "#dfe3e6" });
      rerender(<EmulationSettings appId={APP_ID} detail={makeDetail({ platformName: "" })} />);
      expect(screen.getByText(/This system does not start without a BIOS image/)).toBeInTheDocument();

      // Subfolder span vs root file without span, description line, and clean row with single bios-file-cells child.
      const dcRow = screen.getByText("dc_boot.bin").closest("[data-testid='bios-file-row']") as HTMLElement;
      expect(within(dcRow).getByTestId("bios-file-name").querySelector("span")).toHaveTextContent("dc/");
      expect(within(dcRow).getByTestId("bios-file-name").querySelector("span")).toHaveStyle({ color: "#8f98a0" });
      expect(within(dcRow).getByText("Dreamcast boot ROM")).toBeInTheDocument();

      const plainRow = screen.getByText("plain.bin").closest("[data-testid='bios-file-row']") as HTMLElement;
      expect(within(plainRow).getByTestId("bios-file-name").querySelector("span")).toBeNull();
      expect(plainRow.children).toHaveLength(1);

      // Unclaimed library row shows IDENTIFYING with spinner only on the checksum currently
      // being asked, clears spinner once settled, and shows DOWNLOAD_DAMAGED on checksum failure.
      const libRow = screen.getByText("lib_extra.bin").closest("[data-testid='bios-file-row']") as HTMLElement;
      const queuedRow = screen.getByText("lib_queued.bin").closest("[data-testid='bios-file-row']") as HTMLElement;
      expect(within(libRow).getByText(IDENTIFYING)).toBeInTheDocument();
      expect(within(libRow).getByTestId("spinner")).toBeInTheDocument();
      expect(within(queuedRow).getByText(IDENTIFYING)).toBeInTheDocument();
      expect(within(queuedRow).queryByTestId("spinner")).not.toBeInTheDocument();

      await act(async () => {
        idDeferred.resolve({ success: true, known_as: ["PSX Extra"] });
      });
      await waitFor(() => expect(within(queuedRow).getByText("Not recognised")).toBeInTheDocument());
      expect(within(libRow).queryByTestId("spinner")).not.toBeInTheDocument();
      expect(within(libRow).queryByText(IDENTIFYING)).not.toBeInTheDocument();

      fireEvent.click(within(libRow).getByRole("button", { name: "Download" }));
      expect(await within(libRow).findByText(DOWNLOAD_DAMAGED)).toBeInTheDocument();

      // When active_core_label names the emulator, contentsWords names it in mismatch wording.
      vi.mocked(backend.getBiosStatus).mockResolvedValueOnce({
        ...answer,
        bios_status: { ...answer.bios_status!, active_core_label: "Flycast" },
      });
      await act(async () => {
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "bios", platform_slug: "psx" } }),
        );
      });
      expect(await within(dcRow).findByText("wrong file — not the image Flycast expects")).toBeInTheDocument();
    });
  });

  describe("edge transitions across picker, stranded recovery, appId switches, and unmount", () => {
    it("covers platform_system_off wording, picker styles, busy spinner, error clearing, logTags, and launch verdict transitions", async () => {
      const { rerender } = render(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            emulatorDataAvailable: false,
            emulatorDataReason: "platform_system_off",
            emulatorSource: { kind: "retrodeck", starts_games: true },
            emulatorPlatformSystem: {
              state: "switched_off",
              source: "retrodeck",
              platform: "PlayStation",
              system: "psx",
            },
            emulators: [],
          })}
        />,
      );
      await screen.findByTestId("bios-table-header");
      expect(screen.getByTestId("desktop-emulator-unavailable")).toHaveTextContent(
        "System psx is switched off in RetroDECK.",
      );

      // Uninstalled game with emulators: no picker spinners, active button is blue and
      // opacity 1, uninstalled is opacity 0.5, and matching chosen_in_frontend omits
      // frontendChoiceSentence.
      const beetleChosen = makeEntry("Beetle PSX HW", { is_default: true, chosen_in_frontend: true });
      const missing = makeEntry("PCSX ReARMed", { reason: "not_installed" });
      rerender(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            installed: false,
            emulators: [beetleChosen, missing],
            activeCoreLabel: "Beetle PSX HW",
            platformSlug: "",
          })}
        />,
      );
      const activeBtn = screen.getByRole("button", { name: /Beetle PSX HW \(default\) \(chosen in ES-DE\)/ });
      const disabledBtn = screen.getByRole("button", { name: /PCSX ReARMed — emulator not installed/ });
      expect(activeBtn).toHaveStyle({ color: "#4fa3ff", opacity: "1" });
      expect(disabledBtn).toHaveStyle({ color: "#dfe3e6", opacity: "0.5" });
      expect(within(activeBtn).queryByTestId("spinner")).not.toBeInTheDocument();
      expect(screen.queryByText(/chosen in ES-DE; Tender starts/)).not.toBeInTheDocument();

      // Empty platformSlug blocks core picks.
      fireEvent.click(activeBtn);
      await flushMacrotask();
      expect(coreOverride.executeGameCoreChange).not.toHaveBeenCalled();

      // Enabling installed triggers getEmulatorLaunches; emulator_sources cancels an
      // in-flight ask on the same romId, installed toggling true -> false -> true clears
      // stale verdicts back to spinner, and switching romId ignores stale answers.
      const swan = makeEntry("SwanStation");
      const launches42First = deferred<GameLaunch[]>();
      const launches42Second = deferred<GameLaunch[]>();
      const launches42AfterSources = deferred<GameLaunch[]>();
      const launches42Reopened = deferred<GameLaunch[]>();
      const launches99 = deferred<GameLaunch[]>();
      vi.mocked(backend.getEmulatorLaunches)
        .mockReturnValueOnce(launches42First.promise)
        .mockReturnValueOnce(launches42Second.promise)
        .mockReturnValueOnce(launches42AfterSources.promise)
        .mockReturnValueOnce(launches42Reopened.promise)
        .mockReturnValueOnce(launches99.promise);

      rerender(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            installed: true,
            romId: 42,
            emulators: [beetleChosen, swan],
            activeCoreLabel: "Beetle PSX HW",
          })}
        />,
      );
      expect(backend.getEmulatorLaunches).toHaveBeenCalledTimes(1);

      // Non-emulator_sources events do not re-ask entry launches.
      act(() => {
        globalThis.dispatchEvent(
          new CustomEvent("romm_data_changed", { detail: { type: "bios", platform_slug: "psx" } }),
        );
      });
      expect(backend.getEmulatorLaunches).toHaveBeenCalledTimes(1);

      // emulator_sources cancels launches42First and starts launches42Second on the same romId=42.
      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "emulator_sources" } }));
      });
      expect(backend.getEmulatorLaunches).toHaveBeenCalledTimes(2);
      await act(async () => {
        launches42First.resolve([
          makeLaunch({
            state: "not_this_emulator",
            entry: "Beetle PSX HW",
            file: "cancelled-same-rom.pbp",
            extension: ".pbp",
            reason: "format_refused",
          }),
        ]);
      });
      expect(screen.queryByText(/cancelled-same-rom\.pbp/)).not.toBeInTheDocument();

      await act(async () => {
        launches42Second.resolve([
          makeLaunch({
            state: "not_this_emulator",
            entry: "Beetle PSX HW",
            file: "settled42.pbp",
            extension: ".pbp",
            reason: "format_refused",
          }),
        ]);
      });
      expect(screen.getByText("settled42.pbp is not a disc image it opens")).toBeInTheDocument();

      // emulator_sources after verdicts settled clears settled42.pbp back to spinner while re-asking.
      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "emulator_sources" } }));
      });
      expect(screen.queryByText(/settled42\.pbp/)).not.toBeInTheDocument();
      expect(within(screen.getByRole("button", { name: /Beetle PSX HW/ })).getByTestId("spinner")).toBeInTheDocument();

      await act(async () => {
        launches42AfterSources.resolve([
          makeLaunch({
            state: "not_this_emulator",
            entry: "Beetle PSX HW",
            file: "settled42.pbp",
            extension: ".pbp",
            reason: "format_refused",
          }),
        ]);
      });
      expect(screen.getByText("settled42.pbp is not a disc image it opens")).toBeInTheDocument();

      // Toggling installed true -> false -> true on romId=42 clears stale settled42.pbp and shows spinner.
      rerender(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            installed: false,
            romId: 42,
            emulators: [beetleChosen, swan],
            activeCoreLabel: "Beetle PSX HW",
          })}
        />,
      );
      rerender(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            installed: true,
            romId: 42,
            emulators: [beetleChosen, swan],
            activeCoreLabel: "Beetle PSX HW",
          })}
        />,
      );
      expect(screen.queryByText(/settled42\.pbp/)).not.toBeInTheDocument();
      expect(within(screen.getByRole("button", { name: /Beetle PSX HW/ })).getByTestId("spinner")).toBeInTheDocument();

      rerender(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            installed: true,
            romId: 99,
            emulators: [beetleChosen, swan],
            activeCoreLabel: "Beetle PSX HW",
          })}
        />,
      );
      await act(async () => {
        launches42Reopened.resolve([
          makeLaunch({
            state: "not_this_emulator",
            entry: "Beetle PSX HW",
            file: "stale.pbp",
            extension: ".pbp",
            reason: "format_refused",
          }),
        ]);
      });
      expect(screen.queryByText(/stale\.pbp/)).not.toBeInTheDocument();

      await act(async () => {
        launches99.resolve([
          makeLaunch({
            state: "starts",
            entry: "Beetle PSX HW",
            file: "castlevania.chd",
            extension: ".chd",
            start_verdict: { starts: false, reason: "not_established", remarks: [] },
          }),
        ]);
      });
      const settledBtn = screen.getByRole("button", { name: /Beetle PSX HW/ });
      const unlistedBtn = screen.getByRole("button", { name: /SwanStation/ });
      expect(settledBtn.children).toHaveLength(1);
      expect(unlistedBtn.children).toHaveLength(1);

      // Overlapping core picks across romId=42 and romId=99 preserve busyRomId=99 when 42 settles first.
      const pick42 = deferred<{ status: "applied" }>();
      const pick99 = deferred<{ status: "applied" }>();
      rerender(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            installed: false,
            romId: 42,
            emulators: [beetleChosen],
            activeCoreLabel: "Beetle PSX HW",
          })}
        />,
      );
      vi.mocked(coreOverride.executeGameCoreChange).mockReturnValueOnce(pick42.promise);
      fireEvent.click(screen.getByRole("button", { name: /Beetle PSX HW/ }));

      rerender(
        <EmulationSettings
          appId={APP_ID}
          detail={makeDetail({
            installed: false,
            romId: 99,
            emulators: [beetleChosen],
            activeCoreLabel: "Beetle PSX HW",
          })}
        />,
      );
      vi.mocked(coreOverride.executeGameCoreChange).mockReturnValueOnce(pick99.promise);
      fireEvent.click(screen.getByRole("button", { name: /Beetle PSX HW/ }));
      const overlapHeading = screen.getByText("EMULATOR / CORE").parentElement!;
      expect(within(overlapHeading).getByTestId("spinner")).toBeInTheDocument();

      await act(async () => {
        pick42.resolve({ status: "applied" });
      });
      expect(within(overlapHeading).getByTestId("spinner")).toBeInTheDocument();
      await act(async () => {
        pick99.resolve({ status: "applied" });
      });
      await waitFor(() => expect(within(overlapHeading).queryByTestId("spinner")).not.toBeInTheDocument());
      vi.mocked(coreOverride.executeGameCoreChange).mockClear();

      // Core pick failure -> subsequent core pick busy spinner, duplicate-click guard,
      // logTag, call order, error clearing, and ownCoreDispatch reset.
      vi.mocked(coreOverride.executeGameCoreChange).mockResolvedValueOnce({ status: "failed", message: "First error" });
      fireEvent.click(settledBtn);
      expect(await screen.findByTestId("desktop-core-error")).toHaveTextContent("First error");
      expect(coreOverride.executeGameCoreChange).toHaveBeenLastCalledWith(
        expect.objectContaining({ logTag: "desktopChangeGameCore" }),
      );

      const coreChangePending = deferred<{ status: "applied" }>();
      const callOrder: string[] = [];
      vi.mocked(backend.getBiosStatus).mockImplementation(async () => {
        callOrder.push("rereadBios");
        return makeBiosAnswer({ files: [] });
      });
      const onCoreEvent = (e: Event) => {
        if ((e as CustomEvent).detail?.type === "core_changed") callOrder.push("announceCore");
      };
      globalThis.addEventListener("romm_data_changed", onCoreEvent);

      try {
        rerender(
          <EmulationSettings
            appId={APP_ID}
            detail={makeDetail({
              installed: true,
              romId: 99,
              emulators: [beetleChosen],
              activeCoreLabel: "Beetle PSX HW",
              hasGameOverride: true,
            })}
          />,
        );
        vi.mocked(coreOverride.executeGameCoreChange).mockImplementationOnce(async ({ onSuccess }) => {
          const result = await coreChangePending.promise;
          await onSuccess?.();
          return result;
        });

        const pinnedBtn = screen.getByRole("button", { name: /Beetle PSX HW.*\(pinned\)/ });
        fireEvent.click(pinnedBtn);
        expect(screen.queryByTestId("desktop-core-error")).not.toBeInTheDocument();
        const headingRow = screen.getByText("EMULATOR / CORE").parentElement!;
        expect(within(headingRow).getByTestId("spinner")).toBeInTheDocument();
        expect(pinnedBtn).toBeDisabled();
        fireEvent.click(pinnedBtn);
        expect(coreOverride.executeGameCoreChange).toHaveBeenCalledTimes(2);
        expect(coreOverride.executeGameCoreChange).toHaveBeenLastCalledWith(
          expect.objectContaining({ logTag: "desktopResetGameCore" }),
        );

        callOrder.length = 0;
        await act(async () => {
          coreChangePending.resolve({ status: "applied" });
        });
        await waitFor(() => expect(within(headingRow).queryByTestId("spinner")).not.toBeInTheDocument());
        expect(callOrder).toEqual(["rereadBios", "announceCore"]);

        // External core_changed right after own core change still re-reads (ownCoreDispatch reset in finally).
        vi.mocked(backend.getBiosStatus).mockClear();
        act(() => {
          globalThis.dispatchEvent(
            new CustomEvent("romm_data_changed", { detail: { type: "core_changed", platform_fs_slug: "psx" } }),
          );
        });
        await waitFor(() => expect(backend.getBiosStatus).toHaveBeenCalledWith(99));
      } finally {
        globalThis.removeEventListener("romm_data_changed", onCoreEvent);
      }
    });

    it("logs failed BIOS reads, ignores events while romId is null or stranded, re-reads on appId change, and detaches tab listener on unmount", async () => {
      const logSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => undefined);
      vi.mocked(sharedReads.getBiosStatusShared).mockRejectedValueOnce(new Error("read boom"));

      const { unmount: unmountFailed } = render(
        <EmulationSettings appId={APP_ID} detail={makeDetail({ romId: 42 })} />,
      );
      await screen.findByTestId("bios-read-failed");
      expect(logSpy).toHaveBeenCalledWith(
        "DesktopEmulationSettings: BIOS read for appId=12345 romId=42 failed: Error: read boom",
      );
      unmountFailed();

      // Fresh mount: initial shared read runs, optional sections stay hidden when empty,
      // romm_tab_switch scrolls the card into view, and stranding unmounts the card cleanly.
      vi.clearAllMocks();
      const emptyOkAnswer = makeBiosAnswer(
        {
          emulator_tabs: [{ emulator: "DUCKSTATION", label: "DuckStation", launching: true, requirements_met: true }],
          files: [makeBiosFile("dl-on-app2.bin", { library: makeLibMatch("dl-on-app2.bin", 91, "91") })],
        },
        "ok",
      );
      vi.mocked(sharedReads.getBiosStatusShared).mockResolvedValue(emptyOkAnswer);
      vi.mocked(backend.getBiosStatus).mockResolvedValue(emptyOkAnswer);

      const { rerender, unmount } = render(<EmulationSettings appId={APP_ID} detail={makeDetail({ romId: 42 })} />);
      const card = await screen.findByTestId("desktop-emulation-card");
      expect(sharedReads.getBiosStatusShared).toHaveBeenCalledWith(42);
      expect(screen.queryByTestId("bios-group")).not.toBeInTheDocument();
      expect(screen.queryByTestId("bios-unclaimed-header")).not.toBeInTheDocument();

      const scrollSpy = vi.fn();
      card.scrollIntoView = scrollSpy;
      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_tab_switch", { detail: { tab: "bios" } }));
      });
      expect(scrollSpy).toHaveBeenCalledTimes(1);

      // Switching appId with the same romId=42 issues getBiosStatusShared again and updates handleActionReread.
      vi.mocked(sharedReads.getBiosStatusShared).mockClear();
      vi.mocked(gameDetailStore.refreshBiosStatus).mockClear();
      rerender(<EmulationSettings appId={APP_ID + 1} detail={makeDetail({ romId: 42 })} />);
      await waitFor(() => expect(sharedReads.getBiosStatusShared).toHaveBeenCalledWith(42));
      await screen.findByText("dl-on-app2.bin");

      const midActionDownload = deferred<Awaited<ReturnType<typeof backend.downloadPlatformFirmwareFile>>>();
      vi.mocked(backend.downloadPlatformFirmwareFile)
        .mockResolvedValueOnce({ success: true, message: "ok", downloaded: 1 })
        .mockReturnValueOnce(midActionDownload.promise);
      fireEvent.click(screen.getByRole("button", { name: "Download" }));
      await waitFor(() => expect(gameDetailStore.refreshBiosStatus).toHaveBeenCalledWith(APP_ID + 1));

      // Transitioning romId -> null mid-action makes rereadCurrentBios return early without calling getBiosStatus.
      fireEvent.click(screen.getByRole("button", { name: "Download" }));
      vi.mocked(backend.getBiosStatus).mockClear();
      rerender(<EmulationSettings appId={APP_ID + 1} detail={makeDetail({ romId: null })} />);
      await act(async () => {
        midActionDownload.resolve({ success: true, message: "ok", downloaded: 1 });
      });
      await flushMacrotask();
      expect(backend.getBiosStatus).not.toHaveBeenCalled();

      // Transitioning romId -> null while a shared read is pending drops the answer and ignores events.
      const pendingNewApp = deferred<BiosAnswer>();
      vi.mocked(sharedReads.getBiosStatusShared).mockReturnValueOnce(pendingNewApp.promise);
      rerender(<EmulationSettings appId={APP_ID + 2} detail={makeDetail({ romId: 77 })} />);
      expect(sharedReads.getBiosStatusShared).toHaveBeenCalledWith(77);

      rerender(<EmulationSettings appId={APP_ID + 2} detail={makeDetail({ romId: null })} />);
      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "emulator_sources" } }));
      });
      await flushMacrotask();
      expect(backend.getBiosStatus).not.toHaveBeenCalled();

      await act(async () => {
        pendingNewApp.resolve(makeBiosAnswer({ files: [makeBiosFile("dropped-null.bin")] }));
      });
      const stillPending = deferred<BiosAnswer>();
      vi.mocked(backend.getBiosStatus).mockReturnValueOnce(stillPending.promise);
      rerender(<EmulationSettings appId={APP_ID + 2} detail={makeDetail({ romId: 77 })} />);
      expect(screen.getByTestId("bios-checking")).toBeInTheDocument();
      expect(screen.queryByText("dropped-null.bin")).not.toBeInTheDocument();

      // Unmounting an unstranded card detaches its tab listener; mounted transition into
      // stranded (`false -> true`) detaches tab & data listeners, and mounting while already
      // stranded never attaches a tab listener.
      const tabSpy = vi.fn(() => "bios");
      const makeSpyTabEvent = () =>
        new CustomEvent("romm_tab_switch", { detail: Object.defineProperty({}, "tab", { get: tabSpy }) });

      const secondMount = render(<EmulationSettings appId={APP_ID + 3} detail={makeDetail({ romId: 42 })} />);
      await within(secondMount.container).findByTestId("desktop-emulation-card");
      secondMount.unmount();

      scrollSpy.mockClear();
      vi.mocked(sharedReads.getBiosStatusShared).mockClear();
      vi.mocked(backend.getBiosStatus).mockClear();
      act(() => setStrandedAnswer("reloads"));
      const strandedMount = render(<EmulationSettings appId={APP_ID + 4} detail={makeDetail({ romId: 42 })} />);
      act(() => {
        globalThis.dispatchEvent(new CustomEvent("romm_data_changed", { detail: { type: "emulator_sources" } }));
        globalThis.dispatchEvent(makeSpyTabEvent());
      });
      await flushMacrotask();
      expect(tabSpy).not.toHaveBeenCalled();
      expect(scrollSpy).not.toHaveBeenCalled();
      expect(sharedReads.getBiosStatusShared).not.toHaveBeenCalled();
      expect(backend.getBiosStatus).not.toHaveBeenCalled();
      strandedMount.unmount();
      unmount();
    });
  });
});
