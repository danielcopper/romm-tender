/**
 * The selector ladder's first rung, against class maps shaped like the ones
 * `@decky/ui` resolves out of Steam: made-up hashes, with nothing readable in
 * them for the class-name rung to match. The rest of the suite runs with those
 * maps undefined (test-setup.ts), where every finder falls past the first rung,
 * so only this file sees the first rung answer.
 */

import { describe, it, expect, vi } from "vitest";
import { findPlayBarAndContainer, locateOverviewPanel, locatePlayButton, playBarRung } from "./elementSelectors";

const HASHED = vi.hoisted(() => ({
  overviewPanel: "_2hVxG7mQ4wLz9RkTnBq1Ye",
  playButton: "_3Kd8sWq1ZpN0cXv5LmR7tA",
  playButtonContainer: "_1Qz6fH2jKx8VbN4mPw0sLc",
  playBar: "_7Ty3uE9oGc5Ja1SdXk2nVw",
}));

vi.mock("../../utils/deckyUiInternals", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../utils/deckyUiInternals")>()),
  appDetailsClasses: { AppDetailsOverviewPanel: HASHED.overviewPanel },
  appActionButtonClasses: { PlayButton: HASHED.playButton, PlayButtonContainer: HASHED.playButtonContainer },
  playSectionClasses: { PlayBar: HASHED.playBar },
  basicAppDetailsSectionStylerClasses: {},
}));

/** An element carrying only `className`, appended to `parent`. */
function add(parent: ParentNode, className: string, tag: "div" | "button" = "div"): HTMLElement {
  const el = document.createElement(tag);
  el.className = className;
  parent.appendChild(el);
  return el;
}

describe("the module-class rung", () => {
  it("finds the overview panel by its resolved class, ahead of a class-name match before it", () => {
    const doc = document.implementation.createHTMLDocument("Steam Desktop");
    add(doc.body, "AppDetailsOverviewPanel");
    const panel = add(doc.body, `${HASHED.overviewPanel} Panel`);

    expect(locateOverviewPanel(doc)).toEqual({ element: panel, rung: "module class" });
  });

  it("finds the Play button by its resolved class, ahead of a class-name match before it", () => {
    const root = document.createElement("div");
    add(root, "PlayButton", "button");
    const button = add(root, `${HASHED.playButton} Panel`, "button");

    expect(locatePlayButton(root)).toEqual({ element: button, rung: "module class" });
  });

  it("finds the Play button's container by its resolved class", () => {
    const root = document.createElement("div");
    const container = add(root, HASHED.playButtonContainer);
    add(container, HASHED.playButton, "button");

    expect(locatePlayButton(root)).toEqual({ element: container, rung: "module class" });
  });

  it("recognises the play bar by its resolved class", () => {
    const panel = document.createElement("div");
    const playBar = add(panel, `${HASHED.playBar} Panel`);
    const playSection = add(playBar, "Panel");
    add(panel, "Panel");

    expect(playBarRung(playBar)).toBe("module class");
    expect(findPlayBarAndContainer(panel, playSection)).toEqual({
      playBarTop: playBar,
      container: panel,
      rung: "module class",
    });
  });
});
