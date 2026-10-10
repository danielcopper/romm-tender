import { describe, it, expect } from "vitest";
import { statusUnreadSentence } from "./runningAppsWording";

describe("statusUnreadSentence", () => {
  it("names one app as it", () => {
    expect(statusUnreadSentence(["Celeste"])).toBe(
      "Steam lists Celeste as running, and Tender can't tell whether it still is. " +
        "Quit it if it's open; if it has already closed, restart Steam.",
    );
  });

  it("names several apps as they", () => {
    expect(statusUnreadSentence(["Celeste", "Hades", "Metroid"])).toBe(
      "Steam lists Celeste, Hades and Metroid as running, and Tender can't tell whether they still are. " +
        "Quit any that are open; if they have already closed, restart Steam.",
    );
  });
});
