import { describe, it, expect, vi } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { UpdatesSection, NOT_INSTALLED_PROGRAM } from "./UpdatesSection";
import type { UpdateNoticeState } from "../../utils/updateNoticeStore";

const STATE: UpdateNoticeState = {
  available: true,
  newer: true,
  latestVersion: "0.34.0",
  currentVersion: "0.33.0",
  enabled: true,
  installedProgram: true,
};

const renderSection = (over: Partial<UpdateNoticeState> = {}, props: { checking?: boolean; result?: string } = {}) => {
  const onEnabledChange = vi.fn();
  const onCheckNow = vi.fn();
  const utils = render(
    <UpdatesSection
      update={{ ...STATE, ...over }}
      checking={props.checking ?? false}
      result={props.result ?? ""}
      onEnabledChange={onEnabledChange}
      onCheckNow={onCheckNow}
    />,
  );
  return { ...utils, onEnabledChange, onCheckNow };
};

describe("UpdatesSection", () => {
  it("states the installed and the available version", () => {
    const { getByTestId } = renderSection();
    expect(getByTestId("updates-installed").textContent).toBe("0.33.0");
    expect(getByTestId("updates-available").textContent).toBe("0.34.0");
  });

  it("still names a newer release the card was dismissed for", () => {
    const { getByTestId } = renderSection({ available: false });
    expect(getByTestId("updates-available").textContent).toBe("0.34.0");
  });

  it("says when nothing newer is out", () => {
    const { getByTestId } = renderSection({ available: false, newer: false, latestVersion: "0.33.0" });
    expect(getByTestId("updates-available").textContent).toBe("None newer");
  });

  it("says when no check has established anything yet", () => {
    const { getByTestId } = renderSection({ available: false, newer: false, latestVersion: null });
    expect(getByTestId("updates-available").textContent).toBe("Not known yet");
  });

  it("says the daily check is off rather than claiming anything about releases", () => {
    const { getByTestId } = renderSection({ enabled: false, available: false, newer: false, latestVersion: null });
    expect(getByTestId("updates-available").textContent).toContain("the daily check is off");
  });

  it("a run from a checkout says it is a development build and points at the installer", () => {
    const { getByTestId } = renderSection({ installedProgram: false });
    expect(NOT_INSTALLED_PROGRAM).toBe("Development build — install updates with the installer.");
    expect(getByTestId("updates-not-installed").textContent).toBe(NOT_INSTALLED_PROGRAM);
  });

  it("the installed program carries no such line", () => {
    const { queryByTestId } = renderSection();
    expect(queryByTestId("updates-not-installed")).toBeNull();
  });

  it("the switch shows its state and reports a flip", () => {
    const { getByTestId, onEnabledChange } = renderSection({ enabled: true });
    const toggle = getByTestId("toggle-input") as HTMLInputElement;
    expect(toggle.checked).toBe(true);

    fireEvent.click(toggle);

    expect(onEnabledChange).toHaveBeenCalledWith(false);
  });

  it("Check now presses through, and is dead while a check is in flight", () => {
    const idle = renderSection();
    fireEvent.click(idle.getByText("Check now"));
    expect(idle.onCheckNow).toHaveBeenCalledTimes(1);
    idle.unmount();

    const busy = renderSection({}, { checking: true });
    expect((busy.getByText("Checking…") as HTMLButtonElement).disabled).toBe(true);
  });

  it("shows the last check's result, and nothing where there is none", () => {
    expect(renderSection().queryByTestId("updates-result")).toBeNull();
    const { getByTestId } = renderSection({}, { result: "You have the newest release." });
    expect(getByTestId("updates-result").textContent).toBe("You have the newest release.");
  });

  it("offers no install button", () => {
    const { container } = renderSection();
    const buttons = [...container.querySelectorAll("button")].map((b) => b.textContent);
    expect(buttons).toEqual(["Check now"]);
  });
});
