import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { DownloadingButton } from "./DownloadingButton";
import { throbberIn } from "../../test-utils/throbber";

const renderButton = (props: Partial<Parameters<typeof DownloadingButton>[0]> = {}) =>
  render(
    <DownloadingButton
      progressRatio={0.45}
      isExtracting={false}
      isPaused={false}
      isResumable={true}
      onPauseResume={vi.fn()}
      onCancel={vi.fn()}
      {...props}
    />,
  );

describe("DownloadingButton", () => {
  it("reads the transfer's percentage and hands pause and cancel to their presses", () => {
    const onCancel = vi.fn();
    const onPauseResume = vi.fn();
    renderButton({ onCancel, onPauseResume });

    expect(screen.getByRole("progressbar")).toHaveTextContent(/^45%$/);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "45");
    fireEvent.click(screen.getByLabelText("Cancel download"));
    expect(onCancel).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText("Pause download"));
    expect(onPauseResume).toHaveBeenCalledTimes(1);
  });

  it("fills as far as the transfer has come, in the desktop client's colours", () => {
    const { container } = renderButton({ progressRatio: 1 });

    const fill = container.querySelector<HTMLElement>(".tender-desktop-dl-fill");
    expect(fill?.style.width).toBe("100%");
    expect(fill?.style.background).toBe("linear-gradient(to right, rgb(112, 214, 29) 0%, rgb(1, 167, 91) 60%)");
  });

  it("offers no pause where the download cannot be resumed", () => {
    renderButton({ isResumable: false });

    expect(screen.queryByLabelText("Pause download")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Cancel download")).toBeInTheDocument();
  });

  it("offers neither pause nor cancel while the download is unpacked, with the ring in cancel's place", () => {
    renderButton({ progressRatio: 0.8, isExtracting: true });

    expect(screen.getByRole("progressbar")).toHaveTextContent("Extracting… 80%");
    expect(screen.queryByLabelText("Cancel download")).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Pause download")).not.toBeInTheDocument();
    const unpacking = screen.getByLabelText("Extracting");
    expect(unpacking).toBeDisabled();
    expect(unpacking).toHaveStyle({ cursor: "default" });
    expect(throbberIn(unpacking)).not.toBeNull();
  });

  it("draws no ring while the download runs", () => {
    const { container } = renderButton();

    expect(throbberIn(container)).toBeNull();
  });

  it("reads Paused with its percentage, glows amber, and offers resume", () => {
    const onPauseResume = vi.fn();
    const { container } = renderButton({ progressRatio: 0.25, isPaused: true, onPauseResume });

    expect(screen.getByRole("progressbar")).toHaveTextContent("Paused (25%)");
    const group = container.querySelector<HTMLElement>(".tender-desktop-play-btn-group");
    expect(group?.style.boxShadow).toContain("rgba(212,167,44,0.7)");
    fireEvent.click(screen.getByLabelText("Resume download"));
    expect(onPauseResume).toHaveBeenCalledTimes(1);
  });
});
