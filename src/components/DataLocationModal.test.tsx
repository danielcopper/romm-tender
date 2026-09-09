import { describe, it, expect, beforeEach, afterEach, vi, type MockInstance } from "vitest";
import { render, act, fireEvent } from "@testing-library/react";
import { createElement, type ReactElement } from "react";
import { showModal } from "@decky/ui";
import { DataLocationModalHost, candidateDetail } from "./DataLocationModal";
import * as backend from "../api/backend";
import type { DataLocationCandidate } from "../api/backend";

// Per-file mock so the modal's own closeModal prop can be captured — the
// "Cancel is a pure UI close" leg is exactly that prop being invoked.
const capturedModalCloseFns: Array<(() => void) | undefined> = [];

vi.mock("@decky/ui", () => {
  type AnyProps = Record<string, unknown> & { children?: unknown };
  return {
    ModalRoot: (p: AnyProps & { closeModal?: () => void }) => {
      capturedModalCloseFns.push(p.closeModal);
      return createElement("div", { "data-testid": "modal-root" }, p.children as never);
    },
    ConfirmModal: (p: AnyProps) => createElement("div", { "data-testid": "confirm-modal" }, p.children as never),
    DialogButton: ({ children, onClick, disabled }: AnyProps & { disabled?: boolean }) =>
      createElement("button", { onClick, disabled }, children as never),
    showModal: vi.fn(),
  };
});

interface ConfirmModalProps {
  strTitle?: string;
  strDescription?: string;
  strOKButtonText?: string;
  strCancelButtonText?: string;
  onOK?: () => void;
  onCancel?: () => void;
}

/** Props of the confirm handed to `showModal`, or a loud failure. */
function lastConfirmModalProps(): ConfirmModalProps {
  const calls = vi.mocked(showModal).mock.calls;
  const el = calls[calls.length - 1]?.[0] as ReactElement<ConfirmModalProps> | undefined;
  if (!el) throw new Error("showModal was not called");
  return el.props;
}

const OLD_COPY: DataLocationCandidate = {
  source: "decky-romm-sync",
  path: "/home/deck/homebrew/data/decky-romm-sync",
  present: true,
  size_bytes: 268_000_000,
  changed_at: "2026-08-01T10:00:00+00:00",
};

const NEW_COPY: DataLocationCandidate = {
  source: "romm-tender",
  path: "/home/deck/homebrew/data/romm-tender",
  present: true,
  size_bytes: 1_024,
  changed_at: "2026-09-01T10:00:00+00:00",
};

const flushAsync = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

function buttonsByText(container: HTMLElement, text: string): HTMLButtonElement[] {
  return Array.from(container.querySelectorAll("button")).filter((b) => b.textContent === text) as HTMLButtonElement[];
}

function useCopyButtons(container: HTMLElement): HTMLButtonElement[] {
  return buttonsByText(container, "Use this copy");
}

/** The first button carrying *text*, or a loud failure — never a silent skip. */
function firstButton(container: HTMLElement, text: string): HTMLButtonElement {
  const found = buttonsByText(container, text)[0];
  if (!found) throw new Error(`button "${text}" not found`);
  return found;
}

/** Render and pick the first copy, leaving the modal in its answered phase. */
async function renderAnswered(): Promise<HTMLElement> {
  vi.mocked(backend.getDataLocationCandidates).mockResolvedValue({ candidates: [OLD_COPY, NEW_COPY] });
  vi.mocked(backend.chooseDataLocation).mockResolvedValue({ success: true });

  const { container } = render(<DataLocationModalHost />);
  await flushAsync();
  fireEvent.click(firstButton(container, "Use this copy"));
  await flushAsync();
  return container;
}

describe("candidateDetail", () => {
  it("carries the year, because two copies can be a year apart", () => {
    expect(candidateDetail(OLD_COPY)).toContain("2026");
  });

  it("says the size is unknown rather than showing a partial one", () => {
    expect(candidateDetail({ ...OLD_COPY, size_bytes: null })).toBe("size could not be measured");
  });

  it("says a folder that has gone is gone", () => {
    expect(candidateDetail({ ...OLD_COPY, present: false })).toBe("no longer on disk");
  });
});

