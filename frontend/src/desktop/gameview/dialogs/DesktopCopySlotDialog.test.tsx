import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopCopySlotDialog } from "./DesktopCopySlotDialog";
import type { SaveSlotSummary } from "../../../types";

describe("DesktopCopySlotDialog", () => {
  const availableSlots: SaveSlotSummary[] = [
    { slot: "default", count: 2, source: "server", latest_updated_at: null },
    { slot: "speedrun", count: 1, source: "server", latest_updated_at: null },
    { slot: "casual", count: 3, source: "server", latest_updated_at: null },
    { slot: "", count: 1, source: "server", latest_updated_at: null },
  ];

  it("renders title, description, eligible target slots, and new slot input", () => {
    render(
      <DesktopCopySlotDialog
        sourceSlot="default"
        availableSlots={availableSlots}
        onDismiss={vi.fn()}
        onCopy={vi.fn()}
      />,
    );

    expect(screen.getByRole("dialog", { name: "Copy save to slot" })).toBeInTheDocument();
    expect(
      screen.getByText(
        "Copies this save into the chosen slot, which becomes the active slot. The original save is kept.",
      ),
    ).toBeInTheDocument();

    // Eligible target slots (excludes sourceSlot 'default' and legacy '')
    expect(screen.getByRole("button", { name: "speedrun" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "casual" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "default" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Manual archive" })).toBeNull();

    expect(screen.getByPlaceholderText("New slot name…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create & Copy" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("shows empty-targets notice when no eligible existing slots remain", () => {
    render(
      <DesktopCopySlotDialog
        sourceSlot="default"
        availableSlots={[{ slot: "default", count: 1, source: "server", latest_updated_at: null }]}
        onDismiss={vi.fn()}
        onCopy={vi.fn()}
      />,
    );

    expect(screen.getByText("No other slots yet — create one below.")).toBeInTheDocument();
  });

  it("calls onCopy when an existing target slot button is clicked", () => {
    const onCopy = vi.fn();
    render(
      <DesktopCopySlotDialog
        sourceSlot="default"
        availableSlots={availableSlots}
        onDismiss={vi.fn()}
        onCopy={onCopy}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "speedrun" }));
    expect(onCopy).toHaveBeenCalledWith("speedrun");
  });

  it("calls onCopy with trimmed new slot name when Create & Copy is clicked", () => {
    const onCopy = vi.fn();
    render(
      <DesktopCopySlotDialog
        sourceSlot="default"
        availableSlots={availableSlots}
        onDismiss={vi.fn()}
        onCopy={onCopy}
      />,
    );

    const input = screen.getByPlaceholderText("New slot name…");
    fireEvent.change(input, { target: { value: "  hardcore  " } });

    const createAndCopyBtn = screen.getByRole("button", { name: "Create & Copy" });
    expect(createAndCopyBtn).not.toBeDisabled();
    fireEvent.click(createAndCopyBtn);

    expect(onCopy).toHaveBeenCalledWith("hardcore");
  });

  it("submits new slot on Enter key press in the input", () => {
    const onCopy = vi.fn();
    render(
      <DesktopCopySlotDialog
        sourceSlot="default"
        availableSlots={availableSlots}
        onDismiss={vi.fn()}
        onCopy={onCopy}
      />,
    );

    const input = screen.getByPlaceholderText("New slot name…");
    fireEvent.change(input, { target: { value: "ironman" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onCopy).toHaveBeenCalledWith("ironman");
  });

  it("calls onDismiss when Cancel is clicked or Escape is pressed in input", () => {
    const onDismiss = vi.fn();
    render(
      <DesktopCopySlotDialog
        sourceSlot="default"
        availableSlots={availableSlots}
        onDismiss={onDismiss}
        onCopy={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);

    const input = screen.getByPlaceholderText("New slot name…");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onDismiss).toHaveBeenCalledTimes(2);
  });
});
