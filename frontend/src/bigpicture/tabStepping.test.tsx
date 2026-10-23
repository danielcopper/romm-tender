import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { Focusable } from "@decky/ui";
import type { FC } from "react";
import { TabStepScope, steppedTab, useBumperHandler, useStepTabsOnBumpers } from "./tabStepping";

const L1 = 5;
const R1 = 6;
const R2 = 8;

/** The tabs' stand-in: what the scope's bumpers step while they are shown. */
const Tabs: FC<{ step: ((by: number) => void) | null }> = ({ step }) => {
  useStepTabsOnBumpers(step);
  return null;
};

/** A node of the scope outside the tabs, as the play row is. */
const Row: FC = () => (
  <Focusable onButtonDown={useBumperHandler()}>
    <button type="button">row</button>
  </Focusable>
);

/** Pull *button* on *from*, and answer whether the event got past the scope. */
function pull(from: Element, button: number): boolean {
  let reachedSteam = false;
  const outside = from.ownerDocument.body as HTMLElement;
  const listener = () => {
    reachedSteam = true;
  };
  outside.addEventListener("decky-button-down", listener);
  fireEvent(from, new CustomEvent("decky-button-down", { detail: { button }, bubbles: true }));
  outside.removeEventListener("decky-button-down", listener);
  return reachedSteam;
}

describe("L1 and R1 over a game page's tabs", () => {
  const page = (step: ((by: number) => void) | null) =>
    render(
      <TabStepScope>
        <Row />
        <Tabs step={step} />
      </TabStepScope>,
    );

  it("steps the shown tabs back with L1 and on with R1, and keeps the bumper from going on to Steam", () => {
    const step = vi.fn();
    const { getByText } = page(step);
    expect(pull(getByText("row"), R1)).toBe(false);
    expect(pull(getByText("row"), L1)).toBe(false);
    expect(step.mock.calls).toEqual([[1], [-1]]);
  });

  it("lets every other button go on to Steam, L2 and R2 included", () => {
    const step = vi.fn();
    const { getByText } = page(step);
    expect(pull(getByText("row"), R2)).toBe(true);
    expect(step).not.toHaveBeenCalled();
  });

  it("lets L1 and R1 go on to Steam while no tabs are shown", () => {
    const { getByText } = page(null);
    expect(pull(getByText("row"), R1)).toBe(true);
  });
});

describe("steppedTab", () => {
  const ids = ["info", "bios", "artwork"];

  it("steps to the next and the previous tab", () => {
    expect(steppedTab(ids, "bios", 1)).toBe("artwork");
    expect(steppedTab(ids, "bios", -1)).toBe("info");
  });

  it("goes round from the last tab to the first and back", () => {
    expect(steppedTab(ids, "artwork", 1)).toBe("info");
    expect(steppedTab(ids, "info", -1)).toBe("artwork");
  });

  it("steps from the first tab where the active one is not among them", () => {
    expect(steppedTab(ids, "saves", 1)).toBe("bios");
  });
});
