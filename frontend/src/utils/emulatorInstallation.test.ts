import { describe, expect, it } from "vitest";
import { isNotEstablished, notEstablishedCause } from "./emulatorInstallation";

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
