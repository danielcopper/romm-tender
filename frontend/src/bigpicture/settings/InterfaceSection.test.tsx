import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { InterfaceSection } from "./InterfaceSection";

describe("InterfaceSection", () => {
  it("shows the toggle with the line saying where it applies", () => {
    render(<InterfaceSection qamEntryPosition="above_decky" onQamEntryPositionChange={vi.fn()} />);

    expect(screen.getByText("Place Tender above Decky Loader")).toBeInTheDocument();
    expect(screen.getByTestId("toggle-desc")).toHaveTextContent(
      "Applies only where Decky Loader is installed. Takes effect the next time you open the Quick Access Menu.",
    );
  });

  it("is on for above and off for below", () => {
    const { rerender } = render(<InterfaceSection qamEntryPosition="above_decky" onQamEntryPositionChange={vi.fn()} />);
    expect(screen.getByTestId("toggle-input")).toBeChecked();

    rerender(<InterfaceSection qamEntryPosition="below_decky" onQamEntryPositionChange={vi.fn()} />);
    expect(screen.getByTestId("toggle-input")).not.toBeChecked();
  });

  it("answers below when switched off and above when switched on", () => {
    const onChange = vi.fn();
    const { rerender } = render(
      <InterfaceSection qamEntryPosition="above_decky" onQamEntryPositionChange={onChange} />,
    );

    fireEvent.click(screen.getByTestId("toggle-input"));
    expect(onChange).toHaveBeenLastCalledWith("below_decky");

    rerender(<InterfaceSection qamEntryPosition="below_decky" onQamEntryPositionChange={onChange} />);
    fireEvent.click(screen.getByTestId("toggle-input"));
    expect(onChange).toHaveBeenLastCalledWith("above_decky");
  });
});
