import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi } from "vitest";
import { render, fireEvent, within } from "@testing-library/react";
import type { IconBaseProps, IconType } from "react-icons";
import { FaCheckCircle, FaChevronDown, FaExclamationTriangle, FaInfoCircle } from "react-icons/fa";
import { EmulatorSourcesSection } from "./EmulatorSourcesSection";
import { SOURCES_READING, SOURCES_UNREAD } from "../../utils/emulatorSourceWording";
import { AMBER, GREEN, MUTED, SELECTION_ACCENT } from "../layout/pane";
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

/**
 * Press *button* the way the device does: Steam reports a press on a disabled
 * control, where the browser (and React, which drops a click on an element
 * whose `disabled` prop is set) does not. So the button's own `onClick` prop is
 * called directly, off the props React keeps on the element.
 */
function pressAsTheDeviceDoes(button: HTMLButtonElement): void {
  const propsKey = Object.keys(button).find((key) => key.startsWith("__reactProps"));
  const props = (propsKey ? (button as unknown as Record<string, unknown>)[propsKey] : undefined) as
    { onClick?: () => void } | undefined;
  if (props?.onClick === undefined) throw new Error("the button carries no onClick prop");
  props.onClick();
}

/** The arrow that moves the source named *name* in *direction*, by its accessible name — it shows no text. */
function arrow(container: HTMLElement, name: string, direction: "up" | "down"): HTMLButtonElement {
  return within(container).getByRole("button", { name: `Move ${name} ${direction}` });
}

