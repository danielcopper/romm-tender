import "@testing-library/jest-dom/vitest";
import type { ComponentProps } from "react";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DesktopUnsyncedSavesDialog } from "./DesktopUnsyncedSavesDialog";

function renderDialog(overrides: Partial<ComponentProps<typeof DesktopUnsyncedSavesDialog>> = {}) {
  const onChoice = vi.fn();
  render(
    <DesktopUnsyncedSavesDialog
      versionName="Super Mario (USA)"
      stranding="unsynced"
      serverReachable={true}
      versionGone={false}
      onChoice={onChoice}
      {...overrides}
    />,
  );
  return onChoice;
}

describe("DesktopUnsyncedSavesDialog — sync offered", () => {
  it("renders the copy and all three buttons", () => {
    renderDialog();

    const dialog = screen.getByRole("dialog", { name: "Unsynced saves" });
    expect(dialog).toHaveTextContent(
      "\"Super Mario (USA)\" has saves that haven't been synced to RomM. They stay on disk, but won't sync until you switch back.",
    );
    expect(screen.getByRole("button", { name: "Sync now & switch" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Switch anyway" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("says Tender couldn't check when the stranding is unchecked", () => {
    renderDialog({ stranding: "unchecked" });

    expect(screen.getByRole("dialog", { name: "Unsynced saves" })).toHaveTextContent(
      "Tender couldn't check whether \"Super Mario (USA)\" has saves that haven't been synced to RomM. They stay on disk, but won't sync until you switch back.",
    );
  });

  it.each([
    { name: "Sync now & switch", choice: "sync_and_switch" },
    { name: "Switch anyway", choice: "switch_anyway" },
    { name: "Cancel", choice: "cancel" },
  ] as const)("calls onChoice with '$choice' on $name click", ({ name, choice }) => {
    const onChoice = renderDialog();

    fireEvent.click(screen.getByRole("button", { name }));
    expect(onChoice).toHaveBeenCalledWith(choice);
  });
});

describe("DesktopUnsyncedSavesDialog — RomM unreachable", () => {
  it("renders the unreachable copy and only Switch anyway and Cancel", () => {
    renderDialog({ serverReachable: false });

    expect(screen.getByRole("dialog", { name: "Unsynced saves" })).toHaveTextContent(
      "\"Super Mario (USA)\" has saves that haven't been synced to RomM. RomM is not reachable right now, so they can't be synced first. They stay on disk, but won't sync until you switch back.",
    );
    expect(screen.queryByRole("button", { name: "Sync now & switch" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Switch anyway" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });

  it("calls onChoice with 'switch_anyway' on Switch anyway click", () => {
    const onChoice = renderDialog({ serverReachable: false });

    fireEvent.click(screen.getByRole("button", { name: "Switch anyway" }));
    expect(onChoice).toHaveBeenCalledWith("switch_anyway");
  });

  it("calls onChoice with 'cancel' on Escape key", () => {
    const onChoice = renderDialog({ serverReachable: false });

    fireEvent.keyDown(window, { key: "Escape" });
    expect(onChoice).toHaveBeenCalledWith("cancel");
  });
});

describe("DesktopUnsyncedSavesDialog — version gone from RomM", () => {
  it("says the saves can't be uploaded and offers no sync, even with RomM reachable", () => {
    renderDialog({ versionGone: true });

    expect(screen.getByRole("dialog", { name: "Unsynced saves" })).toHaveTextContent(
      '"Super Mario (USA)" has saves that haven\'t been synced to RomM. "Super Mario (USA)" is no longer available on RomM, so its saves can\'t be uploaded. They stay on disk.',
    );
    expect(screen.queryByRole("button", { name: "Sync now & switch" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Switch anyway" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cancel" })).toBeInTheDocument();
  });
});
