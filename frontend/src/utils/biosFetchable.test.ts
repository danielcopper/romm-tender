// One predicate, two surfaces: the platform detail's download affordances and
// the game page's decision to draw a row at all. Each clause is pinned on its
// own, because a clause lost here changes both surfaces at once and in opposite
// directions — the platform page would offer a download that cannot succeed,
// the game page would leave off a row it could have pointed at.

import { describe, it, expect } from "vitest";
import { downloadName, isFetchable } from "./biosFetchable";

/** A row the library holds and the user has not got yet — fetchable, and the
 *  baseline each case below takes one fact away from. */
const fetchableRow = { on_server: true, downloaded: false, declared_kind: "file" } as const;

describe("isFetchable", () => {
  it("answers for a file the library holds that is not at its destination", () => {
    expect(isFetchable(fetchableRow)).toBe(true);
  });

  it("refuses a file the RomM library does not hold", () => {
    // Where the launch requires it, it still counts in the required counts —
    // there is simply nothing to press.
    expect(isFetchable({ ...fetchableRow, on_server: false })).toBe(false);
  });

  it("refuses a file that is already there", () => {
    expect(isFetchable({ ...fetchableRow, downloaded: true })).toBe(false);
  });

  it("refuses a declared folder, whatever the library says about the name", () => {
    // The emulator lists a folder, so there is no file to fetch into it; what
    // satisfies it is an image INSIDE it, which is a different row. The backend
    // stamps every folder row `on_server: False`, so the flag is set here
    // deliberately to show the clause standing on its own.
    expect(isFetchable({ on_server: true, downloaded: false, declared_kind: "directory" })).toBe(false);
  });

  it("refuses a file whose place the resolver could not look at", () => {
    // A download there could overwrite a file nobody could see.
    expect(isFetchable({ ...fetchableRow, caveats: ["firmware-path-inaccessible"] })).toBe(false);
    expect(isFetchable({ ...fetchableRow, caveats: ["firmware-image-identified"] })).toBe(true);
  });

  it("treats a payload that never spoke about the library as not fetchable", () => {
    // The game page's row type leaves `on_server` out of an older payload.
    // Absent is the safe direction: a row shown as unfetchable costs a button,
    // a row shown as fetchable costs a download that cannot succeed.
    expect(isFetchable({ downloaded: false, declared_kind: "file" })).toBe(false);
  });

  it("answers a row whose declaration names no kind", () => {
    // Only `"directory"` is excluded, so a payload carrying no `declared_kind`
    // is treated as the file it almost always is rather than withheld.
    expect(isFetchable({ on_server: true, downloaded: false })).toBe(true);
  });
});

describe("isFetchable over the firmware store", () => {
  const library = {
    firmware_id: 1,
    file_name: "sega_101.bin",
    platform_slug: "saturn",
    matched: "content" as const,
    md5: "abc",
    size: 1,
    no_checksum: false,
  };
  const free = { downloaded: false, library, store_file: null, link: "free" as const, place_refusal: null };

  it("offers a library file the store does not hold yet", () => {
    expect(isFetchable(free)).toBe(true);
  });

  it("offers one whose place another platform's copy serves, into this platform's own folder", () => {
    expect(isFetchable({ ...free, downloaded: true, link: "linked_elsewhere" })).toBe(true);
  });

  it("refuses a row no library file fills, one the store holds, and one RomM states no checksum for", () => {
    expect(isFetchable({ ...free, library: null })).toBe(false);
    expect(isFetchable({ ...free, store_file: "sega_101.bin" })).toBe(false);
    expect(isFetchable({ ...free, library: { ...library, no_checksum: true } })).toBe(false);
  });

  it("refuses a place something not Tender's lies at, and one the resolver could not look at", () => {
    expect(isFetchable({ ...free, link: "occupied" })).toBe(false);
    expect(isFetchable({ ...free, place_refusal: { reason: "unreadable", folder: null, source: "retrodeck" } })).toBe(
      false,
    );
  });

  it("names a download by the library file, which may not be the row's name", () => {
    expect(downloadName({ file_name: "saturn_bios.bin", library })).toBe("sega_101.bin");
    expect(downloadName({ file_name: "saturn_bios.bin" })).toBe("saturn_bios.bin");
  });
});
