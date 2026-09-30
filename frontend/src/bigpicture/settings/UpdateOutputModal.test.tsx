import { describe, it, expect, beforeEach, vi } from "vitest";
import { fireEvent, render } from "@testing-library/react";
import { showModal } from "@decky/ui";
import { getUpdateOutput, logError, type UpdateOutput } from "../../api/backend";
import { OUTPUT_MISSING, OUTPUT_UNREAD, UpdateOutputModal, showUpdateOutput } from "./UpdateOutputModal";

vi.mock("../../api/backend", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api/backend")>()),
  logError: vi.fn(),
}));

/** 20:58 local time, whatever zone the suite runs in. */
const RAN_AT = new Date(2026, 8, 30, 20, 58, 13).getTime() / 1000;

const CHECK_REFUSED: UpdateOutput = {
  success: true,
  ran_at: RAN_AT,
  installer: {
    lines: ["[..] Installing   trying 1.0.52", "[!!] Installing   the new version does not start"],
    earlier: 0,
  },
  new_version: null,
  missing: null,
};

const stops = (container: HTMLElement) => [...container.querySelectorAll('[data-focusable-if-empty="true"]')];
const headings = (container: HTMLElement) => [...container.querySelectorAll("b")].map((b) => b.textContent);

describe("UpdateOutputModal", () => {
  it("titles the window with the time the installer ran, and shows its lines under The installer", () => {
    const { getByText, container } = render(<UpdateOutputModal output={CHECK_REFUSED} attemptedVersion="1.0.52" />);

    expect(getByText("What the installer said — 20:58")).toBeTruthy();
    expect(headings(container)).toEqual(["The installer"]);
    expect(stops(container).map((stop) => stop.textContent)).toEqual([
      "[..] Installing   trying 1.0.52\n[!!] Installing   the new version does not start",
    ]);
  });

  it("adds what the version it tried printed after a rollback, named after that version", () => {
    const output: UpdateOutput = {
      ...CHECK_REFUSED,
      new_version: { lines: ["host: load the panel from http://127.0.0.1:1/index.js?token=[hidden]"], earlier: 0 },
    };

    const { container } = render(<UpdateOutputModal output={output} attemptedVersion="1.0.32" />);

    expect(headings(container)).toEqual(["The installer", "1.0.32, when it tried to start"]);
    expect(stops(container)[1]?.textContent).toBe(
      "host: load the panel from http://127.0.0.1:1/index.js?token=[hidden]",
    );
  });

  it("walks a long part in focus stops of a screenful each, every line kept, and says what was left out", () => {
    const lines = Array.from({ length: 30 }, (_, i) => `line ${i}`);
    const output: UpdateOutput = { ...CHECK_REFUSED, installer: { lines, earlier: 270 } };

    const { container, getByText } = render(<UpdateOutputModal output={output} attemptedVersion="1.0.52" />);

    const shown = stops(container).map((stop) => stop.textContent);
    expect(shown).toHaveLength(3);
    expect(shown.join("\n").split("\n")).toEqual(lines);
    expect(getByText("270 earlier lines are not shown.")).toBeTruthy();
  });

  it("says one earlier line in the singular", () => {
    const output: UpdateOutput = { ...CHECK_REFUSED, installer: { lines: ["x"], earlier: 1 } };

    expect(
      render(<UpdateOutputModal output={output} attemptedVersion="1.0.52" />).getByText("1 earlier line is not shown."),
    ).toBeTruthy();
  });

  it.each<["rotated" | "terminal", string]>([
    ["rotated", "This output is no longer in the system journal — it keeps only the last hours of logs."],
    ["terminal", "This update was run in a terminal, so its output is there, not in the journal."],
  ])("says why there is nothing where the journal holds no run (%s), as a focus stop of its own", (missing, said) => {
    const output: UpdateOutput = { success: true, ran_at: null, installer: null, new_version: null, missing };

    const { container, getByText } = render(<UpdateOutputModal output={output} attemptedVersion="1.0.52" />);

    expect(OUTPUT_MISSING[missing]).toBe(said);
    expect(getByText("What the installer said")).toBeTruthy();
    expect(headings(container)).toEqual([]);
    expect(stops(container).map((stop) => stop.textContent)).toEqual([said]);
  });

  it.each<[string, UpdateOutput | null]>([
    ["a journal that could not be read", { success: false, reason: "journal_unreadable", message: "x" }],
    ["an answer that did not arrive", null],
  ])("says the output could not be read for %s", (_case, output) => {
    const { container } = render(<UpdateOutputModal output={output} attemptedVersion="1.0.52" />);

    expect(stops(container).map((stop) => stop.textContent)).toEqual([OUTPUT_UNREAD]);
  });

  it("closes on Close", () => {
    const closeModal = vi.fn();
    const { getByText } = render(
      <UpdateOutputModal output={CHECK_REFUSED} attemptedVersion="1.0.52" closeModal={closeModal} />,
    );

    fireEvent.click(getByText("Close"));

    expect(closeModal).toHaveBeenCalledOnce();
  });
});

describe("showUpdateOutput", () => {
  beforeEach(() => {
    vi.mocked(showModal).mockClear();
    vi.mocked(getUpdateOutput).mockReset();
    vi.mocked(logError).mockClear();
  });

  it("asks for the failure it was pressed for and opens the window over the answer", async () => {
    vi.mocked(getUpdateOutput).mockResolvedValue(CHECK_REFUSED);

    await showUpdateOutput("2026-09-30T20:58:13Z", "1.0.52");

    expect(getUpdateOutput).toHaveBeenCalledWith("2026-09-30T20:58:13Z");
    expect(vi.mocked(showModal).mock.calls[0]?.[0]).toEqual(
      <UpdateOutputModal output={CHECK_REFUSED} attemptedVersion="1.0.52" />,
    );
  });

  it("opens the window saying so, and logs, where the call failed", async () => {
    vi.mocked(getUpdateOutput).mockRejectedValue(new Error("connection lost"));

    await showUpdateOutput(null, "1.0.52");

    expect(vi.mocked(showModal).mock.calls[0]?.[0]).toEqual(
      <UpdateOutputModal output={null} attemptedVersion="1.0.52" />,
    );
    expect(logError).toHaveBeenCalledOnce();
  });
});
