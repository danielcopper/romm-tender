import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi, afterEach } from "vitest";
import { act, render, fireEvent, within } from "@testing-library/react";
import type { IconBaseProps, IconType } from "react-icons";
import { FaCheckCircle, FaChevronDown, FaExclamationTriangle, FaInfoCircle } from "react-icons/fa";
import { EmulatorSourcesSection } from "./EmulatorSourcesSection";
import { SOURCES_READING, SOURCES_UNREAD } from "../../utils/emulatorSourceWording";
import { ENTRY_FOCUS_DELAY_MS, FOCUS_STOPS } from "../../utils/entryFocus";
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
const RETROARCH: EmulatorSource = {
  kind: "bare_retroarch_flatpak",
  enabled: true,
  starts_games: false,
  root: "/home/deck/.var/app/org.libretro.RetroArch",
  findings: [],
  catalogue: "unavailable",
};
const BOTH: EmulatorSourcesListing = { sources: [RETRODECK, EMUDECK], answering: "retrodeck" };

function listed(...sources: EmulatorSource[]): EmulatorSourcesListing {
  return { sources, answering: "retrodeck" };
}

function renderSection(listing: EmulatorSourcesListing | null | undefined, busy = false) {
  const onSwitch = vi.fn();
  const onMove = vi.fn();
  const view = render(<EmulatorSourcesSection listing={listing} busy={busy} onSwitch={onSwitch} onMove={onMove} />);
  /** Render the section again with what the parent now holds. */
  const answer = (next: EmulatorSourcesListing | null | undefined, nextBusy = false) =>
    view.rerender(<EmulatorSourcesSection listing={next} busy={nextBusy} onSwitch={onSwitch} onMove={onMove} />);
  return { ...view, onSwitch, onMove, answer };
}

/**
 * Lay the cards out one under the other, 100 apart from `shift` down, by their
 * place in the document — happy-dom lays nothing out, so a card's offset is
 * mocked and what is pinned is the decision to slide, not the slide.
 */
function layCardsOut(): { shift: (by: number) => void } {
  let shift = 0;
  vi.spyOn(HTMLElement.prototype, "offsetTop", "get").mockImplementation(function (this: HTMLElement) {
    const cards = [...this.ownerDocument.querySelectorAll('[data-testid^="source-card-"]')];
    const at = cards.indexOf(this);
    return at === -1 ? 0 : shift + at * 100;
  });
  return {
    shift: (by) => {
      shift = by;
    },
  };
}

