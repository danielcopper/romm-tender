import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, waitFor } from "@testing-library/react";
import type { TenderFolderState, TenderPlaceAsks, TenderPlaceRefusal } from "../../types";
import { TENDER_FOLDER_AT_HOME } from "../../test-utils/tenderFolder";
import { AnotherFolderModal } from "./AnotherFolderModal";

const CHOSEN: TenderFolderState = {
  ...TENDER_FOLDER_AT_HOME,
  place: "/home/deck/Games",
  folder: "/home/deck/Games/Tender",
  default: false,
};

function asks(place: string, overrides: Partial<TenderPlaceAsks> = {}): TenderPlaceAsks {
  return { place, folder: `${place}/Tender`, tender_parent: null, existing: place, missing: null, ...overrides };
}

const REFUSED: TenderPlaceRefusal = {
  success: false,
  reason: "place_not_writable",
  message: "~/locked cannot be written to.",
};

function open(
  check: (text: string) => Promise<TenderPlaceAsks | TenderPlaceRefusal>,
  choose: (text: string) => Promise<TenderFolderState | TenderPlaceRefusal> = async () => CHOSEN,
) {
  const closeModal = vi.fn();
  const onAccepted = vi.fn();
  const chooseSpy = vi.fn(choose);
  const view = render(
    <AnotherFolderModal
      closeModal={closeModal}
      drivesRoot="/run/media/deck"
      check={check}
      choose={chooseSpy}
      onAccepted={onAccepted}
    />,
  );
  const field = view.getByTestId("text-field") as HTMLInputElement;
  const type = (text: string) => fireEvent.change(field, { target: { value: text } });
  const ok = () => fireEvent.click(view.getByText("OK"));
  return { ...view, field, type, ok, closeModal, onAccepted, choose: chooseSpy };
}

