import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, act, fireEvent } from "@testing-library/react";
import { useEffect, FC } from "react";
import { showModal } from "@decky/ui";
import {
  DataLocationNotice,
  DATA_LOCATION_CHOICE_TITLE,
  DATA_LOCATION_FAILED_TITLE,
  dataLocationFailedMessage,
} from "./DataLocationNotice";
import { getDataLocationNotice } from "../api/backend";
import { fetchDataLocationState, setDataLocationState } from "../utils/dataLocationStore";

const flushAsync = () =>
  act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });

/**
 * The real notice behind the plugin-load fetch, so the callable → store →
 * render pipeline is exercised end-to-end without mounting all of MainPage.
 */
const NoticeHost: FC = () => {
  useEffect(() => {
    fetchDataLocationState().catch(() => {});
  }, []);
  return <DataLocationNotice />;
};

function buttonByText(container: HTMLElement, text: string): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll("button")).find((b) => b.textContent === text) as
    HTMLButtonElement | undefined;
}

describe("dataLocationFailedMessage", () => {
  it("leads with the backend's reason, which is the only thing that says what to fix", () => {
    const message = dataLocationFailedMessage("[Errno 28] No space left on device");
    expect(message).toContain("[Errno 28] No space left on device");
    expect(message).toContain("try again the next time it starts");
  });

  it("still says the data is safe when no reason came through", () => {
    expect(dataLocationFailedMessage(null)).toContain("Nothing was lost");
  });
});

describe("DataLocationNotice", () => {
  beforeEach(() => {
    setDataLocationState({ pending: false, kind: null, message: null });
    vi.mocked(getDataLocationNotice).mockReset();
    vi.mocked(showModal).mockReset();
  });

  it("renders nothing while no condition stands", async () => {
    vi.mocked(getDataLocationNotice).mockResolvedValue({ pending: false, kind: null, message: null });
    const { container } = render(<NoticeHost />);
    await flushAsync();

    expect(container.querySelector('[data-testid="data-location-notice"]')).toBeNull();
  });

  it("raises the choice card with a button that opens its modal", async () => {
    vi.mocked(getDataLocationNotice).mockResolvedValue({ pending: true, kind: "choice", message: null });
    const { container } = render(<NoticeHost />);
    await flushAsync();

    const card = container.querySelector('[data-testid="data-location-notice"]');
    expect(card).not.toBeNull();
    expect(card!.textContent).toContain(DATA_LOCATION_CHOICE_TITLE);

    const button = buttonByText(container, "Choose a copy");
    expect(button).toBeDefined();
    fireEvent.click(button!);
    expect(vi.mocked(showModal)).toHaveBeenCalledTimes(1);
  });

  it("raises the failure card with the backend reason and no action", async () => {
    vi.mocked(getDataLocationNotice).mockResolvedValue({
      pending: true,
      kind: "failed",
      message: "[Errno 13] Permission denied",
    });
    const { container } = render(<NoticeHost />);
    await flushAsync();

    const card = container.querySelector('[data-testid="data-location-notice"]');
    expect(card!.textContent).toContain(DATA_LOCATION_FAILED_TITLE);
    expect(card!.textContent).toContain("[Errno 13] Permission denied");
    expect(buttonByText(container, "Choose a copy")).toBeUndefined();
  });

  it("makes the actionless failure card a focus stop, not a container", async () => {
    // It sits at the bottom of the status block with no button under it, and a
    // QAM region scrolls only by moving focus into it — a bare Focusable would
    // leave the card unreachable and therefore unread.
    vi.mocked(getDataLocationNotice).mockResolvedValue({ pending: true, kind: "failed", message: null });
    const { container } = render(<NoticeHost />);
    await flushAsync();

    const card = container.querySelector('[data-testid="data-location-notice"]');
    expect(card!.parentElement).toHaveAttribute("tabindex", "0");
  });
});
