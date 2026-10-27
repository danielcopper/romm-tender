import { describe, expect, it } from "vitest";
import { libretroEmu } from "../test-utils/coreFixtures";
import { activeEmulatorNote, isNotEstablished, notEstablishedCause, notEstablishedNote } from "./emulatorInstallation";

describe("an installation that could not be established", () => {
  it.each(["not_established", "no_find_rule", "find_rules_unread", "lookup_unsupported", "path_unseen"])(
    "is what %s says",
    (reason) => {
      expect(isNotEstablished(reason)).toBe(true);
    },
  );

  it.each(["not_installed", "inject", "quoting", null])("is not what %s says", (reason) => {
    expect(isNotEstablished(reason)).toBe(false);
    expect(notEstablishedCause(reason)).toBeNull();
  });

  it("names its cause where the backend knows one, and none where it does not", () => {
    expect(notEstablishedCause("find_rules_unread")).toBe("ES-DE's find rules could not be read");
    expect(notEstablishedCause("not_established")).toBeNull();
  });

  it("takes no inherited property for a reason", () => {
    expect(isNotEstablished("toString")).toBe(false);
  });
});

describe("the note beside such an entry", () => {
  it("names the cause where one is known, and stands alone where not", () => {
    expect(notEstablishedNote("no_find_rule")).toBe("installation not established: ES-DE has no rule for finding it");
    expect(notEstablishedNote("not_established")).toBe("installation not established");
  });

  it("is absent for any other reason", () => {
    expect(notEstablishedNote("not_installed")).toBeNull();
    expect(notEstablishedNote(null)).toBeNull();
  });
});

describe("the note under the emulator a page launches with", () => {
  const paprium = { ...libretroEmu("genesis_plus_gx_expanded_libretro", "Paprium"), reason: "lookup_unsupported" };
  const mgba = libretroEmu("mgba_libretro", "mGBA", true);

  it("names the emulator and the cause where the active one could not be established", () => {
    expect(activeEmulatorNote([mgba, paprium], "Paprium")).toBe(
      "Whether Paprium is installed could not be established: Tender cannot follow ES-DE's lookup for it yet.",
    );
  });

  it("is absent where the active emulator is another, or there is none", () => {
    expect(activeEmulatorNote([mgba, paprium], "mGBA")).toBeNull();
    expect(activeEmulatorNote([mgba, paprium], null)).toBeNull();
  });
});
