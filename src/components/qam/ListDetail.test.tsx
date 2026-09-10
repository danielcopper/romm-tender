/**
 * ListDetail tests — focus selects, and A stays with the row's own control.
 *
 * The @decky/ui stub in `src/test-setup.ts` renders every Focusable as a div
 * that forwards onFocus, so focus moving onto a row is driven with a real
 * focusin event rather than Steam's gamepad engine.
 */

import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { useState, type CSSProperties, type FC, type ReactNode } from "react";
import { ENTRY_STOP_ATTR } from "../../utils/entryFocus";
import { ListDetail, type ListDetailItem, type ListDetailProps } from "./ListDetail";

const PLATFORMS = [
  { id: "n64", name: "Nintendo 64" },
  { id: "psx", name: "PlayStation" },
];

function platformItems(onToggle: (id: string) => void): ListDetailItem[] {
  return PLATFORMS.map((platform) => ({
    id: platform.id,
    render: (selected: boolean) => (
      <button onClick={() => onToggle(platform.id)}>
        {platform.name}
        {selected ? " (selected)" : ""}
      </button>
    ),
  }));
}

/** A page-shaped host: it owns the selection, as a real wide page does. */
const ControlledHost: FC<{ onSelect?: (id: string) => void; onToggle?: (id: string) => void }> = ({
  onSelect,
  onToggle,
}) => {
  const [selectedId, setSelectedId] = useState<string | null>("n64");
  return (
    <ListDetail
      items={platformItems(onToggle ?? (() => {}))}
      selectedId={selectedId}
      onSelect={(id) => {
        setSelectedId(id);
        onSelect?.(id);
      }}
      renderDetail={(id) => <div>detail for {id ?? "nothing"}</div>}
    />
  );
};

