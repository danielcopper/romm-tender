import { describe, it, expect, beforeEach, vi } from "vitest";
import { render } from "@testing-library/react";
import { createElement as ce } from "react";
import { UpdateAnnouncementNotice } from "./UpdateAnnouncementNotice";
import { UpdateFailureNotice } from "./UpdateFailureNotice";
import { UpdateNotice } from "./UpdateNotice";
import { resetUpdateNoticeStoreForTests, setUpdateNoticeState } from "../utils/updateNoticeStore";
import { resetUpdateOutcomeStoreForTests, setUpdateOutcomeState } from "../utils/updateOutcomeStore";

// Re-mocked so Field and ButtonItem echo the props that place their children:
// the global stub in test-setup.ts drops them. Steam's ButtonItem draws a Field
// row (`@decky/ui`'s components/ButtonItem.js finds it by Field's
// `childrenContainerWidth` among its props), so on all three cards the space
// above the buttons is the Field's.
vi.mock("@decky/ui", () => {
  type AnyProps = Record<string, unknown> & { children?: unknown };
  const passThrough = (p: AnyProps) => ce("div", null, p.children as never);
  return {
    PanelSectionRow: passThrough,
    Focusable: passThrough,
    DialogButton: (p: AnyProps) => ce("button", null, p.children as never),
    Field: (p: AnyProps) =>
      ce(
        "div",
        {
          "data-testid": "field",
          "data-bottom-separator": p.bottomSeparator,
          "data-children-layout": p.childrenLayout,
        },
        p.children as never,
      ),
    ButtonItem: (p: AnyProps) =>
      ce(
        "div",
        { "data-testid": "button-item", "data-bottom-separator": p.bottomSeparator, "data-layout": p.layout },
        ce("button", null, p.children as never),
      ),
  };
});

describe("the update cards' button rows", () => {
  beforeEach(() => {
    resetUpdateNoticeStoreForTests();
    resetUpdateOutcomeStoreForTests();
  });

  it("the announcement card's Dismiss is a ButtonItem below, with no separator", () => {
    setUpdateOutcomeState({
      announcement: { version: "1.3.0", direction: "updated" },
      failure: null,
      failureDismissed: false,
    });
    const { container } = render(<UpdateAnnouncementNotice />);
    const rows = [...container.querySelectorAll("button")].map((b) => b.closest("[data-testid='button-item']"));
    expect(rows).toHaveLength(1);
    expect(rows[0]?.getAttribute("data-layout")).toBe("below");
    expect(rows[0]?.getAttribute("data-bottom-separator")).toBe("none");
  });

  it.each([
    [
      "the available-update card",
      () => {
        setUpdateNoticeState({
          available: true,
          newer: true,
          latestVersion: "0.34.0",
          currentVersion: "0.33.0",
          enabled: true,
          installedProgram: true,
        });
        return <UpdateNotice onOpenUpdates={() => {}} />;
      },
    ],
    [
      "the rolled-back card",
      () => {
        setUpdateOutcomeState({
          announcement: null,
          failure: { attemptedVersion: "0.34.0", restoredVersion: "0.33.0", rolledBackAt: "2026-09-25T10:15:00Z" },
          failureDismissed: false,
        });
        return <UpdateFailureNotice onOpenUpdates={() => {}} />;
      },
    ],
  ])("%s puts both buttons in one Field below, with no separator", (_name, card) => {
    const { container } = render(card());
    const buttons = [...container.querySelectorAll("button")];
    expect(buttons.map((b) => b.textContent)).toEqual(["Open Updates", "Dismiss"]);
    const fields = new Set(buttons.map((b) => b.closest("[data-testid='field']")));
    expect(fields.size).toBe(1);
    const [field] = fields;
    expect(field?.getAttribute("data-children-layout")).toBe("below");
    expect(field?.getAttribute("data-bottom-separator")).toBe("none");
  });
});
