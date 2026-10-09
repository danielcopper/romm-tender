import { describe, it, expect } from "vitest";
import { statusUnreadSentence } from "./runningAppsWording";

describe("statusUnreadSentence", () => {
  it("names one app as it", () => {
    expect(statusUnreadSentence(["Celeste"])).toBe(
      "Steam lists Celeste as running, and Tender can't tell whether it is. If it has closed, restart Steam.",
    );
  });

  it("names several apps as they", () => {
    expect(statusUnreadSentence(["Celeste", "Hades", "Metroid"])).toBe(
      "Steam lists Celeste, Hades and Metroid as running, and Tender can't tell whether they are. " +
        "If they have closed, restart Steam.",
    );
  });
});
