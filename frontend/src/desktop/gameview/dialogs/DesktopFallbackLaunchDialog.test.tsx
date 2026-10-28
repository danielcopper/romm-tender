import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopFallbackLaunchDialog } from "./DesktopFallbackLaunchDialog";

describe("DesktopFallbackLaunchDialog", () => {
  it("renders default description when no message is provided", () => {
    const onChoice = vi.fn();
    render(<DesktopFallbackLaunchDialog onChoice={onChoice} />);

    const dialog = screen.getByRole("dialog", { name: "Save Sync Unavailable" });
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveTextContent("Couldn't sync saves with RomM server. Launch with local saves?");
    expect(screen.getByRole("button", { name: "Launch Anyway" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("renders custom message when provided", () => {
    const onChoice = vi.fn();
    render(<DesktopFallbackLaunchDialog message="Server returned 500" onChoice={onChoice} />);

    const dialog = screen.getByRole("dialog", { name: "Save Sync Unavailable" });
    expect(dialog).toHaveTextContent("Server returned 500 — launch with local saves?");
  });

  it("calls onChoice with true when clicking Launch Anyway", () => {
    const onChoice = vi.fn();
    render(<DesktopFallbackLaunchDialog onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: "Launch Anyway" }));
    expect(onChoice).toHaveBeenCalledWith(true);
  });

  it("calls onChoice with false when clicking Cancel", () => {
    const onChoice = vi.fn();
    render(<DesktopFallbackLaunchDialog onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onChoice).toHaveBeenCalledWith(false);
  });

  it("calls onChoice with false on Escape key", () => {
    const onChoice = vi.fn();
    render(<DesktopFallbackLaunchDialog onChoice={onChoice} />);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onChoice).toHaveBeenCalledWith(false);
  });
});
