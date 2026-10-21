import "@testing-library/jest-dom/vitest";
import { beforeEach, describe, expect, it, vi } from "vitest";
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
    acceptTyped: vi.fn(),
    switchToRecorded: vi.fn(),
    forget: vi.fn(),
    ...overrides,
  };
}

/** The controller's A press on *el*, the way Steam delivers it to the stop that holds it. */
function press(el: Element): void {
  fireEvent(el, new CustomEvent("decky-button-down", { detail: { button: 1 }, bubbles: true }));
}

describe("TenderFolderSection", () => {
  beforeEach(() => {
    vi.mocked(showModal).mockClear();
  });

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
      <TenderFolderSection
        folder={view({
          default: false,
          place: SD.mount_point,
          folder: SD.folder,
          drive: { kind: "sd_card", name: "Emulation", mount_point: SD.mount_point, connected: true },
        })}
      />,
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
      (tile) => tile.parentElement?.parentElement,
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
    press(internal);

    expect(folder.chooseDefault).not.toHaveBeenCalled();
  });

  it("each other tile chooses its place", () => {
    const folder = view({ default: false, place: SD.mount_point, folder: SD.folder });
    const { getByTestId } = render(<TenderFolderSection folder={folder} />);

    press(getByTestId("tender-folder-tile-internal"));
    expect(folder.chooseDefault).toHaveBeenCalledTimes(1);

    const other = view({ drives: [SD, USB] });
    const second = render(<TenderFolderSection folder={other} />);
    press(second.getByTestId(`tender-folder-tile-${USB.mount_point}`));
    expect(other.chooseDrive).toHaveBeenCalledWith(USB.mount_point);
  });

  it("says where to keep the folder when no drive is connected", () => {
    const { getByText } = render(<TenderFolderSection folder={view({ drives: [] })} />);

    expect(getByText("Insert an SD card or connect a drive to keep Tender's folder there.")).toBeInTheDocument();
  });

  it("another folder opens its window with the drives root for the example, and hands it the place accepted", () => {
    const folder = view();
    const { getByText } = render(<TenderFolderSection folder={folder} />);

    expect(getByText("Type a path, for example a folder on one of the drives above")).toBeInTheDocument();
    press(getByText("Another folder…"));

    const calls = vi.mocked(showModal).mock.calls;
    const modal = calls[calls.length - 1]?.[0] as ReactElement<{ drivesRoot: string; onAccepted: unknown }>;
    expect(modal.props.drivesRoot).toBe("/run/media/deck");
    expect(modal.props.onAccepted).toBe(folder.acceptTyped);
  });

  it("keeps the tile of a drive in use that is not connected: greyed, outlined, Not connected, and dead", () => {
    const sd = "/run/media/deck/Emulation";
    const folder = view({
      default: false,
      place: sd,
      folder: `${sd}/Tender`,
      condition: "drive_not_mounted",
      drives: [],
      drive: { kind: "sd_card", name: "Emulation", mount_point: sd, connected: false },
    });
    const { getByTestId, getByText } = render(<TenderFolderSection folder={folder} />);

    expect(getByText("In use: SD card · Emulation")).toBeInTheDocument();
    expect(getByTestId("tender-folder-pill")).toHaveTextContent("Not connected");
    const tile = getByTestId(`tender-folder-tile-${sd}`);
    expect(tile).toHaveAttribute("data-in-use", "true");
    expect(tile).toHaveAttribute("data-not-connected", "true");
    expect(tile).toHaveTextContent("SD card");
    expect(tile).toHaveTextContent("Emulation");
    expect(tile).toHaveTextContent("Not connected");
    expect(tile).not.toHaveTextContent("free");
    expect((tile.parentElement as HTMLElement).style.opacity).toBe("0.6");
    press(tile);
    expect(folder.chooseDrive).not.toHaveBeenCalled();
  });

  it("nothing can be pressed while a change is in flight", () => {
    const folder = view({ default: false, place: SD.mount_point, folder: SD.folder }, { busy: true });
    const { getByTestId, getByText } = render(<TenderFolderSection folder={folder} />);

    press(getByTestId("tender-folder-tile-internal"));
    press(getByText("Another folder…"));

    expect(folder.chooseDefault).not.toHaveBeenCalled();
    expect(vi.mocked(showModal)).not.toHaveBeenCalled();
  });

  it("a focused tile keeps its dark fill and shows the accent outline, and the in-use tile keeps its border", () => {
    const { getByTestId } = render(<TenderFolderSection folder={view()} />);
    const stop = getByTestId("tender-folder-tile-internal").parentElement as HTMLElement;

    expect(stop.style.borderColor).toBe("#1a9fff");
    fireEvent.focus(stop);

    expect(stop.style.background).toBe("#323a46");
    expect(stop.style.outlineColor).toBe("#1a9fff");
    expect(stop.style.outlineWidth).toBe("2px");
    expect(stop.style.borderColor).toBe("#1a9fff");
  });

  it("the badge sits in the tile's corner on one line, and the kind line never wraps", () => {
    const { getByTestId } = render(<TenderFolderSection folder={view()} />);
    const tile = getByTestId("tender-folder-tile-internal");
    const badge = [...tile.querySelectorAll("span")].find((span) => span.textContent === "In use") as HTMLElement;

    expect(badge.style.position).toBe("absolute");
    expect(badge.style.whiteSpace).toBe("nowrap");
    expect((tile.firstElementChild as HTMLElement).style.whiteSpace).toBe("nowrap");
  });

  it("a path may break only after a slash", () => {
    const { getByTestId } = render(<TenderFolderSection folder={view()} />);
    const path = [...getByTestId(`tender-folder-tile-${SD.mount_point}`).querySelectorAll("span")].find(
      (span) => span.textContent === SD.folder,
    ) as HTMLElement;

    expect(path.querySelectorAll("wbr")).toHaveLength(5);
    for (const wbr of path.querySelectorAll("wbr")) expect(wbr.previousSibling?.textContent).toBe("/");
  });

  it("the status line is no stop of its own; the tiles below it are", () => {
    const { getByText, getByTestId } = render(<TenderFolderSection folder={view()} />);

    expect(getByText("In use: Internal storage").closest("[data-activate]")).toBeNull();
    expect(getByTestId("tender-folder-tile-internal").closest("[data-activate]")).not.toBeNull();
  });

  it("another folder is one row holding its line and a chevron", () => {
    const { getByText } = render(<TenderFolderSection folder={view()} />);
    const row = getByText("Another folder…").closest("[data-activate]") as HTMLElement;

    expect(row).toHaveTextContent("Type a path, for example a folder on one of the drives above");
    expect(row).toHaveTextContent("›");
  });

  it("shows why a choice was refused", () => {
    const { getByText } = render(
      <TenderFolderSection folder={view({}, { refusal: "/home/deck/Tender is already Tender's folder." })} />,
    );

    expect(getByText("/home/deck/Tender is already Tender's folder.")).toBeInTheDocument();
  });
});
