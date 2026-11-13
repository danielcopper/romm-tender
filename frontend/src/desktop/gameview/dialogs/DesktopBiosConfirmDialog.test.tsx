import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { deleteBiosFileConfirmCopy, replaceBiosConfirmCopy } from "../../../utils/useBiosActions";
import { DesktopBiosConfirmDialog } from "./DesktopBiosConfirmDialog";

describe("DesktopBiosConfirmDialog", () => {
  it("draws the title, description, and confirm action for a replace prompt", () => {
    const copy = replaceBiosConfirmCopy("scph5501.bin", "/bios/scph5501.bin", "/backups");
    render(<DesktopBiosConfirmDialog copy={copy} tone="primary" onChoice={vi.fn()} />);

    const dialog = screen.getByRole("dialog", { name: "Replace scph5501.bin?" });
    expect(dialog).toHaveTextContent(copy.description);
    expect(screen.getByRole("button", { name: "Replace" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("answers true when the confirm button is clicked", () => {
    const onChoice = vi.fn();
    const copy = deleteBiosFileConfirmCopy("scph5501.bin");
    render(<DesktopBiosConfirmDialog copy={copy} tone="danger" onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    expect(onChoice).toHaveBeenCalledWith(true);
  });

  it("answers false on Cancel", () => {
    const onChoice = vi.fn();
    const copy = deleteBiosFileConfirmCopy("scph5501.bin");
    render(<DesktopBiosConfirmDialog copy={copy} tone="danger" onChoice={onChoice} />);

    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(onChoice).toHaveBeenCalledWith(false);
  });

  it("answers false on Escape", () => {
    const onChoice = vi.fn();
    const copy = deleteBiosFileConfirmCopy("scph5501.bin");
    render(<DesktopBiosConfirmDialog copy={copy} tone="danger" onChoice={onChoice} />);

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onChoice).toHaveBeenCalledWith(false);
  });
});