/** The markup *Icon* draws on its own, given *props*. */
function glyph(Icon: IconType, props: IconBaseProps): string {
  const { container, unmount } = render(<Icon {...props} />);
  const markup = container.querySelector("svg")!.outerHTML;
  unmount();
  return markup;
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
    expect(lines).not.toHaveTextContent("not established");
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

    expect(arrow(container, "RetroDECK", "up")).toBeDisabled();
    expect(arrow(container, "RetroDECK", "down")).not.toBeDisabled();
    expect(arrow(container, "EmuDeck", "up")).not.toBeDisabled();
    expect(arrow(container, "EmuDeck", "down")).toBeDisabled();
    fireEvent.click(arrow(container, "EmuDeck", "up"));
    fireEvent.click(arrow(container, "RetroDECK", "down"));
    fireEvent.click(arrow(container, "RetroDECK", "up"));

    expect(onMove.mock.calls).toEqual([
      ["emudeck", "up"],
      ["retrodeck", "down"],
    ]);
  });

  it("ignores a press on a dead end button, which the device still reports", () => {
    const { container, onMove } = renderSection(BOTH);

    pressAsTheDeviceDoes(arrow(container, "RetroDECK", "up"));
    pressAsTheDeviceDoes(arrow(container, "EmuDeck", "down"));

    expect(onMove).not.toHaveBeenCalled();
  });

  it("ignores a press on a move button while a change is in flight, as the device reports it", () => {
    const { container, onMove } = renderSection(BOTH, true);

    pressAsTheDeviceDoes(arrow(container, "EmuDeck", "up"));
    pressAsTheDeviceDoes(arrow(container, "RetroDECK", "down"));

    expect(onMove).not.toHaveBeenCalled();
  });

  it("passes a press on a live move button through", () => {
    const { container, onMove } = renderSection(BOTH);

    pressAsTheDeviceDoes(arrow(container, "EmuDeck", "up"));
    pressAsTheDeviceDoes(arrow(container, "RetroDECK", "down"));

    expect(onMove.mock.calls).toEqual([
      ["emudeck", "up"],
      ["retrodeck", "down"],
    ]);
  });

  it("takes no press while a change is in flight", () => {
    const { container, getAllByTestId, onMove, onSwitch } = renderSection(BOTH, true);
    fireEvent.click(arrow(container, "EmuDeck", "up"));
    fireEvent.click(getAllByTestId("toggle-input")[1]!);
    expect(onMove).not.toHaveBeenCalled();
    expect(onSwitch).not.toHaveBeenCalled();
  });

  it("disables both arrows of every source while a change is in flight", () => {
    const { container } = renderSection(BOTH, true);
    for (const name of ["RetroDECK", "EmuDeck"]) {
      expect(arrow(container, name, "up")).toBeDisabled();
      expect(arrow(container, name, "down")).toBeDisabled();
    }
  });

  it("names each arrow after the source and the direction, and shows no text on it", () => {
    const { container } = renderSection(BOTH);
    const labels = [...container.querySelectorAll("button")].map((button) => button.getAttribute("aria-label"));

    expect(labels).toEqual(["Move RetroDECK up", "Move RetroDECK down", "Move EmuDeck up", "Move EmuDeck down"]);
    expect(arrow(container, "EmuDeck", "up")).toHaveTextContent(/^$/);
  });

  it("puts the arrows on the name's line, before the source's folder and health", () => {
    const { getByTestId } = renderSection(BOTH);
    const card = getByTestId("source-card-emudeck");
    const nameLine = getByTestId("source-name-emudeck").parentElement!;

    expect(within(nameLine).getAllByRole("button")).toHaveLength(2);
    expect(within(card).getAllByRole("button")).toHaveLength(2);
    expect(nameLine.compareDocumentPosition(getByTestId("source-lines-emudeck"))).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("numbers every card by its source's place in the order", () => {
    const { getByTestId } = renderSection({ sources: [EMUDECK, RETRODECK], answering: "retrodeck" });

    expect(getByTestId("source-place-emudeck")).toHaveTextContent("1");
    expect(getByTestId("source-place-retrodeck")).toHaveTextContent("2");
    expect(within(getByTestId("source-card-emudeck")).getByTestId("source-place-emudeck")).toBeInTheDocument();
  });

  it("fills a switched-on source's number with the accent and greys a switched-off one's, name included", () => {
    const { getByTestId } = renderSection({
      sources: [RETRODECK, { ...EMUDECK, enabled: false }],
      answering: "retrodeck",
    });
    const on = getByTestId("source-place-retrodeck");
    const off = getByTestId("source-place-emudeck");

    expect(on.style.background).toBe(SELECTION_ACCENT);
    expect(on.style.color).toBe("#ffffff");
    expect(getByTestId("source-name-retrodeck").style.color).toBe("#ffffff");
    expect(off.style.background).toBe("transparent");
    expect(off.style.border).toBe(`2px solid ${MUTED}`);
    expect(off.style.color).toBe(MUTED);
    expect(getByTestId("source-name-emudeck").style.color).toBe(MUTED);
  });

  it("leads every health line with the icon of its tone", () => {
    const broken = { ...EMUDECK, findings: [{ code: "root-missing", data: { path: "/sd" } }] };
    const { getByTestId } = renderSection({ sources: [RETRODECK, broken], answering: "retrodeck" });
    const tones = (kind: string) =>
      within(getByTestId(`source-lines-${kind}`))
        .getAllByTestId("source-line-icon")
        .map((icon) => [
          icon.dataset.tone,
          icon.style.color,
          icon.querySelector("svg")?.outerHTML,
          icon.nextElementSibling?.textContent,
        ]);
    const ok = glyph(FaCheckCircle, { size: 11, "aria-hidden": true });
    const warning = glyph(FaExclamationTriangle, { size: 11, "aria-hidden": true });
    const info = glyph(FaInfoCircle, { size: 11, "aria-hidden": true });

    expect(tones("retrodeck")).toEqual([["ok", GREEN, ok, "No problems found."]]);
    expect(tones("emudeck")).toEqual([
      [
        "warning",
        AMBER,
        warning,
        "EmuDeck: its folder /sd does not exist. If it is on an SD card or another drive, insert it.",
      ],
      ["warning", AMBER, warning, "EmuDeck's emulator list cannot be read yet."],
      ["info", MUTED, info, "Tender cannot start games through EmuDeck yet."],
    ]);
  });

  it("points the up arrow up by turning the down chevron over, and leaves the down arrow as drawn", () => {
    const { container } = renderSection(BOTH);
    const chevron = (direction: "up" | "down") =>
      arrow(container, "EmuDeck", direction).querySelector("svg")!.outerHTML;

    expect(chevron("up")).toBe(glyph(FaChevronDown, { size: 12, style: { transform: "rotate(180deg)" } }));
    expect(chevron("down")).toBe(glyph(FaChevronDown, { size: 12 }));
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
