import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { Focusable } from "@decky/ui";
import type { FC } from "react";
import { EmulatorStepScope, useStepOnTriggers, useTriggerHandler } from "./emulatorStepping";

const L1 = 5;
const L2 = 7;
const R2 = 8;

/** A selector's stand-in: what the scope's triggers step while it is mounted. */
const Selector: FC<{ step: ((by: number) => void) | null }> = ({ step }) => {
  useStepOnTriggers(step);
  return null;
};

/** A node further in the scope, as the play row and the tab bar are. */
const Inner: FC = () => (
  <Focusable data-testid="inner" onButtonDown={useTriggerHandler()}>
    <button type="button">inner</button>
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

describe("L2 and R2 over a page's emulator selector", () => {
  const page = (step: ((by: number) => void) | null) =>
    render(
      <EmulatorStepScope>
        {(onButtonDown) => (
          <Focusable onButtonDown={onButtonDown}>
            <button type="button">outer</button>
            <Selector step={step} />
            <Inner />
          </Focusable>
        )}
      </EmulatorStepScope>,
    );

  it("steps the shown selector back with L2 and on with R2, from the scope's own node and from one further in", () => {
    const step = vi.fn();
    const { getByText } = page(step);
    pull(getByText("outer"), R2);
    pull(getByText("inner"), L2);
    expect(step.mock.calls).toEqual([[1], [-1]]);
  });

  it("keeps a trigger it stepped from going on to Steam", () => {
    const { getByText } = page(vi.fn());
    expect(pull(getByText("inner"), R2)).toBe(false);
  });

  it("leaves every other button to Steam, L1 included", () => {
    const step = vi.fn();
    const { getByText } = page(step);
    expect(pull(getByText("inner"), L1)).toBe(true);
    expect(step).not.toHaveBeenCalled();
  });

  it("does nothing, and lets the trigger go on, where no selector is shown", () => {
    const { getByText } = page(null);
    expect(pull(getByText("inner"), R2)).toBe(true);
  });

  it("stops stepping a selector once it is gone", () => {
    const step = vi.fn();
    const { getByText, rerender } = page(step);
    rerender(
      <EmulatorStepScope>
        {(onButtonDown) => (
          <Focusable onButtonDown={onButtonDown}>
            <button type="button">outer</button>
          </Focusable>
        )}
      </EmulatorStepScope>,
    );
    pull(getByText("outer"), R2);
    expect(step).not.toHaveBeenCalled();
  });
});
