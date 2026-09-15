import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent, act, within } from "@testing-library/react";
import { createElement, forwardRef } from "react";
import { CustomHeadersModal } from "./CustomHeadersModal";

// Local @decky/ui mock (mirrors SgdbApiKeyModal.test.tsx): ModalRoot passes its
// children through; DialogButton renders a real <button> forwarding onClick +
// disabled + children; TextField forwards label + description + value + onChange
// to a real <input>; Focusable forwards its ref and data-testid to a wrapping
// div so a row's own fields and buttons can be queried within it.
type AnyProps = Record<string, unknown> & { children?: unknown };
interface TextFieldProps {
  label?: string;
  description?: string;
  value?: string;
  bIsPassword?: boolean;
  onChange?: (e: { target: { value: string } }) => void;
}

vi.mock("@decky/ui", () => ({
  ModalRoot: (p: AnyProps) => createElement("div", { "data-testid": "modal-root" }, p.children as never),
  Focusable: forwardRef<HTMLDivElement, AnyProps>((p, ref) =>
    createElement("div", { ref, style: p.style, "data-testid": p["data-testid"] as never }, p.children as never),
  ),
  DialogButton: ({ children, onClick, disabled }: AnyProps & { onClick?: () => void; disabled?: boolean }) =>
    createElement("button", { onClick, disabled }, children as never),
  // `placeholder` is not on TextFieldProps — @decky/ui types the component's
  // props as HTMLAttributes, a level above where React declares it.
  TextField: (p: TextFieldProps & { placeholder?: string }) =>
    createElement("input", {
      "data-testid": `field-${p.label ?? ""}`,
      "data-description": p.description ?? "",
      "data-placeholder": p.placeholder ?? "",
      "data-is-password": p.bIsPassword ? "true" : "false",
      value: p.value ?? "",
      onChange: (e: unknown) => p.onChange?.(e as { target: { value: string } }),
    }),
}));

const saveOk = () => vi.fn().mockResolvedValue({ success: true });
const saveFail = (message: string) => vi.fn().mockResolvedValue({ success: false, message });

// Flush the floating submit() promise (onClick fires `void submit()`), then let
// React apply the resulting state updates.
async function flushSubmit() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

const rows = (container: HTMLElement): HTMLElement[] =>
  Array.from(container.querySelectorAll<HTMLElement>('[data-testid^="header-row-"]'));

const nameField = (row: HTMLElement): HTMLInputElement =>
  within(row).getByTestId("field-Header name") as HTMLInputElement;
const valueField = (row: HTMLElement): HTMLInputElement => within(row).getByTestId("field-Value") as HTMLInputElement;

