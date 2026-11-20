import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { DesktopNewSlotDialog } from "./DesktopNewSlotDialog";
import { assertDialogEscapeScopedToOwnWindow } from "./popupEscapeTestHelper";

describe("DesktopNewSlotDialog", () => {
  it("renders title, description, input, tone styles, and action buttons", () => {
    render(<DesktopNewSlotDialog onDismiss={vi.fn()} onCreate={vi.fn().mockResolvedValue(null)} />);

    expect(screen.getByRole("dialog", { name: "New Save Slot" })).toBeInTheDocument();
    expect(
      screen.getByText("Enter a name for the new save slot. It will become the active slot immediately."),
    ).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Slot Name (e.g. speedrun, casual)")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();

    const cancelBtn = screen.getByRole("button", { name: "Cancel" });
    expect(cancelBtn).toBeInTheDocument();
    expect(cancelBtn.style.backgroundColor).toBe("transparent");
    expect(cancelBtn.style.opacity).toBe("");

    const createBtn = screen.getByRole("button", { name: "Create Slot" });
    expect(createBtn).toBeDisabled();
    expect(createBtn.style.backgroundImage).toContain("#1a9fff");
    expect(createBtn.style.opacity).toBe("0.5");
  });

  it("calls onCreate with trimmed input when Create Slot is clicked", async () => {
    const onCreate = vi.fn().mockResolvedValue(null);
    render(<DesktopNewSlotDialog onDismiss={vi.fn()} onCreate={onCreate} />);

    const input = screen.getByPlaceholderText("Slot Name (e.g. speedrun, casual)");
    fireEvent.change(input, { target: { value: "  speedrun  " } });

    const createBtn = screen.getByRole("button", { name: "Create Slot" });
    expect(createBtn).not.toBeDisabled();
    expect(createBtn.style.opacity).toBe("");
    fireEvent.click(createBtn);

    await waitFor(() => expect(onCreate).toHaveBeenCalledWith("speedrun"));
  });

  it("submits on Enter key press when non-empty and ignores other keys or empty Enter", async () => {
    const onCreate = vi.fn().mockResolvedValue(null);
    render(<DesktopNewSlotDialog onDismiss={vi.fn()} onCreate={onCreate} />);

    const input = screen.getByPlaceholderText("Slot Name (e.g. speedrun, casual)");
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onCreate).not.toHaveBeenCalled();

    fireEvent.change(input, { target: { value: "casual" } });
    fireEvent.keyDown(input, { key: "a" });
    expect(onCreate).not.toHaveBeenCalled();

    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(onCreate).toHaveBeenCalledWith("casual"));
  });

  it("calls onDismiss when Cancel is clicked or Escape is pressed", () => {
    const onDismiss = vi.fn();
    render(<DesktopNewSlotDialog onDismiss={onDismiss} onCreate={vi.fn().mockResolvedValue(null)} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);

    const input = screen.getByPlaceholderText("Slot Name (e.g. speedrun, casual)");
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onDismiss).toHaveBeenCalledTimes(2);
  });

  it("renders inline error returned by onCreate", async () => {
    render(<DesktopNewSlotDialog onDismiss={vi.fn()} onCreate={vi.fn().mockResolvedValue("Slot already exists")} />);

    fireEvent.change(screen.getByPlaceholderText("Slot Name (e.g. speedrun, casual)"), { target: { value: "dup" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Slot" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Slot already exists");
  });

  it("listens for Escape on the dialog's own Window rather than globalThis.window", () => {
    assertDialogEscapeScopedToOwnWindow((onDismiss) => (
      <DesktopNewSlotDialog onDismiss={onDismiss} onCreate={vi.fn().mockResolvedValue(null)} />
    ));
  });

  it("clears prior inline error on retry, ignores duplicate Enter while in flight, and blocks Escape while creating", async () => {
    let finish!: (err: string | null) => void;
    const onDismiss = vi.fn();
    const onCreate = vi.fn(
      () =>
        new Promise<string | null>((resolve) => {
          finish = resolve;
        }),
    );
    render(<DesktopNewSlotDialog onDismiss={onDismiss} onCreate={onCreate} />);

    const input = screen.getByPlaceholderText("Slot Name (e.g. speedrun, casual)");
    fireEvent.change(input, { target: { value: "broken" } });
    fireEvent.click(screen.getByRole("button", { name: "Create Slot" }));

    const creatingBtn = screen.getByRole("button", { name: "Creating…" });
    expect(creatingBtn).toBeDisabled();
    expect(creatingBtn.style.opacity).toBe("0.5");

    const cancelBtn = screen.getByRole("button", { name: "Cancel" });
    expect(cancelBtn).toBeDisabled();
    expect(cancelBtn.style.opacity).toBe("0.5");

    // Duplicate Enter or Escape must do nothing while the request is in flight.
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onCreate).toHaveBeenCalledTimes(1);
    expect(onDismiss).not.toHaveBeenCalled();

    finish("Can't create a slot — RomM server is not reachable");

    expect(await screen.findByRole("alert")).toHaveTextContent("Can't create a slot — RomM server is not reachable");
    expect(screen.getByRole("button", { name: "Create Slot" })).not.toBeDisabled();

    // Resubmitting clears the previous inline error immediately while the second attempt runs.
    fireEvent.click(screen.getByRole("button", { name: "Create Slot" }));
    expect(screen.queryByRole("alert")).toBeNull();
    finish(null);
    await waitFor(() => expect(screen.getByRole("button", { name: "Create Slot" })).not.toBeDisabled());
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
