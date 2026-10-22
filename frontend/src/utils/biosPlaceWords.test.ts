// The lines a BIOS row carries about Tender's own part in it, in the words the
// decisions give them, and the sentences around the table.

import { describe, expect, it } from "vitest";

import type { LibraryMatch } from "../types";
import {
  DOWNLOAD_DAMAGED,
  backupLines,
  biosPlaceLines,
  keptLines,
  libraryRowLines,
  placedSentence,
  storeOnlySentence,
  type PlaceRow,
} from "./biosPlaceWords";

const library = (fields: Partial<LibraryMatch> = {}): LibraryMatch => ({
  firmware_id: 1,
  file_name: "sega_101.bin",
  platform_slug: "saturn",
  matched: "content",
  md5: "abc",
  size: 1,
  no_checksum: false,
  ...fields,
});

const row = (fields: Partial<PlaceRow> = {}): PlaceRow => ({
  library: library(),
  link: "linked",
  linked_for: null,
  place_refusal: null,
  satisfied: true,
  local_path: "/home/deck/retrodeck/bios/saturn_bios.bin",
  store_file: "sega_101.bin",
  declared_by: "yabasanshiro_libretro.so",
  declared_kind: "file",
  ...fields,
});

describe("biosPlaceLines", () => {
  it("says a folder holds no BIOS image yet, under the row that names the folder", () => {
    const folder = row({
      declared_kind: "directory",
      library: null,
      link: null,
      store_file: null,
      satisfied: false,
    });

    expect(biosPlaceLines(folder, "LRPS2")).toEqual(["No BIOS image here yet"]);
  });

  it("says nothing of a row whose link is in place", () => {
    expect(biosPlaceLines(row(), "YabaSanshiro")).toEqual([]);
  });

  it("says a store copy waits to be linked", () => {
    expect(biosPlaceLines(row({ link: "not_linked" }), null)).toEqual([
      "In Tender's firmware store, not linked here yet",
    ]);
  });

  it("names the platform whose copy a link of Tender's points at", () => {
    const lines = biosPlaceLines(row({ link: "linked_elsewhere", linked_for: "gb" }), null, {}, (slug) =>
      slug === "gb" ? "Game Boy" : slug,
    );

    expect(lines).toEqual(["Linked to Tender's copy for Game Boy"]);
  });

  it("says another file lies at the place, by the resolver's verdict over it", () => {
    expect(biosPlaceLines(row({ link: "occupied", satisfied: false }), null)).toEqual([
      "Another file lies here, and the emulator does not accept it",
    ]);
    expect(biosPlaceLines(row({ link: "occupied", satisfied: null }), null)).toEqual([
      "Another file lies here; it could not be checked",
    ]);
    expect(biosPlaceLines(row({ link: "occupied", satisfied: true }), null)).toEqual([]);
  });

  it("says a file was matched by name, and why", () => {
    expect(biosPlaceLines(row({ library: library({ matched: "archive_name" }) }), null)).toEqual([
      "Matched by name: the archive's contents cannot be compared",
    ]);
    expect(biosPlaceLines(row({ library: library({ matched: "no_checksum_name" }) }), null)).toEqual([
      "Matched by name: this file has no known checksum",
    ]);
  });

  it("says RomM states no checksum, so nothing can be checked", () => {
    expect(biosPlaceLines(row({ library: library({ no_checksum: true }) }), null)).toEqual([
      "RomM states no checksum for sega_101.bin, so Tender cannot check a download.",
    ]);
  });

  it("names a folder that is not there yet and the emulator to start", () => {
    const refusal = { reason: "folder_missing" as const, folder: "/home/deck/.var/app/x/data/Cemu" };

    expect(biosPlaceLines(row({ place_refusal: refusal, link: "free" }), "Cemu")).toEqual([
      "/home/deck/.var/app/x/data/Cemu does not exist yet. Start Cemu once, then try again.",
    ]);
  });

  it("names the folder under a root the resolver only assumed", () => {
    const refusal = { reason: "root_assumed" as const, folder: "/home/deck/retrodeck/bios" };

    expect(biosPlaceLines(row({ place_refusal: refusal, link: "free" }), null)).toEqual([
      "RetroDECK's settings could not be read, so Tender places nothing in /home/deck/retrodeck/bios.",
    ]);
  });

  it("says what the latest attempt on this page did", () => {
    const lines = biosPlaceLines(row(), null, {
      linkFailure: "Permission denied",
      damaged: true,
      replacedTo: "/data/backups/bios/2026-10-21_20-00-00/home/deck/retrodeck/bios/saturn_bios.bin",
    });

    expect(lines).toEqual([
      "Could not place a link at /home/deck/retrodeck/bios/saturn_bios.bin: Permission denied",
      DOWNLOAD_DAMAGED,
      "Replaced. The old file is in /data/backups/bios/2026-10-21_20-00-00/home/deck/retrodeck/bios/saturn_bios.bin.",
    ]);
  });
});