describe("ListDetail", () => {
  // The scroll-panel test swaps deckyUiInternals for a stub in the module
  // registry, where a `vi.doMock` otherwise stands for the rest of the file and
  // would reach any later test that imports dynamically.
  afterEach(() => {
    vi.doUnmock("../../utils/deckyUiInternals");
    vi.resetModules();
  });

  it("selects the row that takes focus and swaps the detail with it", () => {
    const onSelect = vi.fn();
    render(<ControlledHost onSelect={onSelect} />);

    expect(screen.getByText("detail for n64")).toBeInTheDocument();
    fireEvent.focusIn(screen.getByRole("button", { name: /PlayStation/ }));

    expect(onSelect).toHaveBeenCalledWith("psx");
    expect(screen.getByText("detail for psx")).toBeInTheDocument();
    expect(screen.queryByText("detail for n64")).not.toBeInTheDocument();
  });

  it("tells the row whether it is the selected one", () => {
    render(<ControlledHost />);

    expect(screen.getByRole("button", { name: "Nintendo 64 (selected)" })).toBeInTheDocument();
    fireEvent.focusIn(screen.getByRole("button", { name: "PlayStation" }));

    expect(screen.getByRole("button", { name: "PlayStation (selected)" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nintendo 64" })).toBeInTheDocument();
  });

  it("reports a selection only when it changes", () => {
    const onSelect = vi.fn();
    render(<ControlledHost onSelect={onSelect} />);

    // focusin fires again for every move between controls inside one row, and on
    // the way back from the detail pane. A page may do real work on onSelect, so
    // the same id must not arrive twice.
    fireEvent.focusIn(screen.getByRole("button", { name: /Nintendo 64/ }));
    fireEvent.focusIn(screen.getByRole("button", { name: /Nintendo 64/ }));

    expect(onSelect).not.toHaveBeenCalled();

    fireEvent.focusIn(screen.getByRole("button", { name: /PlayStation/ }));
    fireEvent.focusIn(screen.getByRole("button", { name: /PlayStation/ }));

    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("leaves A to the row's own control", () => {
    const onSelect = vi.fn();
    const onToggle = vi.fn();
    render(<ControlledHost onSelect={onSelect} onToggle={onToggle} />);

    fireEvent.click(screen.getByRole("button", { name: "PlayStation" }));

    expect(onToggle).toHaveBeenCalledWith("psx");
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("puts the list and the detail in two separately navigable regions", () => {
    render(<ControlledHost />);
    const [list, detail] = [...screen.getAllByTestId("focusable")[0]!.children] as HTMLElement[];

    expect(list).toContainElement(screen.getByRole("button", { name: /Nintendo 64/ }));
    expect(list).toContainElement(screen.getByRole("button", { name: /PlayStation/ }));
    expect(detail).toContainElement(screen.getByText("detail for n64"));
    expect(list?.style.overflow).toBe("auto");
    expect(detail?.style.overflow).toBe("auto");
  });

  it("gives each pane Steam's scroll panel, so the two scroll independently", async () => {
    vi.resetModules();
    vi.doMock("../../utils/deckyUiInternals", () => ({
      ScrollPanel: ({ style, children }: { style?: CSSProperties; children?: ReactNode }) => (
        <div data-testid="scroll-panel" style={style}>
          {children}
        </div>
      ),
    }));
    const Scrolling = (await import("./ListDetail")).ListDetail as FC<ListDetailProps>;

    render(
      <Scrolling
        items={platformItems(() => {})}
        selectedId="n64"
        onSelect={vi.fn()}
        renderDetail={() => <button>a detail row</button>}
      />,
    );
    const [list, detail] = screen.getAllByTestId("scroll-panel");

    // Two panels rather than one around both: the panes scroll independently, so
    // a long detail must not carry the list up with it.
    expect(list).toContainElement(screen.getByRole("button", { name: /Nintendo 64/ }));
    expect(detail).toContainElement(screen.getByRole("button", { name: "a detail row" }));
    expect(list?.style.width).toBe("264px");
    expect(detail?.style.height).toBe("100%");
  });

  it("opens a newly selected entry's detail at its own top", () => {
    // The pane is remounted on selection, so its scroll position cannot carry
    // over from the platform before it — a new pane opening part-way down its
    // own BIOS table is what this prevents. happy-dom lays nothing out, so what
    // is pinned is the remount; that the scroll actually returns to the top is
    // the browser's own behaviour for a fresh element.
    const { rerender } = render(
      <ListDetail
        items={platformItems(() => {})}
        selectedId="n64"
        onSelect={vi.fn()}
        renderDetail={(id) => <div>detail for {id ?? "nothing"}</div>}
      />,
    );
    const before = screen.getByText("detail for n64").closest("[style]");

    rerender(
      <ListDetail
        items={platformItems(() => {})}
        selectedId="psx"
        onSelect={vi.fn()}
        renderDetail={(id) => <div>detail for {id ?? "nothing"}</div>}
      />,
    );

    expect(screen.getByText("detail for psx")).toBeInTheDocument();
    expect(before).not.toBeInTheDocument();
  });

  it("puts a whole-list control above the rows without making it a selection", () => {
    const onSelect = vi.fn();
    const onEnableAll = vi.fn();
    render(
      <ListDetail
        items={platformItems(() => {})}
        listHeader={<button onClick={onEnableAll}>Enable all</button>}
        selectedId="n64"
        onSelect={onSelect}
        renderDetail={(id) => <div>detail for {id ?? "nothing"}</div>}
      />,
    );

    const header = screen.getByRole("button", { name: "Enable all" });
    // Reaching it must not report a selection: it belongs to the list, not to a
    // row of it, and a page may do real work on onSelect.
    fireEvent.focusIn(header);
    fireEvent.click(header);

    expect(onEnableAll).toHaveBeenCalledTimes(1);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("makes a control-less row a focus stop, and selects it on press", () => {
    // A row that renders a label and nothing else is a bare container: Steam's
    // base panel takes `focusable` from an activate handler, so without one the
    // reader cannot reach the row and cannot scroll past it. The stub surfaces
    // the handler as `data-activate`, which is all happy-dom can show — it has
    // no nav tree.
    const onSelect = vi.fn();
    render(
      <ListDetail
        items={[
          { id: "general", render: () => <span>General</span> },
          { id: "library", render: () => <span>Library</span> },
        ]}
        selectedId="general"
        onSelect={onSelect}
        renderDetail={(id) => <div>detail for {id ?? "nothing"}</div>}
        selectOnActivate
      />,
    );

    const row = screen.getByText("Library").closest("[data-testid='focusable']") as HTMLElement;
    expect(row.dataset.activate).toBe("true");

    fireEvent(row, new CustomEvent("decky-button-down", { detail: { button: 1 }, bubbles: true }));

    expect(onSelect).toHaveBeenCalledWith("library");
  });

  it("leaves a row a plain container without selectOnActivate, so A stays with its control", () => {
    // The default: a row that carries a toggle must not spend A on the
    // selection, which focus already made.
    render(<ControlledHost />);

    const row = screen.getByRole("button", { name: /PlayStation/ }).closest("[data-testid='focusable']");
    expect((row as HTMLElement).dataset.activate).toBeUndefined();
  });

  it("declares the selected row the area entry focus belongs in, and moves the mark with it", () => {
    // Focus selects here, so a page opened on a section other than its first
    // would have that section overwritten by entry focus landing on row one.
    // The mark is what the placer reads; that Steam then puts focus there, and
    // that the row's own onFocus finds the selection already matching, is the
    // device's to show — happy-dom has no nav tree and nothing here runs the
    // frame's timer.
    const { rerender } = render(
      <ListDetail
        items={platformItems(() => {})}
        selectedId="psx"
        onSelect={vi.fn()}
        renderDetail={(id) => <div>detail for {id ?? "nothing"}</div>}
      />,
    );
    const declared = () => document.querySelectorAll(`[${ENTRY_STOP_ATTR}]`);
    const rowOf = (name: RegExp) => screen.getByRole("button", { name }).closest(`[${ENTRY_STOP_ATTR}]`);

    // Not the first row: that is the whole point, and asserting on the first
    // would pass on the accident this fixes.
    expect(declared()).toHaveLength(1);
    expect(rowOf(/PlayStation/)).not.toBeNull();
    expect(rowOf(/Nintendo 64/)).toBeNull();

    rerender(
      <ListDetail
        items={platformItems(() => {})}
        selectedId="n64"
        onSelect={vi.fn()}
        renderDetail={(id) => <div>detail for {id ?? "nothing"}</div>}
      />,
    );

    expect(declared()).toHaveLength(1);
    expect(rowOf(/Nintendo 64/)).not.toBeNull();
    expect(rowOf(/PlayStation/)).toBeNull();
  });

  it("declares nothing where nothing is selected", () => {
    // A list that opens with no selection — the Library page's platforms —
    // names no area, so the frame falls back to the body's first stop and the
    // page opens exactly where it did before.
    render(
      <ListDetail
        items={platformItems(() => {})}
        selectedId={null}
        onSelect={vi.fn()}
        renderDetail={() => <div>pick one</div>}
      />,
    );

    expect(document.querySelectorAll(`[${ENTRY_STOP_ATTR}]`)).toHaveLength(0);
  });

  it("keeps the row a stable element as the selection moves through it", () => {
    // The mark rides a wrapper every row has, marked or not: a wrapper that
    // appeared when a row became selected would remount the row Steam is
    // standing on, mid-navigation.
    const { rerender } = render(
      <ListDetail
        items={platformItems(() => {})}
        selectedId="n64"
        onSelect={vi.fn()}
        renderDetail={() => <div>detail</div>}
      />,
    );
    const row = screen.getByRole("button", { name: /Nintendo 64/ });

    rerender(
      <ListDetail
        items={platformItems(() => {})}
        selectedId="psx"
        onSelect={vi.fn()}
        renderDetail={() => <div>detail</div>}
      />,
    );

    expect(screen.getByRole("button", { name: /Nintendo 64/ })).toBe(row);
  });

  it("renders a detail for an empty selection", () => {
    render(
      <ListDetail
        items={[]}
        selectedId={null}
        onSelect={vi.fn()}
        renderDetail={(id) => <div>{id ?? "nothing"}</div>}
      />,
    );

    expect(screen.getByText("nothing")).toBeInTheDocument();
  });
});