function transformOf(getByTestId: (id: string) => HTMLElement, kind: string): string {
  return getByTestId(`source-card-${kind}`).style.transform;
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

  it("says a RetroDECK that is not set up is installed, with no root and no other line", () => {
    const notSetUp = {
      ...RETRODECK,
      root: null,
      catalogue: "unavailable" as const,
      findings: [{ code: "not-set-up", data: { path: "/rd.json", app_id: "net.retrodeck.retrodeck" } }],
    };
    const { queryByTestId, getByTestId } = renderSection({ sources: [notSetUp], answering: "retrodeck" });

    expect(getByTestId("source-name-retrodeck")).toHaveTextContent("RetroDECK");
    expect(queryByTestId("source-root-retrodeck")).toBeNull();
    expect(getByTestId("source-lines-retrodeck")).toHaveTextContent(
      "RetroDECK is installed but has not been set up yet. Start RetroDECK once and finish its first-run setup.",
    );
    expect(getByTestId("source-lines-retrodeck")).not.toHaveTextContent("not established");
  });

  it("shows a switched-off source with its switch off, still listed", () => {
    const { getAllByTestId } = renderSection({
      sources: [RETRODECK, { ...EMUDECK, enabled: false }],
      answering: "retrodeck",
    });
    expect(getAllByTestId("toggle-input").map((input) => (input as HTMLInputElement).checked)).toEqual([true, false]);
  });

  it("says on a switched-off source's card that Tender cannot start games through it (#2265 D6)", () => {
    const { getByTestId } = renderSection(listed(RETRODECK, { ...EMUDECK, enabled: false }));

    expect(getByTestId("source-lines-emudeck")).toHaveTextContent("Tender cannot start games through EmuDeck yet.");
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

  it("leaves the folder and health lines out of the focus stops, between the arrows and the switch", () => {
    const { getByTestId } = renderSection(BOTH);
    const card = getByTestId("source-card-emudeck");
    const lines = getByTestId("source-lines-emudeck");
    const stops = [...card.querySelectorAll<HTMLElement>(FOCUS_STOPS)];

    expect(stops.map((stop) => stop.getAttribute("aria-label") ?? stop.dataset.testid)).toEqual([
      "Move EmuDeck up",
      "Move EmuDeck down",
      "toggle-input",
    ]);
    expect(stops.filter((stop) => stop.contains(lines) || lines.contains(stop))).toEqual([]);
    expect(stops[1]!.compareDocumentPosition(lines)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    expect(lines.compareDocumentPosition(stops[2]!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  });

  /** Sliding a card to its new place (D34). The slide itself is the device's to show. */
  describe("when the order changes", () => {
    afterEach(() => {
      vi.restoreAllMocks();
    });

    it("draws each card whose place changed back at its old place, and leaves the others as they are", () => {
      layCardsOut();
      const { getByTestId, answer } = renderSection(listed(RETRODECK, EMUDECK, RETROARCH));

      answer(listed(EMUDECK, RETRODECK, RETROARCH));

      expect(transformOf(getByTestId, "retrodeck")).toBe("translateY(-100px)");
      expect(transformOf(getByTestId, "emudeck")).toBe("translateY(100px)");
      expect(transformOf(getByTestId, RETROARCH.kind)).toBe("");
    });

    it("lets a moved card go to its new place on the next frame, eased", async () => {
      layCardsOut();
      const { getByTestId, answer } = renderSection(BOTH);

      answer(listed(EMUDECK, RETRODECK));

      const card = getByTestId("source-card-emudeck");
      await vi.waitFor(() => {
        expect(card.style.transform).toBe("");
        expect(card.style.transition).toBe("transform 200ms ease-out");
      });
    });

    it("draws nothing back on the first render", () => {
      layCardsOut();
      const { getByTestId } = renderSection(listed(EMUDECK, RETRODECK));

      expect(transformOf(getByTestId, "emudeck")).toBe("");
      expect(transformOf(getByTestId, "retrodeck")).toBe("");
    });

    it("draws nothing back where a card only shifted and the order stayed", () => {
      const layout = layCardsOut();
      const { getByTestId, answer } = renderSection(BOTH);

      layout.shift(40);
      answer(listed(RETRODECK, EMUDECK), true);

      expect(transformOf(getByTestId, "retrodeck")).toBe("");
      expect(transformOf(getByTestId, "emudeck")).toBe("");
    });

    it("draws nothing back for a source that joins the list", () => {
      layCardsOut();
      const { getByTestId, answer } = renderSection(BOTH);

      answer(listed(RETRODECK, EMUDECK, RETROARCH));

      expect(transformOf(getByTestId, RETROARCH.kind)).toBe("");
    });

    it("draws nothing back when the list comes back after a notice", () => {
      layCardsOut();
      const { getByTestId, answer } = renderSection(BOTH);

      answer(undefined);
      answer(listed(EMUDECK, RETRODECK));

      expect(transformOf(getByTestId, "emudeck")).toBe("");
      expect(transformOf(getByTestId, "retrodeck")).toBe("");
    });
  });

  /** Focus after a move (D34). That Steam's own pointer follows is the device's to show. */
  describe("after a move", () => {
    afterEach(() => {
      vi.useRealTimers();
    });

    /** Let the placement's delay run out. */
    function settle(): void {
      act(() => {
        vi.advanceTimersByTime(ENTRY_FOCUS_DELAY_MS);
      });
    }

    it("hands focus to the moved source's other arrow where the move took it to the end", () => {
      vi.useFakeTimers();
      const { container, answer } = renderSection(BOTH);

      fireEvent.click(arrow(container, "EmuDeck", "up"));
      answer(BOTH, true);
      answer(listed(EMUDECK, RETRODECK));
      settle();

      expect(arrow(container, "EmuDeck", "down")).toHaveFocus();
    });

    it("keeps focus on the pressed arrow where the source can move on", () => {
      vi.useFakeTimers();
      const { container, answer } = renderSection(listed(RETRODECK, EMUDECK, RETROARCH));

      fireEvent.click(arrow(container, "RetroArch (Flatpak)", "up"));
      answer(listed(RETRODECK, RETROARCH, EMUDECK));
      settle();

      expect(arrow(container, "RetroArch (Flatpak)", "up")).toHaveFocus();
    });

    it("places nothing until the listing the press asked for has arrived and the change is over", () => {
      vi.useFakeTimers();
      const { container, answer } = renderSection(BOTH);
      const moved = listed(EMUDECK, RETRODECK);

      fireEvent.click(arrow(container, "RetroDECK", "down"));
      answer(BOTH, true);
      settle();
      expect(document.body).toHaveFocus();
      answer(BOTH);
      settle();
      expect(document.body).toHaveFocus();
      answer(moved, true);
      settle();
      expect(document.body).toHaveFocus();
      answer(moved);
      settle();

      expect(arrow(container, "RetroDECK", "up")).toHaveFocus();
    });

    it("leaves focus where the reader took it outside the cards in the meantime", () => {
      vi.useFakeTimers();
      const { container, answer } = renderSection(BOTH);
      const elsewhere = document.createElement("button");
      document.body.append(elsewhere);

      fireEvent.click(arrow(container, "EmuDeck", "up"));
      answer(BOTH, true);
      elsewhere.focus();
      answer(listed(EMUDECK, RETRODECK));
      settle();

      expect(elsewhere).toHaveFocus();
      elsewhere.remove();
    });

    it("places nothing for a source the answer no longer lists", () => {
      vi.useFakeTimers();
      const { container, answer } = renderSection(BOTH);

      fireEvent.click(arrow(container, "EmuDeck", "up"));
      answer(listed(RETRODECK));
      settle();

      expect(document.body).toHaveFocus();
    });
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
