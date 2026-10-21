import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { showModal } from "@decky/ui";
import type { ReactElement } from "react";
import type { TenderFolderDrive, TenderFolderState } from "../../types";
import type { TenderFolderView } from "../useTenderFolder";
import { TENDER_FOLDER_AT_HOME } from "../../test-utils/tenderFolder";
import { TenderFolderSection } from "./TenderFolderSection";

const SD: TenderFolderDrive = {
  name: "Emulation",
  mount_point: "/run/media/deck/Emulation",
  kind: "sd_card",
  folder: "/run/media/deck/Emulation/Tender",
  free_bytes: 460 * 1024 ** 3,
};
const USB: TenderFolderDrive = {
  name: "Archive",
  mount_point: "/run/media/deck/Archive",
  kind: "usb_drive",
  folder: "/run/media/deck/Archive/Tender",
  free_bytes: 2 * 1024 ** 3,
};
const NVME: TenderFolderDrive = {
  ...USB,
  name: "Fast",
  mount_point: "/run/media/deck/Fast",
  kind: "drive",
  folder: "/run/media/deck/Fast/Tender",
};

function view(state: Partial<TenderFolderState> = {}, overrides: Partial<TenderFolderView> = {}): TenderFolderView {
  return {
    state: { ...TENDER_FOLDER_AT_HOME, drives: [SD], ...state },
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

function lastModal(): ReactElement<{
  label: string;
  fieldLabel?: string;
  description?: string;
  value: string;
  onSubmit: (v: string) => void;
}> {
  const calls = vi.mocked(showModal).mock.calls;
  return calls[calls.length - 1]?.[0] as never;
}

describe("TenderFolderSection", () => {
  it("states the place in use, that it is reachable, and its folder", () => {
    const { getByText, getByTestId } = render(<TenderFolderSection folder={view()} />);

    expect(getByText("In use: Internal storage")).toBeInTheDocument();
    expect(getByTestId("tender-folder-pill")).toHaveTextContent("Reachable");
    expect(getByText("/home/deck/Tender")).toBeInTheDocument();
  });

  it.each([
    ["drive_not_mounted", "Not connected"],
    ["folder_missing", "Missing"],
    ["folder_not_writable", "Not writable"],
  ] as const)("names why the folder in use cannot be used: %s", (condition, pill) => {
    const { getByTestId } = render(<TenderFolderSection folder={view({ condition })} />);

    expect(getByTestId("tender-folder-pill")).toHaveTextContent(pill);
  });

  it("names a drive in use by its kind and name, and another place as another folder", () => {
    const onSd = render(
      <TenderFolderSection folder={view({ default: false, place: SD.mount_point, folder: SD.folder })} />,
    );
    expect(onSd.getByText("In use: SD card · Emulation")).toBeInTheDocument();
    onSd.unmount();

    const elsewhere = render(
      <TenderFolderSection
        folder={view({ default: false, place: "/home/deck/Games", folder: "/home/deck/Games/Tender" })}
      />,
    );
    expect(elsewhere.getByText("In use: Another folder")).toBeInTheDocument();
  });

  it("offers one tile per place, two to a row: the internal storage marked Default, then each drive by kind", () => {
    const { getByTestId, container } = render(<TenderFolderSection folder={view({ drives: [SD, USB, NVME] })} />);

    const internal = getByTestId("tender-folder-tile-internal");
    expect(internal).toHaveTextContent("Internal storage");
    expect(internal).toHaveTextContent("Default");
    expect(internal).toHaveTextContent("~/Tender");
    expect(internal).toHaveTextContent("41.00 GB free");
    expect(getByTestId(`tender-folder-tile-${SD.mount_point}`)).toHaveTextContent("SD card");
    expect(getByTestId(`tender-folder-tile-${SD.mount_point}`)).toHaveTextContent("Emulation");
    expect(getByTestId(`tender-folder-tile-${SD.mount_point}`)).toHaveTextContent("460.00 GB free");
    expect(getByTestId(`tender-folder-tile-${USB.mount_point}`)).toHaveTextContent("USB drive");
    expect(getByTestId(`tender-folder-tile-${NVME.mount_point}`)).toHaveTextContent("Drive");
    const rows = [...container.querySelectorAll("[data-testid^='tender-folder-tile-']")].map(
      (tile) => tile.closest("button")?.parentElement,
    );
    expect(rows[0]).toBe(rows[1]);
    expect(rows[2]).toBe(rows[3]);
    expect(rows[1]).not.toBe(rows[2]);
  });

  it("outlines the place in use, and pressing it chooses nothing", () => {
    const folder = view();
    const { getByTestId } = render(<TenderFolderSection folder={folder} />);

    const internal = getByTestId("tender-folder-tile-internal");
    expect(internal).toHaveAttribute("data-in-use", "true");
    expect(internal).toHaveTextContent("In use");
    expect(getByTestId(`tender-folder-tile-${SD.mount_point}`)).not.toHaveAttribute("data-in-use");
    fireEvent.click(internal);

    expect(folder.chooseDefault).not.toHaveBeenCalled();
  });

  it("each other tile chooses its place", () => {
    const folder = view({ default: false, place: SD.mount_point, folder: SD.folder });
    const { getByTestId } = render(<TenderFolderSection folder={folder} />);

    fireEvent.click(getByTestId("tender-folder-tile-internal"));
    expect(folder.chooseDefault).toHaveBeenCalledTimes(1);

    const other = view({ drives: [SD, USB] });
    const second = render(<TenderFolderSection folder={other} />);
    fireEvent.click(second.getByTestId(`tender-folder-tile-${USB.mount_point}`));
    expect(other.chooseDrive).toHaveBeenCalledWith(USB.mount_point);
  });

  it("says where to keep the folder when no drive is connected", () => {
    const { getByText } = render(<TenderFolderSection folder={view({ drives: [] })} />);

    expect(getByText("Insert an SD card or connect a drive to keep Tender's folder there.")).toBeInTheDocument();
  });

  it("another folder opens an empty text field with an example under it, and the typed place is chosen", () => {
    const folder = view();
    const { getByText } = render(<TenderFolderSection folder={folder} />);

    expect(getByText("Type a path, for example a folder on a drive below")).toBeInTheDocument();
    fireEvent.click(getByText("Another folder…"));

    const modal = lastModal();
    expect(modal.props.label).toBe("Another folder…");
    expect(modal.props.value).toBe("");
    expect(modal.props.fieldLabel).toBe("Tender puts its folder named Tender inside the place you enter.");
    expect(modal.props.description).toBe("For example ~/Games or /run/media/deck/Archive");
    modal.props.onSubmit("~/Games");
    expect(folder.chooseTyped).toHaveBeenCalledWith("~/Games");
  });

  it("no tile can be pressed while a change is in flight", () => {
    const { getAllByRole } = render(<TenderFolderSection folder={view({}, { busy: true })} />);

    expect(getAllByRole("button").every((button) => (button as HTMLButtonElement).disabled)).toBe(true);
  });

  it("shows why a choice was refused", () => {
    const { getByText } = render(
      <TenderFolderSection folder={view({}, { refusal: "/home/deck/Tender is already Tender's folder." })} />,
    );

    expect(getByText("/home/deck/Tender is already Tender's folder.")).toBeInTheDocument();
  });
});
