import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopUncheckedSourceDialog } from "./DesktopUncheckedSourceDialog";

describe("DesktopUncheckedSourceDialog", () => {
  it("asks whether to start though the source could not be checked", () => {
    render(<DesktopUncheckedSourceDialog onChoice={vi.fn()} />);

    const dialog = screen.getByRole("dialog", { name: "Emulator Source Unchecked" });
    expect(dialog).toHaveTextContent(
      "Tender could not check whether the emulator source is switched on. Start anyway?",
    );
    expect(screen.getByRole("button", { name: "Start" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("calls onChoice with true when clicking Start", () => {
    const onChoice = vi.fn();
    render(<DesktopUncheckedSourceDialog onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: "Start" }));
    expect(onChoice).toHaveBeenCalledWith(true);
  });

  it("calls onChoice with false when clicking Cancel", () => {
    const onChoice = vi.fn();
    render(<DesktopUncheckedSourceDialog onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onChoice).toHaveBeenCalledWith(false);
  });

  it("calls onChoice with false on Escape key", () => {
    const onChoice = vi.fn();
    render(<DesktopUncheckedSourceDialog onChoice={onChoice} />);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onChoice).toHaveBeenCalledWith(false);
  });
});
