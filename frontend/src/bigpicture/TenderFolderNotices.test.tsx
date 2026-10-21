import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import type { TenderFolderView } from "./useTenderFolder";
import { TENDER_FOLDER_AT_HOME } from "../test-utils/tenderFolder";
import { TenderFolderNotices } from "./TenderFolderNotices";

const SD_FOLDER = "/run/media/deck/SD/Tender";

function view(state: Partial<NonNullable<TenderFolderView["state"]>>, refusal: string | null = null): TenderFolderView {
  return {
    state: { ...TENDER_FOLDER_AT_HOME, ...state },
    busy: false,
    refusal,
    chooseDefault: vi.fn(),
    chooseDrive: vi.fn(),
    acceptTyped: vi.fn(),
    switchToRecorded: vi.fn(),
    forget: vi.fn(),
  };
}

describe("TenderFolderNotices", () => {
  it("shows nothing with nothing recorded, whatever state the folder is in", () => {
    const { container } = render(
      <TenderFolderNotices folder={view({ condition: "drive_not_mounted" })} onChooseAnotherPlace={vi.fn()} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("shows nothing before the folder was read", () => {
    const folder = { ...view({}), state: undefined };
    const { container } = render(<TenderFolderNotices folder={folder} onChooseAnotherPlace={vi.fn()} />);

    expect(container).toBeEmptyDOMElement();
  });

  it.each([
    ["drive_not_mounted", "the drive is not connected."],
    ["folder_missing", "the folder is missing."],
    ["folder_not_writable", "the folder cannot be written to."],
  ] as const)("names the place and the reason while it is unreachable: %s", (state, ending) => {
    const { getByTestId } = render(
      <TenderFolderNotices
        folder={view({ folder: SD_FOLDER, condition: state, unreachable: state })}
        onChooseAnotherPlace={vi.fn()}
      />,
    );

    const card = getByTestId("tender-folder-unreachable");
    expect(card).toHaveTextContent("Tender's folder cannot be reached");
    expect(card).toHaveTextContent(`${SD_FOLDER}: ${ending}`);
    expect(card).toHaveTextContent("Nothing has been removed.");
  });

  it("an unreachable folder's ways out are another place and forgetting what is recorded there; no dismiss", () => {
    const folder = view({ folder: SD_FOLDER, condition: "drive_not_mounted", unreachable: "drive_not_mounted" });
    const onChooseAnotherPlace = vi.fn();
    const { getAllByRole, getByText } = render(
      <TenderFolderNotices folder={folder} onChooseAnotherPlace={onChooseAnotherPlace} />,
    );

    expect(getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Choose another place",
      "Forget what is recorded there",
    ]);
    fireEvent.click(getByText("Choose another place"));
    fireEvent.click(getByText("Forget what is recorded there"));

    expect(onChooseAnotherPlace).toHaveBeenCalledTimes(1);
    expect(folder.forget).toHaveBeenCalledWith(SD_FOLDER);
  });

  it("says where files are recorded besides the setting's place, with its two ways out", () => {
    const folder = view({ elsewhere: [SD_FOLDER] });
    const { getByTestId, getAllByRole, getByText } = render(
      <TenderFolderNotices folder={folder} onChooseAnotherPlace={vi.fn()} />,
    );

    const card = getByTestId(`tender-folder-elsewhere-${SD_FOLDER}`);
    expect(card).toHaveTextContent("Tender's files are recorded elsewhere");
    expect(card).toHaveTextContent(
      `The settings name /home/deck/Tender as Tender's folder, but Tender's files are recorded under ${SD_FOLDER}.`,
    );
    expect(card).toHaveTextContent("Nothing has been removed.");
    expect(getAllByRole("button").map((button) => button.textContent)).toEqual([
      "Use that folder",
      "Forget what is recorded there",
    ]);

    fireEvent.click(getByText("Use that folder"));
    fireEvent.click(getByText("Forget what is recorded there"));
    expect(folder.switchToRecorded).toHaveBeenCalledWith(SD_FOLDER);
    expect(folder.forget).toHaveBeenCalledWith(SD_FOLDER);
  });

  it("shows why a way out was refused beside the notice it came from", () => {
    const { getByText } = render(
      <TenderFolderNotices
        folder={view({ elsewhere: [SD_FOLDER] }, "A removed-game cleanup is in progress.")}
        onChooseAnotherPlace={vi.fn()}
      />,
    );

    expect(getByText("A removed-game cleanup is in progress.")).toBeInTheDocument();
  });
});
