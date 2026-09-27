// The bookkeeping both Library tabs' writes stand on, pinned where no page
// stands around it. What an answer then shows is each tab's, and is pinned in
// `PlatformsTab.test.tsx` and `CollectionsTab.test.tsx`.

import { describe, it, expect } from "vitest";
import { createLatestWrites, createLineWrites, createWriteSequence } from "./latestWrites";

describe("createWriteSequence", () => {
  it("numbers each target's writes on their own, from 1", () => {
    const writes = createWriteSequence();
    expect(writes.latest("a")).toBe(0);
    expect(writes.issue("a")).toBe(1);
    expect(writes.issue("a")).toBe(2);
    expect(writes.issue("b")).toBe(1);
    expect(writes.latest("a")).toBe(2);
  });

  it("holds only the newest write of a target as the latest", () => {
    const writes = createWriteSequence();
    const first = writes.issue("a");
    expect(writes.isLatest("a", first)).toBe(true);
    const second = writes.issue("a");
    expect(writes.isLatest("a", first)).toBe(false);
    expect(writes.isLatest("a", second)).toBe(true);
    writes.issue("b");
    expect(writes.isLatest("a", second)).toBe(true);
  });
});

describe("createLatestWrites", () => {
  it("knows nothing as stored until something is seeded or confirmed", () => {
    const writes = createLatestWrites<boolean>();
    expect(writes.stored("a")).toBeUndefined();
    writes.confirm("a", true);
    expect(writes.stored("a")).toBe(true);
  });

  it("takes a seed in place of everything it held", () => {
    const writes = createLatestWrites<boolean>();
    writes.seed([
      ["a", true],
      ["b", false],
    ]);
    writes.seed([["a", false]]);
    expect(writes.stored("a")).toBe(false);
    expect(writes.stored("b")).toBeUndefined();
  });

  it("moves the stored value of every target a write wrote once it is stored, and no other", () => {
    const writes = createLatestWrites<boolean>();
    writes.seed([
      ["a", false],
      ["b", false],
      ["c", false],
    ]);
    const write = writes.issue(["a", "b"], true);
    expect(writes.stored("a")).toBe(false);
    write.stored();
    expect([writes.stored("a"), writes.stored("b"), writes.stored("c")]).toEqual([true, true, false]);
  });

  it("moves the stored value on for a write that is no longer the latest", () => {
    const writes = createLatestWrites<boolean>();
    writes.seed([["a", false]]);
    const older = writes.issue(["a"], true);
    writes.issue(["a"], false);
    older.stored();
    expect(writes.stored("a")).toBe(true);
  });

  it("speaks for a target only while no later write to it was issued", () => {
    const writes = createLatestWrites<boolean>();
    const batch = writes.issue(["a", "b"], true);
    expect(batch.isLatest("a")).toBe(true);
    const single = writes.issue(["b"], false);
    expect(batch.isLatest("a")).toBe(true);
    expect(batch.isLatest("b")).toBe(false);
    expect(single.isLatest("b")).toBe(true);
  });

  it("never speaks for a target it did not write", () => {
    const writes = createLatestWrites<boolean>();
    const write = writes.issue(["a"], true);
    expect(write.isLatest("b")).toBe(false);
  });

  it("keeps the write numbers across a seed, so an answer still out stays ordered", () => {
    const writes = createLatestWrites<boolean>();
    const older = writes.issue(["a"], true);
    writes.seed([["a", false]]);
    expect(writes.latest("a")).toBe(1);
    const newer = writes.issue(["a"], false);
    expect(older.isLatest("a")).toBe(false);
    expect(newer.isLatest("a")).toBe(true);
  });
});

describe("createLineWrites", () => {
  it("lets a write speak on its line only while no later write was issued there", () => {
    const lines = createLineWrites();
    const first = lines.issue("list");
    expect(first()).toBe(true);
    const pane = lines.issue("pane");
    expect(first()).toBe(true);
    const second = lines.issue("list");
    expect(first()).toBe(false);
    expect(second()).toBe(true);
    expect(pane()).toBe(true);
  });

  it("silences every write issued before an entry, on every line, and none issued after", () => {
    const lines = createLineWrites();
    const list = lines.issue("list");
    const pane = lines.issue("pane");
    lines.enter();
    expect(list()).toBe(false);
    expect(pane()).toBe(false);
    const after = lines.issue("list");
    expect(after()).toBe(true);
  });
});