describe("CustomHeadersModal", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows no rows and an add affordance when nothing is configured", () => {
    const { container, getByText } = render(<CustomHeadersModal storedNames={[]} onSave={saveOk()} />);
    expect(rows(container)).toHaveLength(0);
    expect(getByText("No custom headers.")).toBeTruthy();
    expect(getByText("Add header")).toBeTruthy();
  });

  it("renders a stored header by name with an empty, obscured value field", () => {
    const { container } = render(<CustomHeadersModal storedNames={["P-Access-Token"]} onSave={saveOk()} />);
    const [row] = rows(container);
    expect(nameField(row!).value).toBe("P-Access-Token");
    expect(valueField(row!).value).toBe("");
    expect(valueField(row!).getAttribute("data-is-password")).toBe("true");
    // The dots sit in the field and the sentence sits below it. Whether Steam's
    // own TextField renders a prop its type does not declare is a device
    // question — what is pinned here is only that the modal hands it over.
    expect(valueField(row!).getAttribute("data-placeholder")).toBe("••••");
    expect(valueField(row!).getAttribute("data-description")).toBe("stored — leave blank to keep it");
  });

  it("saves an untouched stored row as 'keep', carrying no value", async () => {
    const onSave = saveOk();
    const { getByText } = render(<CustomHeadersModal storedNames={["P-Access-Token"]} onSave={onSave} />);
    fireEvent.click(getByText("Save"));
    await flushSubmit();
    expect(onSave).toHaveBeenCalledWith([{ name: "P-Access-Token", value_action: "keep" }]);
  });

  it("flips a stored row to 'set' the moment its value is typed into", async () => {
    const onSave = saveOk();
    const { container, getByText } = render(<CustomHeadersModal storedNames={["P-Access-Token"]} onSave={onSave} />);
    fireEvent.change(valueField(rows(container)[0]!), { target: { value: "fresh" } });
    fireEvent.click(getByText("Save"));
    await flushSubmit();
    expect(onSave).toHaveBeenCalledWith([{ name: "P-Access-Token", value_action: "set", value: "fresh" }]);
  });

  it("saves a renamed stored row as 'set' — a kept value is keyed by its name", async () => {
    const onSave = saveOk();
    const { container, getByText } = render(<CustomHeadersModal storedNames={["P-Access-Token"]} onSave={onSave} />);
    fireEvent.change(nameField(rows(container)[0]!), { target: { value: "P-Access-Token-Id" } });
    fireEvent.click(getByText("Save"));
    await flushSubmit();
    expect(onSave).toHaveBeenCalledWith([{ name: "P-Access-Token-Id", value_action: "set", value: "" }]);
  });

  it("adds a row and saves it as 'set'", async () => {
    const onSave = saveOk();
    const { container, getByText } = render(<CustomHeadersModal storedNames={[]} onSave={onSave} />);
    fireEvent.click(getByText("Add header"));
    fireEvent.change(nameField(rows(container)[0]!), { target: { value: "X-Token" } });
    fireEvent.change(valueField(rows(container)[0]!), { target: { value: "abc" } });
    fireEvent.click(getByText("Save"));
    await flushSubmit();
    expect(onSave).toHaveBeenCalledWith([{ name: "X-Token", value_action: "set", value: "abc" }]);
  });

  it("removes the row it was asked to remove, not the one at its old position", async () => {
    const onSave = saveOk();
    const { container, getByText } = render(
      <CustomHeadersModal storedNames={["X-First", "X-Second"]} onSave={onSave} />,
    );
    fireEvent.click(within(rows(container)[0]!).getByText("Remove"));
    expect(rows(container)).toHaveLength(1);
    expect(nameField(rows(container)[0]!).value).toBe("X-Second");

    fireEvent.click(getByText("Save"));
    await flushSubmit();
    // The survivor keeps its stored value: removing its neighbour must not
    // shift which stored name this row belongs to.
    expect(onSave).toHaveBeenCalledWith([{ name: "X-Second", value_action: "keep" }]);
  });

  it("saves an emptied list, which is how every header is cleared", async () => {
    const onSave = saveOk();
    const { container, getByText } = render(<CustomHeadersModal storedNames={["X-Token"]} onSave={onSave} />);
    fireEvent.click(within(rows(container)[0]!).getByText("Remove"));
    fireEvent.click(getByText("Save"));
    await flushSubmit();
    expect(onSave).toHaveBeenCalledWith([]);
  });

  it("closes on a successful save", async () => {
    const closeModal = vi.fn();
    const { getByText } = render(<CustomHeadersModal closeModal={closeModal} storedNames={[]} onSave={saveOk()} />);
    fireEvent.click(getByText("Save"));
    await flushSubmit();
    expect(closeModal).toHaveBeenCalledTimes(1);
  });

  it("stays open and surfaces the backend's refusal", async () => {
    const closeModal = vi.fn();
    const { container, getByText, getByTestId } = render(
      <CustomHeadersModal closeModal={closeModal} storedNames={[]} onSave={saveFail("'Host' is set by the plugin")} />,
    );
    fireEvent.click(getByText("Add header"));
    fireEvent.change(nameField(rows(container)[0]!), { target: { value: "Host" } });
    fireEvent.click(getByText("Save"));
    await flushSubmit();
    expect(closeModal).not.toHaveBeenCalled();
    expect(getByTestId("custom-headers-error").textContent).toBe("'Host' is set by the plugin");
  });

  it("reports a rejected save rather than closing on it", async () => {
    const closeModal = vi.fn();
    const onSave = vi.fn().mockRejectedValue(new Error("bridge down"));
    const { getByText, getByTestId } = render(
      <CustomHeadersModal closeModal={closeModal} storedNames={[]} onSave={onSave} />,
    );
    fireEvent.click(getByText("Save"));
    await flushSubmit();
    expect(closeModal).not.toHaveBeenCalled();
    expect(getByTestId("custom-headers-error").textContent).toContain("Could not save the headers");
  });

  it("clears a stale error as soon as the user edits a field", async () => {
    const { container, getByText, getByTestId, queryByTestId } = render(
      <CustomHeadersModal storedNames={["X-Token"]} onSave={saveFail("'X-Token' needs a value.")} />,
    );
    fireEvent.click(getByText("Save"));
    await flushSubmit();
    expect(getByTestId("custom-headers-error")).toBeTruthy();

    fireEvent.change(valueField(rows(container)[0]!), { target: { value: "v" } });
    expect(queryByTestId("custom-headers-error")).toBeNull();
  });

  it("Cancel closes without saving", () => {
    const closeModal = vi.fn();
    const onSave = saveOk();
    const { getByText } = render(<CustomHeadersModal closeModal={closeModal} storedNames={[]} onSave={onSave} />);
    fireEvent.click(getByText("Cancel"));
    expect(closeModal).toHaveBeenCalledTimes(1);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("disables Save while one is in flight, so a double press cannot save twice", async () => {
    let resolveSave: ((result: { success: boolean }) => void) | undefined;
    const onSave = vi.fn().mockReturnValue(
      new Promise<{ success: boolean }>((resolve) => {
        resolveSave = resolve;
      }),
    );
    const { getByText } = render(<CustomHeadersModal storedNames={[]} onSave={onSave} />);
    fireEvent.click(getByText("Save"));
    await act(async () => {
      await Promise.resolve();
    });
    const button = getByText("Saving…") as HTMLButtonElement;
    expect(button.disabled).toBe(true);

    fireEvent.click(button);
    expect(onSave).toHaveBeenCalledTimes(1);

    resolveSave?.({ success: true });
    await flushSubmit();
  });
});
