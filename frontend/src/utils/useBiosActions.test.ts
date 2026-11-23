import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import * as backend from "../api/backend";
import type { BiosFileStatus, EmulatorTab } from "../types";
import {
  UNCLAIMED_LIBRARY_HEADING,
  announceBiosChange,
  biosBulkOffer,
  biosDownloadButtonState,
  biosRowAction,
  useBiosActions,
  useBiosTable,
} from "./useBiosActions";

function libMatch(fileName: string, firmwareId: number, md5: string): NonNullable<BiosFileStatus["library"]> {
  return {
    file_name: fileName,
    firmware_id: firmwareId,
    md5,
    platform_slug: "psx",
    matched: "content",
    size: 524288,
    no_checksum: false,
  };
}

function row(overrides: Partial<BiosFileStatus> = {}): BiosFileStatus {
  return {
    file_name: "scph5501.bin",
    local_path: "/bios/scph5501.bin",
    declared_path: "scph5501.bin",
    description: "",
    required_by_active: true,
    wanted: "needed",
    downloaded: false,
    on_server: true,
    satisfied: false,
    used_by_active: true,
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
    delete_name: null,
    deletable_count: 0,
    ...overrides,
  };
}

describe("biosRowAction", () => {
  it("offers download first when the row is fetchable and the server is reachable", () => {
    const file = row({
      library: libMatch("SCPH5501.BIN", 19, "abc"),
      deletable_count: 1,
      delete_name: "scph5501.bin",
    });

    expect(biosRowAction(file, true, false)).toEqual({
      kind: "download",
      name: "SCPH5501.BIN",
      firmwareId: 19,
    });
  });

  it("withdraws download when RomM is offline and falls back to delete when Tender holds a record", () => {
    const file = row({
      deletable_count: 1,
      delete_name: "scph5501.bin",
    });

    expect(biosRowAction(file, true, true)).toEqual({
      kind: "delete",
      name: "scph5501.bin",
    });
  });

  it("offers replace when the backend marks replace_offered with a local_path", () => {
    const file = row({
      replace_offered: true,
      local_path: "/bios/scph5501.bin",
      deletable_count: 1,
      delete_name: "scph5501.bin",
    });

    expect(biosRowAction(file, false, false)).toEqual({
      kind: "replace",
      place: "/bios/scph5501.bin",
      fileName: "scph5501.bin",
    });
  });

  it("offers no delete when downloaded is true without deletable_count or delete_name", () => {
    expect(biosRowAction(row({ downloaded: true, deletable_count: 0 }), false, false)).toBeNull();
    expect(biosRowAction(row({ downloaded: true, deletable_count: 1, delete_name: null }), false, false)).toBeNull();
  });
});

describe("biosDownloadButtonState", () => {
  it("reports spinner or failed only for the busy platform folder and matching button id", () => {
    expect(
      biosDownloadButtonState("psx", "psx", { downloadPending: "required", downloadFailed: null }, "required"),
    ).toBe("spinner");
    expect(biosDownloadButtonState("psx", "psx", { downloadPending: null, downloadFailed: "all" }, "all")).toBe(
      "failed",
    );
    expect(
      biosDownloadButtonState("saturn", "psx", { downloadPending: "required", downloadFailed: null }, "required"),
    ).toBeNull();
    expect(
      biosDownloadButtonState("psx", "psx", { downloadPending: "other.bin", downloadFailed: null }, "required"),
    ).toBeNull();
  });
});

