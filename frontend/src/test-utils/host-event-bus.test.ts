/**
 * The harness's own dispatch, which every component test in the suite emits
 * through.
 *
 * What is pinned here is the one property no call site reveals: `emitHostEvent`
 * iterates a snapshot, so a listener that tears its peers down while it runs —
 * a `useEffect` cleanup reached from another listener — does not take the event
 * away from them.
 */

import { describe, expect, it } from "vitest";

import { emitHostEvent, hostEventListenerCount, mockAddEventListener, mockRemoveEventListener } from "./host-event-bus";

describe("one emitted backend event", () => {
  it("reaches the listeners registered when it was emitted, even ones removed part-way through", () => {
    const heard: string[] = [];
    const second = () => heard.push("second");
    const first = () => {
      heard.push("first");
      mockRemoveEventListener("sync_progress", first);
      mockRemoveEventListener("sync_progress", second);
    };
    mockAddEventListener("sync_progress", first);
    mockAddEventListener("sync_progress", second);

    emitHostEvent("sync_progress", {});

    // A Set iterator walks the LIVE set, so an entry deleted before it is
    // reached is skipped in silence — no throw, `second` simply never runs.
    // Dispatching over a snapshot is what stops one listener's teardown from
    // taking the event away from a peer that was registered when it was emitted.
    expect(heard).toEqual(["first", "second"]);

    expect(hostEventListenerCount("sync_progress")).toBe(0);
    emitHostEvent("sync_progress", {});
    // The removals did take effect — for the NEXT emit, not the one they ran in.
    expect(heard).toEqual(["first", "second"]);
  });
});
