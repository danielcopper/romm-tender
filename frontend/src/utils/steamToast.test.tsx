// What a test here CANNOT see: happy-dom loads none of Steam's stylesheets, so
// every class below is an attribute value and nothing more. That a toast fits
// the popup window, that the tab entry is reachable with a controller, and that
// `Multiline` buys a second line are all device questions — what is pinned here
// is which layout is chosen and which of Steam's classes each element carries,
// because those are what decide it.

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import type { ToastData } from "../api/host";
import type { ToastClasses } from "./deckyUiInternals";
import {
  SteamToast,
  TOAST_LOCATION_BIG_PICTURE_POPUP,
  TOAST_LOCATION_DESKTOP_POPUP,
  TOAST_LOCATION_NOTIFICATION_TAB,
} from "./steamToast";

// Stand-ins for Steam's minified hashes: one per name, and distinct, so an
// element carrying the wrong one is a failure rather than a coincidence.
const CLASSES: ToastClasses = {
  ShortTemplate: "c-short",
  TwoLine: "c-twoline",
  StandardTemplateContainer: "c-container",
  StandardTemplate: "c-standard",
  StandardTemplateDesktop: "c-standard-desktop",
  DesktopToastTemplate: "c-desktop-toast",
  Content: "c-content",
  Header: "c-header",
  Title: "c-title",
  Timestamp: "c-timestamp",
  Body: "c-body",
  StandardNotificationDescription: "c-description",
  StandardNotificationSubText: "c-subtext",
  Multiline: "c-multiline",
  NewIndicator: "c-new",
};

const TOAST: ToastData = { title: "Tender", body: "Sync finished" };
const CREATED_MS = Date.UTC(2026, 0, 2, 14, 35);

const draw = (toast: ToastData, location: number | undefined, newIndicator = false) =>
  render(
    <SteamToast
      toast={toast}
      location={location}
      createdMs={CREATED_MS}
      newIndicator={newIndicator}
      classes={CLASSES}
    />,
  );

const classesOf = (text: string): string => screen.getByText(text).className;

describe("the Big Picture popup layout", () => {
  it("draws the short two-line template, with the title and the body", () => {
    const { container } = draw(TOAST, TOAST_LOCATION_BIG_PICTURE_POPUP);
    expect(container.querySelector(".c-short.c-twoline")).not.toBeNull();
    expect(classesOf("Tender")).toBe("c-title");
    expect(classesOf("Sync finished")).toBe("c-body");
  });

  it("leaves the subtext out, because the popup window is fixed and clips it", () => {
    draw({ ...TOAST, subtext: "14 games" }, TOAST_LOCATION_BIG_PICTURE_POPUP);
    expect(screen.queryByText("14 games")).toBeNull();
  });

  it("carries neither the standard container nor a timestamp, which belong to the other two", () => {
    const { container } = draw(TOAST, TOAST_LOCATION_BIG_PICTURE_POPUP);
    expect(container.querySelector(".c-container")).toBeNull();
    expect(container.querySelector(".c-timestamp")).toBeNull();
  });
});

describe("the desktop-client popup layout", () => {
  it("draws Steam's desktop toast template inside the standard container", () => {
    const { container } = draw(TOAST, TOAST_LOCATION_DESKTOP_POPUP);
    expect(container.querySelector(".c-container > .c-desktop-toast.c-standard-desktop")).not.toBeNull();
  });

  it("lets a long body run to the lines Multiline buys, rather than clipping it to one", () => {
    // The difference from the tab entry, and the reason this layout exists at
    // all: without the class Steam ellipsises a description after one line.
    draw(TOAST, TOAST_LOCATION_DESKTOP_POPUP);
    expect(classesOf("Sync finished")).toBe("c-description c-multiline");
  });

  it("draws the subtext when there is one, and nothing when there is not", () => {
    draw({ ...TOAST, subtext: "14 games" }, TOAST_LOCATION_DESKTOP_POPUP);
    expect(classesOf("14 games")).toBe("c-subtext c-multiline");
    const { container } = draw(TOAST, TOAST_LOCATION_DESKTOP_POPUP);
    expect(container.querySelector(".c-subtext")).toBeNull();
  });

  it("shows no new indicator — that mark belongs to the tab, where an entry stays", () => {
    const { container } = draw(TOAST, TOAST_LOCATION_DESKTOP_POPUP, true);
    expect(container.querySelector(".c-new")).toBeNull();
  });
});

