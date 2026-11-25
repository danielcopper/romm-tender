import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopCopySlotDialog } from "./DesktopCopySlotDialog";
import { assertDialogEscapeScopedToOwnWindow } from "../../../test-utils/popupEscapeTestHelper";
import type { SaveSlotSummary } from "../../../types";

describe("DesktopCopySlotDialog", () => {
  const availableSlots: SaveSlotSummary[] = [
    { slot: "default", count: 2, source: "server", latest_updated_at: null },
    { slot: "speedrun", count: 1, source: "server", latest_updated_at: null },
    { slot: "casual", count: 3, source: "server", latest_updated_at: null },
    { slot: "", count: 1, source: "server", latest_updated_at: null },
  ];

  const renderDialog = ({
    slots = availableSlots,
    onDismiss = vi.fn(),
    onCopy = vi.fn(),
  }: {
    slots?: SaveSlotSummary[];
    onDismiss?: () => void;
    onCopy?: (targetSlot: string) => void;
  } = {}) =>
    render(<DesktopCopySlotDialog sourceSlot="default" availableSlots={slots} onDismiss={onDismiss} onCopy={onCopy} />);

  it("renders title, description, eligible target slots, tone styles, and new slot input", () => {
    renderDialog();

    expect(screen.getByRole("dialog", { name: "Copy save to slot" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "Copies this save into the chosen slot, which becomes the active slot. The original save is kept.",
      ),
    ).toBeInTheDocument();

    // Eligible target slots (excludes sourceSlot 'default' and legacy '')
    const speedrunBtn = screen.getByRole("button", { name: "speedrun" });
    expect(speedrunBtn).toBeInTheDocument();
    expect(speedrunBtn.style.backgroundImage).toBe("none");
    expect(screen.getByRole("button", { name: "casual" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "default" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Manual archive" })).toBeNull();

    expect(screen.getByPlaceholderText("New slot name…")).toBeInTheDocument();
    const createBtn = screen.getByRole("button", { name: "Create & Copy" });
    expect(createBtn).toBeDisabled();
    expect(createBtn.style.backgroundImage).toContain("#1a9fff");
    expect(createBtn.style.opacity).toBe("0.5");

    const cancelBtn = screen.getByRole("button", { name: "Cancel" });
    expect(cancelBtn).toBeInTheDocument();
    expect(cancelBtn.style.backgroundColor).toBe("transparent");
  });

  it("shows empty-targets notice when no eligible existing slots remain", () => {
    renderDialog({ slots: [{ slot: "default", count: 1, source: "server", latest_updated_at: null }] });

    expect(screen.getByText("No other slots yet — create one below.")).toBeInTheDocument();
  });

  it("calls onCopy when an existing target slot button is clicked", () => {
    const onCopy = vi.fn();
    renderDialog({ onCopy });

    fireEvent.click(screen.getByRole("button", { name: "speedrun" }));
    expect(onCopy).toHaveBeenCalledWith("speedrun");
  });

  it("calls onCopy with trimmed new slot name when Create & Copy is clicked or Enter is pressed", () => {
    const onCopy = vi.fn();
    renderDialog({ onCopy });

    const input = screen.getByPlaceholderText("New slot name…");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onCopy).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "  hardcore  " } });
    fireEvent.keyDown(input, { key: "a" });
    expect(onCopy).not.toHaveBeenCalled();

    const createAndCopyBtn = screen.getByRole("button", { name: "Create & Copy" });
    expect(createAndCopyBtn).not.toBeDisabled();
    expect(createAndCopyBtn.style.opacity).toBe("");
    fireEvent.click(createAndCopyBtn);
    expect(onCopy).toHaveBeenCalledWith("hardcore");

    fireEvent.change(input, { target: { value: "ironman" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onCopy).toHaveBeenCalledWith("ironman");
  });

  it("calls onDismiss when Cancel is clicked or Escape is pressed in input", () => {
    const onDismiss = vi.fn();
    renderDialog({ onDismiss });

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);

    const input = screen.getByPlaceholderText("New slot name…");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onDismiss).toHaveBeenCalledTimes(2);
  });

  it("listens for Escape on the dialog's own Window rather than globalThis.window", () => {
    assertDialogEscapeScopedToOwnWindow((onDismiss) => (
      <DesktopCopySlotDialog
        sourceSlot="default"
        availableSlots={availableSlots}
        onDismiss={onDismiss}
        onCopy={vi.fn()}
      />
    ));
  });
});