describe("biosBulkOffer", () => {
  const tab: EmulatorTab = {
    emulator: "swanstation_libretro.so",
    label: "SwanStation",
    launching: true,
    requirements_met: false,
  };

  it("counts fetchable required rows for the shown emulator and gates downloads on server reachability", () => {
    const missingReq = row({
      file_name: "scph5501.bin",
      fetch_for_required: true,
      library: libMatch("scph5501.bin", 1, "1"),
    });
    const optionalMissing = row({
      file_name: "scph5502.bin",
      required_by_active: false,
      fetch_for_required: false,
      library: libMatch("scph5502.bin", 2, "2"),
    });

    const online = biosBulkOffer(
      { files: [missingReq, optionalMissing], server_count: 2, local_count: 0, deletable_count: 3 },
      tab,
      [missingReq, optionalMissing],
      false,
      "shown",
    );
    expect(online).toEqual({
      shownEmulator: "swanstation_libretro.so",
      requiredLabel: "Download required for SwanStation",
      requiredMissing: 1,
      canDownloadRequired: true,
      canDownloadAll: true,
      deletable: 3,
    });

    const offline = biosBulkOffer(
      { files: [missingReq, optionalMissing], server_count: 2, local_count: 0, deletable_count: 3 },
      tab,
      [missingReq, optionalMissing],
      true,
      "shown",
    );
    expect(offline.canDownloadRequired).toBe(false);
    expect(offline.canDownloadAll).toBe(false);
    expect(offline.deletable).toBe(3);
  });

  it("passes null shownEmulator on the platform page's launching tab and the tab's emulator on another tab", () => {
    const otherTab: EmulatorTab = { ...tab, emulator: "duckstation", label: "DuckStation", launching: false };
    expect(biosBulkOffer({ files: [] }, tab, [], false, "pick").shownEmulator).toBeNull();
    expect(biosBulkOffer({ files: [] }, otherTab, [], false, "pick").shownEmulator).toBe("duckstation");
  });

  it("falls back to counting on_server and on_server && downloaded rows when aggregate counts are omitted", () => {
    const serverHeld = row({ file_name: "a.bin", on_server: true, downloaded: true });
    const localOnly = row({ file_name: "b.bin", on_server: false, downloaded: true });
    const fetchableMissing = row({
      file_name: "c.bin",
      on_server: true,
      downloaded: false,
      library: libMatch("c.bin", 3, "3"),
    });

    // When all on_server rows are already downloaded (even with a local-only row beside them), done === total -> false.
    expect(
      biosBulkOffer(
        { files: [serverHeld, localOnly, row({ file_name: "d.bin", on_server: false, downloaded: false })] },
        tab,
        [],
        false,
        "shown",
      ).canDownloadAll,
    ).toBe(false);

    // Single fetchable row (`on_server: true, downloaded: false`) has `done = 0` vs `total = 1` -> true.
    expect(biosBulkOffer({ files: [fetchableMissing] }, tab, [], false, "shown").canDownloadAll).toBe(true);

    // When `server_count` is supplied without `local_count`, `done` counts `on_server && downloaded` rows.
    expect(
      biosBulkOffer({ files: [serverHeld, fetchableMissing], server_count: 1 }, tab, [], false, "shown").canDownloadAll,
    ).toBe(false);
    expect(biosBulkOffer({ files: [localOnly, fetchableMissing] }, tab, [], false, "shown").canDownloadAll).toBe(true);
  });
});

