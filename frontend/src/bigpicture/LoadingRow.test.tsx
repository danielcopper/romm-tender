import { describe, it, expect, vi } from "vitest";
import { render } from "@testing-library/react";
import { createElement as ce } from "react";
import { LoadingRow, LOADING_SPINNER_SIZE } from "./LoadingRow";

vi.mock("@decky/ui", () => {
  type AnyProps = Record<string, unknown> & { children?: unknown };
  return {
    PanelSectionRow: (p: AnyProps) => ce("div", { "data-testid": "panel-row" }, p.children as never),
  };
});

describe("LoadingRow", () => {
  it("renders a spinner constrained to the fixed LOADING_SPINNER_SIZE", () => {
    const { getByTestId } = render(<LoadingRow />);
    const spinner = getByTestId("spinner");
    expect(spinner.style.width).toBe(`${LOADING_SPINNER_SIZE}px`);
    expect(spinner.style.height).toBe(`${LOADING_SPINNER_SIZE}px`);
  });

  it("keeps the loading row's throbber within 32px", () => {
    expect(LOADING_SPINNER_SIZE).toBeGreaterThan(0);
    expect(LOADING_SPINNER_SIZE).toBeLessThanOrEqual(32);
  });

  it("renders inside a PanelSectionRow", () => {
    const { getByTestId } = render(<LoadingRow />);
    expect(getByTestId("panel-row")).not.toBeNull();
  });

  it("shows no caption text when no label is given", () => {
    const { container } = render(<LoadingRow />);
    expect(container.textContent).toBe("");
  });

  it("renders the optional label beside the spinner", () => {
    const { getByText } = render(<LoadingRow label="Loading platforms…" />);
    expect(getByText("Loading platforms…")).not.toBeNull();
  });
});
