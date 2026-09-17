/**
 * Whose copy of `@decky/ui` the page blames, and what it is allowed to claim.
 *
 * Every case here is a sentence a user reads, so the assertions are on the
 * sentence rather than on the shape that produced it — a page that named the
 * right copy and then worded it as the other would pass a structural test.
 *
 * The bundle kind and the Decky reading are both passed in. The kind is a
 * build constant, so a test that let it default could only ever see one of the
 * two bundles; the reading is a thunk, which is also how the standalone case
 * asserts that nothing on the machine is touched at all.
 */

import { describe, expect, it, vi } from "vitest";

import { type DeckyCopy, readDeckyCopy, readSearchingCopy } from "./searchingCopy";
import { describeFailure, type StartupReport } from "./steamModules";

const report = (missing: string[], missingPackageNames = missing, checked = 33): StartupReport => ({
  ok: false,
  missing,
  missingPackageNames,
  checked,
});

const deckyCopy = (overrides: Partial<DeckyCopy> = {}): DeckyCopy => ({
  carries: () => true,
  version: "v3.2.8",
  ...overrides,
});

/** The window Decky writes, as far as anything here reads it. */
interface DeckyWindow {
  DFL?: unknown;
  DeckyPluginLoader?: unknown;
}

const deckyWindow = () => globalThis.window as unknown as DeckyWindow;

function withDeckyWindow(parts: DeckyWindow, body: () => void): void {
  const w = deckyWindow();
  const had = { DFL: w.DFL, DeckyPluginLoader: w.DeckyPluginLoader };
  Object.assign(w, parts);
  try {
    body();
  } finally {
    w.DFL = had.DFL;
    w.DeckyPluginLoader = had.DeckyPluginLoader;
  }
}

describe("which copy of @decky/ui ran the searches", () => {
  it("is Tender's own in the standalone bundle, and asks the machine nothing", () => {
    const readCopy = vi.fn<() => DeckyCopy>(() => deckyCopy());
    expect(readSearchingCopy(report(["Tabs"]), "standalone", readCopy)).toEqual({ owner: "tender" });
    expect(readCopy).not.toHaveBeenCalled();
  });

  it("is Decky's in the coexistence bundle, with the version it is running", () => {
    const copy = readSearchingCopy(report(["Tabs"]), "coexistence", () => deckyCopy());
    expect(copy).toEqual({ owner: "decky", carriesEveryName: true, version: "v3.2.8" });
  });

  it("reports the disagreement when Decky's copy does not export a name that missed", () => {
    const copy = readSearchingCopy(report(["Tabs", "Spinner"]), "coexistence", () =>
      deckyCopy({ carries: (name) => name !== "Spinner" }),
    );
    expect(copy).toEqual({ owner: "decky", carriesEveryName: false, version: "v3.2.8" });
  });

  it("asks only about the names @decky/ui exports", () => {
    // `SP_REACT` is installed by the React bootstrap and `ControllerGlyph` is a
    // predicate of ours, so a Decky in perfect step with us carries neither —
    // asking about them would report a package disagreement on every miss.
    const asked: string[] = [];
    readSearchingCopy(report(["SP_REACT", "Tabs", "ControllerGlyph"], ["Tabs"]), "coexistence", () =>
      deckyCopy({
        carries: (name) => {
          asked.push(name);
          return true;
        },
      }),
    );
    expect(asked).toEqual(["Tabs"]);
  });

  it("claims no disagreement when Decky's copy could not be asked at all", () => {
    // An absence has to be demonstrated. With no `DFL` to question, the page
    // must not tell the user the two programs disagree about the package.
    const copy = readSearchingCopy(report(["Tabs"]), "coexistence", () => deckyCopy({ carries: null }));
    expect(copy).toEqual({ owner: "decky", carriesEveryName: true, version: "v3.2.8" });
    expect(describeFailure(report(["Tabs"]), copy)).not.toContain("does not carry");
  });
});

describe("what can be read off Decky Loader", () => {
  it("answers for a name its @decky/ui exports and against one it does not", () => {
    withDeckyWindow({ DFL: { DialogButton: () => null } }, () => {
      const copy = readDeckyCopy();
      expect(copy.carries?.("DialogButton")).toBe(true);
      expect(copy.carries?.("NoSuchExportZzz")).toBe(false);
    });
  });

  it("answers for a name its copy exports with no value behind it", () => {
    // What an unmatched `@decky/ui` predicate leaves: the export is declared,
    // so the name is there and the value is `undefined`. That is a stale Steam
    // lookup in Decky's copy, not a package the two disagree about.
    withDeckyWindow({ DFL: { Tabs: undefined } }, () => {
      expect(readDeckyCopy().carries?.("Tabs")).toBe(true);
    });
  });

  it("can be asked nothing when there is no DFL", () => {
    withDeckyWindow({ DFL: undefined }, () => {
      expect(readDeckyCopy().carries).toBeNull();
    });
  });

  it("reads the version Decky is running", () => {
    withDeckyWindow({ DeckyPluginLoader: { deckyState: { _versionInfo: { current: "v3.2.8" } } } }, () => {
      expect(readDeckyCopy().version).toBe("v3.2.8");
    });
  });

  it("has no version when the loader is not there", () => {
    withDeckyWindow({ DeckyPluginLoader: undefined }, () => {
      expect(readDeckyCopy().version).toBeNull();
    });
  });

  it("has no version when the path it reads has moved", () => {
    // The field is internal and behind an underscore, and a Decky that renamed
    // it is exactly the skew being diagnosed.
    withDeckyWindow({ DeckyPluginLoader: { deckyState: { versionInfo: { current: "v9.9.9" } } } }, () => {
      expect(readDeckyCopy().version).toBeNull();
    });
  });

  it("has no version when reading it throws", () => {
    const loader = {
      get deckyState(): never {
        throw new Error("a future Decky keeps its state somewhere else");
      },
    };
    withDeckyWindow({ DeckyPluginLoader: loader }, () => {
      expect(readDeckyCopy().version).toBeNull();
    });
  });

  it("reads `current` and never `remote`", () => {
    // `remote` is GitHub release data about the PUBLISHED version — a release
    // existing does not mean this machine installed it.
    const versionInfo = {
      current: "v3.2.8",
      get remote(): never {
        throw new Error("nothing may consult the published version");
      },
    };
    withDeckyWindow({ DeckyPluginLoader: { deckyState: { _versionInfo: versionInfo } } }, () => {
      expect(readDeckyCopy().version).toBe("v3.2.8");
    });
  });
});
