import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { showModal } from "@decky/ui";
import type { ReactElement } from "react";
import type { TenderFolderView } from "../useTenderFolder";
import { TENDER_FOLDER_AT_HOME } from "../../test-utils/tenderFolder";
import { TenderFolderSection } from "./TenderFolderSection";

const SD = {
  name: "SD",
  mount_point: "/run/media/deck/SD",
  folder: "/run/media/deck/SD/Tender",
  free_bytes: 3 * 1024 ** 3,
};

function view(overrides: Partial<TenderFolderView> = {}): TenderFolderView {
  return {
    state: { ...TENDER_FOLDER_AT_HOME, drives: [SD] },
    busy: false,
    refusal: null,
    chooseDefault: vi.fn(),
    chooseDrive: vi.fn(),
    chooseTyped: vi.fn(),
    switchToRecorded: vi.fn(),
    forget: vi.fn(),
    ...overrides,
  };
}

describe("TenderFolderSection", () => {
  it("offers the internal default, each drive with its free space, and another folder", () => {
    const { getAllByRole, getByText } = render(<TenderFolderSection folder={view()} />);

    expect(getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Internal storage — ~/Tender (default)",
      "SD — /run/media/deck/SD/Tender",
      "Another folder…",
    ]);
    expect(getByText("3.00 GB free")).toBeInTheDocument();
  });

  it("states the current place, and why it cannot be used where it cannot", () => {
    const ok = render(<TenderFolderSection folder={view()} />);
    expect(ok.getByText("Current place")).toBeInTheDocument();
    expect(ok.getByText("/home/deck/Tender")).toBeInTheDocument();
    ok.unmount();

    const gone = render(
      <TenderFolderSection
        folder={view({
          state: { ...TENDER_FOLDER_AT_HOME, folder: "/run/media/deck/SD/Tender", condition: "drive_not_mounted" },
        })}
      />,
    );
    expect(gone.getByText("/run/media/deck/SD/Tender: the drive is not connected.")).toBeInTheDocument();
  });

  it("each row chooses its place", () => {
    const folder = view();
    const { getByText } = render(<TenderFolderSection folder={folder} />);

    fireEvent.click(getByText("Internal storage — ~/Tender (default)"));
    fireEvent.click(getByText("SD — /run/media/deck/SD/Tender"));

    expect(folder.chooseDefault).toHaveBeenCalledTimes(1);
    expect(folder.chooseDrive).toHaveBeenCalledWith("/run/media/deck/SD");
  });

  it("another folder is typed into a text field and then chosen", () => {
    const folder = view();
    const { getByText } = render(<TenderFolderSection folder={folder} />);

    fireEvent.click(getByText("Another folder…"));

    const calls = vi.mocked(showModal).mock.calls;
    const modal = calls[calls.length - 1]?.[0] as ReactElement<{ label: string; onSubmit: (v: string) => void }>;
    expect(modal.props.label).toBe("Another folder…");
    modal.props.onSubmit("~/Games");
    expect(folder.chooseTyped).toHaveBeenCalledWith("~/Games");
  });

  it("no row can be pressed while a change is in flight", () => {
    const { getAllByRole } = render(<TenderFolderSection folder={view({ busy: true })} />);

    expect(getAllByRole("button").every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
  });

  it("shows why a choice was refused", () => {
    const { getByText } = render(<TenderFolderSection folder={view({ refusal: "The drive SD is not connected." })} />);

    expect(getByText("The drive SD is not connected.")).toBeInTheDocument();
  });
});