describe("useBiosActions hook", () => {
  const platform = { slug: "psx", folder: "psx" };
  const mountActions = (
    romId: number | null = 42,
    reread: () => Promise<void> = vi.fn().mockResolvedValue(undefined),
    setStatus = vi.fn(),
  ) => ({
    setStatus,
    ...renderHook(() =>
      useBiosActions({
        setBusy: vi.fn(),
        setStatus,
        reread,
        announce: vi.fn(),
        romId,
      }),
    ),
  });
  const triggerAllActions = (actions: ReturnType<typeof useBiosActions>) => {
    act(() => {
      actions.downloadOne(platform, "scph5501.bin", 19);
      actions.replaceBios(platform, "/bios/scph5501.bin");
      actions.deleteBios(platform);
      actions.deleteBiosFile(platform, "scph5501.bin");
    });
  };

  it("records link failures before awaiting reread and clears downloadPending once finished", async () => {
    vi.spyOn(backend, "downloadPlatformFirmwareFile").mockResolvedValueOnce({
      success: true,
      message: "ok",
      downloaded: 1,
      link_failures: [{ file_name: "scph5501.bin", place: "/bios/scph5501.bin", reason: "permission_denied" }],
    });

    let finishReread!: () => void;
    const reread = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finishReread = resolve;
        }),
    );

    const { result } = mountActions(42, reread);

    act(() => {
      result.current.downloadOne(platform, "scph5501.bin", 19);
    });

    await waitFor(() => expect(reread).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(result.current.attemptFor("psx", "/bios/scph5501.bin", "scph5501.bin").linkFailure).toBe(
        "permission_denied",
      ),
    );
    expect(result.current.downloadPending).toBe("scph5501.bin");

    await act(async () => {
      finishReread();
    });
    await waitFor(() => expect(result.current.downloadPending).toBeNull());
  });

  it("logs unexpected throws with the action tag and target across download, replace, and delete calls", async () => {
    const logSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => undefined);
    vi.spyOn(backend, "downloadPlatformFirmwareFile").mockRejectedValueOnce(new Error("net down"));
    vi.spyOn(backend, "replaceBiosFile").mockRejectedValueOnce(new Error("disk full"));
    vi.spyOn(backend, "deletePlatformBios").mockRejectedValueOnce(new Error("busy"));
    vi.spyOn(backend, "deleteBiosFile").mockRejectedValueOnce(new Error("locked"));

    const { result, unmount } = mountActions(42);
    triggerAllActions(result.current);

    await waitFor(() => expect(logSpy).toHaveBeenCalledTimes(4));
    expect(logSpy).toHaveBeenCalledWith(
      "useBiosActions: download (scph5501.bin, slug=psx folder=psx romId=42) failed: Error: net down",
    );
    expect(logSpy).toHaveBeenCalledWith(
      "useBiosActions: replaceBios (/bios/scph5501.bin, slug=psx folder=psx romId=42) failed: Error: disk full",
    );
    expect(logSpy).toHaveBeenCalledWith(
      "useBiosActions: deleteBios (psx, slug=psx folder=psx romId=42) failed: Error: busy",
    );
    expect(logSpy).toHaveBeenCalledWith(
      "useBiosActions: deleteBiosFile (scph5501.bin, slug=psx folder=psx romId=42) failed: Error: locked",
    );
    unmount();
  });

  it("logs refused (!result.success) responses across download, replace, and delete calls", async () => {
    const logSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => undefined);
    vi.spyOn(backend, "downloadPlatformFirmwareFile").mockResolvedValueOnce({
      success: false,
      reason: "bios_download_failed",
      message: "checksum mismatch",
    });
    vi.spyOn(backend, "replaceBiosFile").mockResolvedValueOnce({
      success: false,
      reason: "backup_failed",
      message: "backup failed",
    });
    vi.spyOn(backend, "deletePlatformBios").mockResolvedValueOnce({
      success: false,
      reason: "delete_incomplete",
      message: "store locked",
    });
    vi.spyOn(backend, "deleteBiosFile").mockResolvedValueOnce({
      success: false,
      reason: "not_found",
      message: "record missing",
    });

    const { result, unmount } = mountActions(null);
    triggerAllActions(result.current);

    await waitFor(() => expect(logSpy).toHaveBeenCalledTimes(4));
    expect(logSpy).toHaveBeenCalledWith(
      "useBiosActions: download (scph5501.bin, slug=psx folder=psx) failed: checksum mismatch",
    );
    expect(logSpy).toHaveBeenCalledWith(
      "useBiosActions: replaceBios (/bios/scph5501.bin, slug=psx folder=psx) failed: backup failed",
    );
    expect(logSpy).toHaveBeenCalledWith("useBiosActions: deleteBios (psx, slug=psx folder=psx) failed: store locked");
    expect(logSpy).toHaveBeenCalledWith(
      "useBiosActions: deleteBiosFile (scph5501.bin, slug=psx folder=psx) failed: record missing",
    );
    unmount();
  });

  it("logs zero-downloaded batch failures with romId, and stays quiet while joining kept lines on successful deletes", async () => {
    const logSpy = vi.spyOn(backend, "logWarn").mockImplementation(() => undefined);
    vi.spyOn(backend, "downloadPlatformFirmwareFile").mockResolvedValueOnce({
      success: true,
      downloaded: 0,
      failed: ["scph5501.bin"],
      message: "hash mismatch",
    });
    vi.spyOn(backend, "deletePlatformBios").mockResolvedValueOnce({
      success: true,
      deleted_count: 1,
      message: "Removed 1 file.",
    });
    vi.spyOn(backend, "deleteBiosFile").mockResolvedValueOnce({
      success: true,
      deleted_count: 1,
      message: "Removed scph5501.bin.",
      kept: [
        { kind: "link", source: "retrodeck", place: "/bios/psx/scph5501.bin", folder: "/bios/psx" },
        {
          kind: "file",
          source: "retrodeck",
          place: "/bios/psx/old.bin",
          file_name: "old.bin",
          folder: "/bios/psx",
          why: "not_installed",
        },
      ],
    });

    const { result, setStatus, unmount } = mountActions(42);

    act(() => {
      result.current.downloadOne(platform, "scph5501.bin", 19);
    });
    await waitFor(() =>
      expect(logSpy).toHaveBeenCalledWith(
        "useBiosActions: download (scph5501.bin, slug=psx folder=psx romId=42) failed: hash mismatch",
      ),
    );

    logSpy.mockClear();
    setStatus.mockClear();
    act(() => {
      result.current.deleteBios(platform);
    });
    await waitFor(() =>
      expect(setStatus).toHaveBeenCalledWith({ folder: "psx", scope: "bios", text: "Removed 1 file." }),
    );
    expect(logSpy).not.toHaveBeenCalled();

    setStatus.mockClear();
    act(() => {
      result.current.deleteBiosFile(platform, "scph5501.bin");
    });
    await waitFor(() =>
      expect(setStatus).toHaveBeenCalledWith({
        folder: "psx",
        scope: "bios",
        text: [
          "Left a link in /bios/psx: RetroDECK's settings could not be read. Tender removes it once they can be read.",
          "Left old.bin in /bios/psx: RetroDECK is not installed. Delete it again once it is.",
        ].join("\n"),
      }),
    );
    expect(logSpy).not.toHaveBeenCalled();
    unmount();
  });
});

