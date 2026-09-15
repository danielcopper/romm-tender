// One predicate, two surfaces: the platform detail's download affordances and
// the game page's decision to draw a row at all. Each clause is pinned on its
// own, because a clause lost here changes both surfaces at once and in opposite
// directions — the platform page would offer a download that cannot succeed,
// the game page would leave off a row it could have pointed at.

import { describe, it, expect } from "vitest";
import { isFetchable } from "./biosFetchable";

/** A row the library holds and the user has not got yet — fetchable, and the
 *  baseline each case below takes one fact away from. */
const fetchableRow = { on_server: true, downloaded: false, declared_kind: "file" } as const;

describe("isFetchable", () => {
  it("answers for a file the library holds that is not at its destination", () => {
    expect(isFetchable(fetchableRow)).toBe(true);
  });

  it("refuses a file the RomM library does not hold", () => {
    // It still counts towards readiness — there is simply nothing to press.
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
