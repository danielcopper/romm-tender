import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopCoreChangeDialog } from "./DesktopCoreChangeDialog";

describe("DesktopCoreChangeDialog", () => {
  it("renders core labels and warning message", () => {
    const onChoice = vi.fn();
    render(<DesktopCoreChangeDialog oldLabel="PCSX ReARMed" newLabel="DuckStation" onChoice={onChoice} />);

    const dialog = screen.getByRole("dialog", { name: "Emulator Core Changed" });
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveTextContent("PCSX ReARMed → DuckStation");
    expect(dialog).toHaveTextContent("Save Compatibility Warning");
    expect(screen.getByRole("button", { name: "Continue" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("calls onChoice with true when clicking Continue", () => {
    const onChoice = vi.fn();
    render(<DesktopCoreChangeDialog oldLabel="PCSX ReARMed" newLabel="DuckStation" onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(onChoice).toHaveBeenCalledWith(true);
  });

  it("calls onChoice with false when clicking Cancel", () => {
    const onChoice = vi.fn();
    render(<DesktopCoreChangeDialog oldLabel="PCSX ReARMed" newLabel="DuckStation" onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onChoice).toHaveBeenCalledWith(false);
  });

  it("calls onChoice with false on Escape key", () => {
    const onChoice = vi.fn();
    render(<DesktopCoreChangeDialog oldLabel="PCSX ReARMed" newLabel="DuckStation" onChoice={onChoice} />);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onChoice).toHaveBeenCalledWith(false);
  });
});