describe("announceBiosChange", () => {
  it("dispatches romm_data_changed with type bios, slug, and folder", () => {
    const listener = vi.fn();
    globalThis.addEventListener("romm_data_changed", listener);
    try {
      announceBiosChange("psx", "psx-jp");
      expect(listener).toHaveBeenCalledTimes(1);
      expect((listener.mock.calls[0]![0] as CustomEvent).detail).toEqual({
        type: "bios",
        platform_slug: "psx",
        platform_fs_slug: "psx-jp",
      });
    } finally {
      globalThis.removeEventListener("romm_data_changed", listener);
    }
  });
});

describe("useBiosTable", () => {
  it("shares the unclaimed heading, keeps selected emulator tab across answers, and derives rows, identifications, and fetchable sets", async () => {
    expect(UNCLAIMED_LIBRARY_HEADING).toBe("In your RomM library, asked for by no emulator here");
    vi.spyOn(backend, "identifyLibraryFirmware").mockResolvedValueOnce({
      success: true,
      known_as: ["PS1 Extra"],
    });
    const launchingTab: EmulatorTab = {
      emulator: "DUCKSTATION",
      label: "DuckStation",
      launching: true,
      requirements_met: false,
    };
    const altRow = row({
      file_name: "scph5502.bin",
      declared_by: "mednafen_psx_hw_libretro.so",
      library: libMatch("scph5502.bin", 2, "md5-alt"),
    });
    const altTab: EmulatorTab = {
      emulator: "mednafen_psx_hw_libretro.so",
      label: "Beetle PSX HW",
      launching: false,
      requirements_met: false,
      rows: [altRow],
    };
    const launchingRow = row({
      file_name: "scph5501.bin",
      declared_by: "DUCKSTATION",
      library: libMatch("scph5501.bin", 1, "md5-launch"),
    });
    const libraryRow = row({
      file_name: "extra.bin",
      declared_by: null,
      wanted: "not_needed",
      required_by_active: false,
      library: libMatch("extra.bin", 3, "md5-lib"),
    });

    const initialAnswer = {
      active_core_label: "DuckStation",
      emulator_tabs: [launchingTab, altTab],
      files: [launchingRow, libraryRow],
    };

    const { result, rerender, unmount } = renderHook(({ answer }) => useBiosTable(answer), {
      initialProps: { answer: initialAnswer },
    });

    await waitFor(() => expect(result.current.identifications.answers["md5-lib"]).toEqual(["PS1 Extra"]));
    expect(result.current.tab).toBe(launchingTab);
    expect(result.current.shownEmulatorLabel).toBe("DuckStation");
    expect(result.current.shownRows.map((r) => r.file_name)).toEqual(["scph5501.bin"]);
    expect(result.current.libraryRows.map((r) => r.file_name)).toEqual(["extra.bin"]);
    expect(Array.from(result.current.shownFetchable)).toEqual(["scph5501.bin"]);
    expect(Array.from(result.current.libraryFetchable)).toEqual(["extra.bin"]);

    act(() => {
      result.current.selectTab(altTab);
    });
    expect(result.current.tab).toBe(altTab);
    expect(result.current.shownEmulatorLabel).toBe("Beetle PSX HW");
    expect(result.current.shownRows.map((r) => r.file_name)).toEqual(["scph5502.bin"]);
    expect(Array.from(result.current.shownFetchable)).toEqual(["scph5502.bin"]);

    // Rerendering with a fresh answer (e.g. after a version switch) preserves the selected non-launching tab.
    rerender({
      answer: {
        active_core_label: "DuckStation",
        emulator_tabs: [{ ...launchingTab }, { ...altTab }],
        files: [launchingRow, libraryRow],
      },
    });
    expect(result.current.tab?.emulator).toBe("mednafen_psx_hw_libretro.so");
    expect(result.current.shownEmulatorLabel).toBe("Beetle PSX HW");

    // Choosing the launching tab follows whichever emulator launches, not the one launching when it was chosen.
    act(() => {
      result.current.selectTab(launchingTab);
    });
    expect(result.current.tab?.launching).toBe(true);
    rerender({
      answer: {
        active_core_label: "Beetle PSX HW",
        emulator_tabs: [
          { ...launchingTab, launching: false },
          { ...altTab, launching: true },
        ],
        files: [launchingRow, libraryRow],
      },
    });
    expect(result.current.tab?.emulator).toBe("mednafen_psx_hw_libretro.so");
    unmount();
  });
});
