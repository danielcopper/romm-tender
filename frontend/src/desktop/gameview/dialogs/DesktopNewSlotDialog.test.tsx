import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopNewSlotDialog } from "./DesktopNewSlotDialog";

describe("DesktopNewSlotDialog", () => {
  it("renders title, description, input and action buttons", () => {
    render(<DesktopNewSlotDialog onDismiss={vi.fn()} onCreate={vi.fn()} />);

    expect(screen.getByRole("dialog", { name: "New Save Slot" })).toBeInTheDocument();
    expect(
      screen.getByText("Enter a name for the new save slot. It will become the active slot immediately."),
    ).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Slot Name (e.g. speedrun, casual)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create Slot" })).toBeDisabled();
  });

  it("calls onCreate with trimmed input when Create Slot is clicked", () => {
    const onCreate = vi.fn();
    render(<DesktopNewSlotDialog onDismiss={vi.fn()} onCreate={onCreate} />);

    const input = screen.getByPlaceholderText("Slot Name (e.g. speedrun, casual)");
    fireEvent.change(input, { target: { value: "  speedrun  " } });

    const createBtn = screen.getByRole("button", { name: "Create Slot" });
    expect(createBtn).not.toBeDisabled();
    fireEvent.click(createBtn);

    expect(onCreate).toHaveBeenCalledWith("speedrun");
  });

  it("submits on Enter key press", () => {
    const onCreate = vi.fn();
    render(<DesktopNewSlotDialog onDismiss={vi.fn()} onCreate={onCreate} />);

    const input = screen.getByPlaceholderText("Slot Name (e.g. speedrun, casual)");
    fireEvent.change(input, { target: { value: "casual" } });
    fireEvent.keyDown(input, { key: "Enter" });

    expect(onCreate).toHaveBeenCalledWith("casual");
  });

  it("calls onDismiss when Cancel is clicked or Escape is pressed", () => {
    const onDismiss = vi.fn();
    render(<DesktopNewSlotDialog onDismiss={onDismiss} onCreate={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);

    const input = screen.getByPlaceholderText("Slot Name (e.g. speedrun, casual)");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onDismiss).toHaveBeenCalledTimes(2);
  });

  it("renders error message when error prop is provided", () => {
    render(<DesktopNewSlotDialog onDismiss={vi.fn()} onCreate={vi.fn()} error="Slot already exists" />);

    expect(screen.getByText("Slot already exists")).toBeInTheDocument();
  });

  it("renders inline error resolved by async onCreate while showing Creating… in flight", async () => {
    let finish!: (err: string | null) => void;
    const onCreate = vi.fn(
      () =>
        new Promise<string | null>((resolve) => {
          finish = resolve;
        }),
    );
    render(<DesktopNewSlotDialog onDismiss={vi.fn()} onCreate={onCreate} />);

    const input = screen.getByPlaceholderText("Slot Name (e.g. speedrun, casual)");
    fireEvent.change(input, { target: { value: "broken" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Slot" }));

    expect(screen.getByRole("button", { name: "Creating…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeDisabled();

    finish("Can't create a slot — RomM server is not reachable");

    expect(await screen.findByText("Can't create a slot — RomM server is not reachable")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create Slot" })).not.toBeDisabled();
  });
});