describe("the Quick Access notification-tab layout", () => {
  it("draws the standard template with the title, the time, the body and the subtext", () => {
    const { container } = draw({ ...TOAST, subtext: "14 games" }, TOAST_LOCATION_NOTIFICATION_TAB);
    expect(container.querySelector(".c-container > .c-standard")).not.toBeNull();
    expect(classesOf("Tender")).toBe("c-title");
    expect(classesOf("Sync finished")).toBe("c-description");
    expect(classesOf("14 games")).toBe("c-subtext");
    expect(container.querySelector(".c-timestamp")?.textContent).toBe(
      new Date(CREATED_MS).toLocaleTimeString(undefined, { timeStyle: "short" }),
    );
  });

  it("lets the subtext wrap as far as it runs, where Steam's own rule would end it in an ellipsis", () => {
    const { container } = draw(
      { ...TOAST, subtext: "a reason longer than two lines" },
      TOAST_LOCATION_NOTIFICATION_TAB,
    );
    const subtext = container.querySelector<HTMLElement>(".c-subtext");
    expect(subtext?.style.whiteSpace).toBe("normal");
    expect(subtext?.style.overflow).toBe("visible");
    // ...and the entry grows with it, from Steam's own height as the floor.
    const entry = container.querySelector<HTMLElement>(".c-standard");
    expect(entry?.style.height).toBe("auto");
    expect(entry?.style.minHeight).toBe("50px");
  });

  it("is a focus stop, so a reader can reach it and Steam can scroll it into view", () => {
    // A region there scrolls only by moving focus. The stub renders a
    // Focusable that carries an activate handler as `tabindex="0"`, which is
    // the one thing a test can see about it — see test-setup.ts.
    const { container } = draw(TOAST, TOAST_LOCATION_NOTIFICATION_TAB);
    expect(container.querySelector('[data-testid="focusable"][tabindex="0"]')).not.toBeNull();
  });

  it("marks the entry as new when the notification says so, and not otherwise", () => {
    const shown = draw(TOAST, TOAST_LOCATION_NOTIFICATION_TAB, true);
    expect(shown.container.querySelector(".c-new")).not.toBeNull();
    const quiet = draw(TOAST, TOAST_LOCATION_NOTIFICATION_TAB, false);
    expect(quiet.container.querySelector(".c-new")).toBeNull();
  });
});

describe("a location this code does not know", () => {
  it.each([[undefined], [0], [4], [5], [99]])("falls back to the tab layout for %s", (location) => {
    // The only one of the three that is right to render anywhere: it carries
    // every field a toast can have and imposes no size of its own.
    const { container } = draw({ ...TOAST, subtext: "14 games" }, location);
    expect(container.querySelector(".c-container > .c-standard")).not.toBeNull();
    expect(screen.getByText("14 games")).toBeInTheDocument();
  });
});

describe("a class map Steam no longer answers for", () => {
  it("still says everything the toast says, in an unstyled box", () => {
    // What makes the class map cost appearance and not the panel: every read
    // is optional, so a miss leaves the elements in place without their names.
    render(
      <SteamToast
        toast={{ ...TOAST, subtext: "14 games" }}
        location={TOAST_LOCATION_NOTIFICATION_TAB}
        createdMs={CREATED_MS}
        newIndicator
        classes={{}}
      />,
    );
    expect(screen.getByText("Tender")).toBeInTheDocument();
    expect(screen.getByText("Sync finished")).toBeInTheDocument();
    expect(screen.getByText("14 games")).toBeInTheDocument();
  });
});
