import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, fireEvent } from "@testing-library/react";
import { createElement } from "react";
import { ValidatingModalShell } from "./ValidatingModalShell";

// Local @decky/ui mock (mirrors ConnectModal.test.tsx): ModalRoot passes its
// children through (the shell owns its own footer buttons); DialogButton renders
// a real <button> forwarding onClick + disabled + children so the primary and
// Cancel buttons are clickable and their disabled state is assertable (a disabled
// button swallows the click in happy-dom, mirroring the real gate).
type AnyProps = Record<string, unknown> & { children?: unknown };

vi.mock("@decky/ui", () => ({
  // Steam's ModalRoot answers its own OK (Enter in a field, R2) by running
  // `onOK` unless `bOKDisabled`, then closing unless `bCloseAfterOK` is false; a
  // test presses that OK by firing `submit` inside the modal.
  ModalRoot: (p: AnyProps & { onOK?: () => unknown; closeModal?: () => void }) =>
    createElement(
      "div",
      {
        "data-testid": "modal-root",
        onSubmit: (event: Event) => {
          event.preventDefault();
          if (p["bOKDisabled"] === true) return;
          void Promise.resolve(p.onOK?.()).then(() => {
            if (p["bCloseAfterOK"] !== false) p.closeModal?.();
          });
        },
      },
      p.children as never,
    ),
  DialogButton: ({ children, onClick, disabled }: AnyProps & { onClick?: () => void; disabled?: boolean }) =>
    createElement("button", { onClick, disabled }, children as never),
  // The footer's flow direction is recorded as an attribute so the handover is
  // assertable. What it DOES is a device question — happy-dom has no nav tree,
  // so both spellings render and behave identically here.
  Focusable: (p: AnyProps) =>
    createElement(
      "div",
      { "data-testid": "shell-footer", "data-flow-children": p["flow-children"] as never },
      p.children as never,
    ),
}));

// A minimal, always-present body so children rendering is observable and distinct
// from the title.
const child = () => createElement("div", { "data-testid": "shell-child" }, "field content");

