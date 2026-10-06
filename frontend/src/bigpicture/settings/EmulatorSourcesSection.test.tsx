import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent, within } from "@testing-library/react";
import { EmulatorSourcesSection, SOURCES_READING, SOURCES_UNREAD } from "./EmulatorSourcesSection";
import type { EmulatorSource, EmulatorSourcesListing } from "../../types";

const RETRODECK: EmulatorSource = {
  kind: "retrodeck",
  enabled: true,
  starts_games: true,
  root: "/run/media/deck/Emulation/retrodeck",
  findings: [],
  catalogue: "read",
};
const EMUDECK: EmulatorSource = {
  kind: "emudeck",
  enabled: true,
  starts_games: false,
  root: "/run/media/deck/Emulation/Emulation",
  findings: [],
  catalogue: "sealed",
};
const BOTH: EmulatorSourcesListing = { sources: [RETRODECK, EMUDECK], answering: "retrodeck" };

function renderSection(listing: EmulatorSourcesListing | null | undefined, busy = false) {
  const onSwitch = vi.fn();
  const onMove = vi.fn();
  const view = render(<EmulatorSourcesSection listing={listing} busy={busy} onSwitch={onSwitch} onMove={onMove} />);
  return { ...view, onSwitch, onMove };
}

function buttons(container: HTMLElement, text: string): HTMLButtonElement[] {
  return [...container.querySelectorAll("button")].filter((button) => button.textContent === text);
}

describe("EmulatorSourcesSection", () => {
  it("lists RetroDECK and EmuDeck by name with their root, health and switch", () => {
    const { getByTestId, getAllByTestId } = renderSection(BOTH);

    expect(getByTestId("source-name-retrodeck")).toHaveTextContent("RetroDECK");
    expect(getByTestId("source-name-emudeck")).toHaveTextContent("EmuDeck");
    expect(getByTestId("source-root-retrodeck")).toHaveTextContent("/run/media/deck/Emulation/retrodeck");
    expect(getByTestId("source-lines-retrodeck")).toHaveTextContent("No problems found.");
    expect(getAllByTestId("toggle")).toHaveLength(2);
    expect(getAllByTestId("toggle-input").map((input) => (input as HTMLInputElement).checked)).toEqual([true, true]);
  });

  it("says EmuDeck's list cannot be read yet and that Tender cannot start games through it", () => {
    const { getByTestId } = renderSection(BOTH);
    const lines = getByTestId("source-lines-emudeck");

    expect(lines).toHaveTextContent("EmuDeck's emulator list cannot be read yet.");
    expect(lines).toHaveTextContent("Tender cannot start games through EmuDeck yet.");
    expect(getByTestId("source-lines-retrodeck")).not.toHaveTextContent("cannot start games");
  });

  it("leaves the root out where it is a default and words the finding instead", () => {
    const broken = { ...RETRODECK, root: null, findings: [{ code: "marker-missing", data: { path: "/rd.json" } }] };
    const { queryByTestId, getByTestId } = renderSection({ sources: [broken], answering: "retrodeck" });

    expect(queryByTestId("source-root-retrodeck")).toBeNull();
    expect(getByTestId("source-lines-retrodeck")).toHaveTextContent(
      "RetroDECK: its settings file /rd.json is missing.",
    );
  });

  it("shows a switched-off source with its switch off, still listed", () => {
    const { getAllByTestId } = renderSection({
      sources: [RETRODECK, { ...EMUDECK, enabled: false }],
      answering: "retrodeck",
    });
    expect(getAllByTestId("toggle-input").map((input) => (input as HTMLInputElement).checked)).toEqual([true, false]);
  });

  it("switches a source through its toggle", () => {
    const { getAllByTestId, onSwitch } = renderSection(BOTH);
    fireEvent.click(getAllByTestId("toggle-input")[1]!);
    expect(onSwitch).toHaveBeenCalledWith("emudeck", false);
  });

  it("moves a source up and down, and the ends are dead", () => {
    const { container, onMove } = renderSection(BOTH);
    const up = buttons(container, "Move up");
    const down = buttons(container, "Move down");

    expect(up[0]).toBeDisabled();
    expect(down[1]).toBeDisabled();
    fireEvent.click(up[1]!);
    fireEvent.click(down[0]!);
    fireEvent.click(up[0]!);

    expect(onMove.mock.calls).toEqual([
      ["emudeck", "up"],
      ["retrodeck", "down"],
    ]);
  });

  it("takes no press while a change is in flight", () => {
    const { container, getAllByTestId, onMove, onSwitch } = renderSection(BOTH, true);
    fireEvent.click(buttons(container, "Move up")[1]!);
    fireEvent.click(getAllByTestId("toggle-input")[1]!);
    expect(onMove).not.toHaveBeenCalled();
    expect(onSwitch).not.toHaveBeenCalled();
  });

  it("makes every source's information row a focus stop", () => {
    const { getAllByTestId } = renderSection(BOTH);
    const rows = getAllByTestId("field");
    expect(rows).toHaveLength(2);
    expect(rows.every((row) => row.getAttribute("tabindex") === "0")).toBe(true);
  });

  const NOTHING: EmulatorSourcesListing = { sources: [], answering: null };
  it.each<[EmulatorSourcesListing | null | undefined, string]>([
    [undefined, SOURCES_READING],
    [null, SOURCES_UNREAD],
    [NOTHING, "No emulator source was found."],
  ])("says what there is while there is no source to list (%s)", (listing, line) => {
    const { getByTestId } = renderSection(listing);
    const notice = getByTestId("sources-notice");
    expect(notice).toHaveTextContent(line);
    expect(within(notice.closest('[data-testid="field"]')!).getByText(line)).toBeInTheDocument();
  });
});
