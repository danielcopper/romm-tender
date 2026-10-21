import "@testing-library/jest-dom/vitest";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, waitFor } from "@testing-library/react";
import * as backend from "../../api/backend";
import { RommUrlModal } from "./RommUrlModal";

type Answer = { success: boolean; message: string };

const REFUSED: Answer = {
  success: false,
  message:
    "Tender cannot sign in through a user name and password in the RomM URL — leave them out. " +
    "Sign in to RomM under RomM Account; a login for a proxy in front of RomM goes into Custom headers.",
};
const ACCEPTED: Answer = { success: true, message: "Settings saved" };

function open(save: (text: string) => Promise<Answer>, value = "https://romm.local") {
  const closeModal = vi.fn();
  const saveSpy = vi.fn(save);
  const view = render(<RommUrlModal closeModal={closeModal} value={value} save={saveSpy} />);
  const field = view.getByTestId("text-field") as HTMLInputElement;
  const type = (text: string) => fireEvent.change(field, { target: { value: text } });
  const ok = () => fireEvent.click(view.getByText("OK"));
  return { ...view, field, type, ok, closeModal, save: saveSpy };
}

describe("RommUrlModal", () => {
  it("opens on the stored URL", () => {
    const { field } = open(async () => ACCEPTED);

    expect(field.value).toBe("https://romm.local");
  });

  it("a refused save shows its message under the field in amber, and keeps the window open with the text", async () => {
    const window = open(async () => REFUSED);
    window.type("https://user:fake-pw@romm.local");

    window.ok();

    const refusal = await window.findByTestId("romm-url-refusal");
    expect(refusal).toHaveTextContent(REFUSED.message);
    expect(refusal.getAttribute("style")).toContain("color: #d4a72c");
    expect(window.save).toHaveBeenCalledWith("https://user:fake-pw@romm.local");
    expect(window.field.value).toBe("https://user:fake-pw@romm.local");
    expect(window.closeModal).not.toHaveBeenCalled();
  });

  it("Enter or R2 saves as OK does, and a refusal keeps the window open with the text", async () => {
    const window = open(async () => REFUSED);
    window.type("romm.local");

    fireEvent.submit(window.field);

    expect(await window.findByTestId("romm-url-refusal")).toHaveTextContent(REFUSED.message);
    expect(window.save).toHaveBeenCalledWith("romm.local");
    expect(window.field.value).toBe("romm.local");
    expect(window.closeModal).not.toHaveBeenCalled();
  });

  it("a save that could not reach the backend is shown the same way", async () => {
    const window = open(async () => ({ success: false, message: "Failed to save settings" }));

    window.ok();

    expect(await window.findByTestId("romm-url-refusal")).toHaveTextContent("Failed to save settings");
    expect(window.closeModal).not.toHaveBeenCalled();
  });

  it("editing the text drops a shown refusal", async () => {
    const window = open(async () => REFUSED);
    window.ok();
    await window.findByTestId("romm-url-refusal");

    window.type("https://romm.local:8080");

    expect(window.queryByTestId("romm-url-refusal")).toBeNull();
  });

  it("a refusal that arrives after the text was edited is dropped", async () => {
    let answer: (value: Answer) => void = () => undefined;
    const window = open(() => new Promise((resolve) => (answer = resolve)));
    window.ok();

    window.type("https://romm.local:8080");
    answer(REFUSED);

    await waitFor(() => expect(window.getByText("OK")).not.toBeDisabled());
    expect(window.queryByTestId("romm-url-refusal")).toBeNull();
  });

  it("an accepted save closes the window once, by OK or by Enter", async () => {
    const byOk = open(async () => ACCEPTED);
    byOk.ok();
    await waitFor(() => expect(byOk.closeModal).toHaveBeenCalledTimes(1));
    byOk.unmount();

    const byEnter = open(async () => ACCEPTED);
    fireEvent.submit(byEnter.field);
    await waitFor(() => expect(byEnter.closeModal).toHaveBeenCalledTimes(1));
  });

  it("OK and Enter do nothing while the field is empty", async () => {
    const window = open(async () => ACCEPTED);
    window.type("   ");

    expect(window.getByText("OK")).toBeDisabled();
    fireEvent.submit(window.field);
    await Promise.resolve();

    expect(window.save).not.toHaveBeenCalled();
    expect(window.closeModal).not.toHaveBeenCalled();
  });

  it("a save that throws is shown as one that could not reach the backend, and logged without its text", async () => {
    const logSpy = vi.spyOn(backend, "logError").mockImplementation(() => {});
    const window = open(async () => {
      throw new TypeError("token=fake-token-77");
    });

    window.ok();

    expect(await window.findByTestId("romm-url-refusal")).toHaveTextContent("Failed to save settings");
    expect(logSpy).toHaveBeenCalledWith("Failed to save the RomM URL: TypeError");
    expect(logSpy.mock.calls.join(" ")).not.toContain("fake-token-77");
    expect(window.closeModal).not.toHaveBeenCalled();
    logSpy.mockRestore();
  });

  it("Cancel while a save is in flight closes the window, and the answer that follows reaches nothing", async () => {
    let answer: (value: Answer) => void = () => undefined;
    const window = open(() => new Promise((resolve) => (answer = resolve)));
    window.ok();

    fireEvent.click(window.getByText("Cancel"));
    answer(REFUSED);
    await Promise.resolve();
    await Promise.resolve();

    expect(window.closeModal).toHaveBeenCalledTimes(1);
    expect(window.queryByTestId("romm-url-refusal")).toBeNull();
  });

  it("a save that goes through after Cancel does not close the window a second time", async () => {
    let answer: (value: Answer) => void = () => undefined;
    const window = open(() => new Promise((resolve) => (answer = resolve)));
    window.ok();

    fireEvent.click(window.getByText("Cancel"));
    answer(ACCEPTED);
    await Promise.resolve();
    await Promise.resolve();

    expect(window.closeModal).toHaveBeenCalledTimes(1);
  });

  it("Cancel closes the window with nothing saved", () => {
    const window = open(async () => ACCEPTED);

    fireEvent.click(window.getByText("Cancel"));

    expect(window.save).not.toHaveBeenCalled();
    expect(window.closeModal).toHaveBeenCalledTimes(1);
  });
});
