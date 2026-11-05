import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { DesktopAdoptExistingDialog } from "./DesktopAdoptExistingDialog";
import { emitHostEvent, hostEventListenerCount } from "../../../test-utils/host-event-bus";
import { debugLog, verifyExistingContent } from "../../../api/backend";
import type { TargetOccupiedResult, VerifyContentResult, VerifyProgressEvent } from "../../../types";

vi.mock("../../../api/backend", () => ({
  verifyExistingContent: vi.fn(),
  debugLog: vi.fn(async () => {}),
}));

const ROM_ID = 42;

function occupied(overrides: Partial<TargetOccupiedResult> = {}): TargetOccupiedResult {
  return {
    success: false,
    reason: "target_occupied",
    message: "'Game.sfc' is already on this device",
    existing: { name: "Game.sfc", path: "/roms/snes/Game.sfc", kind: "file", size_bytes: 2048, modified_at: 0 },
    incoming: { name: "Game.sfc", size_bytes: 1024 },
    sizes_match: false,
    adoptable: true,
    ...overrides,
  };
}

function renderDialog(props: { occupied?: TargetOccupiedResult; candidatePath?: string } = {}) {
  const onChoice = vi.fn();
  const rendered = render(
    <DesktopAdoptExistingDialog
      romId={ROM_ID}
      occupied={props.occupied ?? occupied()}
      candidatePath={props.candidatePath}
      onChoice={onChoice}
    />,
  );
  return { ...rendered, onChoice };
}

beforeEach(() => {
  vi.mocked(verifyExistingContent).mockReset();
});

describe("DesktopAdoptExistingDialog", () => {
  it("states both sides and how they relate, in the shared words", () => {
    renderDialog();
    const dialog = screen.getByRole("dialog", { name: "This Game Is Already on Your Device" });
    expect(dialog).toHaveTextContent("A file is already where this game would be downloaded.");
    expect(dialog).toHaveTextContent("On this device");
    expect(dialog).toHaveTextContent("On the server");
    expect(dialog).toHaveTextContent("What is here is 1.0 KB larger than what the server would send.");
    expect(dialog).not.toHaveTextContent("renames it");
  });

  it("says when what is here last changed, as a muted line of its side", () => {
    renderDialog({ occupied: occupied({ existing: { ...occupied().existing, modified_at: 1_700_000_000 } }) });
    expect(screen.getByText(/^Last changed /)).toHaveStyle({ color: "#8f98a0" });
  });

  it("says a candidate is renamed into place", () => {
    renderDialog({ candidatePath: "/roms/snes/Other.sfc" });
    expect(screen.getByRole("dialog")).toHaveTextContent("Using it renames it to Game.sfc");
    expect(screen.getByRole("dialog")).toHaveTextContent("This file carries this game's name.");
  });

  it("resolves adopt, and cancel from Cancel, Escape and the backdrop", () => {
    const { onChoice } = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Use These Files" }));
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.keyDown(window, { key: "Escape" });
    fireEvent.click(screen.getByRole("button", { name: "Close dialog" }));
    expect(onChoice.mock.calls).toEqual([["adopt"], ["cancel"], ["cancel"], ["cancel"]]);
  });

  it("disables the adopt exit for content it cannot use, and says why", () => {
    const { onChoice } = renderDialog({
      occupied: occupied({ existing: { ...occupied().existing, kind: "link" }, sizes_match: null, adoptable: false }),
    });
    const adopt = screen.getByRole("button", { name: "Can't use this shortcut to somewhere else for this game" });
    expect(adopt).toBeDisabled();
    fireEvent.click(adopt);
    expect(onChoice).not.toHaveBeenCalled();
  });

  it("asks a second time before the destructive exit, and Go Back returns to the choice", () => {
    const { onChoice } = renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Download Instead" }));

    expect(screen.getByText(/Downloading deletes the file that is here now — Game\.sfc, 2\.0 KB/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Use These Files" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Go Back" }));
    expect(screen.getByRole("button", { name: "Use These Files" })).toBeInTheDocument();
    expect(onChoice).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Download Instead" }));
    const deleteAndDownload = screen.getByRole("button", { name: "Delete and Download" });
    expect(deleteAndDownload.style.backgroundImage).toBe("linear-gradient(90deg, #c83c3c 0%, #a02828 100%)");
    fireEvent.click(deleteAndDownload);
    expect(onChoice).toHaveBeenCalledWith("replace");
  });

  it("checks against the server on demand, with its progress and then its verdict", async () => {
    let answer!: (result: VerifyContentResult) => void;
    vi.mocked(verifyExistingContent).mockImplementation(
      () =>
        new Promise((resolve) => {
          answer = resolve;
        }),
    );
    renderDialog({ candidatePath: "/roms/snes/Other.sfc" });

    fireEvent.click(screen.getByRole("button", { name: "Check Against Server" }));
    expect(verifyExistingContent).toHaveBeenCalledWith(ROM_ID, "/roms/snes/Other.sfc");
    expect(screen.getByText("Checking the files…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check Against Server" })).toBeDisabled();

    act(() => {
      emitHostEvent<VerifyProgressEvent>("verify_progress", { rom_id: ROM_ID, bytes_done: 1, bytes_total: 4 });
      emitHostEvent<VerifyProgressEvent>("verify_progress", { rom_id: 99, bytes_done: 9, bytes_total: 10 });
      emitHostEvent<VerifyProgressEvent>("verify_progress", { rom_id: ROM_ID, bytes_done: 3, bytes_total: 0 });
    });
    expect(screen.getByText("Checking the files… 25%")).toBeInTheDocument();

    await act(async () => {
      answer({ status: "mismatch", message: "These files differ", differences: [{ name: "a.bin", detail: "size" }] });
    });
    const verdict = screen.getByText("These files differ");
    expect(verdict.style.color).toBe("#ff6b6b");
    expect(screen.getByText("a.bin: size")).toBeInTheDocument();
    expect(screen.queryByText(/Checking the files/)).not.toBeInTheDocument();
  });

  it("colours a match as one, and reports an unreachable server when the check throws", async () => {
    vi.mocked(verifyExistingContent).mockResolvedValueOnce({ status: "match", message: "Identical", differences: [] });
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: "Check Against Server" }));
    expect((await screen.findByText("Identical")).style.color).toBe("#5ba32b");

    vi.mocked(verifyExistingContent).mockRejectedValueOnce(new Error("socket closed"));
    fireEvent.click(screen.getByRole("button", { name: "Check Against Server" }));
    await waitFor(() => expect(screen.getByText("Couldn't reach the server to check these files")).toBeInTheDocument());
    expect(debugLog).toHaveBeenCalledWith("DesktopAdoptExistingDialog: verify failed: Error: socket closed");
  });

  it("stops listening for verify progress once it closes", () => {
    const { unmount } = renderDialog();
    expect(hostEventListenerCount("verify_progress")).toBe(1);
    unmount();
    expect(hostEventListenerCount("verify_progress")).toBe(0);
  });
});
