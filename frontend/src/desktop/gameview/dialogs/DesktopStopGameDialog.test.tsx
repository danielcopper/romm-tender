import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopStopGameDialog } from "./DesktopStopGameDialog";

describe("DesktopStopGameDialog", () => {
  it("warns that unsaved progress may be lost", () => {
    render(<DesktopStopGameDialog onChoice={vi.fn()} />);

    const dialog = screen.getByRole("dialog", { name: "Stop Game?" });
    expect(dialog).toHaveTextContent("Any progress since the last in-game save may be lost.");
    expect(screen.getByRole("button", { name: "Stop Game" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("answers true on Stop Game", () => {
    const onChoice = vi.fn();
    render(<DesktopStopGameDialog onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: "Stop Game" }));
    expect(onChoice).toHaveBeenCalledWith(true);
  });

  it("answers false on Cancel", () => {
    const onChoice = vi.fn();
    render(<DesktopStopGameDialog onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onChoice).toHaveBeenCalledWith(false);
  });

  it("answers false on Escape", () => {
    const onChoice = vi.fn();
    render(<DesktopStopGameDialog onChoice={onChoice} />);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onChoice).toHaveBeenCalledWith(false);
  });
});
