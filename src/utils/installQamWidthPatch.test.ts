/**
 * The install's checks before it reaches Steam: a class map without either
 * class, no FriendsUI store, no Big Picture window. Each answers its reason
 * before a fiber is read or a patch put on; what lies past them is Steam's.
 * The document holds a placeholder, so an install past the checks asks for its
 * fiber, which `@decky/ui`'s mock answers with none.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import type { QamWidthInstall } from "./qamWidthPatch";

interface Internals {
  classes?: { ViewPlaceholder?: string; Expanded?: string } | undefined;
  store?: object | undefined;
  window?: { document: Document } | undefined;
}

const complete: Required<Internals> = {
  classes: { ViewPlaceholder: "ViewPlaceholder_hash", Expanded: "Expanded_hash" },
  store: { qamFriendsExpanded: true },
  window: { document },
};

/** What the install answers with *internals* in place of `deckyUiInternals`'s, and the `@decky/ui` it reached. */
async function installWith(
  internals: Internals,
): Promise<{ answer: QamWidthInstall; decky: typeof import("@decky/ui") }> {
  vi.resetModules();
  const placeholder = document.createElement("div");
  placeholder.className = "ViewPlaceholder_hash";
  document.body.append(placeholder);
  vi.doMock("./deckyUiInternals", () => ({
    quickAccessMenuClasses: internals.classes,
    FriendsUIStore: internals.store,
    findSP: () => internals.window,
  }));
  const decky = await import("@decky/ui");
  vi.mocked(decky.getReactInstance).mockClear();
  vi.mocked(decky.afterPatch).mockClear();
  const { installQamWidthPatch } = await import("./installQamWidthPatch");
  return { answer: installQamWidthPatch(), decky };
}

afterEach(() => {
  vi.doUnmock("./deckyUiInternals");
  vi.resetModules();
  document.body.replaceChildren();
});

describe("installQamWidthPatch", () => {
  it("reaches the placeholder's fiber once every check passes", async () => {
    const { answer, decky } = await installWith(complete);

    expect(answer).toBe("no-host");
    expect(decky.getReactInstance).toHaveBeenCalledTimes(1);
  });

  it.each([
    ["no class map", undefined],
    ["a class map without Expanded", { ViewPlaceholder: "ViewPlaceholder_hash" }],
    ["a class map without ViewPlaceholder", { Expanded: "Expanded_hash" }],
  ])("answers class-map-incomplete, and reaches no fiber, with %s", async (_shape, classes) => {
    const { answer, decky } = await installWith({ ...complete, classes });

    expect(answer).toBe("class-map-incomplete");
    expect(decky.getReactInstance).not.toHaveBeenCalled();
    expect(decky.afterPatch).not.toHaveBeenCalled();
  });

  it("answers no-friends-ui-store, and reaches no fiber, where the store was not found", async () => {
    const { answer, decky } = await installWith({ ...complete, store: undefined });

    expect(answer).toBe("no-friends-ui-store");
    expect(decky.getReactInstance).not.toHaveBeenCalled();
    expect(decky.afterPatch).not.toHaveBeenCalled();
  });

  it("answers no-big-picture-window, and reaches no fiber, where Big Picture's window was not found", async () => {
    const { answer, decky } = await installWith({ ...complete, window: undefined });

    expect(answer).toBe("no-big-picture-window");
    expect(decky.getReactInstance).not.toHaveBeenCalled();
    expect(decky.afterPatch).not.toHaveBeenCalled();
  });
});
