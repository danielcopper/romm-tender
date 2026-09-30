import { describe, it, expect, beforeEach, vi } from "vitest";
import { act, fireEvent, render } from "@testing-library/react";
import { showModal } from "@decky/ui";
import { getUpdateOutput, logError, type UpdateOutput } from "../../api/backend";
import {
  OUTPUT_GONE,
  OUTPUT_MISSING,
  OUTPUT_READING,
  OUTPUT_UNREAD,
  UpdateOutputModal,
  outputStops,
  showUpdateOutput,
} from "./UpdateOutputModal";

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

/** The window over an answer that has arrived. */
async function shown(output: UpdateOutput | null, attemptedVersion = "1.0.52", closeModal?: () => void) {
  const view = render(
    <UpdateOutputModal
      read={Promise.resolve(output)}
      attemptedVersion={attemptedVersion}
      {...(closeModal ? { closeModal } : {})}
    />,
  );
  await act(async () => {});
  return view;
}

describe("UpdateOutputModal", () => {
  it("says it is reading until the answer arrives, then shows it", async () => {
    let answer: (output: UpdateOutput) => void = () => {};
    const read = new Promise<UpdateOutput>((resolve) => {
      answer = resolve;
    });

    const { container, getByText } = render(<UpdateOutputModal read={read} attemptedVersion="1.0.52" />);

    expect(stops(container).map((stop) => stop.textContent)).toEqual([OUTPUT_READING]);
    expect(getByText("What the installer said")).toBeTruthy();

    await act(async () => answer(CHECK_REFUSED));

    expect(getByText("What the installer said — 20:58")).toBeTruthy();
  });

  it("titles the window with the time the installer ran, and shows its lines under The installer", async () => {
    const { getByText, container } = await shown(CHECK_REFUSED);

    expect(getByText("What the installer said — 20:58")).toBeTruthy();
    expect(headings(container)).toEqual(["The installer"]);
    expect(stops(container).map((stop) => stop.textContent)).toEqual([
      "[..] Installing   trying 1.0.52\n[!!] Installing   the new version does not start",
    ]);
  });

  it("adds what the version it tried printed after a rollback, named after that version", async () => {
    const output: UpdateOutput = {
      ...CHECK_REFUSED,
      new_version: { lines: ["host: load the panel from http://127.0.0.1:1/index.js?token=[hidden]"], earlier: 0 },
    };

    const { container } = await shown(output, "1.0.32");

    expect(headings(container)).toEqual(["The installer", "1.0.32, when it tried to start"]);
    expect(stops(container)[1]?.textContent).toBe(
      "host: load the panel from http://127.0.0.1:1/index.js?token=[hidden]",
    );
  });

  it("walks a long part in focus stops of twelve lines each, every line kept, and says what was left out", async () => {
    const lines = Array.from({ length: 30 }, (_, i) => `line ${i}`);
    const output: UpdateOutput = { ...CHECK_REFUSED, installer: { lines, earlier: 270 } };

    const { container, getByText } = await shown(output);

    const shownStops = stops(container).map((stop) => stop.textContent);
    expect(shownStops.map((stop) => stop.split("\n").length)).toEqual([12, 12, 6]);
    expect(shownStops.join("\n").split("\n")).toEqual(lines);
    expect(getByText("270 earlier lines are not shown.")).toBeTruthy();
  });

  it("says one earlier line in the singular", async () => {
    const output: UpdateOutput = { ...CHECK_REFUSED, installer: { lines: ["x"], earlier: 1 } };

    expect((await shown(output)).getByText("1 earlier line is not shown.")).toBeTruthy();
  });

  it.each<["rotated" | "terminal" | "empty", string]>([
    ["rotated", "This output is no longer in the system journal — it keeps only the last hours of logs."],
    ["terminal", "This update was run in a terminal, so its output is there, not in the journal."],
    ["empty", "The installer left nothing in the journal for this update."],
  ])(
    "says why there is nothing where the journal holds no run (%s), as a focus stop of its own",
    async (missing, said) => {
      const output: UpdateOutput = { success: true, ran_at: null, installer: null, new_version: null, missing };

      const { container, getByText } = await shown(output);

      expect(OUTPUT_MISSING[missing]).toBe(said);
      expect(getByText("What the installer said")).toBeTruthy();
      expect(headings(container)).toEqual([]);
      expect(stops(container).map((stop) => stop.textContent)).toEqual([said]);
    },
  );

  it("says it could not read a reason a later backend gives that this panel has no sentence for", async () => {
    const output = { success: true, ran_at: null, installer: null, new_version: null, missing: "unheard_of" };

    const { container } = await shown(output as unknown as UpdateOutput);

    expect(stops(container).map((stop) => stop.textContent)).toEqual([OUTPUT_UNREAD]);
  });

  it("says the failure is no longer on record where the backend has none to show", async () => {
    const { container } = await shown({ success: false, reason: "not_found", message: "x" });

    expect(stops(container).map((stop) => stop.textContent)).toEqual([OUTPUT_GONE]);
    expect(OUTPUT_GONE).toBe("This failed update is no longer on record.");
  });

  it.each<[string, UpdateOutput | null]>([
    ["a journal that could not be read", { success: false, reason: "journal_unreadable", message: "x" }],
    ["an answer that did not arrive", null],
  ])("says the output could not be read for %s", async (_case, output) => {
    const { container } = await shown(output);

    expect(stops(container).map((stop) => stop.textContent)).toEqual([OUTPUT_UNREAD]);
    expect(OUTPUT_UNREAD).toBe("Tender could not read what the installer said.");
  });

  it("closes on Close", async () => {
    const closeModal = vi.fn();
    const { getByText } = await shown(CHECK_REFUSED, "1.0.52", closeModal);

    fireEvent.click(getByText("Close"));

    expect(closeModal).toHaveBeenCalledOnce();
  });
});

