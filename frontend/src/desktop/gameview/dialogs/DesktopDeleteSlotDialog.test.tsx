import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopDeleteSlotDialog } from "./DesktopDeleteSlotDialog";
import type { SlotDeleteInfo } from "../../../types";

describe("DesktopDeleteSlotDialog", () => {
  const deleteInfo: SlotDeleteInfo = {
    success: true,
    slot: "speedrun",
    source: "server",
    local_file_count: 1,
    local_filenames: ["speedrun.srm"],
    server_save_count: 2,
    server_save_ids: [1, 2],
    is_active: false,
  };

  it("renders title, warning lines, irreversible note, tone styles, and action buttons", () => {
    render(<DesktopDeleteSlotDialog deleteInfo={deleteInfo} onDismiss={vi.fn()} onConfirm={vi.fn()} />);

    expect(screen.getByRole("dialog", { name: "Delete Slot" })).toBeInTheDocument();
    expect(
      screen.getByText("This will permanently delete 2 saves from slot 'speedrun' on the RomM server."),
    ).toBeInTheDocument();
    expect(screen.getByText("1 tracked file will be unlinked.")).toBeInTheDocument();
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();

    const cancelBtn = screen.getByRole("button", { name: "Cancel" });
    expect(cancelBtn.style.backgroundColor).toBe("transparent");
    const deleteBtn = screen.getByRole("button", { name: "Delete" });
    expect(deleteBtn.style.backgroundImage).toContain("#c83c3c");
  });

  it("calls onConfirm when Delete button is clicked", () => {
    const onConfirm = vi.fn();
    render(<DesktopDeleteSlotDialog deleteInfo={deleteInfo} onDismiss={vi.fn()} onConfirm={onConfirm} />);

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("calls onDismiss when Cancel button is clicked or Escape is pressed", () => {
    const onDismiss = vi.fn();
    render(<DesktopDeleteSlotDialog deleteInfo={deleteInfo} onDismiss={onDismiss} onConfirm={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onDismiss).toHaveBeenCalledTimes(1);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onDismiss).toHaveBeenCalledTimes(2);
  });
});