describe("ValidatingModalShell", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders the title, the children, and the primary + Cancel buttons", () => {
    const { getByText, getByTestId } = render(
      <ValidatingModalShell
        title="My Title"
        error={null}
        errorTestId="shell-error"
        submitLabel="Save"
        submitDisabled={false}
        onSubmit={vi.fn()}
      >
        {child()}
      </ValidatingModalShell>,
    );
    expect(getByText("My Title")).toBeTruthy();
    expect(getByTestId("shell-child").textContent).toBe("field content");
    expect(getByText("Save")).toBeTruthy();
    expect(getByText("Cancel")).toBeTruthy();
  });

  it("hides the error line when error is null", () => {
    const { queryByTestId } = render(
      <ValidatingModalShell
        title="My Title"
        error={null}
        errorTestId="shell-error"
        submitLabel="Save"
        submitDisabled={false}
        onSubmit={vi.fn()}
      >
        {child()}
      </ValidatingModalShell>,
    );
    expect(queryByTestId("shell-error")).toBeNull();
  });

  it("shows the error under the given test id when error is non-null", () => {
    const { getByTestId } = render(
      <ValidatingModalShell
        title="My Title"
        error="Something went wrong"
        errorTestId="shell-error"
        submitLabel="Save"
        submitDisabled={false}
        onSubmit={vi.fn()}
      >
        {child()}
      </ValidatingModalShell>,
    );
    expect(getByTestId("shell-error").textContent).toBe("Something went wrong");
  });

  it("renders the given submit label on the primary button", () => {
    const { getByText, queryByText } = render(
      <ValidatingModalShell
        title="My Title"
        error={null}
        errorTestId="shell-error"
        submitLabel="Verifying…"
        submitDisabled={true}
        onSubmit={vi.fn()}
      >
        {child()}
      </ValidatingModalShell>,
    );
    expect(getByText("Verifying…")).toBeTruthy();
    expect(queryByText("Save")).toBeNull();
  });

  it("calls onSubmit when the enabled primary button is clicked", () => {
    const onSubmit = vi.fn();
    const { getByText } = render(
      <ValidatingModalShell
        title="My Title"
        error={null}
        errorTestId="shell-error"
        submitLabel="Save"
        submitDisabled={false}
        onSubmit={onSubmit}
      >
        {child()}
      </ValidatingModalShell>,
    );
    fireEvent.click(getByText("Save"));
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("disables the primary button and swallows the click when submitDisabled is true", () => {
    const onSubmit = vi.fn();
    const { getByText } = render(
      <ValidatingModalShell
        title="My Title"
        error={null}
        errorTestId="shell-error"
        submitLabel="Save"
        submitDisabled={true}
        onSubmit={onSubmit}
      >
        {child()}
      </ValidatingModalShell>,
    );
    expect(getByText("Save")).toBeDisabled();
    fireEvent.click(getByText("Save"));
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("calls closeModal when Cancel is clicked", () => {
    const closeModal = vi.fn();
    const { getByText } = render(
      <ValidatingModalShell
        closeModal={closeModal}
        title="My Title"
        error={null}
        errorTestId="shell-error"
        submitLabel="Save"
        submitDisabled={false}
        onSubmit={vi.fn()}
      >
        {child()}
      </ValidatingModalShell>,
    );
    fireEvent.click(getByText("Cancel"));
    expect(closeModal).toHaveBeenCalledTimes(1);
  });

  it("does not throw when Cancel is clicked with no closeModal provided", () => {
    const { getByText } = render(
      <ValidatingModalShell
        title="My Title"
        error={null}
        errorTestId="shell-error"
        submitLabel="Save"
        submitDisabled={false}
        onSubmit={vi.fn()}
      >
        {child()}
      </ValidatingModalShell>,
    );
    expect(() => fireEvent.click(getByText("Cancel"))).not.toThrow();
  });

  it("asks for horizontal traversal across the two footer buttons", () => {
    // The buttons sit side by side, and a plain container answers to up/down —
    // which is what this footer did on the device until it became a Focusable.
    // Every modal using this shell inherits the answer, so it is pinned here.
    const { getByTestId } = render(
      <ValidatingModalShell
        title="My Title"
        error={null}
        errorTestId="shell-error"
        submitLabel="Save"
        submitDisabled={false}
        onSubmit={vi.fn()}
      >
        {child()}
      </ValidatingModalShell>,
    );
    expect(getByTestId("shell-footer").getAttribute("data-flow-children")).toBe("horizontal");
  });
  describe("Steam's own OK (Enter in a field, R2)", () => {
    const shell = (props: { submitDisabled?: boolean; dialogOKSubmits?: boolean }) => {
      const onSubmit = vi.fn();
      const closeModal = vi.fn();
      const view = render(
        <ValidatingModalShell
          closeModal={closeModal}
          title="My Title"
          error={null}
          errorTestId="shell-error"
          submitLabel="Save"
          submitDisabled={props.submitDisabled ?? false}
          onSubmit={onSubmit}
          {...(props.dialogOKSubmits === undefined ? {} : { dialogOKSubmits: props.dialogOKSubmits })}
        >
          {child()}
        </ValidatingModalShell>,
      );
      return { ...view, onSubmit, closeModal };
    };

    it("with dialogOKSubmits, runs onSubmit and leaves the modal open", async () => {
      const { getByTestId, onSubmit, closeModal } = shell({ dialogOKSubmits: true });

      fireEvent.submit(getByTestId("shell-child"));

      await vi.waitFor(() => expect(onSubmit).toHaveBeenCalledTimes(1));
      await Promise.resolve();
      expect(closeModal).not.toHaveBeenCalled();
    });

    it("with dialogOKSubmits, is held back by submitDisabled as the primary button is", async () => {
      const { getByTestId, onSubmit, closeModal } = shell({ dialogOKSubmits: true, submitDisabled: true });

      fireEvent.submit(getByTestId("shell-child"));
      await Promise.resolve();

      expect(onSubmit).not.toHaveBeenCalled();
      expect(closeModal).not.toHaveBeenCalled();
    });

    it("without it, closes the modal with nothing submitted, as Steam does", async () => {
      const { getByTestId, onSubmit, closeModal } = shell({});

      fireEvent.submit(getByTestId("shell-child"));

      await vi.waitFor(() => expect(closeModal).toHaveBeenCalledTimes(1));
      expect(onSubmit).not.toHaveBeenCalled();
    });
  });
});
