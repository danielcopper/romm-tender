/**
 * What the patch handler does to a tab array — the half of the entry a test can
 * reach.
 *
 * `syncEntry` is what every render pass of Steam's Quick Access menu calls, and
 * it is the whole of the handler's effect: everything above it is `@decky/ui`
 * walking Steam's live React tree to find the array, which exists nowhere in
 * this environment. So these cases drive it with arrays of their own, and what
 * they establish is what a render pass does to a tab array — added once, added
 * again to a replacement array, moved to the end on every pass.
 *
 * **What they cannot establish** is that the handler is ever called: that the
 * renderers are found, patched, and hand back a tree the resolver can walk was
 * measured on the device in #1897 and is on this cut's device list. A suite that
 * faked those out would be asserting against a tree it wrote itself.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { buildEntry, syncEntry, TENDER_TAB_KEY, type QuickAccessTabEntry } from "./quickAccessEntry";
import { PanelErrorBoundary } from "./PanelErrorBoundary";
import type { Plugin } from "../api/host";

// The class map is a webpack probe, so the suite-wide `@decky/ui` stub answers
// `undefined` for it. A getter lets one case hand the entry a map and the next
// take it away again, which a plain value fixed at mock time could not.
const probe = vi.hoisted(() => ({ classes: undefined as Record<string, string> | undefined }));
vi.mock("../utils/deckyUiInternals", () => ({
  get quickAccessMenuClasses() {
    return probe.classes;
  },
}));

/** An entry shaped the way the installer builds it, with stand-ins for its three nodes: `syncEntry` renders none. */
const tenderEntry = (): QuickAccessTabEntry => ({
  key: TENDER_TAB_KEY,
  title: "Tender",
  tab: null,
  panel: null,
  tender: true,
});

/** Somebody else's entry — Decky's, or Steam's own. */
const foreign = (key: string) => ({ key, title: key, tab: null, panel: null });

const keys = (tabs: unknown[]) => tabs.map((tab) => (tab as { key: string }).key);

describe("syncEntry", () => {
  it("adds the entry once, at the end", () => {
    const entry = tenderEntry();
    const tabs: unknown[] = [foreign("notifications"), foreign("decky")];

    syncEntry(tabs, entry);

    expect(keys(tabs)).toEqual(["notifications", "decky", TENDER_TAB_KEY]);
  });

  it("adds nothing on a second pass over the same array", () => {
    const entry = tenderEntry();
    const tabs: unknown[] = [foreign("decky")];

    syncEntry(tabs, entry);
    syncEntry(tabs, entry);
    syncEntry(tabs, entry);

    expect(keys(tabs)).toEqual(["decky", TENDER_TAB_KEY]);
  });

  it("recognises an entry it did not put there, by the marker alone", () => {
    // What a re-injection leaves behind: an entry marked as ours, in an array
    // this module has no memory of. The marker is the whole of the idempotence,
    // so it has to answer for an entry that is not the one in hand.
    const tabs: unknown[] = [foreign("decky"), tenderEntry()];

    syncEntry(tabs, tenderEntry());

    expect(tabs.filter((tab) => (tab as { tender?: boolean }).tender)).toHaveLength(1);
  });

  it("puts the entry in a replacement array too", () => {
    // Every Quick Access remount builds a new tabs array. The first one is not
    // held anywhere, so the pass over the second has to add the entry again.
    const entry = tenderEntry();
    const first: unknown[] = [foreign("decky")];
    syncEntry(first, entry);

    const second: unknown[] = [foreign("decky")];
    syncEntry(second, entry);

    expect(keys(second)).toEqual(["decky", TENDER_TAB_KEY]);
    expect(keys(first)).toEqual(["decky", TENDER_TAB_KEY]);
  });

  it("moves the entry back to the end when a later pass finds it higher up", () => {
    // Whoever patches last pushes last, so a pass can leave Tender above Decky.
    // The placement is re-asserted on every pass rather than set at creation.
    const entry = tenderEntry();
    const tabs: unknown[] = [entry, foreign("decky"), foreign("friends")];

    syncEntry(tabs, entry);

    expect(keys(tabs)).toEqual(["decky", "friends", TENDER_TAB_KEY]);
  });

  it("leaves the order of everyone else's entries alone", () => {
    const entry = tenderEntry();
    const tabs: unknown[] = [foreign("a"), entry, foreign("b"), foreign("c")];

    syncEntry(tabs, entry);

    expect(keys(tabs)).toEqual(["a", "b", "c", TENDER_TAB_KEY]);
  });

  it("survives a pass over an array holding nothing at all", () => {
    const entry = tenderEntry();
    const tabs: unknown[] = [];

    syncEntry(tabs, entry);

    expect(keys(tabs)).toEqual([TENDER_TAB_KEY]);
  });

  it("is unbothered by a hole or a primitive in the array", () => {
    // The array is Steam's, read off a prop of a tree we do not own, so the
    // marker test has to answer for whatever is in it rather than assume objects.
    const entry = tenderEntry();
    const tabs: unknown[] = [null, undefined, 0, "decky", entry];

    syncEntry(tabs, entry);

    expect(tabs[tabs.length - 1]).toBe(entry);
    expect(tabs).toHaveLength(5);
  });
});

describe("buildEntry", () => {
  const plugin = (over: Partial<Plugin> = {}): Plugin => ({
    name: "Tender",
    icon: "the-glyph" as unknown as Plugin["icon"],
    content: "the-panel" as unknown as Plugin["content"],
    ...over,
  });

  it("files the entry under Tender's own key and marks it as ours", () => {
    const entry = buildEntry(plugin());

    expect(entry.key).toBe(TENDER_TAB_KEY);
    // The marker, not the key, is what a later pass recognises it by.
    expect((entry as unknown as Record<string, unknown>)[TENDER_TAB_KEY]).toBe(true);
  });

  describe("heading", () => {
    afterEach(() => {
      probe.classes = undefined;
    });

    it("draws the plugin's name in an element carrying Steam's own heading class", () => {
      // A bare string lands in the panel at body size; Steam's own tabs head
      // theirs larger and bolder through this class. The two readings are in
      // `docs/architecture/qam-panel.md`, The entry.
      probe.classes = { Title: "Title_hash" };

      render(buildEntry(plugin({ name: "Another Name" })).title as ReactElement);

      expect(screen.getByText("Another Name")).toHaveClass("Title_hash");
    });

    it("still draws the name when the class map is missing", () => {
      render(buildEntry(plugin()).title as ReactElement);

      expect(screen.getByText("Tender")).not.toHaveAttribute("class");
    });
  });

  it("draws the glyph the plugin declares rather than one of its own", () => {
    // The start-up-failure branch answers with a different icon, so an entry
    // that chose its own would draw the wrong one on exactly the start-up
    // nothing here can test.
    const icon = "a-different-glyph" as unknown as Plugin["icon"];

    expect(buildEntry(plugin({ icon })).tab).toBe(icon);
  });

  it("puts the boundary around the panel and nothing around the glyph", () => {
    const entry = buildEntry(plugin());

    expect((entry.panel as { type: unknown }).type).toBe(PanelErrorBoundary);
    expect(entry.tab).toBe("the-glyph");
  });
});
