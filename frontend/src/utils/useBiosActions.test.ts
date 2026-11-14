import { describe, expect, it, vi } from "vitest";
import type { BiosFileStatus, EmulatorTab } from "../types";
import {
  announceBiosChange,
  biosBulkOffer,
  biosDownloadButtonState,
  biosRowAction,
  deleteBiosFileConfirmCopy,
  deletePlatformBiosConfirmCopy,
  replaceBiosConfirmCopy,
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
});

describe("BIOS confirmation copy builders", () => {
  it("names the file, place, and backup folder when confirming a replacement", () => {
    expect(replaceBiosConfirmCopy("scph5501.bin", "/bios/scph5501.bin", "/backups/bios")).toEqual({
      title: "Replace scph5501.bin?",
      description: "The file at /bios/scph5501.bin goes to /backups/bios, and Tender's copy is linked in its place.",
      confirmLabel: "Replace",
    });
    expect(replaceBiosConfirmCopy("scph5501.bin", "/bios/scph5501.bin", undefined).description).toContain(
      "the backup folder",
    );
  });

  it("states that user-placed files stay for per-file and per-platform deletes", () => {
    expect(deleteBiosFileConfirmCopy("scph5501.bin")).toEqual({
      title: "Delete scph5501.bin?",
      description: "Tender removes it from its firmware store and every link to it. Files you placed yourself stay.",
      confirmLabel: "Delete",
    });
    expect(deletePlatformBiosConfirmCopy("PlayStation")).toEqual({
      title: "Delete Tender's BIOS downloads for PlayStation?",
      description:
        "Tender removes every BIOS file it downloaded for PlayStation and every link to them. Files you placed yourself stay.",
      confirmLabel: "Delete",
    });
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
