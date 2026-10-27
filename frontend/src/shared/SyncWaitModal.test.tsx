import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, fireEvent, act } from "@testing-library/react";
import { cloneElement, createElement, type ReactElement } from "react";
import { showModal } from "@decky/ui";
import { showSyncWaitModal } from "./SyncWaitModal";
import type { SyncFinish, SyncWaitChoice } from "../utils/launchGate";

// Per-file mock: ConfirmModal drawn as its title, sentence and buttons, so a
// test reads what the question says and presses its buttons. A press closes
// the modal after its handler, as Steam's does.
vi.mock("@decky/ui", () => {
  type Props = Record<string, unknown> & { closeModal?: () => void };
  const press = (props: Props, handler: string) => () => {
    (props[handler] as (() => void) | undefined)?.();
    props.closeModal?.();
  };
  return {
    ConfirmModal: (props: Props) =>
      createElement(
        "div",
        null,
        createElement("h1", null, props["strTitle"] as string),
        createElement("p", null, props["strDescription"] as string),
        createElement("button", { onClick: press(props, "onOK") }, props["strOKButtonText"] as string),
        props["strMiddleButtonText"] === undefined
          ? null
          : createElement(
              "button",
              { onClick: press(props, "onMiddleButton") },
              props["strMiddleButtonText"] as string,
            ),
        createElement("button", { onClick: press(props, "onCancel") }, props["strCancelButtonText"] as string),
      ),
    showModal: vi.fn(),
  };
});

/** A sync that finishes only when the test says so. */
function heldSync(): { finished: Promise<SyncFinish>; finish: (how: SyncFinish) => void } {
  let finish!: (how: SyncFinish) => void;
  const finished = new Promise<SyncFinish>((resolve) => (finish = resolve));
  return { finished, finish };
}

/** Open the question and draw the element it handed to `showModal`. */
function openQuestion(
  finished: Promise<SyncFinish>,
  reason: Parameters<typeof showSyncWaitModal>[0] = { kind: "another_sync", holder: "sync_all" },
) {
  const choice = showSyncWaitModal(reason, finished);
  const calls = vi.mocked(showModal).mock.calls;
  const element = calls[calls.length - 1]?.[0] as ReactElement<{ closeModal?: () => void }> | undefined;
  if (!element) throw new Error("showModal was not called");
  const closeModal = vi.fn();
  const view = render(cloneElement(element, { closeModal }));
  return { choice, closeModal, view };
}

describe("SyncWaitModal — showSyncWaitModal", () => {
  beforeEach(() => {
    vi.mocked(showModal).mockClear();
  });

  it("names why the start waits and offers Wait, Play Anyway and Cancel", () => {
    const { view } = openQuestion(heldSync().finished);

    expect(view.getByText("Waiting for Save Sync")).toBeTruthy();
    expect(
      view.getByText(
        "Sync All Saves Now is still running. Wait a little longer, or play with the saves on this device?",
      ),
    ).toBeTruthy();
    expect(view.getAllByRole("button").map((b) => b.textContent)).toEqual(["Wait", "Play Anyway", "Cancel"]);
  });

  it.each([
    ["Wait", "wait"],
    ["Play Anyway", "play_anyway"],
    ["Cancel", "cancel"],
  ] as const)("%s answers %s", async (label, expected: SyncWaitChoice) => {
    const { choice, view } = openQuestion(heldSync().finished);

    fireEvent.click(view.getByText(label));

    expect(await choice).toBe(expected);
  });

  it("turns into 'your saves are ready' when the sync finishes, and starts nothing by itself", async () => {
    const sync = heldSync();
    const { choice, closeModal, view } = openQuestion(sync.finished);
    let answered = false;
    void choice.then(() => (answered = true));

    await act(async () => {
      sync.finish("ready");
    });

    expect(view.getByText("Saves Ready")).toBeTruthy();
    expect(view.getByText("Your saves are ready. Play now?")).toBeTruthy();
    expect(view.getAllByRole("button").map((b) => b.textContent)).toEqual(["Play", "Cancel"]);
    expect(answered).toBe(false);
    expect(closeModal).not.toHaveBeenCalled();
  });

  it.each([
    ["Play", "answered"],
    ["Cancel", "cancel"],
  ] as const)("on the ready question %s answers %s", async (label, expected: SyncWaitChoice) => {
    const sync = heldSync();
    const { choice, view } = openQuestion(sync.finished);
    await act(async () => {
      sync.finish("ready");
    });

    fireEvent.click(view.getByText(label));

    expect(await choice).toBe(expected);
  });

  it("closes itself when the sync answers with something to act on first", async () => {
    const sync = heldSync();
    const { choice, closeModal } = openQuestion(sync.finished);

    await act(async () => {
      sync.finish("answered");
    });

    expect(await choice).toBe("answered");
    expect(closeModal).toHaveBeenCalledTimes(1);
  });

  it("a sync that finishes after the user answered changes nothing", async () => {
    const sync = heldSync();
    const { choice, closeModal, view } = openQuestion(sync.finished);
    fireEvent.click(view.getByText("Cancel"));
    expect(await choice).toBe("cancel");
    view.unmount();

    await act(async () => {
      sync.finish("answered");
    });

    expect(closeModal).toHaveBeenCalledTimes(1);
  });
});