describe("AnotherFolderModal", () => {
  it("opens with an empty field", () => {
    const { field } = open(async (text) => asks(text));

    expect(field.value).toBe("");
  });

  it("labels the field and gives the example below it", () => {
    const { field } = open(async (text) => asks(text));

    expect(field.dataset["label"]).toBe("Tender puts its folder named Tender inside the place you enter.");
    expect(field.dataset["description"]).toBe("For example ~/Games or /run/media/deck/Archive");
  });

  it("OK is disabled while a question is shown", async () => {
    const window = open(async () => asks("/sd/Tender", { tender_parent: "/sd" }));
    window.type("/sd/Tender");
    expect(window.getByText("OK")).not.toBeDisabled();
    window.ok();
    await window.findByTestId("another-folder-question");

    expect(window.getByText("OK")).toBeDisabled();
  });

  it("an answer that arrives after the text was edited is dropped, and nothing is chosen", async () => {
    let answer: (asks: TenderPlaceAsks) => void = () => undefined;
    const window = open(() => new Promise((resolve) => (answer = resolve)));
    window.type("~/Games");
    window.ok();

    window.type("~/Other");
    answer(asks("/home/deck/Games"));

    await waitFor(() => expect(window.getByText("OK")).not.toBeDisabled());
    expect(window.choose).not.toHaveBeenCalled();
    expect(window.onAccepted).not.toHaveBeenCalled();
    expect(window.closeModal).not.toHaveBeenCalled();
  });

  it("a question that arrives after the text was edited is not shown, so Keep as typed chooses nothing", async () => {
    let answer: (asks: TenderPlaceAsks) => void = () => undefined;
    const window = open(() => new Promise((resolve) => (answer = resolve)));
    window.type("/sd/Tender");
    window.ok();

    window.type("/sd/Other");
    answer(asks("/sd/Tender", { tender_parent: "/sd" }));

    await waitFor(() => expect(window.getByText("OK")).not.toBeDisabled());
    expect(window.queryByTestId("another-folder-question")).toBeNull();
    expect(window.queryByText("Keep as typed")).toBeNull();
    expect(window.choose).not.toHaveBeenCalled();
  });

  it("a refusal that arrives after the text was edited is not shown under the new text", async () => {
    let answer: (refusal: TenderPlaceRefusal) => void = () => undefined;
    const window = open(() => new Promise((resolve) => (answer = resolve)));
    window.type("~/locked");
    window.ok();

    window.type("~/Games");
    answer(REFUSED);

    await waitFor(() => expect(window.getByText("OK")).not.toBeDisabled());
    expect(window.queryByTestId("another-folder-refusal")).toBeNull();
  });

  it("an accepted place closes the window and is handed on", async () => {
    const window = open(async () => asks("/home/deck/Games"));
    window.type("~/Games");

    window.ok();

    await waitFor(() => expect(window.onAccepted).toHaveBeenCalledWith(CHOSEN));
    expect(window.choose).toHaveBeenCalledWith("~/Games");
    expect(window.closeModal).toHaveBeenCalledTimes(1);
  });

  it("a refusal is shown under the field in amber, the text is kept and the window stays open", async () => {
    const window = open(async () => REFUSED);
    window.type("~/locked");

    window.ok();

    const refusal = await window.findByTestId("another-folder-refusal");
    expect(refusal).toHaveTextContent("~/locked cannot be written to.");
    expect(refusal.style.color).toBe("#d4a72c");
    expect(window.field.value).toBe("~/locked");
    expect(window.closeModal).not.toHaveBeenCalled();
    expect(window.onAccepted).not.toHaveBeenCalled();
  });

  it("a refused choice keeps the window open too", async () => {
    const window = open(
      async () => asks("/home/deck/locked"),
      async () => REFUSED,
    );
    window.type("~/locked");

    window.ok();

    expect(await window.findByTestId("another-folder-refusal")).toHaveTextContent("~/locked cannot be written to.");
    expect(window.closeModal).not.toHaveBeenCalled();
  });

  it("editing the text drops a shown refusal, and the next OK asks again for the new text", async () => {
    const check = vi.fn(async (text: string) => (text === "~/locked" ? REFUSED : asks("/home/deck/Games")));
    const window = open(check);
    window.type("~/locked");
    window.ok();
    await window.findByTestId("another-folder-refusal");

    window.type("~/Games");

    expect(window.queryByTestId("another-folder-refusal")).toBeNull();
    window.ok();
    await waitFor(() => expect(window.onAccepted).toHaveBeenCalled());
    expect(check).toHaveBeenLastCalledWith("~/Games");
  });

  it("missing folders are asked about in the same window; Create chooses", async () => {
    const window = open(async () => asks("/home/deck/Games", { existing: "/home/deck", missing: "Games" }));
    window.type("~/Games");
    window.ok();

    const question = await window.findByTestId("another-folder-question");
    expect(question).toHaveTextContent(
      "/home/deck/Games does not exist yet; only /home/deck does. Create Games/ there?",
    );
    expect(window.closeModal).not.toHaveBeenCalled();
    fireEvent.click(window.getByText("Create"));

    await waitFor(() => expect(window.onAccepted).toHaveBeenCalledWith(CHOSEN));
    expect(window.choose).toHaveBeenCalledWith("~/Games");
  });

  it("a question's Cancel drops the question and keeps the window", async () => {
    const window = open(async () => asks("/home/deck/Games", { existing: "/home/deck", missing: "Games" }));
    window.type("~/Games");
    window.ok();
    await window.findByTestId("another-folder-question");

    fireEvent.click(window.getAllByText("Cancel")[0] as HTMLElement);

    expect(window.queryByTestId("another-folder-question")).toBeNull();
    expect(window.closeModal).not.toHaveBeenCalled();
    expect(window.choose).not.toHaveBeenCalled();
  });

  it("editing the text drops a shown question", async () => {
    const window = open(async () => asks("/sd/Tender", { tender_parent: "/sd" }));
    window.type("/sd/Tender");
    window.ok();
    await window.findByTestId("another-folder-question");

    window.type("/sd/Tende");

    expect(window.queryByTestId("another-folder-question")).toBeNull();
  });

  it("a place named Tender is asked about in the same window; Use parent checks and chooses the parent", async () => {
    const check = vi.fn(async (text: string) =>
      text === "/sd/Tender" ? asks("/sd/Tender", { tender_parent: "/sd" }) : asks("/sd"),
    );
    const window = open(check);
    window.type("/sd/Tender");
    window.ok();

    const question = await window.findByTestId("another-folder-question");
    expect(question).toHaveTextContent(
      "Tender keeps its files in a folder named Tender inside the place you choose, so this would make /sd/Tender/Tender. Use /sd instead?",
    );
    fireEvent.click(window.getByText("Use /sd"));

    await waitFor(() => expect(window.choose).toHaveBeenCalledWith("/sd"));
    expect(check).toHaveBeenLastCalledWith("/sd");
  });

  it("Keep as typed chooses the place as typed", async () => {
    const window = open(async () => asks("/sd/Tender", { tender_parent: "/sd" }));
    window.type("/sd/Tender");
    window.ok();
    await window.findByTestId("another-folder-question");

    fireEvent.click(window.getByText("Keep as typed"));

    await waitFor(() => expect(window.choose).toHaveBeenCalledWith("/sd/Tender"));
  });

  it("Cancel in the footer closes the window with nothing chosen", () => {
    const window = open(async (text) => asks(text));

    fireEvent.click(window.getByText("Cancel"));

    expect(window.closeModal).toHaveBeenCalledTimes(1);
    expect(window.onAccepted).not.toHaveBeenCalled();
  });
});