describe("outputStops", () => {
  it("holds at most twelve lines in one stop", () => {
    const lines = Array.from({ length: 13 }, (_, i) => `${i}`);

    expect(outputStops(lines)).toEqual([lines.slice(0, 12).join("\n"), "12"]);
  });

  it("starts a new stop before its text would reach 1200 characters, so no stop is taller than the dialog", () => {
    const long = "A".repeat(500);

    expect(outputStops([long, long, long, "x"])).toEqual([`${long}\n${long}`, `${long}\nx`]);
  });

  it("keeps a line too long for any stop whole, in a stop of its own", () => {
    const longer = "B".repeat(1500);

    expect(outputStops(["a", longer, "b"])).toEqual(["a", longer, "b"]);
  });

  it("makes no stop of no lines", () => {
    expect(outputStops([])).toEqual([]);
  });
});

describe("showUpdateOutput", () => {
  beforeEach(async () => {
    // A press left pending by a test would keep every later one closed.
    await act(async () => {});
    vi.mocked(showModal).mockClear();
    vi.mocked(getUpdateOutput).mockReset();
    vi.mocked(logError).mockClear();
  });

  /** The window the last press opened, rendered once its answer has arrived. */
  async function opened() {
    const calls = vi.mocked(showModal).mock.calls;
    const modal = calls[calls.length - 1]?.[0];
    const view = render(<>{modal}</>);
    await act(async () => {});
    return view;
  }

  it("opens the window at once and asks for the failure it was pressed for", async () => {
    vi.mocked(getUpdateOutput).mockResolvedValue(CHECK_REFUSED);

    showUpdateOutput("2026-09-30T20:58:13Z", "1.0.52");

    expect(showModal).toHaveBeenCalledOnce();
    expect(getUpdateOutput).toHaveBeenCalledWith("2026-09-30T20:58:13Z");
    expect((await opened()).getByText("What the installer said — 20:58")).toBeTruthy();
  });

  it("opens no second window for a press while the first one's answer is on its way", async () => {
    let answer: (output: UpdateOutput) => void = () => {};
    vi.mocked(getUpdateOutput).mockReturnValue(
      new Promise<UpdateOutput>((resolve) => {
        answer = resolve;
      }),
    );

    showUpdateOutput(null, "1.0.52");
    showUpdateOutput(null, "1.0.52");

    expect(showModal).toHaveBeenCalledOnce();
    expect(getUpdateOutput).toHaveBeenCalledOnce();

    await act(async () => answer(CHECK_REFUSED));
    vi.mocked(getUpdateOutput).mockResolvedValue(CHECK_REFUSED);
    showUpdateOutput(null, "1.0.52");

    expect(showModal).toHaveBeenCalledTimes(2);
  });

  it("logs a call that failed, and the window says so", async () => {
    vi.mocked(getUpdateOutput).mockRejectedValue(new Error("connection lost"));

    showUpdateOutput(null, "1.0.52");
    const { container } = await opened();

    expect(stops(container).map((stop) => stop.textContent)).toEqual([OUTPUT_UNREAD]);
    expect(logError).toHaveBeenCalledOnce();
  });
});