describe("libraryRowLines", () => {
  const unclaimed = row({ declared_by: null, link: null, store_file: null });

  it("says the identification is on its way, then its answer", () => {
    expect(libraryRowLines(unclaimed, "checking")).toEqual(["Checking…"]);
    expect(libraryRowLines(unclaimed, [])).toEqual(["Not recognised"]);
    expect(libraryRowLines(unclaimed, ["dmg_boot.bin", "gb_bios.bin"])).toEqual([
      "Recognised as dmg_boot.bin, gb_bios.bin",
    ]);
  });

  it("says nothing of an identification nothing could ask", () => {
    expect(libraryRowLines(unclaimed, "unknown")).toEqual([]);
    expect(libraryRowLines(unclaimed, undefined)).toEqual([]);
  });

  it("says a store copy is used by no emulator", () => {
    expect(libraryRowLines({ ...unclaimed, store_file: "extra.bin" }, undefined)).toEqual([
      "In Tender's firmware store, used by no emulator",
    ]);
  });
});

describe("storeOnlySentence", () => {
  it("says why a download is kept in the store only", () => {
    expect(storeOnlySentence("unsupported", "Ryubing", "Switch")).toBe(
      "Ryubing is not supported yet. Downloads are kept in Tender's firmware store and linked once it is.",
    );
    expect(storeOnlySentence("no_source", null, "Switch")).toBe(
      "No emulator source is set up for Switch. Downloads are kept in Tender's firmware store and linked once one is.",
    );
    expect(storeOnlySentence(null, "Ryubing", "Switch")).toBeNull();
  });
});

describe("placedSentence", () => {
  it("says how many were placed and how many stayed in the way, only where one did", () => {
    expect(placedSentence({ placed: 3, left_in_place: 1 })).toBe("3 placed, 1 left in place: another file lies there");
    expect(placedSentence({ placed: 3, left_in_place: 0 })).toBeNull();
  });
});

describe("keptLines", () => {
  it("says once per folder that a link stays until the source's settings can be read", () => {
    const lines = keptLines([
      { kind: "link", place: "/r/bios/a.bin", folder: "/r/bios", source: "retrodeck" },
      { kind: "link", place: "/r/bios/kronos/b.bin", folder: "/r/bios", source: "retrodeck" },
      { kind: "link", place: "/e/bios/c.bin", folder: null, source: "emudeck" },
    ]);

    expect(lines).toEqual([
      "Left a link in /r/bios: RetroDECK's settings could not be read. Tender removes it once they can be read.",
      "Left a link in /e/bios: EmuDeck's settings could not be read. Tender removes it once they can be read.",
    ]);
  });

  it("says an older download stays until it is deleted again", () => {
    const lines = keptLines([
      { kind: "file", place: "/r/bios/dc_boot.bin", folder: "/r/bios", source: "retrodeck", file_name: "dc_boot.bin" },
    ]);

    expect(lines).toEqual([
      "Left dc_boot.bin in /r/bios: RetroDECK's settings could not be read. Delete it again once they can be read.",
    ]);
    expect(keptLines(undefined)).toEqual([]);
  });
});

describe("backupLines", () => {
  it("names each file moved aside and where it went", () => {
    expect(backupLines(["/d/backups/bios/2026-10-21_20-00-00/home/deck/Tender/bios/saturn/sega_101.bin"])).toEqual([
      "A different sega_101.bin was already in Tender's firmware store. It was moved to /d/backups/bios/2026-10-21_20-00-00/home/deck/Tender/bios/saturn/sega_101.bin.",
    ]);
    expect(backupLines(undefined)).toEqual([]);
  });
});