describe("DataLocationModalHost", () => {
  let logSpy: MockInstance<(msg: string) => void>;

  beforeEach(() => {
    capturedModalCloseFns.length = 0;
    vi.mocked(showModal).mockClear();
    vi.mocked(backend.getDataLocationCandidates).mockReset();
    vi.mocked(backend.chooseDataLocation).mockReset();
    logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
    vi.stubGlobal("SteamClient", { System: { RestartPC: vi.fn() }, User: { StartRestart: vi.fn() } });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows both copies with their path, size and last change", async () => {
    vi.mocked(backend.getDataLocationCandidates).mockResolvedValue({ candidates: [OLD_COPY, NEW_COPY] });

    const { container } = render(<DataLocationModalHost />);
    await flushAsync();

    const old = container.querySelector('[data-testid="data-location-candidate-decky-romm-sync"]');
    expect(old).not.toBeNull();
    expect(old!.textContent).toContain("/home/deck/homebrew/data/decky-romm-sync");
    expect(old!.textContent).toContain("255.6 MB");
    expect(old!.textContent).toContain("last changed");
    expect(container.querySelector('[data-testid="data-location-candidate-romm-tender"]')).not.toBeNull();
    expect(useCopyButtons(container)).toHaveLength(2);
  });

  it("lists a copy that has gone, and refuses to offer it", async () => {
    vi.mocked(backend.getDataLocationCandidates).mockResolvedValue({
      candidates: [OLD_COPY, { ...NEW_COPY, present: false, size_bytes: null, changed_at: null }],
    });

    const { container } = render(<DataLocationModalHost />);
    await flushAsync();

    const gone = container.querySelector('[data-testid="data-location-candidate-romm-tender"]');
    expect(gone).not.toBeNull();
    expect(gone!.textContent).toContain("no longer on disk");
    expect(useCopyButtons(container)).toHaveLength(1);
  });

  it("records the picked copy and then offers a restart that names the device", async () => {
    const container = await renderAnswered();

    expect(vi.mocked(backend.chooseDataLocation)).toHaveBeenCalledWith("decky-romm-sync");
    expect(container.textContent).toContain("the next time it starts");
    expect(container.textContent).toContain("Restarting your Steam Deck");
    expect(useCopyButtons(container)).toHaveLength(0);
    expect(buttonsByText(container, "Restart device now")).toHaveLength(1);
  });

  it("says nothing changes until the restart, and that the other copy is still pickable", async () => {
    const container = await renderAnswered();

    expect(container.textContent).toContain("Nothing changes until then");
    expect(container.textContent).toContain("Tender keeps using the copy it started with");
    expect(container.textContent).toContain("you can pick the other one any time before you restart");
  });

  it("asks before rebooting instead of rebooting on the press", async () => {
    const container = await renderAnswered();
    fireEvent.click(firstButton(container, "Restart device now"));

    expect(SteamClient.System.RestartPC).not.toHaveBeenCalled();
    const props = lastConfirmModalProps();
    expect(props.strTitle).toBe("Restart your Steam Deck?");
    expect(props.strDescription).toContain("closes your games and reboots the device");
    expect(props.strOKButtonText).toBe("Restart now");
    expect(props.strCancelButtonText).toBe("Cancel");
  });

  it("reboots once when the confirmation is accepted", async () => {
    const container = await renderAnswered();
    fireEvent.click(firstButton(container, "Restart device now"));
    lastConfirmModalProps().onOK?.();

    expect(SteamClient.System.RestartPC).toHaveBeenCalledTimes(1);
  });

  it("leaves the choice modal standing and reboots nothing when the confirmation is declined", async () => {
    const container = await renderAnswered();
    fireEvent.click(firstButton(container, "Restart device now"));

    // Declining runs nothing at all: the confirm carries no onCancel, so the
    // recorded answer and the modal behind it are untouched either way.
    expect(lastConfirmModalProps().onCancel).toBeUndefined();
    expect(SteamClient.System.RestartPC).not.toHaveBeenCalled();
    expect(container.querySelector('[data-testid="data-location-modal"]')).not.toBeNull();
    expect(buttonsByText(container, "Restart device now")).toHaveLength(1);
    expect(buttonsByText(container, "Later")).toHaveLength(1);
  });

  it("closes with Cancel before a choice and with Later after one", async () => {
    vi.mocked(backend.getDataLocationCandidates).mockResolvedValue({ candidates: [OLD_COPY, NEW_COPY] });
    vi.mocked(backend.chooseDataLocation).mockResolvedValue({ success: true });
    const closeModal = vi.fn();

    const { container } = render(<DataLocationModalHost closeModal={closeModal} />);
    await flushAsync();
    expect(buttonsByText(container, "Cancel")).toHaveLength(1);
    expect(buttonsByText(container, "Later")).toHaveLength(0);

    fireEvent.click(firstButton(container, "Use this copy"));
    await flushAsync();

    expect(buttonsByText(container, "Cancel")).toHaveLength(0);
    fireEvent.click(firstButton(container, "Later"));
    expect(closeModal).toHaveBeenCalledTimes(1);
  });

  it("offers no restart button on a Steam build without RestartPC", async () => {
    vi.stubGlobal("SteamClient", { System: {}, User: { StartRestart: vi.fn() } });
    vi.mocked(backend.getDataLocationCandidates).mockResolvedValue({ candidates: [OLD_COPY] });
    vi.mocked(backend.chooseDataLocation).mockResolvedValue({ success: true });

    const { container } = render(<DataLocationModalHost />);
    await flushAsync();
    fireEvent.click(firstButton(container, "Use this copy"));
    await flushAsync();

    expect(buttonsByText(container, "Restart device now")).toHaveLength(0);
    expect(container.textContent).toContain("Restarting your Steam Deck");
  });

  it("keeps the choice open and shows why when the answer is refused", async () => {
    vi.mocked(backend.getDataLocationCandidates).mockResolvedValue({ candidates: [OLD_COPY] });
    vi.mocked(backend.chooseDataLocation).mockResolvedValue({
      success: false,
      reason: "write_failed",
      message: "Could not record your choice: Read-only file system",
    });

    const { container } = render(<DataLocationModalHost />);
    await flushAsync();
    fireEvent.click(firstButton(container, "Use this copy"));
    await flushAsync();

    expect(container.textContent).toContain("Read-only file system");
    expect(useCopyButtons(container)).toHaveLength(1);
    expect(firstButton(container, "Use this copy").disabled).toBe(false);
    expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("write_failed"));
  });

  it("surfaces a rejected answer instead of leaving the modal silent", async () => {
    vi.mocked(backend.getDataLocationCandidates).mockResolvedValue({ candidates: [OLD_COPY] });
    vi.mocked(backend.chooseDataLocation).mockRejectedValue(new Error("backend gone"));

    const { container } = render(<DataLocationModalHost />);
    await flushAsync();
    fireEvent.click(firstButton(container, "Use this copy"));
    await flushAsync();

    expect(container.textContent).toContain("backend gone");
    expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("threw"));
    expect(useCopyButtons(container)).toHaveLength(1);
  });

  it("cancels without recording anything", async () => {
    vi.mocked(backend.getDataLocationCandidates).mockResolvedValue({ candidates: [OLD_COPY] });
    const closeModal = vi.fn();

    const { container } = render(<DataLocationModalHost closeModal={closeModal} />);
    await flushAsync();
    fireEvent.click(firstButton(container, "Cancel"));

    expect(closeModal).toHaveBeenCalledTimes(1);
    expect(vi.mocked(backend.chooseDataLocation)).not.toHaveBeenCalled();
  });

  it("reports a candidate read that threw", async () => {
    vi.mocked(backend.getDataLocationCandidates).mockRejectedValue(new Error("no backend"));

    const { container } = render(<DataLocationModalHost />);
    await flushAsync();

    expect(container.textContent).toContain("Could not read the older data folders.");
    expect(logSpy).toHaveBeenCalledWith(expect.stringContaining("no backend"));
  });

  it("cannot be dismissed by clicking away while the answer is in flight", async () => {
    vi.mocked(backend.getDataLocationCandidates).mockResolvedValue({ candidates: [OLD_COPY] });
    let settle: (value: { success: true }) => void = () => {};
    vi.mocked(backend.chooseDataLocation).mockReturnValue(
      new Promise<{ success: true }>((resolve) => {
        settle = resolve;
      }),
    );

    const { container } = render(<DataLocationModalHost />);
    await flushAsync();
    fireEvent.click(firstButton(container, "Use this copy"));
    await flushAsync();

    expect(capturedModalCloseFns[capturedModalCloseFns.length - 1]).toBeUndefined();
    await act(async () => {
      settle({ success: true });
      await Promise.resolve();
    });
  });
});
