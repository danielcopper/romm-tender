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

  it.each([
    ["Start Anyway", "start_anyway"],
    ["Retry connection", "retry"],
    ["Cancel", "cancel"],
    ["Close dialog", "cancel"],
  ])("answers %s with %s", (label, choice) => {
    const onChoice = vi.fn();
    render(<DesktopOfflineDriftDialog onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: label }));
    expect(onChoice).toHaveBeenCalledExactlyOnceWith(choice);
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
});
