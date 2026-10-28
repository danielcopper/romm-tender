import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopOfflineDriftDialog } from "./DesktopOfflineDriftDialog";

describe("DesktopOfflineDriftDialog", () => {
  it("renders title, description and all three buttons", () => {
    const onChoice = vi.fn();
    render(<DesktopOfflineDriftDialog onChoice={onChoice} />);

    const dialog = screen.getByRole("dialog", { name: "RomM Unreachable" });
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveTextContent(
      "Your local save has unsynced changes. Playing now may create a conflict you'll resolve later. Start anyway?",
    );
    expect(screen.getByRole("button", { name: "Start Anyway" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry connection" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("calls onChoice with 'start_anyway' when clicking Start Anyway", () => {
    const onChoice = vi.fn();
    render(<DesktopOfflineDriftDialog onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: "Start Anyway" }));
    expect(onChoice).toHaveBeenCalledWith("start_anyway");
  });

  it("calls onChoice with 'retry' when clicking Retry connection", () => {
    const onChoice = vi.fn();
    render(<DesktopOfflineDriftDialog onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: "Retry connection" }));
    expect(onChoice).toHaveBeenCalledWith("retry");
  });

  it("calls onChoice with 'cancel' when clicking Cancel", () => {
    const onChoice = vi.fn();
    render(<DesktopOfflineDriftDialog onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onChoice).toHaveBeenCalledWith("cancel");
  });

  it("calls onChoice with 'cancel' on Escape key", () => {
    const onChoice = vi.fn();
    render(<DesktopOfflineDriftDialog onChoice={onChoice} />);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onChoice).toHaveBeenCalledWith("cancel");
  });

  it("stays open on a key other than Escape", () => {
    const onChoice = vi.fn();
    render(<DesktopOfflineDriftDialog onChoice={onChoice} />);

    fireEvent.keyDown(window, { key: "Enter" });
    expect(onChoice).not.toHaveBeenCalled();
  });

  it("calls onChoice with 'cancel' on backdrop click", () => {
    const onChoice = vi.fn();
    render(<DesktopOfflineDriftDialog onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    expect(onChoice).toHaveBeenCalledWith("cancel");
  });
});
