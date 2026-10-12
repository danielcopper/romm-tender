import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { debugLog } from "../../api/backend";
import { rommOverviewBlock } from "./rommOverviewBlock";

vi.mock("../RomMPlaySection", () => ({
  RomMPlaySection: () => {
    throw new Error("the play section fell over");
  },
}));

vi.mock("../RomMGameInfoPanel", () => ({
  RomMGameInfoPanel: () => <div>the info panel</div>,
}));

describe("rommOverviewBlock", () => {
  beforeEach(() => {
    vi.mocked(debugLog).mockClear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("puts one line in place of a section that throws, and leaves the rest of the page mounted", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(
      <div>
        <div>{"Steam's own header"}</div>
        {rommOverviewBlock(42, "overview-panel")}
      </div>,
    );

    expect(screen.getByText("Tender could not show this game's section.")).toBeInTheDocument();
    expect(screen.getByText("Steam's own header")).toBeInTheDocument();
    expect(screen.queryByText("the info panel")).not.toBeInTheDocument();
    // The line keeps the wrapper's place in Steam's layout.
    expect(screen.getByText("Tender could not show this game's section.").parentElement).toHaveClass("overview-panel");
  });

  it("sends the failure, with where it happened, to the debug log", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});

    render(<div>{rommOverviewBlock(42, "overview-panel")}</div>);

    const lines = vi.mocked(debugLog).mock.calls.map((call) => String(call[0]));
    const ours = lines.filter((line) => line.includes("the play section fell over"));
    expect(ours).toHaveLength(1);
    expect(ours[0]).toContain("RomMPlaySection");
  });
});
