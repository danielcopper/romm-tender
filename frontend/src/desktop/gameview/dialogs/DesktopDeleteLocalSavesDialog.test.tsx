import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopDeleteLocalSavesDialog } from "./DesktopDeleteLocalSavesDialog";
import { assertDialogEscapeScopedToOwnWindow } from "../../../test-utils/popupEscapeTestHelper";
import { DELETE_LOCAL_SAVES_DESCRIPTION } from "../../../utils/saveSlotWording";

describe("DesktopDeleteLocalSavesDialog", () => {
  it("renders its title, the description, the irreversible note, and Cancel before Delete", () => {
    render(<DesktopDeleteLocalSavesDialog onDismiss={vi.fn()} onConfirm={vi.fn()} />);

    expect(screen.getByRole("dialog", { name: "Delete Local Saves" })).toBeInTheDocument();
    expect(screen.getByText(DELETE_LOCAL_SAVES_DESCRIPTION)).toBeInTheDocument();
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();

    const footer = screen.getByRole("button", { name: "Delete" }).parentElement!;
    expect(Array.from(footer.querySelectorAll("button"), (b) => b.textContent)).toEqual(["Cancel", "Delete"]);
    expect(screen.getByRole("button", { name: "Delete" }).style.backgroundImage).toContain("#c83c3c");
  });

  it("calls onConfirm when Delete is clicked", () => {
    const onConfirm = vi.fn();
    render(<DesktopDeleteLocalSavesDialog onDismiss={vi.fn()} onConfirm={onConfirm} />);

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("calls onDismiss when Cancel is clicked or Escape is pressed", () => {
    const onDismiss = vi.fn();
    render(<DesktopDeleteLocalSavesDialog onDismiss={onDismiss} onConfirm={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onDismiss).toHaveBeenCalledTimes(2);
  });

  it("listens for Escape on the dialog's own Window rather than globalThis.window", () => {
    assertDialogEscapeScopedToOwnWindow((onDismiss) => (
      <DesktopDeleteLocalSavesDialog onDismiss={onDismiss} onConfirm={vi.fn()} />
    ));
  });
});
