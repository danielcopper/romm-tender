import "@testing-library/jest-dom/vitest";
import { describe, it, expect } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { Window as HappyWindow } from "happy-dom";
import { useEffect, type FC } from "react";
import type { AskDialog } from "./askDialog";
import { useDialogHost } from "./useDialogHost";

let ask!: AskDialog;

const Host: FC = () => {
  const host = useDialogHost();
  useEffect(() => {
    ask = host.ask;
  }, [host.ask]);
  return (
    <div data-testid="host-root">
      <span>page</span>
      {host.element}
    </div>
  );
};

const Question: FC<{ label: string; onAnswer: (answer: string) => void }> = ({ label, onAnswer }) => (
  <div role="dialog" aria-label={label}>
    <button type="button" onClick={() => onAnswer(`${label}:yes`)}>
      Yes
    </button>
  </div>
);

describe("useDialogHost", () => {
  it("draws the dialog outside the asking tree and settles with the answer given", async () => {
    render(<Host />);
    let answer!: Promise<string>;
    act(() => {
      answer = ask("dismissed", (resolve) => <Question label="first" onAnswer={resolve} />);
    });

    const dialog = screen.getByRole("dialog", { name: "first" });
    expect(screen.getByTestId("host-root")).not.toContainElement(dialog);
    expect(dialog.parentElement).toBe(document.body);

    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    await expect(answer).resolves.toBe("first:yes");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("draws the dialog into the body of the asker's own document, not the panel's", () => {
    const other = new HappyWindow();
    const container = other.document.createElement("div");
    other.document.body.appendChild(container);
    const { unmount } = render(<Host />, { container: container as unknown as HTMLElement });
    act(() => {
      void ask("dismissed", (resolve) => <Question label="there" onAnswer={resolve} />);
    });

    const drawn = other.document.body.querySelector('[aria-label="there"]');
    expect(drawn?.parentElement).toBe(other.document.body);
    expect(document.body.querySelector('[aria-label="there"]')).toBeNull();
    unmount();
  });

  it("settles an open question with its dismissed answer when the asker unmounts", async () => {
    const { unmount } = render(<Host />);
    let answer!: Promise<string>;
    act(() => {
      answer = ask("dismissed", (resolve) => <Question label="first" onAnswer={resolve} />);
    });
    unmount();
    await expect(answer).resolves.toBe("dismissed");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("answers a question asked after unmount with its dismissed answer, drawing nothing", async () => {
    const { unmount } = render(<Host />);
    unmount();
    await expect(ask("gone", () => <Question label="late" onAnswer={() => {}} />)).resolves.toBe("gone");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("dismisses the open question when a newer one is asked", async () => {
    render(<Host />);
    let first!: Promise<string>;
    let second!: Promise<string>;
    act(() => {
      first = ask("first-dismissed", (resolve) => <Question label="first" onAnswer={resolve} />);
    });
    act(() => {
      second = ask("second-dismissed", (resolve) => <Question label="second" onAnswer={resolve} />);
    });

    await expect(first).resolves.toBe("first-dismissed");
    expect(screen.queryByRole("dialog", { name: "first" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Yes" }));
    await expect(second).resolves.toBe("second:yes");
  });

  it("leaves a newer dialog open when an older one answers again", async () => {
    render(<Host />);
    let answerFirst!: (value: string) => void;
    let first!: Promise<string>;
    act(() => {
      first = ask("dismissed", (resolve) => {
        answerFirst = resolve;
        return <Question label="first" onAnswer={resolve} />;
      });
    });
    act(() => answerFirst("one"));
    await expect(first).resolves.toBe("one");
    act(() => {
      void ask("dismissed", (resolve) => <Question label="second" onAnswer={resolve} />);
    });

    act(() => answerFirst("again"));

    expect(screen.getByRole("dialog", { name: "second" })).toBeInTheDocument();
  });
});
